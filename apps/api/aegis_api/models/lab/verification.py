"""Claims, claim evidence, verification runs, the discovery registry and research reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import (
    Base,
    CreatedMixin,
    IdMixin,
    OptimisticLockMixin,
    OrgMixin,
    TimestampMixin,
    lab_args,
    lab_fk,
)


class ScientificClaim(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "claims"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "fingerprint", name="uq_lab_claims_org_fingerprint"),
        Index("ix_lab_claims_mission_status", "mission_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("experiments", "SET NULL"), nullable=True, index=True
    )
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("hypotheses", "SET NULL"), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(24))
    metric: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(24), default="deterministic")  # deterministic | model_extracted | human
    status: Mapped[str] = mapped_column(String(24), default="unverified")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    fingerprint: Mapped[str] = mapped_column(String(64))
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ClaimEvidence(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "claim_evidence"
    __table_args__ = lab_args(
        UniqueConstraint("claim_id", "evidence_id", "relation", name="uq_lab_claim_evidence_triple")
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(lab_fk("claims"), index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(16), default="supports")  # supports | contradicts | context
    note: Mapped[str | None] = mapped_column(Text)


class Verification(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "verifications"
    __table_args__ = lab_args(Index("ix_lab_verifications_claim", "claim_id", "created_at"))

    claim_id: Mapped[uuid.UUID] = mapped_column(lab_fk("claims"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | completed | failed
    decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resulting_status: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)
    reproductions_passed: Mapped[int] = mapped_column(Integer, default=0)
    reproductions_failed: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str | None] = mapped_column(String(160))
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class VerificationRun(IdMixin, CreatedMixin, OrgMixin, Base):
    """One verification check (evidence validation, reproduction, independent evaluation, ...)."""

    __tablename__ = "verification_runs"
    __table_args__ = lab_args(UniqueConstraint("verification_id", "check_name", name="uq_lab_verification_runs_check"))

    verification_id: Mapped[uuid.UUID] = mapped_column(lab_fk("verifications"), index=True)
    check_name: Mapped[str] = mapped_column(String(48))
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contradicts: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(Text)
    verifier: Mapped[str] = mapped_column(String(160))
    independent: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Discovery(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "discoveries"
    __table_args__ = lab_args(
        UniqueConstraint("claim_id", name="uq_lab_discoveries_claim"),
        Index("ix_lab_discoveries_org_status", "organization_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(lab_fk("claims"))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    experiment_ids: Mapped[list[str]] = mapped_column(default=list)
    reproduction_ids: Mapped[list[str]] = mapped_column(default=list)
    verifier_ids: Mapped[list[str]] = mapped_column(default=list)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    limitations: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(24), default="candidate")
    requested_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DiscoveryVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "discovery_versions"
    __table_args__ = lab_args(UniqueConstraint("discovery_id", "version", name="uq_lab_discovery_versions_version"))

    discovery_id: Mapped[uuid.UUID] = mapped_column(lab_fk("discoveries"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(160))


class ResearchReport(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "reports"
    __table_args__ = lab_args(Index("ix_lab_reports_mission", "mission_id", "created_at"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="ready")
    sections: Mapped[dict[str, Any]] = mapped_column(default=dict)
    markdown_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    cited_evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    citation_check: Mapped[dict[str, Any]] = mapped_column(default=dict)
    narrative_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    generated_by: Mapped[str | None] = mapped_column(String(160))
