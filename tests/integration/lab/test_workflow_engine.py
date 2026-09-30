"""Inline durable workflow engine: memoized replay, signals, timers, retries, cancellation, children, leases."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from aegis_api.workflows.api import RetryPolicy, WorkflowContext, WorkflowDefinition
from aegis_api.workflows.runtime import ACTIVITIES, ActivityContext, ActivityError

CALLS: dict[str, int] = {}


def _count(name: str) -> int:
    CALLS[name] = CALLS.get(name, 0) + 1
    return CALLS[name]


def _act_echo(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return {"n": _count(f"echo:{p['tag']}"), "org": str(actx.organization_id), "actor": actx.actor.type}


def _act_flaky(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    n = _count(f"flaky:{p['tag']}")
    if n < 3:
        raise ActivityError("transient hiccup", code="transient", retryable=True)
    return {"attempts": n}


def _act_broken(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    _count(f"broken:{p['tag']}")
    raise ActivityError("bad input", code="bad_input", retryable=False)


for _name, _fn in (("test.echo", _act_echo), ("test.flaky", _act_flaky), ("test.broken", _act_broken)):
    ACTIVITIES.setdefault(_name, _fn)


async def wf_signal(ctx: WorkflowContext) -> dict[str, Any]:
    first = await ctx.activity("test.echo", {"tag": ctx.input["tag"]}, key="first")
    payload = await ctx.wait_signal("go", key="go")
    second = await ctx.activity("test.echo", {"tag": ctx.input["tag"]}, key="second")
    return {"first": first["n"], "second": second["n"], "signal": payload}


async def wf_timeout(ctx: WorkflowContext) -> dict[str, Any]:
    payload = await ctx.wait_signal("never", key="never", timeout_seconds=0.01)
    return {"timed_out": payload is None}


async def wf_retry(ctx: WorkflowContext) -> dict[str, Any]:
    ok = await ctx.activity(
        "test.flaky", {"tag": ctx.input["tag"]}, retry=RetryPolicy(max_attempts=5, initial_interval_seconds=0.01)
    )
    try:
        await ctx.activity("test.broken", {"tag": ctx.input["tag"]}, retry=RetryPolicy(max_attempts=5))
    except Exception as exc:  # ActivityFailure is catchable; reaction is part of the workflow
        return {"attempts": ok["attempts"], "caught": type(exc).__name__}
    return {"attempts": ok["attempts"], "caught": None}


async def wf_parent(ctx: WorkflowContext) -> dict[str, Any]:
    child = await ctx.child("test_signal", {"tag": ctx.input["tag"]}, key="child")
    return {"child": child}


async def wf_sleep(ctx: WorkflowContext) -> dict[str, Any]:
    await ctx.sleep(0.01, key="nap")
    return {"slept": True}


@pytest.fixture(autouse=True)
def _register():
    from aegis_api.workflows.definitions import WORKFLOWS

    added = {
        "test_signal": WorkflowDefinition("test_signal", wf_signal, "test"),
        "test_timeout": WorkflowDefinition("test_timeout", wf_timeout, "test"),
        "test_retry": WorkflowDefinition("test_retry", wf_retry, "test"),
        "test_parent": WorkflowDefinition("test_parent", wf_parent, "test"),
        "test_sleep": WorkflowDefinition("test_sleep", wf_sleep, "test"),
    }
    WORKFLOWS.update(added)
    yield
    for key in added:
        WORKFLOWS.pop(key, None)


def _start(workspace, lab, workflow: str, payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client
    from tests.integration.lab.conftest import principal_for

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


def _state(org: uuid.UUID, run_id: uuid.UUID) -> Any:
    from aegis_api.db.session import session_scope
    from aegis_api.models.lab import WorkflowRun

    with session_scope(org) as db:
        run = db.get(WorkflowRun, run_id)
        db.expunge(run)
        return run


def test_signal_suspends_and_replays_without_repeating_activities(workspace, lab):
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client, engine

    tag = uuid.uuid4().hex
    run_id, org = _start(workspace, lab, "test_signal", {"tag": tag})
    assert engine.drive(run_id, org) == "waiting"
    run = _state(org, run_id)
    assert run.status == "waiting" and run.waiting_on == "signal:go"
    assert engine.drive(run_id, org) == "waiting"  # replay: no duplicate side effects while waiting
    assert CALLS[f"echo:{tag}"] == 1
    with session_scope(org) as db:
        client.signal(db, run_id, "go", {"value": 42})
    assert (run_id, org) in engine.poll_due(limit=500)
    assert engine.drive(run_id, org) == "completed"
    run = _state(org, run_id)
    assert run.result == {"first": 1, "second": 2, "signal": {"value": 42}}
    assert CALLS[f"echo:{tag}"] == 2


def test_signal_timeout_and_timer(workspace, lab):
    import time

    from aegis_api.workflows import engine

    run_id, org = _start(workspace, lab, "test_timeout", {})
    first = engine.drive(run_id, org)
    if first == "waiting":
        time.sleep(0.05)
        assert engine.drive(run_id, org) == "completed"
    assert _state(org, run_id).result == {"timed_out": True}
    run_id, org = _start(workspace, lab, "test_sleep", {})
    if engine.drive(run_id, org) == "waiting":
        assert _state(org, run_id).wake_at is not None
        time.sleep(0.05)
        assert engine.drive(run_id, org) == "completed"


def test_retries_transient_and_surfaces_permanent_failures(workspace, lab):
    from aegis_api.workflows import engine

    tag = uuid.uuid4().hex
    run_id, org = _start(workspace, lab, "test_retry", {"tag": tag})
    assert engine.drive(run_id, org) == "completed"
    assert _state(org, run_id).result == {"attempts": 3, "caught": "ActivityFailure"}
    assert CALLS[f"broken:{tag}"] == 1  # non-retryable errors are never retried


def test_child_workflow_and_cancellation(workspace, lab):
    from aegis_api.db.session import session_scope
    from aegis_api.workflows import client, engine

    tag = uuid.uuid4().hex
    parent_id, org = _start(workspace, lab, "test_parent", {"tag": tag})
    assert engine.drive(parent_id, org) == "waiting"
    assert _state(org, parent_id).waiting_on.startswith("child:")
    with session_scope(org) as db:
        client.cancel(db, parent_id)
    assert engine.drive(parent_id, org) == "cancelled"
    assert _state(org, parent_id).status == "cancelled"


def test_lease_prevents_concurrent_drivers(workspace, lab):
    from sqlalchemy import text

    from aegis_api.db.session import session_scope
    from aegis_api.workflows import engine

    run_id, org = _start(workspace, lab, "test_signal", {"tag": uuid.uuid4().hex})
    with session_scope(org) as db:
        db.execute(
            text(
                "UPDATE lab.workflow_runs SET lease_owner = 'other-host', lease_until = now() + interval '1 hour' "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )
    assert engine.drive(run_id, org) == "busy"
