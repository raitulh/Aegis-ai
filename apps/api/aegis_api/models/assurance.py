"""Continuous assurance: schedules, change triggers, evidence exports and inbound contact requests."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin


class AssuranceSchedule(IdMixin, TimestampMixin, OrgMixin, Base):
    """Recurring and change-driven audits for one system."""

    __tablename__ = "assurance_schedules"
    __table_args__ = (Index("ix_assurance_schedules_due", "enabled", "next_run_at"),)

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Periodic cadence (hours); None = change-triggered only.
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Change events that start an audit: deployment, pull_request, model_change, prompt_change, policy_change,
    # tool_change, agent_version, config_change.
    trigger_on: Mapped[list[str]] = mapped_column(default=list)
    categories: Mapped[list[str]] = mapped_column(default=list)
    intensity: Mapped[str] = mapped_column(String(16), default="quick")
    policy_version_ids: Mapped[list[str]] = mapped_column(default=list)
    next_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AssuranceTrigger(IdMixin, CreatedMixin, OrgMixin, Base):
    """A change event (from CI/CD, the SDK, or a configuration change) and the audit it started."""

    __tablename__ = "assurance_triggers"
    __table_args__ = (
        UniqueConstraint("organization_id", "system_id", "event_type", "ref", name="uq_assurance_triggers_ref"),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assurance_schedules.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(200))  # commit SHA, PR number, version label, schedule tick …
    source: Mapped[str] = mapped_column(String(32), default="api")  # api | ci | schedule | system_change
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    categories: Mapped[list[str]] = mapped_column(default=list)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)


class EvidenceExport(IdMixin, CreatedMixin, OrgMixin, Base):
    """A generated evidence package (record of what was exported, its root hash and signature)."""

    __tablename__ = "evidence_exports"

    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(32))  # audit | system_runtime
    root_hash: Mapped[str] = mapped_column(String(64))
    artifact_count: Mapped[int] = mapped_column(Integer, default=0)
    integrity_status: Mapped[str] = mapped_column(String(24))
    signature: Mapped[str | None] = mapped_column(String(200))
    key_id: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class FindingComment(IdMixin, CreatedMixin, OrgMixin, Base):
    """Discussion on a finding. Comments are append-only (history is part of the audit trail)."""

    __tablename__ = "finding_comments"

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_label: Mapped[str | None] = mapped_column(String(320))
    body: Mapped[str] = mapped_column(Text)


class ContactRequest(IdMixin, CreatedMixin, Base):
    """Inbound sales / security-review / private-deployment requests from the public website."""

    __tablename__ = "contact_requests"

    kind: Mapped[str] = mapped_column(String(32))  # sales | demo | security_review | private_deployment
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320))
    company: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="new")
    source_ip_hash: Mapped[str | None] = mapped_column(String(64))
