"""Mission event log: persisted first (``lab.events``), then fanned out over the event bus after commit.

* ``seq`` is allocated per mission by an atomic ``UPDATE ... RETURNING`` on ``lab.missions.event_seq`` so SSE
  event ids are gap-free and strictly increasing even with concurrent writers.
* Publication to the bus happens only after the transaction commits — subscribers never see an event that was
  rolled back, and a lost bus message never loses data (SSE clients re-read from the database).
* Major events are also enqueued as signed outbound webhooks (``WEBHOOK_EVENT_NAMES``).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import structlog
from sqlalchemy import Table, select, update
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import on_commit
from aegis_api.infrastructure.eventbus import get_event_bus, mission_channel
from aegis_api.models.lab import LabEvent, Mission
from aegis_api.services.lab.common import Actor
from engines.lab.enums import WEBHOOK_EVENT_NAMES

log = structlog.get_logger("aegis.lab.events")

MAX_EVENT_DATA_KEYS = 64


def _next_seq(db: Session, mission_id: uuid.UUID) -> int:
    table = cast(Table, Mission.__table__)
    seq = db.execute(
        update(table)
        .where(table.c.id == mission_id)
        .values(event_seq=table.c.event_seq + 1)
        .returning(table.c.event_seq)
    ).scalar_one_or_none()
    if seq is None:
        raise ValueError(f"mission {mission_id} not found for event emission")
    return int(seq)


def serialize(event: LabEvent) -> dict[str, Any]:
    return {
        "id": event.seq,
        "event_id": str(event.id),
        "mission_id": str(event.mission_id) if event.mission_id else None,
        "project_id": str(event.project_id) if event.project_id else None,
        "event_type": event.event_type,
        "level": event.level,
        "message": event.message,
        "data": event.data,
        "actor": event.actor,
        "trace_id": event.trace_id,
        "created_at": (event.created_at or utcnow()).isoformat(),
    }


def emit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    message: str,
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
    level: str = "info",
    actor: Actor | None = None,
    trace_id: str | None = None,
    webhook: bool = True,
) -> LabEvent:
    payload = dict(list((data or {}).items())[:MAX_EVENT_DATA_KEYS])
    seq = _next_seq(db, mission_id) if mission_id else 0
    event = LabEvent(
        organization_id=organization_id,
        mission_id=mission_id,
        project_id=project_id,
        seq=seq,
        event_type=str(event_type),
        level=level,
        message=message[:4000],
        data=payload,
        actor=actor.label if actor else None,
        trace_id=trace_id or structlog.contextvars.get_contextvars().get("trace_id"),
        created_at=utcnow(),
    )
    db.add(event)
    db.flush()
    body = serialize(event)
    if mission_id:
        channel = mission_channel(str(organization_id), str(mission_id))

        def _publish() -> None:
            try:
                get_event_bus().publish(channel, body)
            except Exception:  # the database is the source of truth; subscribers poll as a fallback
                log.warning("event_publish_failed", event_type=event.event_type)

        on_commit(db, _publish)
    webhook_name = WEBHOOK_EVENT_NAMES.get(str(event_type))
    if webhook and webhook_name:
        from aegis_api.services import webhook_service

        webhook_service.enqueue_event(
            db,
            organization_id,
            webhook_name,
            {
                "mission_id": body["mission_id"],
                "project_id": body["project_id"],
                "message": event.message,
                "data": payload,
                "lab_event_id": body["event_id"],
            },
        )
        on_commit(db, _kick_webhooks)
    return event


def _kick_webhooks() -> None:
    try:
        from aegis_api.jobs.dispatcher import dispatch_task

        dispatch_task("aegis_api.processes.event_consumer:deliver_webhooks_once")
    except Exception:  # the event consumer process also polls pending deliveries
        log.debug("webhook_kick_failed")


def list_after(
    db: Session, organization_id: uuid.UUID, mission_id: uuid.UUID, *, after_seq: int = 0, limit: int = 200
) -> list[LabEvent]:
    return list(
        db.scalars(
            select(LabEvent)
            .where(
                LabEvent.organization_id == organization_id,
                LabEvent.mission_id == mission_id,
                LabEvent.seq > after_seq,
            )
            .order_by(LabEvent.seq)
            .limit(max(1, min(limit, 1000)))
        ).all()
    )
