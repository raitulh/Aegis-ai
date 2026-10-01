from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk
from app.models.enums import CertificateStatus


class CertificateTemplate(UUIDPk, Base):
    __tablename__ = "certificate_templates"

    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    heading: Mapped[str] = mapped_column(String(160), default="Certificate of Achievement")
    # Placeholders: {recipient}, {event}, {result}, {issuer}, {date}
    body_template: Mapped[str] = mapped_column(Text, default="Awarded to {recipient} for {result} in {event}.")
    accent_color: Mapped[str] = mapped_column(String(9), default="#7C5CFF")
    signatory_name: Mapped[str | None] = mapped_column(String(120))
    signatory_title: Mapped[str | None] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Certificate(UUIDPk, Base):
    """Rendered content is snapshotted at issuance so old versions stay verifiable."""

    __tablename__ = "certificates"

    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    recipient_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))
    competition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitions.id", ondelete="SET NULL"), index=True)
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    issuer_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"))
    issuer_name: Mapped[str] = mapped_column(String(160))
    event_title: Mapped[str] = mapped_column(String(160))
    result_label: Mapped[str] = mapped_column(String(80))
    rank: Mapped[int | None] = mapped_column(Integer)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("certificate_templates.id", ondelete="SET NULL"))
    template_version: Mapped[int] = mapped_column(Integer, default=1)
    rendered: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=CertificateStatus.valid)
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_reason: Mapped[str | None] = mapped_column(String(500))
    hidden_on_profile: Mapped[bool] = mapped_column(default=False)
    is_demo: Mapped[bool] = mapped_column(default=False)


class BadgeDefinition(UUIDPk, Base):
    __tablename__ = "badge_definitions"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(16))
    icon: Mapped[str] = mapped_column(String(40), default="award")
    color: Mapped[str] = mapped_column(String(9), default="#7C5CFF")
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    # {"type": "first_submission" | "competition_rank" | "course_completed" | "merged_prs" | "path_completed", ...params}
    criteria: Mapped[dict[str, Any]] = mapped_column(default=dict)
    criteria_version: Mapped[int] = mapped_column(Integer, default=1)
    is_manual: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    rarity_label: Mapped[str | None] = mapped_column(String(24))  # display-only
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BadgeAward(UUIDPk, Base):
    __tablename__ = "badge_awards"
    __table_args__ = (UniqueConstraint("badge_id", "user_id"),)

    badge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("badge_definitions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    criteria_version: Mapped[int] = mapped_column(Integer, default=1)
    awarded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # None => automatic
    awarded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    hidden_on_profile: Mapped[bool] = mapped_column(default=False)
