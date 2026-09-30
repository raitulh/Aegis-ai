"""Read side of the event log: filtered, tenant- and project-visibility-scoped queries.

Every query is (1) executed on an RLS-scoped session, (2) explicitly filtered by the actor's
organization (defense in depth — a user who belongs to several organizations must only see the current
one), and (3) restricted to events of projects the actor can see. Organization-level events
(``project_id IS NULL``) are visible to every actor holding the endpoint's permission.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_by_id
from aegis_api.lab.models import LabEvent, Mission

MAX_TYPE_FILTERS = 50


@dataclass(frozen=True)
class EventFilter:
    mission_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    types: tuple[str, ...] | None = None
    subject_type: str | None = None
    subject_id: str | None = None


def normalize_types(*values: Sequence[str] | None) -> tuple[str, ...] | None:
    """Merge repeated / comma-separated ``type`` query values into a bounded, de-duplicated tuple."""
    out: list[str] = []
    for group in values:
        for raw in group or ():
            for item in raw.split(","):
                name = item.strip()
                if not name:
                    continue
                if len(name) > 48:
                    raise ValidationFailed("Event type filter values are at most 48 characters")
                if name not in out:
                    out.append(name)
    if len(out) > MAX_TYPE_FILTERS:
        raise ValidationFailed(f"At most {MAX_TYPE_FILTERS} event types can be filtered at once")
    return tuple(out) or None


def visibility_clause(db: Session, actor: Actor) -> ColumnElement[bool] | None:
    """SQL predicate restricting events to projects visible to the actor (None = no restriction)."""
    ids = visible_project_ids(db, actor)
    if ids is None:
        return None
    return or_(LabEvent.project_id.is_(None), LabEvent.project_id.in_(ids))


def filtered_statement(db: Session, actor: Actor, filters: EventFilter) -> Select[tuple[LabEvent]]:
    stmt = select(LabEvent).where(LabEvent.organization_id == actor.organization_id)
    if filters.mission_id is not None:
        stmt = stmt.where(LabEvent.mission_id == filters.mission_id)
    if filters.project_id is not None:
        stmt = stmt.where(LabEvent.project_id == filters.project_id)
    if filters.types:
        stmt = stmt.where(LabEvent.type.in_(filters.types))
    if filters.subject_type is not None:
        stmt = stmt.where(LabEvent.subject_type == filters.subject_type)
    if filters.subject_id is not None:
        stmt = stmt.where(LabEvent.subject_id == filters.subject_id)
    visible = visibility_clause(db, actor)
    if visible is not None:
        stmt = stmt.where(visible)
    return stmt


def list_events(
    db: Session, actor: Actor, filters: EventFilter, params: CursorParams, mapper: Any
) -> CursorPage[Any]:
    """Ascending keyset page over event ids."""
    if filters.project_id is not None:
        load_project(db, actor, filters.project_id)
    return paginate_by_id(db, filtered_statement(db, actor, filters), params, id_col=LabEvent.id, mapper=mapper)


def get_event(db: Session, actor: Actor, event_id: int) -> LabEvent:
    stmt = filtered_statement(db, actor, EventFilter()).where(LabEvent.id == event_id)
    event = db.scalar(stmt)
    if event is None:
        raise NotFound("Event not found")
    return event


def load_mission_for_events(db: Session, actor: Actor, mission_id: uuid.UUID) -> Mission:
    """The mission, after checking it belongs to the org and its project grants ``mission:read``."""
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    load_project(db, actor, mission.project_id, "mission:read")
    return mission


def latest_event_id(db: Session, actor: Actor) -> int:
    """Highest event id of the organization (the "tail" a new live stream starts from)."""
    value = db.scalar(select(func.max(LabEvent.id)).where(LabEvent.organization_id == actor.organization_id))
    return int(value or 0)


def event_to_dict(event: LabEvent) -> dict[str, Any]:
    """The public (SSE / API) representation of an event."""
    return {
        "id": event.id,
        "type": event.type,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "mission_id": str(event.mission_id) if event.mission_id else None,
        "project_id": str(event.project_id) if event.project_id else None,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
        "payload": event.payload or {},
    }
