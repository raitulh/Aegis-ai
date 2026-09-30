"""Persistence helpers for ``workflow_runs`` / ``workflow_steps`` shared by the launcher, both engines,
the ``workflows.*`` activities and platform maintenance.

``workflow_runs.input`` stores an *envelope*::

    {"flow_input": {...the flow's input...}, "actor": {...workflow actor payload...}}

The actor is the launcher's identity narrowed to a ``workflow`` actor (``Actor.for_workflow``) without
human-only permissions, so activities can never do more than whoever started the workflow — and never
decide approvals or promote strategies themselves.

Every status change goes through :func:`transition` (``assert_transition("workflow", …)``), emits a
``WORKFLOW_UPDATED`` event in the same transaction and records ``WORKFLOW_DURATION`` on terminal states.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import Settings, get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.models import WorkflowRun, WorkflowStep
from aegis_api.lab.observability.metrics import WORKFLOW_DURATION
from aegis_api.lab.workflows.ctx import PayloadError, validate_workflow_key
from aegis_api.lab.workflows.registry import actor_from_payload, actor_to_payload
from engines.lab.states import WorkflowStatus, assert_transition

log = structlog.get_logger("aegis.lab.workflows")

W = WorkflowStatus
TERMINAL_STATUSES: frozenset[str] = frozenset({W.COMPLETED, W.FAILED, W.CANCELLED, W.TIMED_OUT})
ACTIVE_STATUSES: frozenset[str] = frozenset({W.RUNNING, W.WAITING})
RETRYABLE_STATUSES: frozenset[str] = frozenset({W.FAILED, W.TIMED_OUT})

# ``task_queue`` values for local-engine runs: independently dispatched runs vs. children executed inline by
# (and resumed only through) their parent.
LOCAL_QUEUE = "local"
LOCAL_INLINE_QUEUE = "local:inline"

ENGINE_LOCAL = "local"
ENGINE_TEMPORAL = "temporal"

MAX_ERROR_CHARS = 4000
MAX_EVENT_ERROR_CHARS = 500
MAX_PENDING_SIGNALS = 100
MAX_DELIVERED_SIGNALS_KEPT = 50

STEP_RUNNING = "running"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def make_envelope(flow_input: dict[str, Any], actor_payload: dict[str, Any]) -> dict[str, Any]:
    return {"flow_input": flow_input, "actor": actor_payload}


def flow_input(run: WorkflowRun) -> dict[str, Any]:
    data = run.input or {}
    value = data.get("flow_input")
    return dict(value) if isinstance(value, dict) else {}


def actor_payload(run: WorkflowRun) -> dict[str, Any]:
    data = run.input or {}
    value = data.get("actor")
    if not isinstance(value, dict) or "organization_id" not in value:
        # Defensive: a row without an actor runs as a minimal workflow actor of its own organization.
        return actor_to_payload(
            Actor(
                kind="workflow",
                organization_id=run.organization_id,
                permissions=frozenset(),
                label=f"workflow:{run.id}",
                workflow_run_id=run.id,
            )
        )
    return dict(value)


def run_actor(run: WorkflowRun) -> Actor:
    return actor_from_payload(actor_payload(run))


def workflow_actor_payload(actor: Actor, workflow_run_id: uuid.UUID) -> dict[str, Any]:
    """The launcher's identity as a workflow actor: same (or fewer) permissions, never human-only ones."""
    from aegis_api.security.rbac import HUMAN_ONLY_PERMISSIONS

    derived = actor.for_workflow(workflow_run_id)
    derived = replace(derived, permissions=frozenset(derived.permissions) - HUMAN_ONLY_PERMISSIONS)
    return actor_to_payload(derived)


def child_actor_payload(parent_payload: dict[str, Any], child_run_id: uuid.UUID) -> dict[str, Any]:
    data = dict(parent_payload)
    data["kind"] = "workflow"
    data["workflow_run_id"] = str(child_run_id)
    data["label"] = f"workflow:{child_run_id}"
    return data


def make_external_id(kind: str, subject_id: str, workflow_key: str) -> str:
    validate_workflow_key(workflow_key)
    value = f"{kind}:{subject_id}:{workflow_key}"
    if len(value) > 255:
        raise ValidationFailed("workflow external id exceeds 255 characters")
    return value


def temporal_workflow_id(run: WorkflowRun) -> str:
    """Temporal workflow ids are namespace-global, so they are prefixed with the organization id."""
    return f"{run.organization_id}:{run.external_id}"


def workflow_channel(organization_id: uuid.UUID | str, workflow_run_id: uuid.UUID | str) -> str:
    """Event-bus channel used to wake a local run waiting for a signal or cancellation."""
    return f"lab:workflow:{organization_id}:{workflow_run_id}"


