"""Shared API schemas (public contracts). Database models are never returned directly."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Message(Schema):
    message: str


class UserMini(Schema):
    id: uuid.UUID
    handle: str
    display_name: str
    avatar_url: str | None = None


class OrgMini(Schema):
    id: uuid.UUID
    slug: str
    name: str
    type: str
    logo_url: str | None = None
    verification_status: str = "unverified"


class AuditEntryOut(Schema):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor: UserMini | None = None
    target_type: str | None
    target_id: str | None
    reason: str | None
    meta: dict[str, Any]


def user_mini(user: Any) -> UserMini | None:
    if user is None:
        return None
    return UserMini(id=user.id, handle=user.handle, display_name=user.display_name, avatar_url=user.avatar_url)


def org_mini(org: Any) -> OrgMini | None:
    if org is None:
        return None
    from app.storage import media_url

    return OrgMini(id=org.id, slug=org.slug, name=org.name, type=org.type, logo_url=media_url(org.logo_key),
                   verification_status=org.verification_status)
