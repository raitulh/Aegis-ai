from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.deps import Actor, get_actor, require_user
from app.core.errors import ValidationFailed
from app.core.pagination import CursorPage, decode_cursor, encode_cursor
from app.core.schemas import Message, Schema
from app.core.security import read_signed_payload
from app.core.time import utcnow
from app.models.community import Notification, NotificationPreference
from app.models.enums import NotificationKind
from app.modules.notifications.service import UNSUBSCRIBE_PURPOSE, preferences_for

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(Schema):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None
    link: str | None
    group_count: int
    read_at: datetime | None
    created_at: datetime


class MarkReadIn(Schema):
    ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    all: bool = False


class PreferenceIn(Schema):
    kind: str
    in_app: bool
    email: bool


class PreferencesIn(Schema):
    preferences: list[PreferenceIn] = Field(max_length=40)


class UnsubscribeIn(Schema):
    token: str = Field(min_length=10, max_length=1000)


@router.get("", response_model=CursorPage[NotificationOut])
def list_notifications(cursor: str | None = Query(None, max_length=200), limit: int = Query(20, ge=1, le=50),
                       unread: bool = False, kind: str | None = Query(None, max_length=32),
                       actor: Actor = Depends(require_user)) -> dict[str, Any]:
    stmt = select(Notification).where(Notification.user_id == actor.id)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    if kind:
        stmt = stmt.where(Notification.kind == kind)
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(Notification.created_at < ts, and_(Notification.created_at == ts, Notification.id < rid)))
    rows = actor.db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)).all()
    next_cursor = encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
    return {"items": rows[:limit], "next_cursor": next_cursor}


@router.get("/unread-count")
def unread_count(actor: Actor = Depends(get_actor)) -> dict[str, int]:
    if not actor.is_authenticated:
        return {"count": 0}
    n = actor.db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == actor.id,
                                                                             Notification.read_at.is_(None))) or 0
    return {"count": n}


@router.post("/read", response_model=Message)
def mark_read(data: MarkReadIn, actor: Actor = Depends(require_user)) -> Message:
    stmt = update(Notification).where(Notification.user_id == actor.id, Notification.read_at.is_(None))
    if not data.all:
        if not data.ids:
            raise ValidationFailed("Pass ids or all=true.")
        stmt = stmt.where(Notification.id.in_(data.ids))
    actor.db.execute(stmt.values(read_at=utcnow()))
    actor.db.commit()
    return Message(message="Marked as read.")


@router.get("/preferences")
def get_preferences(actor: Actor = Depends(require_user)) -> dict[str, dict[str, bool]]:
    return preferences_for(actor.db, actor.id)  # type: ignore[arg-type]


@router.put("/preferences")
def set_preferences(data: PreferencesIn, actor: Actor = Depends(require_user)) -> dict[str, dict[str, bool]]:
    valid = {k.value for k in NotificationKind}
    for p in data.preferences:
        if p.kind not in valid:
            raise ValidationFailed(details={"fields": {"kind": f"Unknown notification type: {p.kind}"}})
        actor.db.execute(insert(NotificationPreference).values(user_id=actor.id, kind=p.kind, in_app=p.in_app, email=p.email)
                         .on_conflict_do_update(index_elements=["user_id", "kind"], set_={"in_app": p.in_app, "email": p.email}))
    actor.db.commit()
    return preferences_for(actor.db, actor.id)  # type: ignore[arg-type]


@router.post("/unsubscribe", response_model=Message, summary="One-click email unsubscribe (signed link, no sign-in needed)")
def unsubscribe(data: UnsubscribeIn, actor: Actor = Depends(get_actor)) -> Message:
    payload = read_signed_payload(data.token, UNSUBSCRIBE_PURPOSE)
    if payload is None:
        raise ValidationFailed("This unsubscribe link is invalid or has expired.", code="token_invalid")
    user_id, kind = uuid.UUID(payload["u"]), payload["k"]
    kinds = [k.value for k in NotificationKind] if kind == "all" else [kind]
    for k in kinds:
        actor.db.execute(insert(NotificationPreference).values(user_id=user_id, kind=k, in_app=True, email=False)
                         .on_conflict_do_update(index_elements=["user_id", "kind"], set_={"email": False}))
    actor.db.commit()
    return Message(message="You will no longer receive these emails. You can change this in notification settings.")
