from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import UserStatus

DEFAULT_PRIVACY: dict[str, bool] = {
    "show_university": True,
    "show_department": True,
    "show_graduation_year": False,
    "show_skills": True,
    "show_activity": True,
    "show_contributions": True,
    "show_certificates": True,
    "show_badges": True,
    "show_university_on_leaderboards": True,
    "indexable": False,
}


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    # Emails are normalized to lower case before persisting, so a plain unique constraint suffices.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    email_verified_at: Mapped[datetime | None]
    password_hash: Mapped[str | None] = mapped_column(String(255))
    handle: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    headline: Mapped[str | None] = mapped_column(String(140))
    bio_md: Mapped[str | None] = mapped_column(Text)
    bio_html: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    cover_style: Mapped[str] = mapped_column(String(32), default="aurora", server_default="aurora")
    # use_alter breaks the users <-> organizations creation cycle (organizations.created_by -> users).
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL", use_alter=True, name="fk_users_university_id_organizations"))
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL", use_alter=True, name="fk_users_department_id_departments"))
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    interests: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    website_url: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    locale: Mapped[str] = mapped_column(String(10), default="en", server_default="en")
    status: Mapped[str] = mapped_column(String(16), default=UserStatus.active, server_default=UserStatus.active)
    status_reason: Mapped[str | None] = mapped_column(String(500))
    status_changed_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    privacy: Mapped[dict[str, Any]] = mapped_column(default=lambda: dict(DEFAULT_PRIVACY))
    open_to_opportunities: Mapped[bool] = mapped_column(default=False, server_default="false")
    onboarding_completed_at: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    dashboard_prefs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(default=False, server_default="false")

    def privacy_flag(self, key: str) -> bool:
        return bool({**DEFAULT_PRIVACY, **(self.privacy or {})}.get(key, False))


class AuthSession(UUIDPk, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    ip_hash: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(200))


class AuthToken(UUIDPk, Base):
    """Single-use tokens: email verification, password reset, org email verification."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AuthEvent(UUIDPk, Base):
    """Security-relevant authentication events. Identifiers are pseudonymized."""

    __tablename__ = "auth_events"
    __table_args__ = (Index("ix_auth_events_created", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(40))
    email_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    suspicious: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OAuthIdentity(UUIDPk, Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_user_id: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PlatformRoleAssignment(UUIDPk, Base):
    __tablename__ = "platform_roles"
    __table_args__ = (UniqueConstraint("user_id", "role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RecentView(Base):
    __tablename__ = "recent_views"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(server_default=func.now())
