"""Missions, agents, agent runs, inter-agent messages, prompt registry, events and durable workflow state."""

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


class Mission(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "missions"
    __table_args__ = lab_args(
        Index("ix_lab_missions_org_status", "organization_id", "status"),
        Index("ix_lab_missions_project_created", "project_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(48), default="general")
    constraints: Mapped[list[str]] = mapped_column(default=list)
    success_criteria: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    compute_budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    time_budget_seconds: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    allowed_tools: Mapped[list[str]] = mapped_column(default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    autonomy_level: Mapped[str] = mapped_column(String(40), default="L1_RESEARCH_AUTOMATION")
    approval_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    phase: Mapped[str | None] = mapped_column(String(32))
    status_reason: Mapped[str | None] = mapped_column(Text)
    current_cycle: Mapped[int] = mapped_column(Integer, default=0)
    max_cycles: Mapped[int] = mapped_column(Integer, default=1)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    brief: Mapped[dict[str, Any]] = mapped_column(default=dict)
    plan: Mapped[dict[str, Any]] = mapped_column(default=dict)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_head_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_seq: Mapped[int] = mapped_column(Integer, default=0)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MissionVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable snapshot of a mission definition (appended on every definition change)."""

    __tablename__ = "mission_versions"
    __table_args__ = lab_args(UniqueConstraint("mission_id", "version", name="uq_lab_mission_versions_mission_version"))

    mission_id: Mapped[uuid.UUID] = mapped_column(lab_fk("missions"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Agent(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "agents"
    __table_args__ = lab_args(UniqueConstraint("organization_id", "name", name="uq_lab_agents_org_name"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AgentVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable agent configuration: model routing, prompt version, strategy version, tools, memory scope,
    permissions (subset), budget, rate limit, timeout, autonomy ceiling and evaluation policy."""

    __tablename__ = "agent_versions"
    __table_args__ = lab_args(UniqueConstraint("agent_id", "version", name="uq_lab_agent_versions_agent_version"))

    agent_id: Mapped[uuid.UUID] = mapped_column(lab_fk("agents"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config_sha256: Mapped[str] = mapped_column(String(64))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AgentRun(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = lab_args(
        Index("ix_lab_agent_runs_mission_created", "mission_id", "created_at"),
        Index("ix_lab_agent_runs_org_status", "organization_id", "status"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agents", "SET NULL"), nullable=True, index=True)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_versions", "SET NULL"), nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="created")
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    output_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    prompt_ref: Mapped[str | None] = mapped_column(String(120))
    prompt_template_sha256: Mapped[str | None] = mapped_column(String(64))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(160))
    model_revision: Mapped[str | None] = mapped_column(String(160))
    model_params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    routing: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tools_used: Mapped[list[str]] = mapped_column(default=list)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    injection_score: Mapped[float] = mapped_column(Float, default=0.0)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AgentMessageRecord(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "agent_messages"
    __table_args__ = lab_args(UniqueConstraint("message_id", name="uq_lab_agent_messages_message_id"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    message_id: Mapped[str] = mapped_column(String(64))
    sender_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    receiver_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    message_type: Mapped[str] = mapped_column(String(24))
    envelope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class PromptTemplateRecord(IdMixin, CreatedMixin, Base):
    """Versioned prompt templates. Built-in templates have organization_id NULL (global, read-only)."""

    __tablename__ = "prompt_templates"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "name", "version", name="uq_lab_prompt_templates_org_name_version")
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(24))
    task_type: Mapped[str] = mapped_column(String(40))
    role: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")
    sha256: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class LabEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only mission event log (served over SSE; ``seq`` is the SSE event id)."""

    __tablename__ = "events"
    __table_args__ = lab_args(
        UniqueConstraint("mission_id", "seq", name="uq_lab_events_mission_seq"),
        Index("ix_lab_events_org_created", "organization_id", "created_at"),
        Index("ix_lab_events_type", "event_type"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(12), default="info")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actor: Mapped[str | None] = mapped_column(String(160))
    trace_id: Mapped[str | None] = mapped_column(String(64))


class WorkflowRun(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    """Durable workflow instance (Temporal-backed, or the built-in inline engine's checkpoint record)."""

    __tablename__ = "workflow_runs"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "workflow", "business_key", name="uq_lab_workflow_runs_business_key"),
        Index("ix_lab_workflow_runs_status_wake", "status", "wake_at"),
        Index("ix_lab_workflow_runs_status_lease", "status", "lease_until"),
    )

    workflow: Mapped[str] = mapped_column(String(64))
    business_key: Mapped[str] = mapped_column(String(160))
    engine: Mapped[str] = mapped_column(String(16))  # temporal | inline
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|running|waiting|completed|failed|cancelled
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    waiting_on: Mapped[str | None] = mapped_column(String(160))
    wake_at: Mapped[datetime | None] = mapped_column(nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("workflow_runs", "SET NULL"), nullable=True)
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255))
    temporal_run_id: Mapped[str | None] = mapped_column(String(255))
    principal: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WorkflowStep(IdMixin, CreatedMixin, OrgMixin, Base):
    """Memoized activity results: replaying a workflow returns these instead of re-executing side effects."""

    __tablename__ = "workflow_steps"
    __table_args__ = lab_args(UniqueConstraint("run_id", "step_key", name="uq_lab_workflow_steps_run_key"))

    run_id: Mapped[uuid.UUID] = mapped_column(lab_fk("workflow_runs"), index=True)
    step_key: Mapped[str] = mapped_column(String(255))
    activity: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))  # completed | failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class WorkflowSignal(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "workflow_signals"
    __table_args__ = lab_args(Index("ix_lab_workflow_signals_run_name", "run_id", "name"))

    run_id: Mapped[uuid.UUID] = mapped_column(lab_fk("workflow_runs"))
    name: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
