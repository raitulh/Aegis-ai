"""Start, signal and cancel workflows on the configured engine.

All three are transactional with the caller's unit of work: the ``lab.workflow_runs`` / ``workflow_signals``
rows are written in the caller's transaction and the engine is only contacted *after commit* (so a rolled-back
request never starts a workflow). Temporal calls that fail after commit are retried by the scheduler's
reconciler (pending runs without a Temporal run id; undelivered signals).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import on_commit
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models.lab import WorkflowRun, WorkflowSignal
from aegis_api.security.context import Principal

log = structlog.get_logger("aegis.workflows.client")

TERMINAL = frozenset({"completed", "failed", "cancelled"})


def start(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workflow: str,
    business_key: str,
    payload: dict[str, Any],
    principal: Principal,
    parent_run_id: uuid.UUID | None = None,
) -> WorkflowRun:
    """Idempotent on (organization, workflow, business_key): an existing run is returned as-is."""
    from aegis_api.workflows.definitions import WORKFLOWS

    if workflow not in WORKFLOWS:
        raise ValidationFailed(f"unknown workflow '{workflow}'")
    if principal.organization_id != organization_id:
        raise ValidationFailed("principal does not belong to the workflow's organization")
    existing = db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.workflow == workflow,
            WorkflowRun.business_key == business_key[:160],
        )
    )
    if existing is not None:
        return existing
    engine = get_settings().effective_workflow_engine
    run = WorkflowRun(
        organization_id=organization_id,
        workflow=workflow,
        business_key=business_key[:160],
        engine=engine,
        status="pending",
        input=payload,
        principal=principal.snapshot(),
        parent_run_id=parent_run_id,
    )
    db.add(run)
    db.flush()
    run_id, org = run.id, organization_id
    on_commit(db, lambda: _launch(run_id, org, engine))
    return run


def _launch(run_id: uuid.UUID, org: uuid.UUID, engine: str) -> None:
    if engine == "inline":
        from aegis_api.jobs.dispatcher import dispatch_task

        dispatch_task("aegis_api.workflows.engine:drive_inline_run", str(run_id), str(org))
        return
    try:
        from aegis_api.workflows import temporal

        temporal.start_run(run_id, org)
    except Exception:
        log.exception("temporal_start_failed_will_reconcile", run_id=str(run_id))


def signal(db: Session, run_id: uuid.UUID, name: str, payload: dict[str, Any]) -> WorkflowSignal | None:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise NotFound("Workflow run not found")
    if run.status in TERMINAL:
        return None
    sig = WorkflowSignal(organization_id=run.organization_id, run_id=run.id, name=name[:160], payload=payload)
    db.add(sig)
    db.flush()
    sig_id, org, engine = sig.id, run.organization_id, run.engine
    on_commit(db, lambda: _deliver(run_id, org, engine, sig_id))
    return sig


def _deliver(run_id: uuid.UUID, org: uuid.UUID, engine: str, signal_id: uuid.UUID) -> None:
    if engine == "inline":
        from aegis_api.jobs.dispatcher import dispatch_task

        dispatch_task("aegis_api.workflows.engine:drive_inline_run", str(run_id), str(org))
        return
    try:
        from aegis_api.workflows import temporal

        temporal.deliver_signal(org, signal_id)
    except Exception:
        log.exception("temporal_signal_failed_will_reconcile", run_id=str(run_id))


def cancel(db: Session, run_id: uuid.UUID) -> None:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.status in TERMINAL:
        return
    run.cancel_requested = True
    org, engine = run.organization_id, run.engine
    for child in db.scalars(
        select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id, WorkflowRun.status.notin_(TERMINAL))
    ).all():
        child.cancel_requested = True

    def _cancel() -> None:
        if engine == "inline":
            from aegis_api.jobs.dispatcher import dispatch_task

            dispatch_task("aegis_api.workflows.engine:drive_inline_run", str(run_id), str(org))
            return
        try:
            from aegis_api.workflows import temporal

            temporal.cancel_run(run_id)
        except Exception:
            log.exception("temporal_cancel_failed", run_id=str(run_id))

    on_commit(db, _cancel)


def describe(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "workflow": run.workflow,
        "business_key": run.business_key,
        "engine": run.engine,
        "status": run.status,
        "waiting_on": run.waiting_on,
        "wake_at": run.wake_at.isoformat() if run.wake_at else None,
        "attempts": run.attempts,
        "error": run.error,
        "result": run.result,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "temporal_workflow_id": run.temporal_workflow_id,
        "temporal_run_id": run.temporal_run_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
