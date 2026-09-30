"""Human approvals, versioned lab policies, MCP registry, tool calls, model catalogue and usage metering."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class Approval(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = lab_args(
        Index("ix_lab_approvals_org_status", "organization_id", "status"),
        Index("ix_lab_approvals_resource", "resource_type", "resource_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    requester_type: Mapped[str] = mapped_column(String(24))
    requester_id: Mapped[str | None] = mapped_column(String(64))
    requester_label: Mapped[str | None] = mapped_column(String(320))
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    required_permission: Mapped[str] = mapped_column(String(64), default="approval:decide")
    policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    signal_name: Mapped[str | None] = mapped_column(String(160))


class LabPolicy(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "policies"
    __table_args__ = lab_args(UniqueConstraint("organization_id", "key", name="uq_lab_policies_org_key"))

    key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    version_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class LabPolicyVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable policy document version (append-only; enforced by trigger)."""

    __tablename__ = "policy_versions"
    __table_args__ = lab_args(UniqueConstraint("policy_id", "version", name="uq_lab_policy_versions_policy_version"))

    policy_id: Mapped[uuid.UUID] = mapped_column(lab_fk("policies"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict[str, Any]] = mapped_column(default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class MCPServer(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = lab_args(UniqueConstraint("organization_id", "name", name="uq_lab_mcp_servers_org_name"))

    name: Mapped[str] = mapped_column(String(120))
    endpoint: Mapped[str] = mapped_column(String(1000))
    transport: Mapped[str] = mapped_column(String(24), default="streamable_http")
    auth_method: Mapped[str] = mapped_column(String(16), default="none")  # none | bearer | header
    auth_header: Mapped[str | None] = mapped_column(String(80))
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    allowed_tools: Mapped[list[str]] = mapped_column(default=list)
    project_ids: Mapped[list[str]] = mapped_column(default=list)  # tenant scope inside the org (empty = org-wide)
    risk_level: Mapped[str] = mapped_column(String(16), default="high")
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    server_info: Mapped[dict[str, Any]] = mapped_column(default=dict)
    protocol_version: Mapped[str | None] = mapped_column(String(24))
    last_health_check_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_health_status: Mapped[str | None] = mapped_column(String(160))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class MCPTool(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "mcp_tools"
    __table_args__ = lab_args(UniqueConstraint("server_id", "name", name="uq_lab_mcp_tools_server_name"))

    server_id: Mapped[uuid.UUID] = mapped_column(lab_fk("mcp_servers"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    schema_sha256: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16), default="high")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)


class LabToolCall(IdMixin, CreatedMixin, OrgMixin, Base):
    """Audit + usage record of every brokered tool invocation (the ToolUsage ledger)."""

    __tablename__ = "tool_calls"
    __table_args__ = lab_args(
        Index("ix_lab_tool_calls_mission_created", "mission_id", "created_at"),
        Index("ix_lab_tool_calls_tool", "organization_id", "tool_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True, index=True)
    tool_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24))  # denied | pending_approval | succeeded | failed | rate_limited
    risk_level: Mapped[str] = mapped_column(String(16))
    policy_decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted
    result_summary: Mapped[str | None] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    result_bytes: Mapped[int | None] = mapped_column(Integer)
    injection_score: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(160))


class ModelConfig(IdMixin, TimestampMixin, Base):
    """Per-organization overrides of the model catalogue (global defaults come from configuration)."""

    __tablename__ = "model_configs"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "provider", "model", name="uq_lab_model_configs_org_model")
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    tier: Mapped[str] = mapped_column(String(16))
    features: Mapped[list[str]] = mapped_column(default=list)
    input_per_mtok: Mapped[float | None] = mapped_column(Float)
    output_per_mtok: Mapped[float | None] = mapped_column(Float)
    typical_latency_ms: Mapped[int | None] = mapped_column(Integer)
    is_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "model_usage"
    __table_args__ = lab_args(
        Index("ix_lab_model_usage_org_created", "organization_id", "created_at"),
        Index("ix_lab_model_usage_mission", "mission_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions", "SET NULL"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True, index=True)
    research_task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    task_type: Mapped[str] = mapped_column(String(40))
    request_id: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thought_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_basis: Mapped[str | None] = mapped_column(String(160))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    routing_reason: Mapped[str | None] = mapped_column(Text)


class ComputeUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "compute_usage"
    __table_args__ = lab_args(Index("ix_lab_compute_usage_org_created", "organization_id", "created_at"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions", "SET NULL"), nullable=True, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("experiments", "SET NULL"), nullable=True, index=True
    )
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("experiment_runs", "SET NULL"), nullable=True)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(lab_fk("compute_jobs"), index=True)
    backend: Mapped[str] = mapped_column(String(24))
    cpu: Mapped[float] = mapped_column(Float)
    memory_mb: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str | None] = mapped_column(String(40))
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    runtime_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_seconds: Mapped[float | None] = mapped_column(Float)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_basis: Mapped[str | None] = mapped_column(String(160))


class StorageUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "storage_usage"
    __table_args__ = lab_args(Index("ix_lab_storage_usage_org_created", "organization_id", "created_at"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions", "SET NULL"), nullable=True)
    object_kind: Mapped[str] = mapped_column(String(24))  # artifact | document | dataset
    object_id: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(8))  # put | delete
    bytes: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[float | None] = mapped_column(Float)