def notify_run(organization_id: uuid.UUID | str, workflow_run_id: uuid.UUID | str) -> None:
    """Best-effort wake-up of a waiting local run (the run also polls, so a lost notification only adds latency)."""
    try:
        from aegis_api.lab.events.bus import get_event_bus

        get_event_bus().notify(workflow_channel(organization_id, workflow_run_id), 0)
    except Exception:
        log.debug("workflow_notify_failed", workflow_run_id=str(workflow_run_id), exc_info=True)


def get_run(db: Session, run_id: uuid.UUID) -> WorkflowRun | None:
    return db.get(WorkflowRun, run_id)


def lock_run(db: Session, run_id: uuid.UUID) -> WorkflowRun | None:
    """Load the run with ``SELECT … FOR UPDATE`` (fresh state, not the identity-map copy)."""
    return db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def emit_run_event(
    db: Session,
    run: WorkflowRun,
    *,
    previous: str | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "workflow_run_id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "previous_status": previous,
        "engine": run.engine,
        "attempt": run.attempt,
        "subject_type": run.subject_type,
        "subject_id": run.subject_id,
        "parent_workflow_run_id": str(run.parent_workflow_run_id) if run.parent_workflow_run_id else None,
        "cancel_requested": run.cancel_requested,
    }
    if reason:
        payload["reason"] = reason[:200]
    if run.error and run.status in TERMINAL_STATUSES:
        payload["error"] = run.error[:MAX_EVENT_ERROR_CHARS]
    if extra:
        payload.update(extra)
    try:
        actor: Actor | None = run_actor(run)
    except (KeyError, ValueError):
        actor = None
    emit(
        db,
        organization_id=run.organization_id,
        type=EventType.WORKFLOW_UPDATED,
        payload=payload,
        mission_id=run.mission_id,
        project_id=run.project_id,
        subject_type="workflow_run",
        subject_id=run.id,
        actor=actor,
    )


