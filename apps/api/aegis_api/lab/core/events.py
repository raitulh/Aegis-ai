"""Domain/live events: transactional outbox + post-commit notification.

``emit()`` inserts into the append-only ``events`` table inside the caller's transaction (so an event
exists iff the state change committed) and schedules a post-commit wake-up on the event bus so SSE
streams and consumers react with low latency. Business logic never talks to Redis directly — the bus
(``aegis_api.lab.events.bus``) is an interface with Redis and in-memory implementations.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy.orm import Session

from aegis_api.lab.core.actor import Actor
from aegis_api.lab.models import LabEvent

log = structlog.get_logger("aegis.lab.events")


class EventType(StrEnum):
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_UPDATED = "MISSION_UPDATED"
    MISSION_PLANNED = "MISSION_PLANNED"
    MISSION_APPROVED = "MISSION_APPROVED"
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_PAUSED = "MISSION_PAUSED"
    MISSION_RESUMED = "MISSION_RESUMED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"
    MISSION_CANCELLED = "MISSION_CANCELLED"
    PHASE_CHANGED = "PHASE_CHANGED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_STEP = "AGENT_STEP"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    SEARCH_PROGRESS = "SEARCH_PROGRESS"
    RESEARCH_PLAN_READY = "RESEARCH_PLAN_READY"
    RESEARCH_COMPLETED = "RESEARCH_COMPLETED"
    RESEARCH_FAILED = "RESEARCH_FAILED"
    SOURCE_ADDED = "SOURCE_ADDED"
    MEMORY_WRITTEN = "MEMORY_WRITTEN"
    HYPOTHESIS_CREATED = "HYPOTHESIS_CREATED"
    HYPOTHESIS_UPDATED = "HYPOTHESIS_UPDATED"
    EXPERIMENT_CREATED = "EXPERIMENT_CREATED"
    EXPERIMENT_VALIDATED = "EXPERIMENT_VALIDATED"
    EXPERIMENT_QUEUED = "EXPERIMENT_QUEUED"
    EXPERIMENT_STARTED = "EXPERIMENT_STARTED"
    EXPERIMENT_LOG = "EXPERIMENT_LOG"
    EXPERIMENT_COMPLETED = "EXPERIMENT_COMPLETED"
    EXPERIMENT_FAILED = "EXPERIMENT_FAILED"
    COMPUTE_JOB_UPDATED = "COMPUTE_JOB_UPDATED"
    FAILURE_ANALYZED = "FAILURE_ANALYZED"
    LESSON_LEARNED = "LESSON_LEARNED"
    STRATEGY_CREATED = "STRATEGY_CREATED"
    STRATEGY_MUTATED = "STRATEGY_MUTATED"
    STRATEGY_PROMOTED = "STRATEGY_PROMOTED"
    STRATEGY_ROLLED_BACK = "STRATEGY_ROLLED_BACK"
    EVOLUTION_GENERATION_COMPLETED = "EVOLUTION_GENERATION_COMPLETED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    CLAIM_CREATED = "CLAIM_CREATED"
    REPRODUCTION_COMPLETED = "REPRODUCTION_COMPLETED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    DISCOVERY_CREATED = "DISCOVERY_CREATED"
    DISCOVERY_STATUS_CHANGED = "DISCOVERY_STATUS_CHANGED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    BUDGET_THRESHOLD = "BUDGET_THRESHOLD"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TOOL_CALLED = "TOOL_CALLED"
    POLICY_DENIED = "POLICY_DENIED"
    REPORT_GENERATED = "REPORT_GENERATED"
    WORKFLOW_UPDATED = "WORKFLOW_UPDATED"


# Outbound webhook event names for major events (payload = the event payload + ids).
WEBHOOK_EVENT_NAMES: dict[str, str] = {
    EventType.MISSION_STARTED: "mission.started",
    EventType.MISSION_COMPLETED: "mission.completed",
    EventType.MISSION_FAILED: "mission.failed",
    EventType.EXPERIMENT_FAILED: "experiment.failed",
    EventType.EXPERIMENT_COMPLETED: "experiment.completed",
    EventType.VERIFICATION_COMPLETED: "verification.completed",
    EventType.DISCOVERY_CREATED: "discovery.created",
    EventType.DISCOVERY_STATUS_CHANGED: "discovery.status_changed",
    EventType.APPROVAL_REQUESTED: "approval.required",
    EventType.APPROVAL_DECIDED: "approval.decided",
    EventType.STRATEGY_PROMOTED: "strategy.promoted",
    EventType.BUDGET_EXCEEDED: "budget.exceeded",
}


def mission_channel(organization_id: uuid.UUID | str, mission_id: uuid.UUID | str) -> str:
    return f"lab:events:{organization_id}:mission:{mission_id}"


def org_channel(organization_id: uuid.UUID | str) -> str:
    return f"lab:events:{organization_id}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value.__class__.__name__ == "Decimal":
        return float(value)
    return value


def emit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    type: str,
    payload: dict[str, Any] | None = None,
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | str | None = None,
    actor: Actor | None = None,
    trace_id: str | None = None,
) -> LabEvent:
    """Append an event to the outbox in the current transaction and wake subscribers after commit."""
    event = LabEvent(
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        mission_id=mission_id,
        subject_type=subject_type,
        subject_id=str(subject_id) if subject_id is not None else None,
        type=str(type),
        payload=_jsonable(payload or {}),
        actor=actor.as_dict() if actor else {"kind": "system"},
        trace_id=trace_id or (actor.trace_id if actor else None),
    )
    db.add(event)
    db.flush()
    channels = [org_channel(organization_id)]
    if mission_id is not None:
        channels.append(mission_channel(organization_id, mission_id))
    event_type = str(type)
    after_commit(db, lambda: _notify(channels, event.id, event_type))
    return event


def _notify(channels: list[str], event_id: int, event_type: str) -> None:
    try:
        from aegis_api.lab.observability.metrics import EVENTS_EMITTED

        EVENTS_EMITTED.labels(event_type).inc()
    except Exception:  # metrics must never break event delivery
        log.debug("events_metric_failed", exc_info=True)
    try:
        from aegis_api.lab.events.bus import get_event_bus

        bus = get_event_bus()
        for channel in channels:
            bus.notify(channel, event_id)
    except Exception:  # notification is best-effort; the outbox row is the source of truth
        log.warning("event_notify_failed", exc_info=True)


def after_commit(db: Session, callback: Any) -> None:
    """Run ``callback()`` after the current transaction commits (dropped on rollback)."""
    db.info.setdefault("after_commit_callbacks", []).append(callback)
