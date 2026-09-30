"""Launching, signalling, cancelling and retrying workflows (the public API other contexts use).

::

    from aegis_api.lab.workflows.launcher import launch_workflow, signal_workflow, cancel_workflow

    run = launch_workflow(db, actor, "ExperimentWorkflow", subject_type="experiment", subject_id=str(exp.id),
                          input={"experiment_id": str(exp.id)}, project_id=exp.project_id)

* ``launch_workflow`` creates the ``workflow_runs`` row in the caller's transaction and starts it on the
  configured engine **after commit** (a rolled-back launch never starts anything). Launches are idempotent:
  the same ``kind`` + ``subject_id`` + ``workflow_key`` returns the existing run (pass a new ``workflow_key``
  for a genuinely new run of a finished subject).
* Temporal start failures leave the run PENDING (logged); the scheduler retries PENDING runs.
* ``signal_workflow`` stores the signal durably with the run (local inbox / Temporal delivery outbox) and
  delivers it after commit, so a workflow never observes a signal before the state change it announces.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import InvalidTransition
from aegis_api.lab.core.events import after_commit
from aegis_api.lab.models import Mission, WorkflowRun
from aegis_api.lab.workflows import runs
from aegis_api.lab.workflows.ctx import (
    MAX_SIGNAL_PAYLOAD_BYTES,
    PayloadError,
    normalize_mapping,
    validate_signal_name,
    validate_workflow_key,
)
from aegis_api.lab.workflows.definitions import flow_options, has_flow
from aegis_api.lab.workflows.local_engine import dispatch_local
from engines.lab.states import WorkflowStatus, assert_transition

log = structlog.get_logger("aegis.lab.workflows.launcher")

W = WorkflowStatus


class AuditActions:
    WORKFLOW_CANCEL_REQUESTED = "WORKFLOW_CANCEL_REQUESTED"
    WORKFLOW_RETRIED = "WORKFLOW_RETRIED"
    WORKFLOW_SIGNALLED = "WORKFLOW_SIGNALLED"


def _uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"Invalid {label} id") from exc


# ---------------------------------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------------------------------
def launch_workflow(
    db: Session,
    actor: Actor,
    kind: str,
    *,
    subject_type: str,
    subject_id: uuid.UUID | str,
    input: dict[str, Any],
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    workflow_key: str = "default",
    parent_workflow_run_id: uuid.UUID | str | None = None,
) -> WorkflowRun:
    """Create (or return the existing) run for ``kind``/``subject``/``workflow_key``; start it after commit."""
    if not has_flow(kind):
        raise ValidationFailed(f"Unknown workflow kind '{kind}'")
    try:
        validate_workflow_key(workflow_key)
        flow_input = normalize_mapping(input, what="workflow input")
    except PayloadError as exc:
        raise ValidationFailed(str(exc)) from exc
    project_uuid = _uuid(project_id, "project")
    mission_uuid = _uuid(mission_id, "mission")
    parent_uuid = _uuid(parent_workflow_run_id, "parent workflow run")
    if project_uuid is not None:
        load_project(db, actor, project_uuid)
    if mission_uuid is not None:
        get_owned(db, Mission, mission_uuid, actor, label="Mission")
    if parent_uuid is not None:
        get_owned(db, WorkflowRun, parent_uuid, actor, label="Workflow run")
    settings = get_settings()
    engine = settings.effective_workflow_engine
    run, created = runs.create_run(
        db,
        organization_id=actor.organization_id,
        kind=kind,
        subject_type=subject_type,
        subject_id=str(subject_id),
        workflow_key=workflow_key,
        flow_input=flow_input,
        actor_payload_for=lambda rid: runs.workflow_actor_payload(actor, rid),
        engine=engine,
        task_queue=settings.temporal_task_queue if engine == runs.ENGINE_TEMPORAL else runs.LOCAL_QUEUE,
        project_id=project_uuid,
        mission_id=mission_uuid,
        parent_workflow_run_id=parent_uuid,
        created_by_id=actor.user_id,
    )
    if run.status == W.PENDING:
        _start_after_commit(db, run)
    log.info("workflow_launched" if created else "workflow_launch_reused", workflow_run_id=str(run.id), kind=kind)
    return run


def _start_after_commit(db: Session, run: WorkflowRun) -> None:
    organization_id, run_id = run.organization_id, run.id
    after_commit(db, lambda: start_run(organization_id, run_id))


def start_run(organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> None:
    """Start a PENDING run on its engine (idempotent; never raises — failures leave it PENDING)."""
    try:
        with tenant_uow(organization_id) as db:
            run = db.get(WorkflowRun, workflow_run_id)
            if run is None or run.status != W.PENDING:
                return
            engine = run.engine
            kind = run.kind
            envelope = runs.temporal_envelope(run) if engine == runs.ENGINE_TEMPORAL else None
            temporal_id = runs.temporal_workflow_id(run)
            task_queue = run.task_queue or get_settings().temporal_task_queue
        if engine != runs.ENGINE_TEMPORAL:
            dispatch_local(organization_id, workflow_run_id)
            return
        from aegis_api.lab.workflows.temporal_engine import get_bridge

        assert envelope is not None
        temporal_run_id = get_bridge().start_workflow(
            kind,
            envelope,
            temporal_id,
            task_queue,
            execution_timeout_seconds=flow_options(kind).execution_timeout_seconds,
        )
        with tenant_uow(organization_id) as db:
            db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == workflow_run_id, WorkflowRun.status == W.PENDING)
                .values(external_run_id=temporal_run_id)
                .execution_options(synchronize_session=False)
            )
        log.info("temporal_workflow_started", workflow_run_id=str(workflow_run_id), kind=kind)
    except ServiceUnavailable as exc:
        log.warning("workflow_start_deferred", workflow_run_id=str(workflow_run_id), reason=str(exc))
    except Exception:
        log.exception("workflow_start_failed", workflow_run_id=str(workflow_run_id))


# ---------------------------------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------------------------------
def signal_workflow(
    db: Session,
    workflow_run_id: uuid.UUID | str,
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    actor: Actor | None = None,
) -> WorkflowRun:
    """Record signal ``name`` for the run (in the caller's transaction) and deliver it after commit.

    ``actor`` (optional) is recorded in the audit log — pass it for signals triggered directly by a person.
    """
    run_id = _uuid(workflow_run_id, "workflow run")
    assert run_id is not None
    try:
        validate_signal_name(name)
        body = normalize_mapping(payload, what="signal payload", max_bytes=MAX_SIGNAL_PAYLOAD_BYTES)
    except PayloadError as exc:
        raise ValidationFailed(str(exc)) from exc
    run = runs.lock_run(db, run_id)
    if run is None:
        raise NotFound("Workflow run not found")
    if runs.is_terminal(run.status):
        raise InvalidTransition(f"Workflow run is {run.status} and no longer accepts signals")
    entry = runs.append_signal(run, name, body)
    db.flush()
    if actor is not None:
        audit(
            db,
            actor,
            AuditActions.WORKFLOW_SIGNALLED,
            "workflow_run",
            run.id,
            after={"signal": name, "signal_id": entry["id"], "kind": run.kind},
        )
    organization_id = run.organization_id
    if run.engine == runs.ENGINE_TEMPORAL:
        after_commit(db, lambda: deliver_signals(organization_id, run_id))
    else:
        after_commit(db, lambda: runs.notify_run(organization_id, run_id))
    log.info("workflow_signalled", workflow_run_id=str(run_id), signal=name, signal_id=entry["id"])
    return run


def deliver_signals(organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> int:
    """Deliver undelivered signals of a Temporal run (outbox); returns how many were accepted."""
    with tenant_uow(organization_id) as db:
        run = db.get(WorkflowRun, workflow_run_id)
        if run is None or run.engine != runs.ENGINE_TEMPORAL or runs.is_terminal(run.status):
            return 0
        pending = [dict(s) for s in (run.signals or []) if isinstance(s, dict) and not s.get("delivered")]
        temporal_id = runs.temporal_workflow_id(run)
    if not pending:
        return 0
    from aegis_api.lab.workflows.temporal_engine import get_bridge

    bridge = get_bridge()
    delivered: set[str] = set()
    for entry in pending:
        try:
            bridge.signal(temporal_id, str(entry.get("name")), dict(entry.get("payload") or {}), entry.get("id"))
        except (ServiceUnavailable, NotFound) as exc:
            log.info("workflow_signal_delivery_deferred", workflow_run_id=str(workflow_run_id), reason=str(exc))
            break
        except Exception:
            log.exception("workflow_signal_delivery_failed", workflow_run_id=str(workflow_run_id))
            break
        delivered.add(str(entry.get("id")))
    if delivered:
        now = utcnow().isoformat()
        with tenant_uow(organization_id) as db:
            run = runs.lock_run(db, workflow_run_id)
            if run is not None:
                run.signals = [
                    {**s, "delivered": True, "delivered_at": now}
                    if isinstance(s, dict) and str(s.get("id")) in delivered
                    else s
                    for s in (run.signals or [])
                ]
    return len(delivered)


# ---------------------------------------------------------------------------------------------------
# Cancel / retry
# ---------------------------------------------------------------------------------------------------
def _load_for_update(db: Session, actor: Actor, workflow_run_id: uuid.UUID | str) -> WorkflowRun:
    run = get_owned(db, WorkflowRun, workflow_run_id, actor, label="Workflow run")
    if run.project_id is not None:
        try:
            load_project(db, actor, run.project_id)
        except NotFound as exc:
            raise NotFound("Workflow run not found") from exc
    locked = runs.lock_run(db, run.id)
    if locked is None:
        raise NotFound("Workflow run not found")
    return locked


def cancel_workflow(db: Session, actor: Actor, workflow_run_id: uuid.UUID | str) -> WorkflowRun:
    """Request cancellation (PENDING runs are cancelled immediately). Callers authorize the actor."""
    run = _load_for_update(db, actor, workflow_run_id)
    if run.status == W.CANCELLED:
        return run
    if runs.is_terminal(run.status):
        assert_transition("workflow", run.status, W.CANCELLED)
    was_pending = run.status == W.PENDING
    run.cancel_requested = True
    if was_pending:
        runs.transition(db, run, W.CANCELLED, error="Cancelled before start", reason="cancelled")
    else:
        db.flush()
        runs.emit_run_event(db, run, previous=run.status, reason="cancel_requested")
    audit(
        db,
        actor,
        AuditActions.WORKFLOW_CANCEL_REQUESTED,
        "workflow_run",
        run.id,
        after={"kind": run.kind, "status": run.status, "engine": run.engine},
    )
    organization_id, run_id, engine = run.organization_id, run.id, run.engine
    temporal_id = runs.temporal_workflow_id(run)
    if engine == runs.ENGINE_TEMPORAL:
        after_commit(db, lambda: _cancel_temporal(temporal_id, run_id))
    else:
        after_commit(db, lambda: runs.notify_run(organization_id, run_id))
    return run


def _cancel_temporal(temporal_id: str, run_id: uuid.UUID) -> None:
    from aegis_api.lab.workflows.temporal_engine import get_bridge

    try:
        get_bridge().cancel(temporal_id)
    except Exception as exc:  # retried by the scheduler (cancel_requested stays set)
        log.warning("temporal_cancel_deferred", workflow_run_id=str(run_id), reason=str(exc))


def retry_workflow(
    db: Session, actor: Actor, workflow_run_id: uuid.UUID | str, *, from_scratch: bool = False
) -> WorkflowRun:
    """FAILED/TIMED_OUT → PENDING as a new attempt, started after commit.

    On the local engine the new attempt resumes from the point of failure (completed steps are replayed)
    unless ``from_scratch`` is set.
    """
    run = _load_for_update(db, actor, workflow_run_id)
    runs.reset_for_retry(db, run, from_scratch=from_scratch)
    audit(
        db,
        actor,
        AuditActions.WORKFLOW_RETRIED,
        "workflow_run",
        run.id,
        after={"kind": run.kind, "attempt": run.attempt, "from_scratch": from_scratch},
    )
    _start_after_commit(db, run)
    return run


# ---------------------------------------------------------------------------------------------------
# Generic flows
# ---------------------------------------------------------------------------------------------------
def dispatch_compute_job(db: Session, actor: Actor, job: Any) -> WorkflowRun:
    """Launch ``ExecutionJobWorkflow`` for a submitted ``ComputeJob`` (one run per job attempt)."""
    return launch_workflow(
        db,
        actor,
        "ExecutionJobWorkflow",
        subject_type="compute_job",
        subject_id=str(job.id),
        input={"job_id": str(job.id)},
        project_id=job.project_id,
        mission_id=job.mission_id,
        workflow_key=f"attempt-{int(getattr(job, 'attempt', 1) or 1)}",
    )


def run_agent_async(db: Session, actor: Actor, agent_run: Any) -> WorkflowRun:
    """Launch ``AgentRunWorkflow`` for a persisted ``AgentRun`` and link the run to it."""
    flow_input: dict[str, Any] = {"agent_run_id": str(agent_run.id), "role": agent_run.role}
    if agent_run.project_id is not None:
        flow_input["project_id"] = str(agent_run.project_id)
    if agent_run.mission_id is not None:
        flow_input["mission_id"] = str(agent_run.mission_id)
    if agent_run.parent_run_id is not None:
        flow_input["parent_run_id"] = str(agent_run.parent_run_id)
    if agent_run.input:
        flow_input["input"] = agent_run.input
    run = launch_workflow(
        db,
        actor,
        "AgentRunWorkflow",
        subject_type="agent_run",
        subject_id=str(agent_run.id),
        input=flow_input,
        project_id=agent_run.project_id,
        mission_id=agent_run.mission_id,
    )
    if getattr(agent_run, "workflow_run_id", None) is None:
        agent_run.workflow_run_id = run.id
        db.flush()
    return run