def transition(
    db: Session,
    run: WorkflowRun,
    target: str,
    *,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Validated status change + ``WORKFLOW_UPDATED`` event (+ duration metric on terminal states)."""
    previous = run.status
    assert_transition("workflow", previous, target)
    now = utcnow()
    run.status = target
    if target == W.RUNNING and run.started_at is None:
        run.started_at = now
    if target in TERMINAL_STATUSES:
        run.completed_at = now
        if result is not None:
            run.result = result
        if error is not None:
            run.error = error[:MAX_ERROR_CHARS]
        started = _as_aware(run.started_at) or _as_aware(run.created_at) or now
        WORKFLOW_DURATION.labels(run.kind, target).observe(max(0.0, (now - started).total_seconds()))
    db.flush()
    emit_run_event(db, run, previous=previous, reason=reason, extra=extra)


def create_run(
    db: Session,
    *,
    organization_id: uuid.UUID,
    kind: str,
    subject_type: str,
    subject_id: str,
    workflow_key: str,
    flow_input: dict[str, Any],
    actor_payload_for: Callable[[uuid.UUID], dict[str, Any]],
    engine: str,
    task_queue: str | None,
    project_id: uuid.UUID | None,
    mission_id: uuid.UUID | None,
    parent_workflow_run_id: uuid.UUID | None,
    created_by_id: uuid.UUID | None,
) -> tuple[WorkflowRun, bool]:
    """Insert the run unless ``(organization, external_id)`` exists; return ``(run, created)``.

    ``INSERT … ON CONFLICT DO NOTHING`` makes concurrent duplicate launches safe: the loser waits for the
    winner's transaction and then reads its committed row.
    """
    if not subject_type or len(subject_type) > 32:
        raise ValidationFailed("subject_type must be 1..32 characters")
    if not subject_id or len(subject_id) > 64:
        raise ValidationFailed("subject_id must be 1..64 characters")
    try:
        external_id = make_external_id(kind, subject_id, workflow_key)
    except PayloadError as exc:
        raise ValidationFailed(str(exc)) from exc
    run_id = uuid.uuid4()
    now = utcnow()
    inserted = db.execute(
        insert(WorkflowRun)
        .values(
            id=run_id,
            organization_id=organization_id,
            project_id=project_id,
            mission_id=mission_id,
            kind=kind,
            subject_type=subject_type,
            subject_id=subject_id,
            engine=engine,
            external_id=external_id,
            external_run_id=None,
            task_queue=task_queue,
            parent_workflow_run_id=parent_workflow_run_id,
            status=W.PENDING,
            input=make_envelope(flow_input, actor_payload_for(run_id)),
            result=None,
            error=None,
            attempt=1,
            cancel_requested=False,
            signals=[],
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_workflow_runs_external")
        .returning(WorkflowRun.id)
    ).scalar_one_or_none()
    run = db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.organization_id == organization_id, WorkflowRun.external_id == external_id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    created = inserted is not None
    if created:
        emit_run_event(db, run, reason="launched")
    return run, created


def reset_for_retry(db: Session, run: WorkflowRun, *, from_scratch: bool = False, reason: str = "retry") -> None:
    """FAILED/TIMED_OUT → PENDING as a new attempt.

    By default completed steps are kept, so the local engine resumes from the point of failure (completed
    activities are replayed, not re-executed); failed/in-flight steps are cleared so they run again.
    ``from_scratch`` clears every recorded step.
    """
    previous = run.status
    assert_transition("workflow", previous, W.PENDING)
    run.status = W.PENDING
    run.attempt = (run.attempt or 1) + 1
    run.error = None
    run.result = None
    run.started_at = None
    run.completed_at = None
    run.heartbeat_at = None
    run.cancel_requested = False
    run.external_run_id = None
    stmt = delete(WorkflowStep).where(WorkflowStep.workflow_run_id == run.id)
    if not from_scratch:
        stmt = stmt.where(WorkflowStep.status != STEP_COMPLETED)
    db.execute(stmt)
    db.flush()
    emit_run_event(db, run, previous=previous, reason=reason, extra={"from_scratch": from_scratch})


def ensure_child_run(
    db: Session,
    parent: WorkflowRun,
    *,
    kind: str,
    flow_input: dict[str, Any],
    workflow_key: str,
    subject_type: str | None,
    subject_id: str | None,
    inline: bool,
) -> tuple[WorkflowRun, bool]:
    """Create (or find) the child run of ``parent``; a FAILED/TIMED_OUT child is reset for a new attempt."""
    parent_actor = actor_payload(parent)
    if parent.engine == ENGINE_LOCAL:
        task_queue: str | None = LOCAL_INLINE_QUEUE if inline else LOCAL_QUEUE
    else:
        task_queue = parent.task_queue
    run, created = create_run(
        db,
        organization_id=parent.organization_id,
        kind=kind,
        subject_type=subject_type or "workflow_run",
        subject_id=subject_id or str(parent.id),
        workflow_key=workflow_key,
        flow_input=flow_input,
        actor_payload_for=lambda rid: child_actor_payload(parent_actor, rid),
        engine=parent.engine,
        task_queue=task_queue,
        project_id=parent.project_id,
        mission_id=parent.mission_id,
        parent_workflow_run_id=parent.id,
        created_by_id=parent.created_by_id,
    )
    if not created and run.status in RETRYABLE_STATUSES:
        reset_for_retry(db, run, reason="child_retry")
    return run, created


def append_signal(run: WorkflowRun, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append a signal to the run's inbox.

    * local engine: the inbox the flow consumes from (consumed entries are removed and recorded as steps);
    * Temporal: a delivery outbox — entries start ``delivered: false`` and are flipped once Temporal accepted
      them (the scheduler re-delivers undelivered entries; the workflow de-duplicates by signal id).
    """
    signals = [s for s in (run.signals or []) if isinstance(s, dict)]
    if run.engine == ENGINE_TEMPORAL:
        pending = [s for s in signals if not s.get("delivered")]
        delivered_rows = [s for s in signals if s.get("delivered")]
        if len(delivered_rows) > MAX_DELIVERED_SIGNALS_KEPT:
            keep = {s["id"] for s in delivered_rows[-MAX_DELIVERED_SIGNALS_KEPT:] if "id" in s}
            signals = [s for s in signals if not s.get("delivered") or s.get("id") in keep]
    else:
        pending = signals
    if len(pending) >= MAX_PENDING_SIGNALS:
        raise ValidationFailed(f"Workflow run already has {len(pending)} pending signals; refusing more")
    entry: dict[str, Any] = {"id": str(uuid.uuid4()), "name": name, "payload": payload, "at": utcnow().isoformat()}
    if run.engine == ENGINE_TEMPORAL:
        entry["delivered"] = False
    run.signals = [*signals, entry]
    return entry


def temporal_envelope(run: WorkflowRun, settings: Settings | None = None) -> dict[str, Any]:
    """Input of a Temporal workflow execution for ``run``."""
    s = settings or get_settings()
    return {
        "workflow_run_id": str(run.id),
        "organization_id": str(run.organization_id),
        "kind": run.kind,
        "attempt": run.attempt,
        "actor": actor_payload(run),
        "input": flow_input(run),
        "task_queues": {"execution": s.temporal_execution_task_queue},
    }
