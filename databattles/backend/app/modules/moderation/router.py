from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.core.deps import Actor, require_moderator, require_user
from app.core.pagination import PageParams
from app.core.schemas import Message, Schema
from app.modules.moderation import service

router = APIRouter(tags=["moderation"])


class ReportIn(Schema):
    target_type: str = Field(pattern="^(thread|comment|project|dataset|user|competition)$")
    target_id: uuid.UUID
    reason: str = Field(pattern="^(spam|abuse|misleading|copyright|privacy|other)$")
    details: str | None = Field(default=None, max_length=1000)


class ResolveIn(Schema):
    target_type: str = Field(pattern="^(thread|comment|project|dataset|user|competition)$")
    target_id: uuid.UUID
    action: str = Field(pattern="^(dismiss|hide|restore|delete|takedown|warn|suspend_user)$")
    note: str = Field(min_length=3, max_length=1000)


class StatusIn(Schema):
    status: str = Field(pattern="^(active|suspended|banned)$")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/reports", response_model=Message, status_code=201)
def report(data: ReportIn, actor: Actor = Depends(require_user)) -> Message:
    service.create_report(actor, data.target_type, data.target_id, data.reason, data.details)
    return Message(message="Thanks — a moderator will review this report.")


@router.get("/me/reports", tags=["me"])
def my_reports(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    return service.my_reports(actor)


@router.get("/moderation/queue")
def moderation_queue(params: PageParams = Depends(), status: str = Query("open", pattern="^(open|actioned|dismissed)$"),
                     target_type: str | None = Query(None, pattern="^(thread|comment|project|dataset|user|competition)$"),
                     actor: Actor = Depends(require_moderator)) -> dict[str, Any]:
    items, total = service.queue(actor, params, status=status, target_type=target_type)
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size,
            "has_next": params.offset + len(items) < total}


@router.post("/moderation/resolve", response_model=Message)
def resolve(data: ResolveIn, actor: Actor = Depends(require_moderator)) -> Message:
    service.resolve(actor, data.target_type, data.target_id, data.action, data.note)
    return Message(message="Reports resolved.")


@router.put("/moderation/users/{user_id}/status", response_model=Message)
def user_status(user_id: uuid.UUID, data: StatusIn, actor: Actor = Depends(require_moderator)) -> Message:
    service.set_user_status(actor, user_id, data.status, data.reason)
    return Message(message=f"Account status set to {data.status}.")
