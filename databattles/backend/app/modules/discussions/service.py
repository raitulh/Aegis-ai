"""Discussions: global categories plus competition- and project-scoped threads.

Scoped threads inherit the visibility of their parent — a private competition's forum is visible only
to people who can see that competition. Markdown is sanitized server-side; edits keep a revision
history; posting is rate limited; moderators (and competition organizers, for their own forums) can
lock, pin, hide and accept answers.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import extract_mentions, render_markdown
from app.core.pagination import PageParams
from app.core.permissions import can_manage_competition, can_view_competition, can_view_project
from app.core.rate_limit import enforce
from app.core.schemas import user_mini
from app.core.time import utcnow
from app.models.community import Comment, DiscussionCategory, PostRevision, Thread, ThreadMute
from app.models.competition import Competition
from app.models.enums import NotificationKind, UserStatus
from app.models.project import Project
from app.models.user import User
from app.modules.notifications.service import notify
from app.modules.search.indexer import index_thread

EDIT_WINDOW_NOTE = "Edits are recorded; moderators can view previous versions."


# ----------------------------------------------------------------------------- context & permissions


def _parent(db: Session, t: Thread) -> tuple[Competition | None, Project | None]:
    comp = db.get(Competition, t.competition_id) if t.competition_id else None
    proj = db.get(Project, t.project_id) if t.project_id else None
    return comp, proj


def can_view_thread(actor: Actor, t: Thread) -> bool:
    comp, proj = _parent(actor.db, t)
    if comp is not None and not can_view_competition(actor, comp):
        return False
    if proj is not None and not can_view_project(actor, proj):
        return False
    if t.hidden and not (actor.is_moderator or (actor.id and actor.id == t.author_id) or (comp and can_manage_competition(actor, comp))):
        return False
    return True


def can_moderate_thread(actor: Actor, t: Thread) -> bool:
    if actor.is_moderator:
        return True
    comp, proj = _parent(actor.db, t)
    if comp is not None and can_manage_competition(actor, comp):
        return True
    if proj is not None:
        from app.core.permissions import can_edit_project

        return can_edit_project(actor, proj)
    return False


def load_thread(actor: Actor, thread_id: uuid.UUID) -> Thread:
    t = actor.db.get(Thread, thread_id)
    if t is None or not can_view_thread(actor, t):
        raise NotFound("Discussion not found.")
    return t


def _resolver(db: Session) -> Any:
    def resolve(handles: Iterable[str]) -> set[str]:
        hs = list(handles)[:20]
        return set(db.scalars(select(User.handle).where(User.handle.in_(hs), User.status == UserStatus.active)))

    return resolve


def _notify_mentions(actor: Actor, t: Thread, body_md: str, key: str) -> None:
    db = actor.db
    handles = list(extract_mentions(body_md))[:10]
    if not handles:
        return
    for user in db.scalars(select(User).where(User.handle.in_(handles), User.status == UserStatus.active)):
        if user.id == actor.id:
            continue
        # Only notify people who can actually see the thread (no leaking private forums).
        viewer = Actor(db=db, user=user)
        if not can_view_thread(viewer, t):
            continue
        notify(db, user.id, NotificationKind.mention, f"@{actor.user.handle} mentioned you in “{t.title[:80]}”",  # type: ignore[union-attr]
               link=f"/discussions/t/{t.id}", dedupe_key=f"mention:{key}:{user.id}")


# ----------------------------------------------------------------------------- listing


def categories(db: Session) -> list[dict[str, Any]]:
    counts = dict(db.execute(select(Thread.category_id, func.count()).where(Thread.hidden.is_(False), Thread.deleted_at.is_(None))
                             .group_by(Thread.category_id)).all())
    return [{"id": c.id, "slug": c.slug, "name": c.name, "description": c.description, "staff_only_posting": c.staff_only_posting,
             "thread_count": counts.get(c.id, 0)} for c in db.scalars(select(DiscussionCategory).order_by(DiscussionCategory.position))]


def _thread_row(t: Thread, authors: dict[uuid.UUID, User]) -> dict[str, Any]:
    author = authors.get(t.author_id) if t.author_id else None  # type: ignore[arg-type]
    return {"id": t.id, "title": t.title, "author": user_mini(author), "pinned": t.pinned, "locked": t.locked, "hidden": t.hidden,
            "is_announcement": t.is_announcement, "reply_count": t.reply_count, "has_accepted_answer": t.accepted_comment_id is not None,
            "last_activity_at": t.last_activity_at, "created_at": t.created_at, "deleted": t.deleted_at is not None}


def list_threads(actor: Actor, params: PageParams, *, category: str | None, competition: str | None, project: str | None,
                 q: str | None, sort: str, mine: bool = False) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    db = actor.db
    stmt = select(Thread).where(Thread.deleted_at.is_(None))
    context: dict[str, Any] = {}
    if competition:
        comp = db.scalar(select(Competition).where(Competition.slug == competition))
        if comp is None or not can_view_competition(actor, comp):
            raise NotFound("Competition not found.")
        stmt = stmt.where(Thread.competition_id == comp.id)
        context = {"competition": {"slug": comp.slug, "title": comp.title}, "can_moderate": can_manage_competition(actor, comp) or actor.is_moderator}
    elif project:
        proj = db.scalar(select(Project).where(Project.slug == project))
        if proj is None or not can_view_project(actor, proj):
            raise NotFound("Project not found.")
        stmt = stmt.where(Thread.project_id == proj.id)
        context = {"project": {"slug": proj.slug, "title": proj.title}}
    else:
        stmt = stmt.where(Thread.competition_id.is_(None), Thread.project_id.is_(None))
        if category:
            cat = db.scalar(select(DiscussionCategory).where(DiscussionCategory.slug == category))
            if cat is None:
                raise NotFound("Category not found.")
            stmt = stmt.where(Thread.category_id == cat.id)
            context = {"category": {"slug": cat.slug, "name": cat.name, "staff_only_posting": cat.staff_only_posting}}
    if mine and actor.is_authenticated:
        stmt = stmt.where(Thread.author_id == actor.id)
    if not actor.is_moderator and not context.get("can_moderate"):
        stmt = stmt.where(or_(Thread.hidden.is_(False), Thread.author_id == actor.id) if actor.is_authenticated else Thread.hidden.is_(False))
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Thread.title.ilike(like), Thread.body_md.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {"activity": Thread.last_activity_at.desc(), "new": Thread.created_at.desc(), "replies": Thread.reply_count.desc(),
             "unanswered": Thread.reply_count.asc()}[sort]
    rows = db.scalars(stmt.order_by(Thread.pinned.desc(), order).limit(params.page_size).offset(params.offset)).all()
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_([t.author_id for t in rows if t.author_id])))}
    return [_thread_row(t, authors) for t in rows], total, context


def thread_detail(actor: Actor, t: Thread, params: PageParams) -> dict[str, Any]:
    db = actor.db
    comp, proj = _parent(db, t)
    moderator = can_moderate_thread(actor, t)
    cstmt = select(Comment).where(Comment.thread_id == t.id)
    if not moderator:
        cstmt = cstmt.where(or_(Comment.hidden.is_(False), Comment.author_id == actor.id) if actor.is_authenticated else Comment.hidden.is_(False))
    total = db.scalar(select(func.count()).select_from(cstmt.subquery())) or 0
    comments = db.scalars(cstmt.order_by(Comment.created_at).limit(params.page_size).offset(params.offset)).all()
    ids = {t.author_id, *[c.author_id for c in comments]}
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_([i for i in ids if i])))}
    accepted = db.get(Comment, t.accepted_comment_id) if t.accepted_comment_id else None
    muted = bool(actor.is_authenticated and db.scalar(select(ThreadMute.thread_id).where(ThreadMute.thread_id == t.id,
                                                                                         ThreadMute.user_id == actor.id)))

    def comment_out(c: Comment) -> dict[str, Any]:
        deleted = c.deleted_at is not None
        return {"id": c.id, "author": None if deleted else user_mini(authors.get(c.author_id) if c.author_id else None),
                "body_html": "" if deleted else c.body_html, "body_md": c.body_md if (not deleted and actor.id and actor.id == c.author_id) else None,
                "reply_to_id": c.reply_to_id, "hidden": c.hidden, "deleted": deleted, "edited_at": c.edited_at, "created_at": c.created_at,
                "is_accepted": t.accepted_comment_id == c.id,
                "can_edit": bool(actor.id and actor.id == c.author_id and not deleted and not t.locked),
                "can_delete": bool((actor.id and actor.id == c.author_id) or moderator) and not deleted}

    author = authors.get(t.author_id) if t.author_id else None
    deleted = t.deleted_at is not None
    return {
        "id": t.id, "title": t.title, "body_html": "" if deleted else t.body_html,
        "body_md": t.body_md if (actor.id and actor.id == t.author_id and not deleted) else None,
        "author": None if deleted else user_mini(author), "pinned": t.pinned, "locked": t.locked, "hidden": t.hidden, "deleted": deleted,
        "is_announcement": t.is_announcement, "reply_count": t.reply_count, "created_at": t.created_at, "edited_at": t.edited_at,
        "last_activity_at": t.last_activity_at,
        "category": _category_ref(db, t.category_id), "competition": {"slug": comp.slug, "title": comp.title} if comp else None,
        "project": {"slug": proj.slug, "title": proj.title} if proj else None,
        "accepted_comment": comment_out(accepted) if accepted and (not accepted.hidden or moderator) else None,
        "comments": {"items": [comment_out(c) for c in comments], "total": total, "page": params.page, "page_size": params.page_size,
                     "has_next": params.offset + len(comments) < total},
        "viewer": {"can_reply": bool(actor.is_authenticated and not t.locked and not deleted and actor.is_active),
                   "can_edit": bool(actor.id and actor.id == t.author_id and not deleted),
                   "can_moderate": moderator, "can_accept": bool(actor.id and (actor.id == t.author_id or moderator)),
                   "muted": muted, "signed_in": actor.is_authenticated},
        "edit_note": EDIT_WINDOW_NOTE,
    }


def _category_ref(db: Session, category_id: uuid.UUID | None) -> dict[str, Any] | None:
    if not category_id:
        return None
    cat = db.get(DiscussionCategory, category_id)
    return {"slug": cat.slug, "name": cat.name} if cat else None


# ----------------------------------------------------------------------------- writing


def create_thread(actor: Actor, *, title: str, body_md: str, category: str | None, competition: str | None,
                  project: str | None, announcement: bool = False) -> Thread:
    db = actor.db
    enforce("thread_create", str(actor.id), limit=5, window_seconds=600)
    title = title.strip()
    if len(title) < 5:
        raise ValidationFailed(details={"fields": {"title": "Use at least 5 characters."}})
    if len(body_md.strip()) < 10:
        raise ValidationFailed(details={"fields": {"body_md": "Add a little more detail (10+ characters)."}})
    t = Thread(title=title[:160], body_md=body_md, body_html=render_markdown(body_md, resolve_mentions=_resolver(db)),
               author_id=actor.id, last_activity_at=utcnow())
    if competition:
        comp = db.scalar(select(Competition).where(Competition.slug == competition))
        if comp is None or not can_view_competition(actor, comp):
            raise NotFound("Competition not found.")
        t.competition_id = comp.id
        if announcement:
            if not can_manage_competition(actor, comp):
                raise Forbidden("Only organizers can post announcements.")
            t.is_announcement = True
            t.pinned = True
    elif project:
        proj = db.scalar(select(Project).where(Project.slug == project))
        if proj is None or not can_view_project(actor, proj):
            raise NotFound("Project not found.")
        t.project_id = proj.id
    else:
        if not category:
            raise ValidationFailed(details={"fields": {"category": "Choose a category."}})
        cat = db.scalar(select(DiscussionCategory).where(DiscussionCategory.slug == category))
        if cat is None:
            raise ValidationFailed(details={"fields": {"category": "Unknown category."}})
        if cat.staff_only_posting and not actor.is_moderator:
            raise Forbidden("Only staff can start threads in this category.")
        t.category_id = cat.id
        if announcement and actor.is_moderator:
            t.is_announcement = True
    db.add(t)
    db.flush()
    _notify_mentions(actor, t, body_md, f"thread:{t.id}")
    index_thread(db, t)
    db.commit()
    return t


def edit_thread(actor: Actor, t: Thread, title: str | None, body_md: str | None) -> Thread:
    db = actor.db
    if actor.id != t.author_id:
        raise Forbidden("Only the author can edit this post.")
    if t.deleted_at:
        raise Conflict("This post was deleted.")
    enforce("post_edit", str(actor.id), limit=30, window_seconds=600)
    db.add(PostRevision(target_type="thread", target_id=t.id, title=t.title, body_md=t.body_md, edited_by=actor.id))
    if title:
        if len(title.strip()) < 5:
            raise ValidationFailed(details={"fields": {"title": "Use at least 5 characters."}})
        t.title = title.strip()[:160]
    if body_md is not None:
        t.body_md = body_md
        t.body_html = render_markdown(body_md, resolve_mentions=_resolver(db))
    t.edited_at = utcnow()
    index_thread(db, t)
    db.commit()
    return t


def delete_thread(actor: Actor, t: Thread, reason: str | None) -> None:
    db = actor.db
    is_author = actor.id == t.author_id
    if not is_author and not can_moderate_thread(actor, t):
        raise Forbidden()
    t.deleted_at = utcnow()
    if not is_author:
        record_audit(db, actor.id, "discussion.thread_delete", target_type="thread", target_id=t.id, reason=reason,
                     competition_id=t.competition_id)
    index_thread(db, t)
    db.commit()


def add_comment(actor: Actor, t: Thread, body_md: str, reply_to_id: uuid.UUID | None) -> Comment:
    db = actor.db
    enforce("comment_create", str(actor.id), limit=20, window_seconds=600)
    if t.locked and not can_moderate_thread(actor, t):
        raise Conflict("This discussion is locked.", code="thread_locked")
    if t.deleted_at:
        raise Conflict("This discussion was deleted.", code="thread_deleted")
    body_md = body_md.strip()
    if len(body_md) < 2:
        raise ValidationFailed(details={"fields": {"body_md": "Write a reply."}})
    reply_to = None
    if reply_to_id:
        reply_to = db.get(Comment, reply_to_id)
        if reply_to is None or reply_to.thread_id != t.id:
            raise ValidationFailed("You can only reply to comments in this discussion.")
    # Duplicate-post guard (double clicks / spam).
    last = db.scalar(select(Comment).where(Comment.thread_id == t.id, Comment.author_id == actor.id).order_by(Comment.created_at.desc()).limit(1))
    if last and last.body_md == body_md and last.deleted_at is None:
        return last
    c = Comment(thread_id=t.id, author_id=actor.id, reply_to_id=reply_to_id, body_md=body_md,
                body_html=render_markdown(body_md, resolve_mentions=_resolver(db)))
    db.add(c)
    t.reply_count += 1
    t.last_activity_at = utcnow()
    db.flush()
    muted = set(db.scalars(select(ThreadMute.user_id).where(ThreadMute.thread_id == t.id)))
    recipients = {t.author_id} | ({reply_to.author_id} if reply_to else set())
    for uid in recipients:
        if uid and uid != actor.id and uid not in muted:
            notify(db, uid, NotificationKind.reply, f"New reply in “{t.title[:80]}”", link=f"/discussions/t/{t.id}#c-{c.id}",
                   dedupe_key=f"reply:{c.id}:{uid}", group_key=f"thread:{t.id}")
    _notify_mentions(actor, t, body_md, f"comment:{c.id}")
    db.commit()
    return c


def _load_comment(actor: Actor, comment_id: uuid.UUID) -> tuple[Comment, Thread]:
    c = actor.db.get(Comment, comment_id)
    if c is None:
        raise NotFound()
    t = load_thread(actor, c.thread_id)
    return c, t


def edit_comment(actor: Actor, comment_id: uuid.UUID, body_md: str) -> Comment:
    db = actor.db
    c, t = _load_comment(actor, comment_id)
    if actor.id != c.author_id or c.deleted_at:
        raise Forbidden("Only the author can edit this reply.")
    if t.locked:
        raise Conflict("This discussion is locked.", code="thread_locked")
    enforce("post_edit", str(actor.id), limit=30, window_seconds=600)
    db.add(PostRevision(target_type="comment", target_id=c.id, body_md=c.body_md, edited_by=actor.id))
    c.body_md = body_md.strip()
    c.body_html = render_markdown(c.body_md, resolve_mentions=_resolver(db))
    c.edited_at = utcnow()
    db.commit()
    return c


def delete_comment(actor: Actor, comment_id: uuid.UUID, reason: str | None) -> None:
    db = actor.db
    c, t = _load_comment(actor, comment_id)
    is_author = actor.id == c.author_id
    if not is_author and not can_moderate_thread(actor, t):
        raise Forbidden()
    if c.deleted_at is None:
        c.deleted_at = utcnow()
        t.reply_count = max(0, t.reply_count - 1)
        if t.accepted_comment_id == c.id:
            t.accepted_comment_id = None
    if not is_author:
        record_audit(db, actor.id, "discussion.comment_delete", target_type="comment", target_id=c.id, reason=reason,
                     competition_id=t.competition_id)
    db.commit()


def accept_answer(actor: Actor, t: Thread, comment_id: uuid.UUID | None) -> None:
    db = actor.db
    if not (actor.id == t.author_id or can_moderate_thread(actor, t)):
        raise Forbidden("Only the thread author or moderators can accept an answer.")
    if comment_id is not None:
        c = db.get(Comment, comment_id)
        if c is None or c.thread_id != t.id or c.deleted_at or c.hidden:
            raise ValidationFailed("Choose a visible reply from this discussion.")
        t.accepted_comment_id = c.id
        if c.author_id and c.author_id != actor.id:
            notify(db, c.author_id, NotificationKind.reply, f"Your reply was accepted in “{t.title[:80]}”",
                   link=f"/discussions/t/{t.id}#c-{c.id}", dedupe_key=f"accepted:{c.id}")
            from app.modules.credentials.badges import evaluate_user_badges

            db.flush()
            evaluate_user_badges(db, c.author_id, trigger="answer_accepted")
    else:
        t.accepted_comment_id = None
    db.commit()


def moderate_thread(actor: Actor, t: Thread, *, action: str, reason: str | None) -> Thread:
    db = actor.db
    if not can_moderate_thread(actor, t):
        raise Forbidden()
    mapping = {"lock": ("locked", True), "unlock": ("locked", False), "pin": ("pinned", True), "unpin": ("pinned", False),
               "hide": ("hidden", True), "unhide": ("hidden", False)}
    if action not in mapping:
        raise ValidationFailed("Unknown action.")
    field, value = mapping[action]
    if field == "hidden" and not (actor.is_moderator or (t.competition_id and can_moderate_thread(actor, t))):
        raise Forbidden()
    setattr(t, field, value)
    record_audit(db, actor.id, f"discussion.thread_{action}", target_type="thread", target_id=t.id, reason=reason,
                 competition_id=t.competition_id)
    index_thread(db, t)
    db.commit()
    return t


def hide_comment(actor: Actor, comment_id: uuid.UUID, hidden: bool, reason: str | None) -> None:
    db = actor.db
    c, t = _load_comment(actor, comment_id)
    if not can_moderate_thread(actor, t):
        raise Forbidden()
    c.hidden = hidden
    record_audit(db, actor.id, "discussion.comment_hide" if hidden else "discussion.comment_unhide", target_type="comment",
                 target_id=c.id, reason=reason, competition_id=t.competition_id)
    db.commit()


def set_mute(actor: Actor, t: Thread, muted: bool) -> None:
    db = actor.db
    if muted:
        if not db.scalar(select(ThreadMute.thread_id).where(ThreadMute.thread_id == t.id, ThreadMute.user_id == actor.id)):
            db.add(ThreadMute(thread_id=t.id, user_id=actor.id))
    else:
        db.execute(delete(ThreadMute).where(ThreadMute.thread_id == t.id, ThreadMute.user_id == actor.id))
    db.commit()


def revisions(actor: Actor, target_type: str, target_id: uuid.UUID) -> list[dict[str, Any]]:
    db = actor.db
    if target_type == "thread":
        t = load_thread(actor, target_id)
        author_id = t.author_id
    else:
        c, t = _load_comment(actor, target_id)
        author_id = c.author_id
    if not (actor.id == author_id or can_moderate_thread(actor, t)):
        raise Forbidden()
    rows = db.scalars(select(PostRevision).where(PostRevision.target_type == target_type, PostRevision.target_id == target_id)
                      .order_by(PostRevision.created_at.desc())).all()
    return [{"id": r.id, "title": r.title, "body_md": r.body_md, "created_at": r.created_at} for r in rows]
