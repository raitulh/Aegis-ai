"""Temporal engine against a real Temporal dev server (``temporal server start-dev`` on 127.0.0.1:7233):
workflow start after commit, activities through the generic ``lab_activity``, signals, cancellation, and the
lab database as system of record."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from aegis_api.workflows.api import WorkflowContext, WorkflowDefinition
from aegis_api.workflows.runtime import ACTIVITIES, ActivityContext
from tests.integration.lab.conftest import principal_for, requires_temporal

pytestmark = [requires_temporal, pytest.mark.temporal]

CALLS: dict[str, int] = {}


def _act_count(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    CALLS[p["tag"]] = CALLS.get(p["tag"], 0) + 1
    return {"n": CALLS[p["tag"]], "actor": actx.actor.type}


ACTIVITIES.setdefault("ttest.count", _act_count)


async def wf_signal(ctx: WorkflowContext) -> dict[str, Any]:
    first = await ctx.activity("ttest.count", {"tag": ctx.input["tag"]}, key="first")
    payload = await ctx.wait_signal("go", key="go", timeout_seconds=60)
    second = await ctx.activity("ttest.count", {"tag": ctx.input["tag"]}, key="second")
    return {"first": first["n"], "second": second["n"], "signal": payload, "actor": first["actor"]}


async def wf_blocked(ctx: WorkflowContext) -> dict[str, Any]:
    await ctx.wait_signal("never", key="never")
    return {}


WORKFLOW_DEFS = {
    "ttest_signal": WorkflowDefinition("ttest_signal", wf_signal, "temporal test", execution_timeout_seconds=120),
    "ttest_blocked": WorkflowDefinition("ttest_blocked", wf_blocked, "temporal test", execution_timeout_seconds=120),
}


class _WorkerThread:
    def __init__(self, definitions: list[WorkflowDefinition] | None = None) -> None:
        self.definitions = definitions
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.worker: Any = None
        self.stop_event: asyncio.Event | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="test-temporal-worker")

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except BaseException as exc:  # surfaced to the test
            self.error = exc
            self.ready.set()

    async def _main(self) -> None:
        from temporalio.worker import Worker

        from aegis_api.config import get_settings
        from aegis_api.workflows.temporal import _make_workflow_class, connect, lab_activity

        client = await connect()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.worker = Worker(
                client,
                task_queue=get_settings().temporal_task_queue,
                workflows=[_make_workflow_class(d) for d in (self.definitions or list(WORKFLOW_DEFS.values()))],
                activities=[lab_activity],
                activity_executor=pool,
            )
            self.stop_event = asyncio.Event()
            run_task = asyncio.ensure_future(self.worker.run())
            self.ready.set()
            await self.stop_event.wait()
            await self.worker.shutdown()
            await run_task

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(30), "worker did not start"
        if self.error:
            raise self.error

    def stop(self) -> None:
        if self.thread.is_alive() and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(60)
        if self.error:
            raise self.error


@pytest.fixture
def temporal_engine(lab, monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.workflows import temporal
    from aegis_api.workflows.definitions import WORKFLOWS

    monkeypatch.setenv("WORKFLOW_ENGINE", "temporal")
    monkeypatch.setenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", f"aegis-test-{uuid.uuid4().hex[:8]}")
    get_settings.cache_clear()
    temporal._BRIDGE = None
    WORKFLOWS.update(WORKFLOW_DEFS)
    worker = _WorkerThread(list(WORKFLOWS.values()))
    worker.start()
    try:
        yield worker
    finally:
        worker.stop()
        for key in WORKFLOW_DEFS:
            WORKFLOWS.pop(key, None)
        temporal._BRIDGE = None
        get_settings.cache_clear()


def _wait_for(org: uuid.UUID, run_id: uuid.UUID, predicate, timeout: float = 60.0) -> Any:
    from aegis_api.db.session import session_scope
    from aegis_api.models.lab import WorkflowRun

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_scope(org) as db:
            run = db.get(WorkflowRun, run_id)
            if run is not None and predicate(run):
                db.expunge(run)
                return run
        time.sleep(0.25)
    raise AssertionError(f"workflow run {run_id} did not reach the expected state in {timeout}s")


def _start(workspace, workflow: str, payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client

    principal = principal_for(workspace)
    with session_scope(principal.organization_id, principal.user_id) as db:
        run = client.start(
            db,
            organization_id=principal.organization_id,
            workflow=workflow,
            business_key=f"t:{uuid.uuid4().hex}",
            payload=payload,
            principal=principal,
        )
        run_id = run.id
    return run_id, principal.organization_id


def test_temporal_signal_roundtrip_and_system_of_record(workspace, temporal_engine):
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client

    tag = uuid.uuid4().hex
    run_id, org = _start(workspace, "ttest_signal", {"tag": tag})
    run = _wait_for(org, run_id, lambda r: r.status == "running" and CALLS.get(tag) == 1)
    assert run.engine == "temporal" and run.temporal_workflow_id == f"lab-{run_id}"
    with session_scope(org) as db:
        client.signal(db, run_id, "go", {"value": 7})
    done = _wait_for(org, run_id, lambda r: r.status in ("completed", "failed"))
    assert done.status == "completed", done.error
    assert done.result == {"first": 1, "second": 2, "signal": {"value": 7}, "actor": "workflow"}
    assert CALLS[tag] == 2


def test_temporal_cancellation_marks_the_run_cancelled(workspace, temporal_engine):
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client

    run_id, org = _start(workspace, "ttest_blocked", {})
    _wait_for(org, run_id, lambda r: r.status == "running")
    with session_scope(org) as db:
        client.cancel(db, run_id)
    run = _wait_for(org, run_id, lambda r: r.status in ("cancelled", "failed", "completed"))
    assert run.status == "cancelled"


def test_temporal_health(temporal_engine):
    from aegis_api.workflows import temporal

    ok, detail = temporal.health()
    assert ok, detail


def test_full_mission_loop_on_temporal(client, workspace, temporal_engine):
    """The same research loop as the inline E2E test, executed by Temporal (child workflows, signals)."""
    from tests.integration.lab.conftest import DOCKER, invite

    if not DOCKER:
        pytest.skip("Docker engine is not available")
    from aegis_api.services.lab import seed

    ids = seed.seed_lab_demo(uuid.UUID(workspace.org_id), uuid.UUID(workspace.user_id))
    mission_id = ids["mission_id"]
    mission = workspace.get(f"/api/v1/missions/{mission_id}").json()
    r = workspace.patch(
        f"/api/v1/missions/{mission_id}",
        json={"config": {**mission["config"], "skip_research": True}},
        headers={"If-Match": str(mission["lock_version"])},
    )
    assert r.status_code == 200, r.text
    r = workspace.post(
        f"/api/v1/missions/{mission_id}/autonomy",
        json={"autonomy_level": "L3_AUTOMATED_EXECUTION", "reason": "sandboxed demo"},
    )
    assert r.status_code == 200
    r = workspace.post(f"/api/v1/missions/{mission_id}/launch")
    assert r.status_code == 202, r.text

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        mission = workspace.get(f"/api/v1/missions/{mission_id}").json()
        if mission["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(1)
    assert mission["status"] == "completed", mission.get("status_reason")
    runs = workspace.get("/api/v1/workflow-runs?page_size=200").json()  # fresh org: all runs belong to this mission
    items = runs["items"] if isinstance(runs, dict) else runs
    assert items and all(r["engine"] == "temporal" for r in items)
    assert {"mission", "experiment", "verification"} <= {r["workflow"] for r in items}
    discoveries = workspace.get(f"/api/v1/discoveries?mission_id={mission_id}").json()["items"]
    assert len(discoveries) == 1 and discoveries[0]["status"] == "human_review"
    reviewer = invite(client, workspace, "reviewer")
    ok = reviewer.post(
        f"/api/v1/discoveries/{discoveries[0]['id']}/review", json={"approve": True, "reason": "independent review"}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    chain = workspace.get(f"/api/v1/missions/{mission_id}/evidence/verify").json()
    assert chain["valid"] is True
