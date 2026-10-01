from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import EmailStr, Field

from app.core.schemas import Schema


class SignupIn(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)
    handle: str | None = Field(default=None, max_length=30)
    accept_terms: bool = True


class LoginIn(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    issue_bearer: bool = False  # API/CLI clients may request a bearer token; browsers use the cookie


class TokenIn(Schema):
    token: str = Field(min_length=10, max_length=200)


class EmailIn(Schema):
    email: EmailStr


class ResetPasswordIn(Schema):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(Schema):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ChangeEmailIn(Schema):
    current_password: str = Field(min_length=1, max_length=256)
    new_email: EmailStr


class MembershipOut(Schema):
    org_id: uuid.UUID
    org_slug: str
    org_name: str
    org_type: str
    role: str
    status: str
    verification_method: str
    verified_at: datetime | None


class MeOut(Schema):
    id: uuid.UUID
    email: str
    email_verified: bool
    handle: str
    display_name: str
    avatar_url: str | None
    headline: str | None
    timezone: str
    platform_roles: list[str]
    memberships: list[MembershipOut]
    onboarding_completed: bool
    open_to_opportunities: bool
    privacy: dict[str, bool]
    github_login: str | None
    has_password: bool = True
    unread_notifications: int
    is_demo: bool
    created_at: datetime


class LoginOut(Schema):
    user: MeOut
    session_token: str | None = None


class SessionOut(Schema):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    current: bool


class MailOut(Schema):
    id: uuid.UUID
    to_email: str
    subject: str
    template: str
    text_body: str | None
    status: str
    created_at: datetime


class OAuthProvidersOut(Schema):
    providers: list[dict[str, Any]]
