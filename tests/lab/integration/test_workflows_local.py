"""Local durable workflow engine: replay-based resume, retries, signals, children, cancellation, launcher
semantics, the workflow-run API and stuck-run recovery — against real PostgreSQL.

Test flows and activities are registered here with unique names (``Test*`` kinds, ``test_wfl.*``
activities). Side effects are counted per test token so re-execution after a simulated crash is observable.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from aegis_api.lab.core.errors import ApprovalRequired, PermanentError, TransientError
from aegis_api.lab.workflows.ctx import ActivityFailed, ChildWorkflowFailed, WorkflowContext
from aegis_api.lab.workflows.definitions import register_flow
from aegis_api.lab.workflows.registry import ActivityContext, activity
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

# ---------------------------------------------------------------------------------------------------
# Test activities (side effects counted per token)
# ---------------------------------------------------------------------------------------------------
_LOCK = threading.Lock()
CALLS: dict[str, int] = {}
CRASH: set[str] = set()
FAIL: set[str] = set()
OBSERVED_CANCEL: set[str] = set()


class SimulatedCrash(BaseException):
    """Stands in for a process kill: not an Exception, so the engine records nothing on the way out."""


def _count(token: str, label: str) -> int:
    with _LOCK:
        key = f"{token}:{label}"
        CALLS[key] = CALLS.get(key, 0) + 1
        return CALLS[key]


def calls(token: str, label: str) -> int:
    with _LOCK:
        return CALLS.get(f"{token}:{label}", 0)


@activity("test_wfl.record", max_attempts=3, initial_interval_seconds=0.01, max_interval_seconds=0.05)
def record_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    n = _count(payload["token"], payload["label"])
    return {
        "label": payload["label"],
        "calls": n,
        "attempt": ctx.attempt,
        "actor_kind": ctx.actor.kind,
        "workflow_run_id": str(ctx.workflow_run_id),
    }


@activity("test_wfl.flaky", max_attempts=4, initial_interval_seconds=0.01, max_interval_seconds=0.05)
def flaky_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    n = _count(payload["token"], "flaky")
    if payload["token"] in FAIL or n <= int(payload.get("fail_times", 0)):
        raise TransientError(f"temporary glitch #{n}")
    return {"calls": n, "attempt": ctx.attempt}


@activity("test_wfl.permanent", max_attempts=5, initial_interval_seconds=0.01)
def permanent_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    _count(payload["token"], "permanent")
    raise PermanentError("the input can never succeed")


@activity("test_wfl.needs_approval", max_attempts=5, initial_interval_seconds=0.01)
def needs_approval_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    _count(payload["token"], "needs_approval")
    raise ApprovalRequired("a human must approve this", approval_id="apr-123")


@activity("test_wfl.crash", max_attempts=3, initial_interval_seconds=0.01)
def crash_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    n = _count(payload["token"], "crash")
    if payload["token"] in CRASH:
        raise SimulatedCrash()
    return {"calls": n, "attempt": ctx.attempt}


@activity("test_wfl.slow", timeout_seconds=60, max_attempts=1)
def slow_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    _count(payload["token"], "slow")
    deadline = time.monotonic() + float(payload.get("seconds", 20))
    while time.monotonic() < deadline:
        if ctx.is_cancelled():
            with _LOCK:
                OBSERVED_CANCEL.add(payload["token"])
            return {"cancelled": True}
        ctx.heartbeat({"progress": "working"})
        time.sleep(0.05)
    return {"cancelled": False}


@activity("test_wfl.too_slow", timeout_seconds=1, max_attempts=2, initial_interval_seconds=0.01)
def too_slow_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    _count(payload["token"], "too_slow")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not ctx.is_cancelled():
        time.sleep(0.05)
    return {}


# ---------------------------------------------------------------------------------------------------
# Test flows
# ---------------------------------------------------------------------------------------------------
async def seq_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    stamp = ctx.now().isoformat()
    ident = ctx.uuid()
    first = await ctx.activity("test_wfl.record", {"token": token, "label": "a1"})
    second = await ctx.activity("test_wfl.record", {"token": token, "label": "a2"})
    if token in CRASH:
        raise SimulatedCrash()
    third = await ctx.activity("test_wfl.record", {"token": token, "label": "a3"})
    ctx.log("seq_flow_done", token=token)
    return {"results": [first, second, third], "stamp": stamp, "ident": ident}


async def crash_in_activity_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    await ctx.activity("test_wfl.record", {"token": token, "label": "before"})
    crashed = await ctx.activity("test_wfl.crash", {"token": token})
    await ctx.activity("test_wfl.record", {"token": token, "label": "after"})
    return {"crash_step": crashed}


async def retry_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    await ctx.activity("test_wfl.record", {"token": token, "label": "first"})
    return await ctx.activity("test_wfl.flaky", {"token": token, "fail_times": data.get("fail_times", 0)})


async def permanent_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    return await ctx.activity("test_wfl.permanent", {"token": data["token"]})


async def catch_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    try:
        await ctx.activity("test_wfl.needs_approval", {"token": data["token"]})
    except ActivityFailed as exc:
        return {"error_type": exc.error_type, "approval_id": (exc.details or {}).get("approval_id"), "code": exc.code}
    return {"error_type": None}


async def signal_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    payload = await ctx.wait_signal("go", timeout_seconds=data.get("timeout", 30))
    await ctx.activity("test_wfl.record", {"token": data["token"], "label": "after_signal"})
    return {"payload": payload, "timed_out": payload is None}


async def child_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    step = await ctx.activity("test_wfl.record", {"token": data["token"], "label": "child"})
    if f"child:{data['token']}" in CRASH:
        raise SimulatedCrash()
    if data.get("fail"):
        await ctx.activity("test_wfl.permanent", {"token": data["token"]})
    return {"doubled": int(data["x"]) * 2, "child_run": step["workflow_run_id"]}


async def parent_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    await ctx.activity("test_wfl.record", {"token": token, "label": "parent"})
    try:
        result = await ctx.child(
            "TestChildFlow", {"token": token, "x": 21, "fail": data.get("fail", False)}, workflow_key="c1"
        )
    except ChildWorkflowFailed as exc:
        return {"child_status": exc.status, "child_error": exc.message}
    if f"parent:{token}" in CRASH:
        raise SimulatedCrash()
    await ctx.activity("test_wfl.record", {"token": token, "label": "parent_after_child"})
    return {"child": result}


async def detached_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    child_id = await ctx.start_detached("TestChildFlow", {"token": data["token"], "x": 1}, workflow_key="bg")
    return {"child_id": child_id}


async def gather_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    token = data["token"]
    left, right = await ctx.gather(
        ctx.activity("test_wfl.record", {"token": token, "label": "left"}),
        ctx.activity("test_wfl.record", {"token": token, "label": "right"}),
    )
    return {"labels": [left["label"], right["label"]]}


async def slow_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    await ctx.activity("test_wfl.slow", {"token": data["token"], "seconds": 20})
    await ctx.activity("test_wfl.record", {"token": data["token"], "label": "after_slow"})
    return {}


async def timeout_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    return await ctx.activity("test_wfl.too_slow", {"token": data["token"]})


async def sleep_flow(ctx: WorkflowContext, data: dict[str, Any]) -> dict[str, Any]:
    await ctx.sleep(float(data.get("seconds", 0.2)))
    return {"slept": True}


for _kind, _fn in (
    ("TestSeqFlow", seq_flow),
    ("TestCrashActivityFlow", crash_in_activity_flow),
    ("TestRetryFlow", retry_flow),
    ("TestPermanentFlow", permanent_flow),
    ("TestCatchFlow", catch_flow),
    ("TestSignalFlow", signal_flow),
    ("TestChildFlow", child_flow),
    ("TestParentFlow", parent_flow),
    ("TestDetachedFlow", detached_flow),
    ("TestGatherFlow", gather_flow),
    ("TestSlowFlow", slow_flow),
    ("TestTimeoutFlow", timeout_flow),
    ("TestSleepFlow", sleep_flow),
):
    register_flow(_kind, _fn)


# ---------------------------------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fast_local_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from aegis_api.config import get_settings
    from aegis_api.lab.workflows import local_engine

    monkeypatch.setattr(get_settings(), "workflow_engine", "local")
    monkeypatch.setattr(local_engine, "HEARTBEAT_INTERVAL_SECONDS", 0.2)
    monkeypatch.setattr(local_engine, "SIGNAL_POLL_MIN_SECONDS", 0.05)
    monkeypatch.setattr(local_engine, "SIGNAL_POLL_MAX_SECONDS", 0.2)
    monkeypatch.setattr(local_engine, "ACTIVITY_POLL_SECONDS", 0.05)
    yield
    local_engine.wait_for_background(timeout=20)


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    """The app with the workflow-run router mounted (until the integrator includes it in lab/router.py)."""
    from fastapi.testclient import TestClient

    from aegis_api.app import create_app
    from aegis_api.lab.workflows.router import router

    app = create_app()
    if not any(getattr(r, "path", "").startswith("/api/v1/workflow-runs") for r in app.routes):
        app.include_router(router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_autostart(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Record (instead of performing) the after-commit start so tests drive the engine explicitly."""
    from aegis_api.lab.workflows import launcher

    started: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(launcher, "start_run", lambda org, run_id: started.append((org, run_id)))
    return started


