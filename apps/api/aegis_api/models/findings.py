"""Findings, evidence, remediation, regression, red team and risk snapshots."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import FindingStatus, RemediationStatus, RunStatus
from aegis_api.models.systems import AISystem


class Finding(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_findings_org_number"),
        Index("ix_findings_org_status_severity", "organization_id", "status", "severity"),
        Index("ix_findings_org_category", "organization_id", "category"),
        Index("ix_findings_fingerprint", "organization_id", "fingerprint"),
    )

    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32))
    dimension: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24), default=FindingStatus.OPEN, index=True)
    description: Mapped[str] = mapped_column(Text)
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    system_version: Mapped[str | None] = mapped_column(String(40))
    model_version: Mapped[str | None] = mapped_column(String(160))
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    control_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("controls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    control_ref: Mapped[str | None] = mapped_column(String(32))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    test_type: Mapped[str | None] = mapped_column(String(32))
    evaluator_key: Mapped[str | None] = mapped_column(String(80))
    evaluator_version: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    impact: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(16))
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_reasons: Mapped[list[str]] = mapped_column(default=list)
    risk_factors: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    sample_size: Mapped[int] = mapped_column(Integer, default=1)
    fingerprint: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence_unavailable_reason: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    system: Mapped[AISystem] = relationship(lazy="joined")


class FindingEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "finding_events"

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(320))
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Evidence(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable audit evidence. Content/hash cannot be modified (enforced by a database trigger)."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_org_kind", "organization_id", "kind"),
        Index("ix_evidence_audit_seq", "audit_id", "seq"),
        Index(
            "uq_evidence_chain_scope_seq",
            "chain_scope",
            "seq",
            unique=True,
            postgresql_where=text("chain_scope IS NOT NULL"),
        ),
    )

    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=True, index=True
    )
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # Hash-chain scope for evidence not produced by an audit (e.g. "mission:<uuid>" for lab evidence).
    chain_scope: Mapped[str | None] = mapped_column(String(96), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted/masked representation
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive_ciphertext: Mapped[str | None] = mapped_column(Text)  # encrypted raw values, reveal w/ permission
    content_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))
    confidence_level: Mapped[str] = mapped_column(String(16))
    confidence_reasons: Mapped[list[str]] = mapped_column(default=list)
    source_uri: Mapped[str | None] = mapped_column(String(1000))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(nullable=True)


class EvidenceLink(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        Index("ix_evidence_links_target", "target_type", "target_id"),
        UniqueConstraint("evidence_id", "target_type", "target_id", name="uq_evidence_links_triple"),
    )

    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID] = mapped_column()
    relation: Mapped[str] = mapped_column(String(32), default="supports")


class Remediation(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "remediations"

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=RemediationStatus.PROPOSED)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | suggested
    suggested_by: Mapped[str | None] = mapped_column(String(160))
    # Structured configuration change applied to the AI system when the remediation is applied.
    change: Mapped[dict[str, Any]] = mapped_column(default=dict)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RegressionTest(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "regression_tests"

    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), index=True)
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("findings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    remediation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("remediations.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32))
    test_type: Mapped[str] = mapped_column(String(32))
    control_ref: Mapped[str | None] = mapped_column(String(32))
    # Fixed test specification (inputs + thresholds) so every re-run measures the same thing.
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RegressionRun(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "regression_runs"

    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), index=True)
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED)
    verdict: Mapped[str | None] = mapped_column(String(16))
    system_version: Mapped[str | None] = mapped_column(String(40))
    results: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class RedTeamRun(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "redteam_runs"

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class RedTeamProbe(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "redteam_probes"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("redteam_runs.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("redteam_probes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    root_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    probe_key: Mapped[str] = mapped_column(String(80))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(40))
    technique: Mapped[str] = mapped_column(String(80))
    payload: Mapped[str] = mapped_column(Text)
    expected_behavior: Mapped[str] = mapped_column(Text)
    observed_behavior: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    detection: Mapped[dict[str, Any]] = mapped_column(default=dict)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"), nullable=True)


class RiskSnapshot(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "risk_snapshots"
    __table_args__ = (Index("ix_risk_snapshots_org_captured", "organization_id", "captured_at"),)

    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True, index=True
    )
    audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)
    captured_at: Mapped[datetime]
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    open_findings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    test_volume: Mapped[int] = mapped_column(Integer, default=0)
    posture: Mapped[str | None] = mapped_column(String(16))
