"""Organizations, users, memberships, sessions, invitations, API keys, secrets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import MembershipStatus, Plan, Role


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    plan: Mapped[str] = mapped_column(String(24), default=Plan.FREE)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    # retention_days (None = indefinite), allow_external_providers, default_evaluator, etc.
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    finding_seq: Mapped[int] = mapped_column(Integer, default=1000)
    onboarding: Mapped[dict[str, Any]] = mapped_column(default=dict)

    memberships: Mapped[list[Membership]] = relationship(back_populates="organization")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(160))
    auth_provider: Mapped[str] = mapped_column(String(24), default="local")  # local | supabase | guest
    auth_subject: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    last_active_at: Mapped[datetime | None] = mapped_column(nullable=True)
    default_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


class Membership(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default=Role.VIEWER)
    status: Mapped[str] = mapped_column(String(16), default=MembershipStatus.ACTIVE)
    last_active_at: Mapped[datetime | None] = mapped_column(nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuthSession(IdMixin, CreatedMixin, Base):
    """Opaque, revocable sessions for self-hosted (local) auth and guest demo sessions."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256))
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthToken(IdMixin, CreatedMixin, Base):
    """Single-use tokens: magic links, password resets."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # magic_link | password_reset
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Invitation(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(32), default=Role.VIEWER)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | accepted | revoked
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApiKey(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    scopes: Mapped[list[str]] = mapped_column(default=list)
    role: Mapped[str] = mapped_column(String(32), default=Role.ANALYST)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Secret(IdMixin, TimestampMixin, OrgMixin, Base):
    """Encrypted credentials (provider keys, endpoint tokens, webhook signing secrets)."""

    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_secrets_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), default="api_key")
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(String(8))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class FeatureFlag(IdMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_feature_flags_org_key"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class SavedFilter(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "saved_filters"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    page: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(String(1000))


class AuditLog(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only record of security-relevant actions (UPDATE/DELETE blocked by trigger)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "organization_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")  # user | api_key | system
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(48))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