def engine():  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.local_engine import LocalWorkflowEngine

    return LocalWorkflowEngine(heartbeat_interval=0.2, signal_poll_min=0.05, signal_poll_max=0.2)


def launch(lab, kind: str, data: dict[str, Any], *, key: str = "default", subject_id: str | None = None) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import launch_workflow

    with lab.db() as db:
        run = launch_workflow(
            db,
            lab.actor(),
            kind,
            subject_type="test",
            subject_id=subject_id or str(uuid.uuid4()),
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


def steps_of(lab, run_id: uuid.UUID) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowStep

    with lab.db() as db:
        rows = db.scalars(select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id)).all()
        for row in rows:
            db.expunge(row)
        return {row.step_key: row for row in rows}


def wait_status(lab, run_id: uuid.UUID, statuses: set[str], timeout: float = 20.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while True:
        run = get_run(lab, run_id)
        if run.status in statuses:
            return run
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} stayed {run.status}, expected {statuses} (error={run.error})")
        time.sleep(0.05)


def make_stale(lab, run_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.models import WorkflowRun

    with lab.db() as db:
        db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(heartbeat_at=utcnow() - timedelta(hours=1))
            .execution_options(synchronize_session=False)
        )


def workflow_events(lab, run_id: uuid.UUID) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import LabEvent

    with lab.db() as db:
        rows = db.scalars(
            select(LabEvent)
            .where(LabEvent.subject_type == "workflow_run", LabEvent.subject_id == str(run_id))
            .order_by(LabEvent.id)
        ).all()
        return [dict(r.payload) for r in rows]


