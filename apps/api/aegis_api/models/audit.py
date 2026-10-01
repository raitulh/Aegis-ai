"""Audits, execution runs, events, test suites/cases/results, evaluator records, model calls, claims, reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import AuditStatus, Intensity, RunStatus
from aegis_api.models.systems import AISystem


class EvaluatorRecord(IdMixin, CreatedMixin, Base):
    """Global registry of evaluator versions. Old versions are retained so historic results stay explainable."""

    __tablename__ = "evaluators"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_evaluators_key_version"),)

    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(24))
    category: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24))  # deterministic | statistical | retrieval | model | rule
    prompt_version: Mapped[str | None] = mapped_column(String(24))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    methodology: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)


class Audit(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "audits"
    __table_args__ = (
        Index("ix_audits_org_status", "organization_id", "status"),
        Index("ix_audits_org_created", "organization_id", "created_at"),
        Index("ix_audits_status_heartbeat", "status", "heartbeat_at"),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    system_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("system_versions.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24), default="audit")  # audit | regression
    status: Mapped[str] = mapped_column(String(24), default=AuditStatus.QUEUED)
    intensity: Mapped[str] = mapped_column(String(16), default=Intensity.STANDARD)
    categories: Mapped[list[str]] = mapped_column(default=list)
    policy_version_ids: Mapped[list[str]] = mapped_column(default=list)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str | None] = mapped_column(String(32))
    test_count: Mapped[int] = mapped_column(Integer, default=0)
    tests_completed: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    missing_categories: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)  # reproducibility manifest
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cost: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_head_hash: Mapped[str | None] = mapped_column(String(64))
    # Execution lease: exactly one worker may hold a running audit; a stale heartbeat means it was lost.
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    system: Mapped[AISystem] = relationship(lazy="joined")


class AuditRun(IdMixin, CreatedMixin, OrgMixin, Base):
    """One execution attempt of an audit by a worker."""

    __tablename__ = "audit_runs"

    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    worker: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """Execution event stream (served over SSE and shown as the audit timeline)."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("audit_id", "seq", name="uq_audit_events_audit_seq"),)

    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(48))
    stage: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | success | warning | error
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class TestSuite(IdMixin, CreatedMixin, OrgMixin, Base):
    __test__ = False
    __tablename__ = "test_suites"

    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24), default="generated")  # generated | regression | custom
    version: Mapped[str] = mapped_column(String(24), default="1")
    generator: Mapped[str | None] = mapped_column(String(80))
    generator_version: Mapped[str | None] = mapped_column(String(24))
    seed: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)


class TestCase(IdMixin, CreatedMixin, OrgMixin, Base):
    __test__ = False
    __tablename__ = "test_cases"
    __table_args__ = (Index("ix_test_cases_suite_category", "suite_id", "category"),)

    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), index=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=True, index=True
    )
    external_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32))
    test_type: Mapped[str] = mapped_column(String(32))
    control_ref: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(300))
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    expected_behavior: Mapped[str] = mapped_column(Text)
    repetitions: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(24), default="generated")
    generator: Mapped[str | None] = mapped_column(String(80))
    generator_version: Mapped[str | None] = mapped_column(String(24))
    seed: Mapped[int | None] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)


class TestResult(IdMixin, CreatedMixin, OrgMixin, Base):
    __test__ = False
    __tablename__ = "test_results"
    __table_args__ = (
        Index("ix_test_results_audit_category", "audit_id", "category"),
        Index("ix_test_results_audit_status", "audit_id", "status"),
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    test_type: Mapped[str] = mapped_column(String(32))
    control_ref: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)
    observed: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evaluator_key: Mapped[str] = mapped_column(String(80))
    evaluator_version: Mapped[str] = mapped_column(String(24))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("findings.id", ondelete="SET NULL", use_alter=True), nullable=True, index=True
    )


class Evaluation(IdMixin, CreatedMixin, OrgMixin, Base):
    """Record of a model-assisted judgment (never the sole basis of a result)."""

    __tablename__ = "evaluations"

    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=True, index=True
    )
    test_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"), nullable=True, index=True
    )
    evaluator_key: Mapped[str] = mapped_column(String(80))
    evaluator_version: Mapped[str] = mapped_column(String(24))
    evaluator_model: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)
    raw_result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    normalized_result: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ModelCall(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "model_calls"

    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(32))  # inference | judge | extraction | embedding
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(160))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Claim(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "claims"

    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=True, index=True
    )
    test_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    support_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    contradiction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    source_title: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(160))
    evaluator_key: Mapped[str] = mapped_column(String(80))
    evaluator_version: Mapped[str] = mapped_column(String(24))
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="SET NULL", use_alter=True), nullable=True
    )


class Report(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "reports"

    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="ready")
    content: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    generated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
