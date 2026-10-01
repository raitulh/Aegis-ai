from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.pagination import PageParams
from app.core.schemas import Message, Schema
from app.modules.discussions import service

router = APIRouter(prefix="/discussions", tags=["discussions"])


class ThreadIn(Schema):
    title: str = Field(min_length=5, max_length=160)
    body_md: str = Field(min_length=10, max_length=40_000)
    category: str | None = Field(default=None, max_length=60)
    competition: str | None = Field(default=None, max_length=80)
    project: str | None = Field(default=None, max_length=80)
    announcement: bool = False


class ThreadEdit(Schema):
    title: str | None = Field(default=None, max_length=160)
    body_md: str | None = Field(default=None, max_length=40_000)


class CommentIn(Schema):
    body_md: str = Field(min_length=2, max_length=20_000)
    reply_to_id: uuid.UUID | None = None


class CommentEdit(Schema):
    body_md: str = Field(min_length=2, max_length=20_000)


class ModerateIn(Schema):
    action: str = Field(pattern="^(lock|unlock|pin|unpin|hide|unhide)$")
    reason: str | None = Field(default=None, max_length=500)


class AcceptIn(Schema):
    comment_id: uuid.UUID | None = None


class ReasonIn(Schema):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/categories")
def categories(actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return service.categories(actor.db)


@router.get("/threads")
def threads(params: PageParams = Depends(), category: str | None = Query(None, max_length=60),
            competition: str | None = Query(None, max_length=80), project: str | None = Query(None, max_length=80),
            q: str | None = Query(None, max_length=80), sort: str = Query("activity", pattern="^(activity|new|replies|unanswered)$"),
            mine: bool = False, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    items, total, context = service.list_threads(actor, params, category=category, competition=competition, project=project,
                                                 q=q, sort=sort, mine=mine)
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size,
            "has_next": params.offset + len(items) < total, "context": context}


@router.post("/threads", status_code=201)
def create_thread(data: ThreadIn, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    t = service.create_thread(actor, title=data.title, body_md=data.body_md, category=data.category, competition=data.competition,
                              project=data.project, announcement=data.announcement)
    return {"id": t.id}


@router.get("/threads/{thread_id}")
def thread(thread_id: uuid.UUID, params: PageParams = Depends(), actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.thread_detail(actor, service.load_thread(actor, thread_id), params)


@router.patch("/threads/{thread_id}", response_model=Message)
def edit_thread(thread_id: uuid.UUID, data: ThreadEdit, actor: Actor = Depends(require_user)) -> Message:
    service.edit_thread(actor, service.load_thread(actor, thread_id), data.title, data.body_md)
    return Message(message="Post updated.")


@router.post("/threads/{thread_id}/delete", response_model=Message)
def delete_thread(thread_id: uuid.UUID, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    service.delete_thread(actor, service.load_thread(actor, thread_id), data.reason)
    return Message(message="Discussion deleted.")


@router.post("/threads/{thread_id}/comments", status_code=201)
def comment(thread_id: uuid.UUID, data: CommentIn, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    c = service.add_comment(actor, service.load_thread(actor, thread_id), data.body_md, data.reply_to_id)
    return {"id": c.id}


@router.patch("/comments/{comment_id}", response_model=Message)
def edit_comment(comment_id: uuid.UUID, data: CommentEdit, actor: Actor = Depends(require_user)) -> Message:
    service.edit_comment(actor, comment_id, data.body_md)
    return Message(message="Reply updated.")


@router.post("/comments/{comment_id}/delete", response_model=Message)
def delete_comment(comment_id: uuid.UUID, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    service.delete_comment(actor, comment_id, data.reason)
    return Message(message="Reply deleted.")


@router.post("/comments/{comment_id}/hide", response_model=Message)
def hide_comment(comment_id: uuid.UUID, data: ReasonIn, hidden: bool = Query(True), actor: Actor = Depends(require_user)) -> Message:
    service.hide_comment(actor, comment_id, hidden, data.reason)
    return Message(message="Reply hidden." if hidden else "Reply restored.")


@router.post("/threads/{thread_id}/accept", response_model=Message)
def accept(thread_id: uuid.UUID, data: AcceptIn, actor: Actor = Depends(require_user)) -> Message:
    service.accept_answer(actor, service.load_thread(actor, thread_id), data.comment_id)
    return Message(message="Answer accepted." if data.comment_id else "Accepted answer cleared.")


@router.post("/threads/{thread_id}/moderate", response_model=Message)
def moderate(thread_id: uuid.UUID, data: ModerateIn, actor: Actor = Depends(require_user)) -> Message:
    service.moderate_thread(actor, service.load_thread(actor, thread_id), action=data.action, reason=data.reason)
    return Message(message="Discussion updated.")


@router.post("/threads/{thread_id}/mute", response_model=Message)
def mute(thread_id: uuid.UUID, muted: bool = Query(True), actor: Actor = Depends(require_user)) -> Message:
    service.set_mute(actor, service.load_thread(actor, thread_id), muted)
    return Message(message="Notifications muted for this discussion." if muted else "Notifications unmuted.")


@router.get("/revisions/{target_type}/{target_id}")
def revisions(target_type: str, target_id: uuid.UUID, actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    if target_type not in ("thread", "comment"):
        from app.core.errors import NotFound

        raise NotFound()
    return service.revisions(actor, target_type, target_id)
