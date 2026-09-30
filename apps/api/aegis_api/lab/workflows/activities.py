"""``workflows.*`` activities: engine bookkeeping for Temporal runs and platform maintenance.

* ``workflows.mark_running`` / ``mark_waiting`` / ``mark_completed`` / ``mark_failed`` / ``mark_cancelled`` —
  called by the Temporal workflow wrappers so ``workflow_runs`` reflects the execution on both engines
  (idempotent; a run can only update its own row).
* ``workflows.register_child`` / ``workflows.get_run_status`` — child workflow bookkeeping (Temporal).
* ``workflows.retention_purge`` / ``workflows.resume_stale`` — maintenance, for platform (system) actors only.
"""

from __future__ import annotations

import uuid
from typing import Any

from aegis_api.db.base import utcnow
from aegis_api.errors import Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import PermanentError
from aegis_api.lab.models import WorkflowRun
from aegis_api.lab.workflows import maintenance, runs
from aegis_api.lab.workflows.ctx import MAX_ERROR_MESSAGE_CHARS, PayloadError, normalize_mapping, normalize_result
from aegis_api.lab.workflows.definitions import has_flow
from aegis_api.lab.workflows.registry import ActivityContext, activity
from engines.lab.states import WorkflowStatus

W = WorkflowStatus

_STATUS_OPTS: dict[str, Any] = {
    "timeout_seconds": 60,
    "max_attempts": 20,
    "initial_interval_seconds": 1.0,
    "max_interval_seconds": 30.0,
}


def _own_run_id(ctx: ActivityContext, payload: dict[str, Any]) -> uuid.UUID:
    raw = payload.get("workflow_run_id") or (str(ctx.workflow_run_id) if ctx.workflow_run_id else None)
    try:
        run_id = uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("workflow_run_id is required") from exc
    if ctx.workflow_run_id is not None and run_id != ctx.workflow_run_id:
        raise PermanentError("A workflow may only update its own run")
    if ctx.actor.workflow_run_id is not None and ctx.actor.workflow_run_id != run_id:
        raise PermanentError("A workflow may only update its own run")
    return run_id


def _lock_own(db: Any, ctx: ActivityContext, payload: dict[str, Any]) -> WorkflowRun:
    run = runs.lock_run(db, _own_run_id(ctx, payload))
    if run is None or run.organization_id != ctx.organization_id:
        raise NotFound("Workflow run not found")
    return run


def _state(run: WorkflowRun) -> dict[str, Any]:
    return {
        "workflow_run_id": str(run.id),
        "status": run.status,
        "terminal": runs.is_terminal(run.status),
        "cancel_requested": run.cancel_requested,
    }


