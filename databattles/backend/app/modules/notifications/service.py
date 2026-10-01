"""Notification fan-out: in-app notifications (deduplicated, grouped) plus optional email."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.ids import uuid7
from app.core.security import make_signed_payload
from app.core.time import utcnow
from app.email.sender import queue_email
from app.models.community import Notification, NotificationPreference
from app.models.enums import EMAIL_DEFAULT_KINDS, NotificationKind, UserStatus
from app.models.user import User

UNSUBSCRIBE_PURPOSE = "unsubscribe"


def preferences_for(db: Session, user_id: uuid.UUID) -> dict[str, dict[str, bool]]:
    rows = db.scalars(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).all()
    stored = {r.kind: {"in_app": r.in_app, "email": r.email} for r in rows}
    return {
        kind.value: stored.get(kind.value, {"in_app": True, "email": kind in EMAIL_DEFAULT_KINDS})
        for kind in NotificationKind
    }


def unsubscribe_url(user_id: uuid.UUID, kind: str) -> str:
    token = make_signed_payload({"u": str(user_id), "k": kind}, UNSUBSCRIBE_PURPOSE, 60 * 60 * 24 * 60)
    return f"{settings.WEB_BASE_URL}/unsubscribe?token={token}"


def notify(
    db: Session,
    user_id: uuid.UUID,
    kind: NotificationKind | str,
    title: str,
    *,
    body: str | None = None,
    link: str | None = None,
    dedupe_key: str,
    group_key: str | None = None,
    email_template: str | None = None,
    email_context: dict[str, Any] | None = None,
) -> None:
    """Create an in-app notification (idempotent via dedupe_key) and queue email if the user opted in.

    Notification previews deliberately avoid sensitive content (no private scores, no message bodies
    from private competitions beyond a short title).
    """
    kind = NotificationKind(kind)
    user = db.get(User, user_id)
    if user is None or user.status != UserStatus.active:
        return
    prefs = preferences_for(db, user_id)[kind.value]
    created = False
    if prefs["in_app"]:
        if group_key:
            existing = db.scalar(
                select(Notification).where(
                    Notification.user_id == user_id, Notification.group_key == group_key,
                    Notification.read_at.is_(None), Notification.created_at > utcnow() - timedelta(hours=24),
                ).order_by(Notification.created_at.desc()).limit(1)
            )
            if existing is not None and existing.dedupe_key != dedupe_key:
                db.execute(update(Notification).where(Notification.id == existing.id).values(
                    group_count=Notification.group_count + 1, title=title[:200], body=(body or "")[:500] or None,
                    link=link, created_at=utcnow()))
                return
        result = db.execute(
            insert(Notification).values(
                id=uuid7(), user_id=user_id, kind=kind.value, title=title[:200], body=(body or "")[:500] or None,
                link=link, dedupe_key=dedupe_key[:200], group_key=group_key,
            ).on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"]).returning(Notification.id)
        )
        # Note: rowcount is unreliable for INSERT .. ON CONFLICT DO NOTHING; RETURNING tells us if a row was created.
        created = result.first() is not None
    else:
        created = True  # still allow email when in-app is muted
    if created and prefs["email"] and email_template:
        ctx = {"name": user.display_name, "title": title, "body": body or "",
               "link": f"{settings.WEB_BASE_URL}{link}" if link and link.startswith("/") else (link or settings.WEB_BASE_URL)}
        ctx.update(email_context or {})
        queue_email(db, user.email, email_template, ctx, dedupe_key=f"notif:{user_id}:{dedupe_key}"[:200],
                    unsubscribe_url=unsubscribe_url(user_id, kind.value))


def notify_many(db: Session, user_ids: list[uuid.UUID], kind: NotificationKind | str, title: str, *, dedupe_prefix: str,
                **kwargs: Any) -> None:
    for uid in dict.fromkeys(user_ids):
        notify(db, uid, kind, title, dedupe_key=f"{dedupe_prefix}:{uid}", **kwargs)
