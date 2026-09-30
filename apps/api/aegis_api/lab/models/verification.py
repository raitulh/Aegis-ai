"""Claims, evidence links, verification, reproduction, discoveries, scientific reviews, mission reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, ProjectScoped, project_scope_fk, user_fk
from engines.lab.states import ClaimStatus, DiscoveryStatus, RunState


class ScientificClaim(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A scientific claim with explicit uncertainty. Promotion depends on configured evidence criteria."""

    __tablename__ = "scientific_claims"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_scientific_claims_mission_status", "mission_id", "status"),
        Index("ix_scientific_claims_org_status", "organization_id", "status"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(
        String(16), default="comparative"
    )  # quantitative|comparative|causal|qualitative
    domain: Mapped[str] = mapped_column(String(64), default="general")
    metric: Mapped[str | None] = mapped_column(String(120))
    direction: Mapped[str | None] = mapped_column(String(8))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    units: Mapped[str | None] = mapped_column(String(48))
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source_type: Mapped[str] = mapped_column(String(32))  # experiment_comparison|agent_run|report|human
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    extracted_by: Mapped[str] = mapped_column(String(16), default="rule")  # rule|agent|human
    extractor_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=ClaimStatus.UNVERIFIED)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # evidence_quality, conflicting_evidence, missing_evidence, failed_reproductions, notes
    uncertainty: Mapped[dict[str, Any]] = mapped_column(default=dict)
    criteria_profile: Mapped[str] = mapped_column(String(48), default="default")
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ClaimEvidence(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only link between a claim and a piece of evidence (anchored in the hash-chained
    ``evidence`` table via ``evidence_id``)."""

    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_type", "ref_id", "relation", name="uq_claim_evidence_link"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scientific_claims.id", ondelete="CASCADE"), index=True)
    # experiment_run|experiment_comparison|artifact_version|evaluation_run|dataset_version|research_source|
    # memory|reproduction|verification|code_snapshot|agent_run
    evidence_type: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    relation: Mapped[str] = mapped_column(String(16), default="supports")  # supports|contradicts|context
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    note: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))


class Verification(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    __tablename__ = "verifications"
    __table_args__ = (project_scope_fk(), Index("ix_verifications_claim", "claim_id"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scientific_claims.id", ondelete="CASCADE"))
    discovery_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    criteria_profile: Mapped[str] = mapped_column(String(48))
    criteria: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    verdict: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    checks: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Agent versions that generated the claim — the verifier must be independent of these.
    generating_agent_version_ids: Mapped[list[str]] = mapped_column(default=list)
    requested_by_id: Mapped[uuid.UUID | None] = user_fk()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class VerificationRun(IdMixin, TimestampMixin, OrgMixin, Base):
    """One verification check (evidence validation, provenance, reproduction, independent evaluation…)."""

    __tablename__ = "verification_runs"
    __table_args__ = (UniqueConstraint("verification_id", "check_type", name="uq_verification_runs_check"),)

    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("verifications.id", ondelete="CASCADE"), index=True)
    check_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evaluator_key: Mapped[str | None] = mapped_column(String(80))
    evaluator_version: Mapped[str | None] = mapped_column(String(24))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reproduction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class Reproduction(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """Independent re-execution of an experiment version and the comparison against the original."""

    __tablename__ = "reproductions"
    __table_args__ = (project_scope_fk(), Index("ix_reproductions_experiment", "experiment_id"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    experiment_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    original_run_ids: Mapped[list[str]] = mapped_column(default=list)
    reproduction_run_ids: Mapped[list[str]] = mapped_column(default=list)
    tolerance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    verdict: Mapped[str | None] = mapped_column(
        String(24)
    )  # reproduced|not_reproduced|partially_reproduced|inconclusive
    metric_deltas: Mapped[dict[str, Any]] = mapped_column(default=dict)
    environment_diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = user_fk()


class Discovery(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """Discovery registry entry. APPROVED/PUBLISHED require configured policy + human approval."""

    __tablename__ = "discoveries"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_discoveries_org_status", "organization_id", "status"),
        Index("ix_discoveries_mission", "mission_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scientific_claims.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    experiment_ids: Mapped[list[str]] = mapped_column(default=list)
    reproduction_ids: Mapped[list[str]] = mapped_column(default=list)
    verifier_ids: Mapped[list[str]] = mapped_column(default=list)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default=DiscoveryStatus.CANDIDATE)
    version: Mapped[int] = mapped_column(Integer, default=1)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = user_fk()
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class DiscoveryVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable snapshot written on every discovery status change."""

    __tablename__ = "discovery_versions"
    __table_args__ = (UniqueConstraint("discovery_id", "version", name="uq_discovery_versions_discovery_version"),)

    discovery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discoveries.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class ScientificReview(IdMixin, CreatedMixin, OrgMixin, OptionalProjectScoped, Base):
    __tablename__ = "scientific_reviews"
    __table_args__ = (project_scope_fk(), Index("ix_scientific_reviews_subject", "subject_type", "subject_id"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(24))  # mission|experiment|claim|discovery|report
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reviewer_type: Mapped[str] = mapped_column(String(16))  # rule|agent|human
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = user_fk()
    checks: Mapped[dict[str, Any]] = mapped_column(default=dict)
    findings: Mapped[list[Any]] = mapped_column(default=list)
    verdict: Mapped[str] = mapped_column(String(16))  # pass|concerns|fail
    score: Mapped[float | None] = mapped_column(Float, nullable=True)


class MissionReport(IdMixin, CreatedMixin, OrgMixin, ProjectScoped, Base):
    __tablename__ = "mission_reports"
    __table_args__ = (
        project_scope_fk(),
        UniqueConstraint("mission_id", "version", name="uq_mission_reports_mission_version"),
    )

    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="final")
    content: Mapped[dict[str, Any]] = mapped_column(default=dict)  # structured sections
    markdown_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    generated_by: Mapped[str] = mapped_column(String(16), default="engine")  # engine|agent
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