@activity("workflows.mark_running", **_STATUS_OPTS)
def mark_running(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """PENDING/WAITING → RUNNING (no-op when already RUNNING or terminal); records the Temporal run id."""
    with tenant_uow(ctx.actor) as db:
        run = _lock_own(db, ctx, payload)
        if runs.is_terminal(run.status):
            return _state(run)
        if run.status != W.RUNNING:
            runs.transition(db, run, W.RUNNING, reason=str(payload.get("reason") or "started")[:64])
        external = payload.get("external_run_id")
        if external:
            run.external_run_id = str(external)[:255]
        run.heartbeat_at = utcnow()
        return _state(run)


@activity("workflows.mark_waiting", **_STATUS_OPTS)
def mark_waiting(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        run = _lock_own(db, ctx, payload)
        if run.status == W.RUNNING:
            signal = str(payload.get("signal") or "")[:64]
            runs.transition(db, run, W.WAITING, reason=f"waiting_for_signal:{signal}" if signal else "waiting")
        run.heartbeat_at = utcnow()
        return _state(run)


@activity("workflows.mark_completed", **_STATUS_OPTS)
def mark_completed(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = normalize_result(payload.get("result"), what="workflow result")
    except (PayloadError, ValueError) as exc:
        raise ValidationFailed(str(exc)) from exc
    with tenant_uow(ctx.actor) as db:
        run = _lock_own(db, ctx, payload)
        if runs.is_terminal(run.status):
            return _state(run)
        if run.status == W.PENDING:
            runs.transition(db, run, W.RUNNING, reason="started")
        if run.status == W.WAITING:
            runs.transition(db, run, W.RUNNING, reason="resumed")
        runs.transition(db, run, W.COMPLETED, result=result, reason="completed")
        run.heartbeat_at = utcnow()
        return _state(run)


def _end(ctx: ActivityContext, payload: dict[str, Any], target: str, default_error: str) -> dict[str, Any]:
    error = str(payload.get("error") or default_error)[:MAX_ERROR_MESSAGE_CHARS]
    with tenant_uow(ctx.actor) as db:
        run = _lock_own(db, ctx, payload)
        if runs.is_terminal(run.status):
            return _state(run)
        if run.status == W.PENDING and target != W.CANCELLED:
            runs.transition(db, run, W.RUNNING, reason="started")
        runs.transition(db, run, target, error=error, reason=str(payload.get("reason") or target.lower())[:64])
        run.heartbeat_at = utcnow()
        return _state(run)


@activity("workflows.mark_failed", **_STATUS_OPTS)
def mark_failed(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    target = W.TIMED_OUT if payload.get("status") == W.TIMED_OUT else W.FAILED
    return _end(ctx, payload, target, "Workflow failed")


@activity("workflows.mark_cancelled", **_STATUS_OPTS)
def mark_cancelled(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _end(ctx, payload, W.CANCELLED, "Cancelled on request")


@activity("workflows.register_child", **_STATUS_OPTS)
def register_child(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Create (or find) the child run of the calling workflow and return what the parent needs to start it."""
    kind = str(payload.get("kind") or "")
    if not has_flow(kind):
        raise ValidationFailed(f"Unknown workflow kind '{kind}'")
    try:
        body = normalize_mapping(payload.get("input"), what="child workflow input")
    except PayloadError as exc:
        raise ValidationFailed(str(exc)) from exc
    detached = bool(payload.get("detached"))
    with tenant_uow(ctx.actor) as db:
        parent = _lock_own(db, ctx, payload)
        child, created = runs.ensure_child_run(
            db,
            parent,
            kind=kind,
            flow_input=body,
            workflow_key=str(payload.get("workflow_key") or "default"),
            subject_type=payload.get("subject_type"),
            subject_id=payload.get("subject_id"),
            inline=not detached,
        )
        db.flush()
        return {
            "workflow_run_id": str(child.id),
            "temporal_workflow_id": runs.temporal_workflow_id(child),
            "status": child.status,
            "created": created,
            "result": child.result if child.status == W.COMPLETED else None,
            "error": child.error if runs.is_terminal(child.status) else None,
            "envelope": runs.temporal_envelope(child),
        }


@activity("workflows.get_run_status", **_STATUS_OPTS)
def get_run_status(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Status of another run of the same organization (used to await a child executing elsewhere)."""
    try:
        target = uuid.UUID(str(payload.get("target_workflow_run_id")))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("target_workflow_run_id is required") from exc
    with tenant_uow(ctx.actor) as db:
        run = db.get(WorkflowRun, target)
        if run is None or run.organization_id != ctx.organization_id:
            raise NotFound("Workflow run not found")
        return {
            "workflow_run_id": str(run.id),
            "status": run.status,
            "result": run.result if run.status == W.COMPLETED else None,
            "error": run.error,
        }


def _require_platform_actor(ctx: ActivityContext) -> None:
    actor = ctx.actor
    platform = actor.kind == "system" or (
        actor.kind == "workflow"
        and actor.user_id is None
        and actor.api_key_id is None
        and actor.service_account_id is None
    )
    if not platform:
        raise Forbidden("Maintenance activities run only for platform (system) actors")


@activity("workflows.retention_purge", timeout_seconds=3600, max_attempts=3)
def retention_purge(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the organization's retention policy (idempotent)."""
    _require_platform_actor(ctx)
    return {"counts": maintenance.retention_purge(ctx.organization_id)}


@activity("workflows.resume_stale", timeout_seconds=600, max_attempts=3)
def resume_stale(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """One stuck-run recovery pass (start pending, resume stale, re-deliver signals) for the organization."""
    _require_platform_actor(ctx)
    return {"counts": maintenance.stuck_run_recovery(ctx.organization_id)}
