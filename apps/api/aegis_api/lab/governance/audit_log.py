"""Read access to the organization's append-only audit log (keyset/cursor pagination)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, decode_cursor, encode_cursor
from aegis_api.lab.governance.schemas import AuditEntryOut
from aegis_api.models import AuditLog

ACTOR_TYPES = ("user", "api_key", "service_account", "agent", "workflow", "system")


def list_audit_entries(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_type: str | None = None,
    user_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> CursorPage[AuditEntryOut]:
    """Newest first. Filters are exact matches; ``since`` is inclusive and ``until`` exclusive."""
    stmt = select(AuditLog).where(AuditLog.organization_id == actor.organization_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if actor_type:
        if actor_type not in ACTOR_TYPES:
            raise ValidationFailed(f"actor_type must be one of {', '.join(ACTOR_TYPES)}")
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    if user_id:
        try:
            stmt = stmt.where(AuditLog.user_id == uuid.UUID(user_id))
        except ValueError as exc:
            raise ValidationFailed("user_id must be a UUID") from exc
    if since is not None and until is not None and since >= until:
        raise ValidationFailed("since must be earlier than until")
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    if params.cursor:
        data = decode_cursor(params.cursor)
        try:
            at = datetime.fromisoformat(str(data["t"]))
            last_id = uuid.UUID(str(data["i"]))
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Invalid pagination cursor") from exc
        stmt = stmt.where(or_(AuditLog.created_at < at, and_(AuditLog.created_at == at, AuditLog.id < last_id)))
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(params.limit + 1)).all()
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)}) if has_more and rows else None
    )
    return CursorPage[AuditEntryOut](
        items=[AuditEntryOut.model_validate(r) for r in rows], next_cursor=next_cursor, limit=params.limit
    )
