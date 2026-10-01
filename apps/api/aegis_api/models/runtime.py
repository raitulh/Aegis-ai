"""Runtime Agent Guard: normalized runtime events, runtime policies (versioned), assignments and approvals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin, utcnow


class RuntimeEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """One normalized runtime event (schema ``aegis.runtime.v1``) and the guard's decision about it.

    ``event_id`` is supplied by the client and unique per workspace, so retried ingestion is idempotent.
    Payloads are stored redacted; raw sensitive values are never persisted."""

    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "event_id", name="uq_runtime_events_org_event"),
        Index("ix_runtime_events_org_occurred", "organization_id", "occurred_at"),
        Index("ix_runtime_events_system_occurred", "system_id", "occurred_at"),
        Index("ix_runtime_events_org_type", "organization_id", "event_type"),
        Index("ix_runtime_events_trace", "organization_id", "trace_id"),
        Index("ix_runtime_events_org_decision", "organization_id", "decision"),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(24), default="aegis.runtime.v1")
    event_type: Mapped[str] = mapped_column(String(48))
    source: Mapped[str] = mapped_column(String(24), default="sdk")  # sdk | mcp | otel | langgraph | crewai | http
    environment: Mapped[str | None] = mapped_column(String(24))
    agent_name: Mapped[str | None] = mapped_column(String(160))
    actor: Mapped[str | None] = mapped_column(String(160))
    session_id: Mapped[str | None] = mapped_column(String(120))
    trace_id: Mapped[str | None] = mapped_column(String(80))
    span_id: Mapped[str | None] = mapped_column(String(64))
    parent_span_id: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    signals: Mapped[dict[str, Any]] = mapped_column(default=dict)  # derived: destination, pii types, ...
    mode: Mapped[str] = mapped_column(String(12), default="observe")  # observe | audit | enforce
    decision: Mapped[str] = mapped_column(String(20), default="allow")  # allow | flag | block | require_approval
    effective_decision: Mapped[str] = mapped_column(String(20), default="allow")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    policy_matches: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)


class RuntimeApproval(IdMixin, TimestampMixin, OrgMixin, Base):
    """A human approval requested by an ``enforce``-mode ``require_approval`` decision."""

    __tablename__ = "runtime_approvals"
    __table_args__ = (Index("ix_runtime_approvals_org_status", "organization_id", "status"),)

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    runtime_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runtime_events.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | denied | expired
    summary: Mapped[str] = mapped_column(String(500))
    rule_ref: Mapped[str | None] = mapped_column(String(200))
    request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expires_at: Mapped[datetime]
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by_label: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text)


class RuntimePolicy(IdMixin, TimestampMixin, OrgMixin, Base):
    """A runtime guard policy (YAML rules). Edits create versions; exactly one version is published."""

    __tablename__ = "runtime_policies"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_runtime_policies_org_key"),)

    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | published | disabled
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    template_key: Mapped[str | None] = mapped_column(String(80))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class RuntimePolicyVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable policy source + compiled rules. Publishing switches the pointer; rollback re-publishes."""

    __tablename__ = "runtime_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_runtime_policy_versions_policy_version"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runtime_policies.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source_yaml: Mapped[str] = mapped_column(Text)
    compiled: Mapped[dict[str, Any]] = mapped_column(default=dict)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | published | superseded
    change_note: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimePolicyAssignment(IdMixin, CreatedMixin, OrgMixin, Base):
    """Where a runtime policy applies: the whole workspace, one environment, or one system."""

    __tablename__ = "runtime_policy_assignments"
    __table_args__ = (
        UniqueConstraint("policy_id", "scope_type", "scope_key", name="uq_runtime_policy_assignments_scope"),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runtime_policies.id", ondelete="CASCADE"), index=True)
    scope_type: Mapped[str] = mapped_column(String(16))  # organization | environment | system
    # "*" for organization, the environment name, or the system id.
    scope_key: Mapped[str] = mapped_column(String(64))
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
