"""Policies, versions, documents, requirements, controls and framework mappings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import (
    ControlAutomation,
    PolicyStatus,
    PolicyVersionStatus,
    Severity,
)
from aegis_api.models.systems import EMBEDDING_DIM


class Policy(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_policies_org_key"),)

    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(32))  # e.g. HR, CDP, SAFE
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=PolicyStatus.DRAFT)
    owner_name: Mapped[str | None] = mapped_column(String(160))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    versions: Mapped[list[PolicyVersion]] = relationship(
        back_populates="policy", foreign_keys="PolicyVersion.policy_id", order_by="PolicyVersion.created_at"
    )


class PolicyVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable once compiled. New text or DSL always creates a new version (history is never overwritten)."""

    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_versions_policy_version"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default=PolicyVersionStatus.DRAFT)
    source_type: Mapped[str] = mapped_column(String(16), default="text")  # text | upload | dsl
    source_text: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    dsl_yaml: Mapped[str | None] = mapped_column(Text)
    compiled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    compiler_version: Mapped[str | None] = mapped_column(String(32))
    compile_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    change_note: Mapped[str | None] = mapped_column(Text)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    policy: Mapped[Policy] = relationship(back_populates="versions", foreign_keys=[policy_id])


class PolicyDocument(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "policy_documents"

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), index=True)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    scan_status: Mapped[str] = mapped_column(String(24), default="not_scanned")
    extracted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DocumentChunk(IdMixin, CreatedMixin, OrgMixin, Base):
    """Normalised text chunks with page/section boundaries preserved for provenance."""

    __tablename__ = "document_chunks"

    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(64))
    heading: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120))


class PolicyRequirement(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "policy_requirements"

    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE"), index=True
    )
    requirement_key: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(String(16))  # must | must_not | should | may
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="SET NULL"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(64))
    source_excerpt: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)


class Control(IdMixin, TimestampMixin, OrgMixin, Base):
    """Executable control generated from a requirement (or defined manually as a custom rule)."""

    __tablename__ = "controls"
    __table_args__ = (
        UniqueConstraint("policy_version_id", "control_id", name="uq_controls_version_control"),
        Index("ix_controls_org_control_id", "organization_id", "control_id"),
    )

    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policies.id", ondelete="CASCADE"), index=True, nullable=True
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE"), index=True, nullable=True
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_requirements.id", ondelete="SET NULL"), nullable=True
    )
    control_id: Mapped[str] = mapped_column(String(32))  # e.g. FAIR-003
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(24))
    test_type: Mapped[str] = mapped_column(String(32))
    test_name: Mapped[str | None] = mapped_column(String(120))
    threshold: Mapped[dict[str, Any]] = mapped_column(default=dict)
    severity: Mapped[str] = mapped_column(String(16), default=Severity.MEDIUM)
    automation: Mapped[str] = mapped_column(String(16), default=ControlAutomation.AUTOMATED)
    required_evidence: Mapped[list[str]] = mapped_column(default=list)
    condition: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="compiled")  # compiled | manual | dsl
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Framework(IdMixin, TimestampMixin, Base):
    """Versioned external reference pack (global when organization_id is NULL) or a custom framework."""

    __tablename__ = "frameworks"
    __table_args__ = (UniqueConstraint("organization_id", "key", "version", name="uq_frameworks_key_version"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(32))
    version_date: Mapped[str | None] = mapped_column(String(32))
    publisher: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16), default="reference")  # reference | custom
    disclaimer: Mapped[str | None] = mapped_column(Text)

    controls: Mapped[list[FrameworkControl]] = relationship(
        back_populates="framework", order_by="FrameworkControl.sort_order"
    )


class FrameworkControl(IdMixin, CreatedMixin, Base):
    __tablename__ = "framework_controls"
    __table_args__ = (UniqueConstraint("framework_id", "ref", name="uq_framework_controls_ref"),)

    framework_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("frameworks.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    group: Mapped[str | None] = mapped_column(String(120))
    domains: Mapped[list[str]] = mapped_column(default=list)
    test_types: Mapped[list[str]] = mapped_column(default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    framework: Mapped[Framework] = relationship(back_populates="controls")


class ControlMapping(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "control_mappings"
    __table_args__ = (UniqueConstraint("control_id", "framework_control_id", name="uq_control_mappings_pair"),)

    control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    framework_control_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("framework_controls.id", ondelete="CASCADE"), index=True
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    mapping_type: Mapped[str] = mapped_column(String(16), default="reference")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)


class ControlAssessment(IdMixin, CreatedMixin, OrgMixin, Base):
    """Per-audit result for a control (drives policy coverage and compliance readiness views)."""

    __tablename__ = "control_assessments"
    __table_args__ = (UniqueConstraint("audit_id", "control_id", name="uq_control_assessments_audit_control"),)

    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    tests_run: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    observed: Mapped[dict[str, Any]] = mapped_column(default=dict)
