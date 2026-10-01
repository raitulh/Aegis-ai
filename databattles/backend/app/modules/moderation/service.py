"""Reports, the moderation queue, moderator actions and account suspension.

Every moderator action requires a reason and writes an audit entry. Reporters are never revealed to the
reported user. Suspended users keep read access to public pages but cannot sign in to act.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import uuid7
from app.core.pagination import PageParams
from app.core.rate_limit import enforce
from app.core.schemas import user_mini
from app.core.time import utcnow
from app.models.community import Comment, Report, Thread
from app.models.competition import Competition
from app.models.dataset import Dataset
from app.models.enums import NotificationKind, ReportReason, ReportStatus, UserStatus
from app.models.project import Project
from app.models.user import AuthSession, PlatformRoleAssignment, User
from app.modules.notifications.service import notify

TARGET_TYPES = ("thread", "comment", "project", "dataset", "user", "competition")


def _target(db: Session, target_type: str, target_id: uuid.UUID) -> Any:
    model = {"thread": Thread, "comment": Comment, "project": Project, "dataset": Dataset, "user": User,
             "competition": Competition}.get(target_type)
    if model is None:
        raise ValidationFailed("Unknown report target.")
    return db.get(model, target_id)


def _target_owner(target_type: str, obj: Any) -> uuid.UUID | None:
    return {"thread": getattr(obj, "author_id", None), "comment": getattr(obj, "author_id", None),
            "project": getattr(obj, "owner_id", None), "dataset": getattr(obj, "owner_user_id", None),
            "user": getattr(obj, "id", None), "competition": getattr(obj, "created_by", None)}.get(target_type)


def _preview(db: Session, target_type: str, obj: Any) -> dict[str, Any]:
    if obj is None:
        return {"title": "(deleted)", "excerpt": None, "url": None}
    if target_type == "thread":
        return {"title": obj.title, "excerpt": obj.body_md[:400], "url": f"/discussions/t/{obj.id}", "hidden": obj.hidden}
    if target_type == "comment":
        return {"title": "Reply", "excerpt": obj.body_md[:400], "url": f"/discussions/t/{obj.thread_id}#c-{obj.id}", "hidden": obj.hidden}
    if target_type == "project":
        return {"title": obj.title, "excerpt": obj.summary, "url": f"/projects/{obj.slug}", "hidden": obj.taken_down}
    if target_type == "dataset":
        return {"title": obj.title, "excerpt": obj.subtitle, "url": f"/datasets/{obj.slug}", "hidden": obj.status == "taken_down"}
    if target_type == "competition":
        return {"title": obj.title, "excerpt": obj.summary, "url": f"/competitions/{obj.slug}", "hidden": obj.frozen}
    return {"title": f"@{obj.handle}", "excerpt": obj.headline, "url": f"/u/{obj.handle}", "hidden": obj.status != UserStatus.active}


def create_report(actor: Actor, target_type: str, target_id: uuid.UUID, reason: str, details: str | None) -> None:
    db = actor.db
    enforce("report_create", str(actor.id), limit=20, window_seconds=3600)
    if target_type not in TARGET_TYPES:
        raise ValidationFailed("Unknown report target.")
    if reason not in {r.value for r in ReportReason}:
        raise ValidationFailed(details={"fields": {"reason": "Choose a reason."}})
    obj = _target(db, target_type, target_id)
    if obj is None:
        raise NotFound()
    if _target_owner(target_type, obj) == actor.id:
        raise Conflict("You cannot report your own content.", code="own_content")
    db.execute(insert(Report).values(id=uuid7(), reporter_id=actor.id, target_type=target_type, target_id=target_id, reason=reason,
                                     details=(details or "")[:1000] or None, status=ReportStatus.open)
               .on_conflict_do_nothing(index_elements=["reporter_id", "target_type", "target_id"],
                                       index_where=Report.status == ReportStatus.open))
    db.commit()


def queue(actor: Actor, params: PageParams, *, status: str, target_type: str | None) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(Report.target_type, Report.target_id, func.count().label("n"), func.min(Report.created_at).label("first"),
                  func.array_agg(func.distinct(Report.reason)).label("reasons")).where(Report.status == status)
    if target_type:
        stmt = stmt.where(Report.target_type == target_type)
    stmt = stmt.group_by(Report.target_type, Report.target_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(func.count().desc(), func.min(Report.created_at)).limit(params.page_size).offset(params.offset)).all()
    out = []
    for ttype, tid, n, first, reasons in rows:
        obj = _target(db, ttype, tid)
        owner_id = _target_owner(ttype, obj) if obj else None
        owner = db.get(User, owner_id) if owner_id else None
        details = db.scalars(select(Report.details).where(Report.target_type == ttype, Report.target_id == tid,
                                                          Report.status == status, Report.details.is_not(None)).limit(5)).all()
        out.append({"target_type": ttype, "target_id": tid, "report_count": n, "first_reported_at": first, "reasons": reasons,
                    "details": details, "preview": _preview(db, ttype, obj), "owner": user_mini(owner),
                    "owner_status": owner.status if owner else None})
    return out, total


ACTIONS = ("dismiss", "hide", "restore", "delete", "takedown", "warn", "suspend_user")


def resolve(actor: Actor, target_type: str, target_id: uuid.UUID, action: str, note: str) -> None:
    db = actor.db
    if action not in ACTIONS:
        raise ValidationFailed("Unknown action.")
    obj = _target(db, target_type, target_id)
    owner_id = _target_owner(target_type, obj) if obj else None
    now = utcnow()
    outcome = {"dismiss": "No action was needed.", "warn": "A moderator issued a warning about this content."}.get(action)
    if obj is not None and action in ("hide", "restore", "delete", "takedown"):
        hide = action != "restore"
        if target_type in ("thread", "comment"):
            if action == "delete":
                obj.deleted_at = now
            else:
                obj.hidden = hide
            if target_type == "thread":
                from app.modules.search.indexer import index_thread

                index_thread(db, obj)
        elif target_type == "project":
            obj.taken_down = hide
            obj.takedown_reason = note[:500] if hide else None
            from app.modules.search.indexer import index_project, remove

            if hide:
                remove(db, "project", obj.id)
            else:
                index_project(db, obj)
        elif target_type == "dataset":
            obj.status = "taken_down" if hide else "active"
            obj.takedown_reason = note[:500] if hide else None
            from app.modules.search.indexer import index_dataset

            index_dataset(db, obj)
        elif target_type == "competition":
            obj.frozen = hide
            obj.frozen_reason = note[:500] if hide else None
        elif target_type == "user":
            raise ValidationFailed("Use suspend_user for accounts.")
        outcome = {"hide": "The content was hidden.", "delete": "The content was removed.", "takedown": "The content was taken down.",
                   "restore": "The content was restored."}[action]
    if action == "suspend_user":
        if owner_id is None:
            raise ValidationFailed("This content has no owner to suspend.")
        set_user_status(actor, owner_id, UserStatus.suspended, note, commit=False)
        outcome = "The account was suspended."
    db.execute(update(Report).where(Report.target_type == target_type, Report.target_id == target_id, Report.status == ReportStatus.open)
               .values(status=ReportStatus.dismissed if action == "dismiss" else ReportStatus.actioned, resolved_by=actor.id,
                       resolution_note=note[:1000], resolved_at=now))
    record_audit(db, actor.id, f"moderation.{action}", target_type=target_type, target_id=target_id, reason=note,
                 meta={"owner_id": str(owner_id) if owner_id else None})
    if owner_id and action != "dismiss":
        notify(db, owner_id, NotificationKind.moderation, "A moderator reviewed your content", body=outcome,
               link="/guidelines", dedupe_key=f"modaction:{target_type}:{target_id}:{action}:{now:%Y%m%d%H%M}",
               email_template="moderation_outcome", email_context={"outcome": outcome, "link": f"{settings.WEB_BASE_URL}/guidelines"})
    db.commit()


def set_user_status(actor: Actor, user_id: uuid.UUID, status: str, reason: str, *, commit: bool = True) -> User:
    db = actor.db
    if not actor.is_moderator:
        raise Forbidden()
    user = db.get(User, user_id)
    if user is None or user.status == UserStatus.deleted:
        raise NotFound()
    if user.id == actor.id:
        raise Conflict("You cannot change your own account status.")
    target_is_staff = db.scalar(select(PlatformRoleAssignment.id).where(PlatformRoleAssignment.user_id == user.id))
    if target_is_staff and not actor.is_admin:
        raise Forbidden("Only platform admins can change the status of staff accounts.")
    if status not in (UserStatus.active, UserStatus.suspended, UserStatus.banned):
        raise ValidationFailed("Unknown status.")
    if status == UserStatus.banned and not actor.is_admin:
        raise Forbidden("Only platform admins can ban accounts.")
    user.status = status
    user.status_reason = reason[:500]
    user.status_changed_at = utcnow()
    if status != UserStatus.active:
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    record_audit(db, actor.id, f"user.{status}", target_type="user", target_id=user.id, reason=reason)
    from app.modules.search.indexer import index_user

    index_user(db, user)
    if commit:
        db.commit()
    return user


def my_reports(actor: Actor) -> list[dict[str, Any]]:
    rows = actor.db.scalars(select(Report).where(Report.reporter_id == actor.id).order_by(Report.created_at.desc()).limit(50)).all()
    return [{"id": r.id, "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason, "status": r.status,
             "created_at": r.created_at, "resolved_at": r.resolved_at} for r in rows]
