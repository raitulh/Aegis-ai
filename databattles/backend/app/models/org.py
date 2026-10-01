from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import MembershipStatus, OrgVerification, VerificationMethod


class Organization(UUIDPk, Timestamps, Base):
    """Universities, clubs, communities, sponsors and companies share one model."""

    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(16), index=True)
    tagline: Mapped[str | None] = mapped_column(String(200))
    description_md: Mapped[str | None] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(500))
    logo_key: Mapped[str | None] = mapped_column(String(300))
    accent_color: Mapped[str | None] = mapped_column(String(9))
    country: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(80))
    email_domains: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    verification_status: Mapped[str] = mapped_column(String(16), default=OrgVerification.unverified,
                                                     server_default=OrgVerification.unverified)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"))
    allow_membership_requests: Mapped[bool] = mapped_column(default=True, server_default="true")
    plan_key: Mapped[str] = mapped_column(String(32), default="free", server_default="free")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(default=False, server_default="false")


class Department(UUIDPk, Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("org_id", "slug"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OrgMembership(UUIDPk, Timestamps, Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=MembershipStatus.pending)
    verification_method: Mapped[str] = mapped_column(String(16), default=VerificationMethod.none)
    verified_at: Mapped[datetime | None]
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    request_note: Mapped[str | None] = mapped_column(String(500))   # visible to org admins only
    review_note: Mapped[str | None] = mapped_column(String(500))    # private reviewer note — never exposed to the member
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class OrgInvite(UUIDPk, Base):
    __tablename__ = "org_invites"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime]
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Plan(Base):
    """Subscription plans. Entitlements are configuration, not code branches."""

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    price_cents_monthly: Mapped[int] = mapped_column(Integer, default=0)
    entitlements: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_public: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class OrgSubscription(UUIDPk, Timestamps, Base):
    __tablename__ = "org_subscriptions"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    plan_key: Mapped[str] = mapped_column(ForeignKey("plans.key"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    provider: Mapped[str] = mapped_column(String(32), default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    current_period_end: Mapped[datetime | None]
    last_payment_failed_at: Mapped[datetime | None]