# ---------------------------------------------------------------------------------------------------
# Execution & replay
# ---------------------------------------------------------------------------------------------------
def test_sequential_activities_complete_via_launcher_dispatch(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestSeqFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    labels = [r["label"] for r in run.result["results"]]
    assert labels == ["a1", "a2", "a3"]
    # Activities run as the (derived) workflow actor of this run.
    assert {r["actor_kind"] for r in run.result["results"]} == {"workflow"}
    assert {r["workflow_run_id"] for r in run.result["results"]} == {str(run_id)}
    assert run.started_at is not None and run.completed_at is not None
    steps = steps_of(lab, run_id)
    assert {"now#1", "uuid#1", "test_wfl.record#1", "test_wfl.record#2", "test_wfl.record#3"} <= set(steps)
    assert all(s.status == "completed" for s in steps.values())
    statuses = [e["status"] for e in workflow_events(lab, run_id)]
    assert statuses[0] == "PENDING" and "RUNNING" in statuses and statuses[-1] == "COMPLETED"


def test_replay_after_crash_does_not_reexecute_completed_activities(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestSeqFlow", {"token": token})
    assert no_autostart == [(lab.org_id, run_id)]
    CRASH.add(token)
    with pytest.raises(SimulatedCrash):
        engine().run(run_id, organization_id=lab.org_id)
    assert (calls(token, "a1"), calls(token, "a2"), calls(token, "a3")) == (1, 1, 0)
    crashed = get_run(lab, run_id)
    assert crashed.status == "RUNNING"  # the "process" died without recording anything
    steps = steps_of(lab, run_id)
    assert steps["test_wfl.record#1"].status == "completed"
    assert steps["test_wfl.record#2"].status == "completed"
    stamp, ident = steps["now#1"].result["value"], steps["uuid#1"].result["value"]

    # While the lease holder's heartbeat is fresh, nobody else may take the run over.
    busy = engine().run(run_id, organization_id=lab.org_id)
    assert busy.executed is False and busy.status == "RUNNING"

    make_stale(lab, run_id)
    CRASH.discard(token)
    outcome = engine().run(run_id, organization_id=lab.org_id)
    assert outcome.status == "COMPLETED"
    # Replay: activities 1-2 were NOT executed again, activity 3 ran exactly once.
    assert (calls(token, "a1"), calls(token, "a2"), calls(token, "a3")) == (1, 1, 1)
    run = get_run(lab, run_id)
    assert [r["calls"] for r in run.result["results"]] == [1, 1, 1]
    # now()/uuid() are replayed from history too.
    assert run.result["stamp"] == stamp and run.result["ident"] == ident
    assert any(e.get("reason") == "resumed" for e in workflow_events(lab, run_id))


def test_step_in_flight_at_crash_is_retried_with_next_attempt(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestCrashActivityFlow", {"token": token})
    CRASH.add(token)
    with pytest.raises(SimulatedCrash):
        engine().run(run_id, organization_id=lab.org_id)
    step = steps_of(lab, run_id)["test_wfl.crash#1"]
    assert (step.status, step.attempt) == ("running", 1)
    CRASH.discard(token)
    make_stale(lab, run_id)
    assert engine().run(run_id, organization_id=lab.org_id).status == "COMPLETED"
    assert calls(token, "before") == 1  # completed before the crash: replayed
    assert calls(token, "crash") == 2  # in flight at the crash: executed again (at-least-once)
    assert calls(token, "after") == 1
    step = steps_of(lab, run_id)["test_wfl.crash#1"]
    assert (step.status, step.attempt) == ("completed", 2)
    assert get_run(lab, run_id).result["crash_step"]["attempt"] == 2


def test_lease_fencing_blocks_a_superseded_executor(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestSeqFlow", {"token": uuid.uuid4().hex})
    first = engine()._claim(lab.org_id, run_id, force=False)
    assert not hasattr(first, "executed")
    assert engine()._claim(lab.org_id, run_id, force=False).executed is False  # fresh lease elsewhere
    second = engine()._claim(lab.org_id, run_id, force=True)
    assert second.token != first.token
    stale = engine()._finalize(first, "COMPLETED", result={"zombie": True})
    assert stale.lease_lost is True
    run = get_run(lab, run_id)
    assert run.status == "RUNNING" and run.result is None


def test_transient_errors_are_retried_with_backoff(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestRetryFlow", {"token": token, "fail_times": 2})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"calls": 3, "attempt": 3}
    assert calls(token, "flaky") == 3
    step = steps_of(lab, run_id)["test_wfl.flaky#1"]
    assert (step.status, step.attempt) == ("completed", 3)


def test_retries_exhausted_fails_the_flow(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    FAIL.add(token)
    try:
        run_id = launch(lab, "TestRetryFlow", {"token": token})
        run = wait_status(lab, run_id, {"FAILED"})
    finally:
        FAIL.discard(token)
    assert calls(token, "flaky") == 4  # max_attempts
    assert "TransientError" in (run.error or "")
    step = steps_of(lab, run_id)["test_wfl.flaky#1"]
    assert (step.status, step.attempt) == ("failed", 4)


def test_non_retryable_error_fails_flow_after_one_attempt(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestPermanentFlow", {"token": token})
    run = wait_status(lab, run_id, {"FAILED"})
    assert calls(token, "permanent") == 1
    assert "PermanentError" in (run.error or "")
    assert steps_of(lab, run_id)["test_wfl.permanent#1"].status == "failed"
    assert workflow_events(lab, run_id)[-1]["status"] == "FAILED"


def test_flow_can_handle_typed_activity_failures(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestCatchFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"error_type": "ApprovalRequired", "approval_id": "apr-123", "code": "approval_required"}
    assert calls(token, "needs_approval") == 1


def test_activity_timeout_is_enforced(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestTimeoutFlow", {"token": token})
    run = wait_status(lab, run_id, {"FAILED"}, timeout=30)
    assert "ActivityTimeout" in (run.error or "")
    assert calls(token, "too_slow") == 2


def test_retry_resumes_from_the_point_of_failure(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import retry_workflow

    token = uuid.uuid4().hex
    FAIL.add(token)
    run_id = launch(lab, "TestRetryFlow", {"token": token})
    wait_status(lab, run_id, {"FAILED"})
    FAIL.discard(token)
    with lab.db() as db:
        run = retry_workflow(db, lab.actor(), run_id)
        assert (run.status, run.attempt) == ("PENDING", 2)
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.attempt == 2 and run.error is None
    assert calls(token, "first") == 1  # completed step of attempt 1 was replayed, not re-executed
    assert calls(token, "flaky") == 5


def test_retry_rejected_for_non_failed_runs(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import retry_workflow
    from engines.lab.states import InvalidTransitionError

    run_id = launch(lab, "TestSeqFlow", {"token": uuid.uuid4().hex})
    wait_status(lab, run_id, {"COMPLETED"})
    with pytest.raises(InvalidTransitionError), lab.db() as db:
        retry_workflow(db, lab.actor(), run_id)


def test_gather_runs_activities_concurrently_with_deterministic_keys(lab) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestGatherFlow", {"token": uuid.uuid4().hex})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"labels": ["left", "right"]}
    steps = steps_of(lab, run_id)
    assert steps["test_wfl.record#1"].result["label"] == "left"
    assert steps["test_wfl.record#2"].result["label"] == "right"


def test_durable_sleep(lab) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestSleepFlow", {"seconds": 0.3})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"slept": True}
    assert steps_of(lab, run_id)["sleep#1"].status == "completed"


# ---------------------------------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------------------------------
def test_signal_wait_receives_signal_sent_from_another_thread(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import signal_workflow

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestSignalFlow", {"token": token, "timeout": 30})
    wait_status(lab, run_id, {"WAITING"})
    assert calls(token, "after_signal") == 0

    def send() -> None:
        with lab.db() as db:
            signal_workflow(db, run_id, "go", {"value": 42})

    sender = threading.Thread(target=send)
    sender.start()
    sender.join()
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"payload": {"value": 42}, "timed_out": False}
    assert run.signals == []  # consumed from the inbox
    step = steps_of(lab, run_id)["signal:go#1"]
    assert step.status == "completed" and step.result["payload"] == {"value": 42}
    statuses = [e["status"] for e in workflow_events(lab, run_id)]
    waiting = statuses.index("WAITING")
    assert "RUNNING" in statuses[waiting:]


def test_signal_wait_timeout(lab) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestSignalFlow", {"token": uuid.uuid4().hex, "timeout": 0.3})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result == {"payload": None, "timed_out": True}
    assert steps_of(lab, run_id)["signal:go#1"].result["timed_out"] is True


def test_signal_sent_before_the_wait_is_kept_in_the_inbox(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import signal_workflow

    run_id = launch(lab, "TestSignalFlow", {"token": uuid.uuid4().hex})
    with lab.db() as db:
        signal_workflow(db, run_id, "go", {"early": True})
    assert len(get_run(lab, run_id).signals) == 1
    outcome = engine().run(run_id, organization_id=lab.org_id)
    assert outcome.status == "COMPLETED"
    assert get_run(lab, run_id).result == {"payload": {"early": True}, "timed_out": False}
    assert "WAITING" not in [e["status"] for e in workflow_events(lab, run_id)]


def test_signal_validation_and_terminal_runs(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.errors import NotFound, ValidationFailed
    from aegis_api.lab.core.errors import InvalidTransition
    from aegis_api.lab.workflows.launcher import signal_workflow

    run_id = launch(lab, "TestSeqFlow", {"token": uuid.uuid4().hex})
    wait_status(lab, run_id, {"COMPLETED"})
    with pytest.raises(InvalidTransition), lab.db() as db:
        signal_workflow(db, run_id, "go", {})
    with pytest.raises(ValidationFailed), lab.db() as db:
        signal_workflow(db, run_id, "bad name!", {})
    with pytest.raises(ValidationFailed), lab.db() as db:
        signal_workflow(db, run_id, "go", {"blob": b"raw"})  # type: ignore[dict-item]
    with pytest.raises(NotFound), lab.db() as db:
        signal_workflow(db, uuid.uuid4(), "go", {})


def test_signals_are_tenant_isolated(lab, other_lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.errors import NotFound
    from aegis_api.lab.workflows.launcher import signal_workflow

    run_id = launch(lab, "TestSignalFlow", {"token": uuid.uuid4().hex})
    with pytest.raises(NotFound), other_lab.db() as db:
        signal_workflow(db, run_id, "go", {"intruder": True})
    assert get_run(lab, run_id).signals == []


# ---------------------------------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------------------------------
def test_child_workflow_runs_inline_and_its_result_is_memoized(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestParentFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result["child"]["doubled"] == 42
    with lab.db() as db:
        child = db.scalars(select(WorkflowRun).where(WorkflowRun.parent_workflow_run_id == run_id)).one()
        assert child.kind == "TestChildFlow"
        assert child.status == "COMPLETED"
        assert child.task_queue == "local:inline"
        assert child.external_id == f"TestChildFlow:{run_id}:c1"
        assert child.project_id == lab.project_id
        assert child.input["actor"]["workflow_run_id"] == str(child.id)
        child_id = child.id
    assert run.result["child"]["child_run"] == str(child_id)
    step = steps_of(lab, run_id)["child:TestChildFlow:c1"]
    assert step.status == "completed" and step.result["workflow_run_id"] == str(child_id)
    assert calls(token, "child") == 1


def test_child_failure_propagates_to_parent(lab) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestParentFlow", {"token": uuid.uuid4().hex, "fail": True})
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert run.result["child_status"] == "FAILED"
    assert "PermanentError" in run.result["child_error"]


def test_parent_crash_after_child_completed_replays_the_child_result(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestParentFlow", {"token": token})
    CRASH.add(f"parent:{token}")
    with pytest.raises(SimulatedCrash):
        engine().run(run_id, organization_id=lab.org_id)
    CRASH.discard(f"parent:{token}")
    assert steps_of(lab, run_id)["child:TestChildFlow:c1"].status == "completed"
    make_stale(lab, run_id)
    assert engine().run(run_id, organization_id=lab.org_id).status == "COMPLETED"
    assert (calls(token, "parent"), calls(token, "child"), calls(token, "parent_after_child")) == (1, 1, 1)


def test_crash_inside_inline_child_resumes_through_the_parent(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestParentFlow", {"token": token})
    CRASH.add(f"child:{token}")
    with pytest.raises(SimulatedCrash):
        engine().run(run_id, organization_id=lab.org_id)
    CRASH.discard(f"child:{token}")
    with lab.db() as db:
        child = db.scalars(select(WorkflowRun).where(WorkflowRun.parent_workflow_run_id == run_id)).one()
        child_id = child.id
        assert (child.status, child.task_queue) == ("RUNNING", "local:inline")
    # Only the parent is stale; the parent's replay re-attaches the (inline) child and resumes it.
    make_stale(lab, run_id)
    assert engine().run(run_id, organization_id=lab.org_id).status == "COMPLETED"
    assert get_run(lab, child_id).status == "COMPLETED"
    assert (calls(token, "parent"), calls(token, "child"), calls(token, "parent_after_child")) == (1, 1, 1)


def test_detached_workflow_is_dispatched_independently(lab) -> None:  # type: ignore[no-untyped-def]
    token = uuid.uuid4().hex
    run_id = launch(lab, "TestDetachedFlow", {"token": token})
    run = wait_status(lab, run_id, {"COMPLETED"})
    child_id = uuid.UUID(run.result["child_id"])
    child = wait_status(lab, child_id, {"COMPLETED"})
    assert child.parent_workflow_run_id == run_id
    assert child.task_queue == "local"
    assert child.result["doubled"] == 2


# ---------------------------------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------------------------------
def test_cancel_running_activity(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import cancel_workflow

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestSlowFlow", {"token": token})
    deadline = time.monotonic() + 15
    while calls(token, "slow") == 0:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    with lab.db() as db:
        run = cancel_workflow(db, lab.actor(), run_id)
        assert run.cancel_requested is True
    run = wait_status(lab, run_id, {"CANCELLED"}, timeout=10)
    assert run.error == "Cancelled on request"
    assert calls(token, "after_slow") == 0
    deadline = time.monotonic() + 5
    while token not in OBSERVED_CANCEL:
        assert time.monotonic() < deadline, "the activity never observed is_cancelled()"
        time.sleep(0.05)


def test_cancel_while_waiting_for_signal(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import cancel_workflow

    token = uuid.uuid4().hex
    run_id = launch(lab, "TestSignalFlow", {"token": token, "timeout": 60})
    wait_status(lab, run_id, {"WAITING"})
    with lab.db() as db:
        cancel_workflow(db, lab.actor(), run_id)
    wait_status(lab, run_id, {"CANCELLED"}, timeout=10)
    assert calls(token, "after_signal") == 0


def test_cancel_pending_and_terminal_runs(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.launcher import cancel_workflow
    from engines.lab.states import InvalidTransitionError

    run_id = launch(lab, "TestSeqFlow", {"token": uuid.uuid4().hex})
    with lab.db() as db:
        assert cancel_workflow(db, lab.actor(), run_id).status == "CANCELLED"
    with lab.db() as db:  # idempotent
        assert cancel_workflow(db, lab.actor(), run_id).status == "CANCELLED"
    outcome = engine().run(run_id, organization_id=lab.org_id)
    assert outcome.executed is False and outcome.status == "CANCELLED"

    done = launch(lab, "TestGatherFlow", {"token": uuid.uuid4().hex})
    engine().run(done, organization_id=lab.org_id)
    with pytest.raises(InvalidTransitionError), lab.db() as db:
        cancel_workflow(db, lab.actor(), done)


# ---------------------------------------------------------------------------------------------------
# Launcher semantics
# ---------------------------------------------------------------------------------------------------
def test_launch_is_idempotent_per_kind_subject_and_key(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    subject = str(uuid.uuid4())
    first = launch(lab, "TestSeqFlow", {"token": "x"}, subject_id=subject)
    again = launch(lab, "TestSeqFlow", {"token": "y"}, subject_id=subject)
    other = launch(lab, "TestSeqFlow", {"token": "x"}, subject_id=subject, key="second")
    assert first == again != other
    run = get_run(lab, first)
    assert run.external_id == f"TestSeqFlow:{subject}:default"
    assert run.engine == "local" and run.status == "PENDING"
    assert run.input["flow_input"] == {"token": "x"}
    assert run.input["actor"]["kind"] == "workflow"
    assert run.input["actor"]["workflow_run_id"] == str(first)
    assert "approval:decide" not in run.input["actor"]["permissions"]  # human-only permissions are dropped


def test_launch_starts_only_after_commit(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun
    from aegis_api.lab.workflows.launcher import launch_workflow

    subject = str(uuid.uuid4())

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), lab.db() as db:
        launch_workflow(db, lab.actor(), "TestSeqFlow", subject_type="test", subject_id=subject, input={})
        assert no_autostart == []
        raise Rollback()
    assert no_autostart == []
    with lab.db() as db:
        assert db.scalars(select(WorkflowRun).where(WorkflowRun.subject_id == subject)).first() is None

    with lab.db() as db:
        run = launch_workflow(db, lab.actor(), "TestSeqFlow", subject_type="test", subject_id=subject, input={})
        assert no_autostart == []  # not before commit
        run_id = run.id
    assert no_autostart == [(lab.org_id, run_id)]


def test_launch_validation(lab, other_lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.errors import NotFound, ValidationFailed
    from aegis_api.lab.workflows.launcher import launch_workflow

    with pytest.raises(ValidationFailed), lab.db() as db:
        launch_workflow(db, lab.actor(), "NoSuchWorkflow", subject_type="test", subject_id="s", input={})
    with pytest.raises(ValidationFailed), lab.db() as db:
        launch_workflow(db, lab.actor(), "TestSeqFlow", subject_type="test", subject_id="s", input={"x": {1, 2}})
    with pytest.raises(ValidationFailed), lab.db() as db:
        launch_workflow(
            db, lab.actor(), "TestSeqFlow", subject_type="test", subject_id="s", input={}, workflow_key="bad key"
        )
    with pytest.raises(NotFound), lab.db() as db:  # another tenant's project
        launch_workflow(
            db,
            lab.actor(),
            "TestSeqFlow",
            subject_type="test",
            subject_id="s",
            input={},
            project_id=other_lab.project_id,
        )


# ---------------------------------------------------------------------------------------------------
# Recovery (scheduler)
# ---------------------------------------------------------------------------------------------------
def test_scheduler_resumes_stale_runs(lab, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows import launcher, maintenance
    from aegis_api.lab.workflows.scheduler import ScheduledTask, Scheduler

    token = uuid.uuid4().hex
    monkeypatch.setattr(launcher, "start_run", lambda org, run_id: None)
    run_id = launch(lab, "TestSeqFlow", {"token": token})
    CRASH.add(token)
    with pytest.raises(SimulatedCrash):
        engine().run(run_id, organization_id=lab.org_id)
    CRASH.discard(token)
    # Fresh heartbeat: the recovery pass leaves the run alone.
    assert maintenance.resume_stale_runs(lab.org_id)["resumed"] == 0
    make_stale(lab, run_id)
    scheduler = Scheduler(tasks=[ScheduledTask("test_resume_stale", lambda: maintenance.resume_stale_runs(lab.org_id))])
    results = scheduler.run_once()
    assert results["test_resume_stale"].status == "ok"
    assert results["test_resume_stale"].detail["resumed"] == 1
    run = wait_status(lab, run_id, {"COMPLETED"})
    assert (calls(token, "a1"), calls(token, "a2"), calls(token, "a3")) == (1, 1, 1)
    assert run.result["results"][2]["calls"] == 1


def test_scheduler_starts_lost_pending_runs(lab, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.models import WorkflowRun
    from aegis_api.lab.workflows import launcher, maintenance

    real_start = launcher.start_run
    monkeypatch.setattr(launcher, "start_run", lambda org, run_id: None)  # the start is "lost"
    run_id = launch(lab, "TestGatherFlow", {"token": uuid.uuid4().hex})
    monkeypatch.setattr(launcher, "start_run", real_start)
    assert maintenance.start_pending_runs(lab.org_id)["started"] == 0  # within the grace period
    with lab.db() as db:
        db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(updated_at=utcnow() - timedelta(minutes=5))
            .execution_options(synchronize_session=False)
        )
    assert maintenance.start_pending_runs(lab.org_id)["started"] == 1
    wait_status(lab, run_id, {"COMPLETED"})


def test_orphaned_inline_child_is_cancelled(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun
    from aegis_api.lab.workflows import maintenance, runs

    parent_id = launch(lab, "TestParentFlow", {"token": uuid.uuid4().hex})
    with lab.db() as db:
        parent = db.get(WorkflowRun, parent_id)
        child, created = runs.ensure_child_run(
            db,
            parent,
            kind="TestChildFlow",
            flow_input={},
            workflow_key="orphan",
            subject_type=None,
            subject_id=None,
            inline=True,
        )
        assert created
        runs.transition(db, parent, "CANCELLED", error="gone")
        child_id = child.id
    with lab.db() as db:
        db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == child_id)
            .values(updated_at=WorkflowRun.updated_at - timedelta(minutes=5))
            .execution_options(synchronize_session=False)
        )
    assert maintenance.start_pending_runs(lab.org_id)["orphans_cancelled"] == 1
    assert get_run(lab, child_id).status == "CANCELLED"


def test_scheduler_task_lock_is_exclusive() -> None:
    from aegis_api.lab.workflows.scheduler import task_lock

    name = f"test-lock-{uuid.uuid4().hex[:8]}"
    with task_lock(name) as first:
        assert first is True
        with task_lock(name) as second:
            assert second is False
    with task_lock(name) as again:
        assert again is True


def test_usage_periods_cover_previous_and_current_month() -> None:
    from datetime import UTC, datetime

    from aegis_api.lab.workflows.scheduler import usage_periods

    assert usage_periods(datetime(2026, 1, 15, tzinfo=UTC)) == ["2025-12", "2026-01"]
    assert usage_periods(datetime(2026, 9, 30, tzinfo=UTC)) == ["2026-08", "2026-09"]


# ---------------------------------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------------------------------
def _viewer_headers(lab) -> dict[str, str]:  # type: ignore[no-untyped-def]
    key = lab.post("/api/v1/api-keys", json={"name": "viewer", "role": "viewer", "scopes": ["read"]})
    assert key.status_code in (200, 201), key.text
    body = key.json()
    return {"Authorization": f"Bearer {body['plaintext']}"}


def test_workflow_runs_api_list_detail_and_isolation(lab, other_lab) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestParentFlow", {"token": uuid.uuid4().hex})
    wait_status(lab, run_id, {"COMPLETED"})

    listed = lab.get("/api/v1/workflow-runs", params={"kind": "TestParentFlow", "limit": 50})
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()["items"]}
    assert str(run_id) in ids
    item = next(i for i in listed.json()["items"] if i["id"] == str(run_id))
    assert item["status"] == "COMPLETED" and item["external_run_id"] is None
    assert "actor" not in item["input"]
    completed = lab.get("/api/v1/workflow-runs", params={"status": "COMPLETED", "kind": "TestParentFlow"}).json()
    assert str(run_id) in {i["id"] for i in completed["items"]}
    assert lab.get("/api/v1/workflow-runs", params={"status": "NOPE"}).status_code == 422

    detail = lab.get(f"/api/v1/workflow-runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["step_counts"].get("completed", 0) >= 2
    assert any(s["step_key"] == "child:TestChildFlow:c1" for s in body["steps"])
    assert len(body["children"]) == 1 and body["children"][0]["kind"] == "TestChildFlow"
    assert body["temporal"] is None

    # Another tenant sees nothing and gets 404 for the id.
    assert other_lab.get(f"/api/v1/workflow-runs/{run_id}").status_code == 404
    other_ids = {i["id"] for i in other_lab.get("/api/v1/workflow-runs").json()["items"]}
    assert str(run_id) not in other_ids


def test_workflow_runs_api_cancel_retry_signal(lab, other_lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestSignalFlow", {"token": uuid.uuid4().hex})

    assert lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "shutdown"}).status_code == 422
    signalled = lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume", "payload": {"k": 1}})
    assert signalled.status_code == 202, signalled.text
    assert signalled.json() == {"workflow_run_id": str(run_id), "name": "resume", "status": "PENDING", "accepted": True}
    approval = lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "approval", "payload": {"ok": True}})
    assert approval.status_code == 202, approval.text  # the owner is a signed-in human with approval:decide
    assert len(get_run(lab, run_id).signals) == 2

    # Idempotent retry of the same signal request.
    headers = {"Idempotency-Key": f"sig-{uuid.uuid4().hex}"}
    first = lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume"}, headers=headers)
    second = lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume"}, headers=headers)
    assert first.status_code == second.status_code == 202
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert len(get_run(lab, run_id).signals) == 3

    # Cross-tenant: 404 for every operation.
    assert other_lab.post(f"/api/v1/workflow-runs/{run_id}/cancel").status_code == 404
    assert other_lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume"}).status_code == 404

    # Retry is only valid for FAILED/TIMED_OUT runs.
    assert lab.post(f"/api/v1/workflow-runs/{run_id}/retry").status_code == 409

    cancelled = lab.post(f"/api/v1/workflow-runs/{run_id}/cancel")
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert lab.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume"}).status_code == 409


def test_workflow_runs_api_permissions(lab, no_autostart) -> None:  # type: ignore[no-untyped-def]
    run_id = launch(lab, "TestSignalFlow", {"token": uuid.uuid4().hex})
    headers = _viewer_headers(lab)
    client = lab.ws.client
    listed = client.get("/api/v1/workflow-runs", headers=headers)
    assert listed.status_code == 200, listed.text
    assert client.get(f"/api/v1/workflow-runs/{run_id}", headers=headers).status_code == 200
    assert client.post(f"/api/v1/workflow-runs/{run_id}/cancel", headers=headers).status_code == 403
    assert client.post(f"/api/v1/workflow-runs/{run_id}/retry", headers=headers).status_code == 403
    signal = client.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "resume"}, headers=headers)
    assert signal.status_code == 403
    approval = client.post(f"/api/v1/workflow-runs/{run_id}/signal", json={"name": "approval"}, headers=headers)
    assert approval.status_code == 403
    assert get_run(lab, run_id).status == "PENDING"
