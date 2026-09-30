"""Temporal engine: launch through ``launch_workflow`` with an in-process worker on a unique task queue.

Requires the Temporal dev server (``TEST_TEMPORAL_ADDRESS``, default 127.0.0.1:7233); skipped otherwise.
Flow status is observed exactly as the API sees it — through ``workflow_runs`` (written by the
``workflows.mark_*`` activities the Temporal workflow wrappers call).
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select, update

from aegis_api.lab.core.errors import PermanentError, TransientError
from aegis_api.lab.workflows.ctx import ActivityFailed, ChildWorkflowFailed, WorkflowContext
from aegis_api.lab.workflows.definitions import register_flow
from aegis_api.lab.workflows.registry import ActivityContext, activity
from tests.conftest import requires_db

TEMPORAL_ADDRESS = os.environ.get("TEST_TEMPORAL_ADDRESS", "127.0.0.1:7233")


def _temporal_up() -> bool:
    host, _, port = TEMPORAL_ADDRESS.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=1.0):
            return True
    except OSError:
        return False


requires_temporal = pytest.mark.skipif(not _temporal_up(), reason=f"Temporal is not reachable at {TEMPORAL_ADDRESS}")

pytestmark = [requires_db, pytest.mark.db]

_LOCK = threading.Lock()
CALLS: dict[str, int] = {}


def _count(token: str, label: str) -> int:
    with _LOCK:
        key = f"{token}:{label}"
        CALLS[key] = CALLS.get(key, 0) + 1
        return CALLS[key]


def calls(token: str, label: str) -> int:
    with _LOCK:
        return CALLS.get(f"{token}:{label}", 0)


@activity("test_wft.record", timeout_seconds=30, max_attempts=3, initial_interval_seconds=0.1)
def record_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    n = _count(payload["token"], payload["label"])
    ctx.heartbeat({"label": payload["label"]})
    return {"label": payload["label"], "calls": n, "attempt": ctx.attempt, "actor_kind": ctx.actor.kind}


@activity("test_wft.flaky", timeout_seconds=30, max_attempts=5, initial_interval_seconds=0.1, max_interval_seconds=0.5)
def flaky_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    n = _count(payload["token"], "flaky")
    if n <= int(payload["fail_times"]):
        raise TransientError(f"temporary glitch #{n}")
    return {"calls": n, "attempt": ctx.attempt}


@activity("test_wft.permanent", timeout_seconds=30, max_attempts=5, initial_interval_seconds=0.1)
def permanent_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    _count(payload["token"], "permanent")
    raise PermanentError("never going to work")


async def seq_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    first = await ctx.activity("test_wft.record", {"token": token, "label": "a1"})
    second = await ctx.activity("test_wft.record", {"token": token, "label": "a2"})
    again = await ctx.activity("test_wft.record", {"token": token, "label": "a1"}, step_key="test_wft.record#1")
    return {
        "labels": [first["label"], second["label"]],
        "memoized": again == first,
        "actor_kind": first["actor_kind"],
        "started": ctx.now().isoformat(),
        "id": ctx.uuid(),
    }


async def signal_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = await ctx.wait_signal("go", timeout_seconds=data.get("timeout", 60))
    await ctx.activity("test_wft.record", {"token": data["token"], "label": "after_signal"})
    return {"payload": payload, "timed_out": payload is None}


async def retry_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    return await ctx.activity("test_wft.flaky", {"token": data["token"], "fail_times": data["fail_times"]})


async def fail_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("catch"):
        try:
            await ctx.activity("test_wft.permanent", {"token": data["token"]})
        except ActivityFailed as exc:
            return {"error_type": exc.error_type, "non_retryable": exc.non_retryable}
    return await ctx.activity("test_wft.permanent", {"token": data["token"]})


async def child_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    await ctx.activity("test_wft.record", {"token": data["token"], "label": "child"})
    if data.get("fail"):
        await ctx.activity("test_wft.permanent", {"token": data["token"]})
    return {"tripled": int(data["x"]) * 3}


async def parent_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    try:
        child = await ctx.child(
            "TestTemporalChildFlow",
            {"token": data["token"], "x": 5, "fail": data.get("fail", False)},
            workflow_key="k1",
        )
    except ChildWorkflowFailed as exc:
        return {"child_status": exc.status}
    detached = await ctx.start_detached("TestTemporalChildFlow", {"token": data["token"], "x": 1}, workflow_key="bg")
    return {"child": child, "detached": detached}


for _kind, _fn in (
    ("TestTemporalSeqFlow", seq_flow),
    ("TestTemporalSignalFlow", signal_flow),
    ("TestTemporalRetryFlow", retry_flow),
    ("TestTemporalFailFlow", fail_flow),
    ("TestTemporalChildFlow", child_flow),
    ("TestTemporalParentFlow", parent_flow),
):
    register_flow(_kind, _fn)


# ---------------------------------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def temporal_worker() -> Iterator[str]:
    """Point settings at the dev server with a unique task queue and run a worker in-process."""
    if not _temporal_up():
        pytest.skip(f"Temporal is not reachable at {TEMPORAL_ADDRESS}")
    from aegis_api.config import get_settings
    from aegis_api.lab.workflows.temporal_engine import build_worker, connect_client, reset_bridge

    queue = f"aegis-test-{uuid.uuid4().hex[:10]}"
    patch = pytest.MonkeyPatch()
    settings = get_settings()
    patch.setattr(settings, "temporal_address", TEMPORAL_ADDRESS)
    patch.setattr(settings, "temporal_namespace", "default")
    patch.setattr(settings, "workflow_engine", "temporal")
    patch.setattr(settings, "temporal_task_queue", queue)
    patch.setattr(settings, "temporal_execution_task_queue", f"{queue}-exec")
    reset_bridge()
    stop = threading.Event()
    ready = threading.Event()
    errors: list[BaseException] = []

    async def serve() -> None:
        client = await connect_client()
        worker = build_worker(client, "default", max_concurrent_activities=8, graceful_shutdown_seconds=2)
        assert worker is not None
        async with worker:
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.1)

    def main() -> None:
        try:
            asyncio.run(serve())
        except BaseException as exc:
            errors.append(exc)
            ready.set()

    thread = threading.Thread(target=main, name="test-temporal-worker", daemon=True)
    thread.start()
    assert ready.wait(60), "worker did not start"
    if errors:
        raise errors[0]
    yield queue
    stop.set()
    thread.join(60)
    reset_bridge()
    patch.undo()


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from aegis_api.app import create_app
    from aegis_api.lab.workflows.router import router

    app = create_app()
    if not any(getattr(r, "path", "").startswith("/api/v1/workflow-runs") for r in app.routes):
        app.include_router(router)
    with TestClient(app) as c:
        yield c


def launch(lab, kind: str, data: dict[str, Any], *, key: str = "default") -> uuid.UUID:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import launch_workflow

    with lab.db() as db:
        run = launch_workflow(
            db,
            lab.actor(),
            kind,
            subject_type="test",
            subject_id=str(uuid.uuid4()),
            input=data,
            project_id=lab.project_id,
            workflow_key=key,
        )
        return run.id


def get_run(lab, run_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun

    with lab.db() as db:
        run = db.get(WorkflowRun, run_id)
        assert run is not None
        db.expunge(run)
        return run


def wait_status(lab, run_id: uuid.UUID, statuses: set[str], timeout: float = 60.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while True:
        run = get_run(lab, run_id)
        if run.status in statuses:
            return run
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} stayed {run.status}, expected {statuses} (error={run.error})")
        time.sleep(0.1)


# ---------------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------------
@pytest.mark.temporal
@requires_temporal
def test_launch_runs_on_temporal_and_records_status(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalSeqFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.engine == "temporal"
    assert run.task_queue == temporal_worker
    assert run.external_run_id  # the Temporal run id
    assert run.result["labels"] == ["a1", "a2"]
    assert run.result["memoized"] is True  # an explicit step key runs at most once per execution
    assert run.result["actor_kind"] == "workflow"
    assert (calls(token, "a1"), calls(token, "a2")) == (1, 1)
    assert run.started_at is not None and run.completed_at is not None


@pytest.mark.temporal
@requires_temporal
def test_signal_delivered_to_waiting_temporal_workflow(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import signal_workflow

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalSignalFlow", {"token": token})
    wait_status(lab, run_id, {"WAITING"})
    assert calls(token, "after_signal") == 0

    def send() -> None:
        with lab.db() as db:
            signal_workflow(db, run_id, "go", {"value": 7})

    sender = threading.Thread(target=send)
    sender.start()
    sender.join()
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"payload": {"value": 7}, "timed_out": False}
    assert [s.get("delivered") for s in run.signals] == [True]  # outbox entry marked delivered
    assert calls(token, "after_signal") == 1


@pytest.mark.temporal
@requires_temporal
def test_signal_wait_timeout_on_temporal(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestTemporalSignalFlow", {"token": uuid.uuid4().hex, "timeout": 1})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"payload": None, "timed_out": True}


@pytest.mark.temporal
@requires_temporal
def test_activity_retry_on_temporal(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalRetryFlow", {"token": token, "fail_times": 2})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"calls": 3, "attempt": 3}
    assert calls(token, "flaky") == 3


@pytest.mark.temporal
@requires_temporal
def test_non_retryable_failure_on_temporal(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalFailFlow", {"token": token})
    run = wait_status(lab, run_id, {"FAILED"})
    assert "PermanentError" in (run.error or "")
    assert calls(token, "permanent") == 1

    caught = launch(lab, "TestTemporalFailFlow", {"token": token, "catch": True})
    run = wait_status(lab, caught, {"COMPLETED"})
    assert run.result == {"error_type": "PermanentError", "non_retryable": True}


@pytest.mark.temporal
@requires_temporal
def test_child_and_detached_workflows_on_temporal(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalParentFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result["child"] == {"tripled": 15}
    detached_id = uuid.UUID(run.result["detached"])
    detached = wait_status(lab, detached_id, {"COMPLETED"})
    assert detached.result == {"tripled": 3}
    with lab.db() as db:
        children = db.scalars(select(WorkflowRun).where(WorkflowRun.parent_workflow_run_id == run_id)).all()
        assert {c.external_id for c in children} == {
            f"TestTemporalChildFlow:{run_id}:k1",
            f"TestTemporalChildFlow:{run_id}:bg",
        }
        assert all(c.status == "COMPLETED" and c.engine == "temporal" for c in children)

    failing = launch(lab, "TestTemporalParentFlow", {"token": uuid.uuid4().hex, "fail": True})
    assert wait_status(lab, failing, {"COMPLETED"}).result == {"child_status": "FAILED"}


@pytest.mark.temporal
@requires_temporal
def test_cancel_temporal_workflow(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import cancel_workflow

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTemporalSignalFlow", {"token": token, "timeout": 300})
    wait_status(lab, run_id, {"WAITING"})
    with lab.db() as db:
        cancel_workflow(db, lab.actor(), run_id)
    run = wait_status(lab, run_id, {"CANCELLED"})
    assert run.cancel_requested is True
    assert calls(token, "after_signal") == 0


@pytest.mark.temporal
@requires_temporal
def test_api_detail_includes_temporal_description(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestTemporalSeqFlow", {"token": uuid.uuid4().hex})
    wait_status(lab, run_id, {"COMPLETED"})
    detail = lab.get(f"/api/v1/workflow-runs/{run_id}")
    assert detail.status_code == 200, detail.text
    temporal = detail.json()["temporal"]
    assert temporal["available"] is True
    assert temporal["status"] == "COMPLETED"
    assert temporal["workflow_type"] == "TestTemporalSeqFlow"
    assert detail.json()["external_run_id"] == temporal["run_id"]


@pytest.mark.temporal
@requires_temporal
def test_scheduler_reconciles_runs_closed_in_temporal(lab, temporal_worker) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun
    from aegis_api.lab.workflows import maintenance

    run_id = launch(lab, "TestTemporalSeqFlow", {"token": uuid.uuid4().hex})
    done = wait_status(lab, run_id, {"COMPLETED"})
    # Simulate a worker that died after the workflow closed but before its final status was recorded.
    with lab.db() as db:
        db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(status="RUNNING", result=None, completed_at=None)
            .execution_options(synchronize_session=False)
        )
    assert maintenance.reconcile_temporal_run(lab.org_id, run_id) is True
    run = get_run(lab, run_id)
    assert run.status == "COMPLETED"
    assert run.result == done.result


def test_temporal_start_failure_leaves_run_pending(lab, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No Temporal server at the configured address: the run stays PENDING for the scheduler to retry."""
    from aegis_api.config import get_settings
    from aegis_api.lab.workflows import maintenance
    from aegis_api.lab.workflows.temporal_engine import reset_bridge

    settings = get_settings()
    monkeypatch.setattr(settings, "temporal_address", "127.0.0.1:9")
    monkeypatch.setattr(settings, "workflow_engine", "temporal")
    monkeypatch.setattr(settings, "temporal_connect_timeout_seconds", 1.0)
    reset_bridge()
    try:
        run_id = launch(lab, "TestTemporalSeqFlow", {"token": uuid.uuid4().hex})
        run = get_run(lab, run_id)
        assert (run.status, run.engine, run.external_run_id) == ("PENDING", "temporal", None)
        # Signals are stored in the outbox and survive the outage.
        from aegis_api.lab.workflows.launcher import signal_workflow

        with lab.db() as db:
            signal_workflow(db, run_id, "go", {"later": True})
        assert [s.get("delivered") for s in get_run(lab, run_id).signals] == [False]
        assert maintenance.deliver_pending_signals(lab.org_id)["signals_delivered"] == 0
    finally:
        reset_bridge()
