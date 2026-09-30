"""Agents, immutable agent versions, persisted agent runs, steps, inter-agent messages, prompt registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import AgentRunStatus, AutonomyLevel


class Agent(IdMixin, TimestampMixin, OrgMixin, Base):
    """A configured AI scientist agent of a specific role. Configuration lives in ``agent_versions``."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_agents_org_name"),)

    role: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class AgentVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable agent configuration. Changing an agent always creates a new version."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # {"task_type": "...", "tier": "fast|default|reasoning", "provider": null, "model": null,
    #  "temperature": 0.2, "max_output_tokens": 4096}
    model_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prompt_key: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = latest active
    strategy_kind: Mapped[str | None] = mapped_column(String(32))
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tools: Mapped[list[str]] = mapped_column(default=list)
    memory_scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Upper bound of what runs of this agent may do; intersected with the triggering actor's permissions.
    permissions: Mapped[list[str]] = mapped_column(default=list)
    # max_cost_usd, max_tokens, max_tool_calls, max_steps
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=30)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    max_autonomy_level: Mapped[str] = mapped_column(String(40), default=AutonomyLevel.L3_AUTOMATED_EXECUTION)
    evaluation_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class AgentRun(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    """A persisted execution of an agent. State lives here — never only in process memory."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_agent_runs_org_status", "organization_id", "status"),
        Index("ix_agent_runs_mission_created", "mission_id", "created_at"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id", ondelete="RESTRICT"))
    role: Mapped[str] = mapped_column(String(48))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=AgentRunStatus.CREATED)
    status_reason: Mapped[str | None] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    # Reproducibility / model version tracking
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    prompt_key: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(160))
    model_config_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tools_used: Mapped[list[str]] = mapped_column(default=list)
    # Effective permission ceiling of this run (never more than agent version ∩ triggering actor).
    granted_permissions: Mapped[list[str]] = mapped_column(default=list)
    autonomy_level: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = money_column()
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    triggered_by: Mapped[str] = mapped_column(String(16), default="user")  # user|workflow|agent|system
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AgentStep(IdMixin, CreatedMixin, OrgMixin, Base):
    """Every important agent action (LLM call, tool call, message, decision, state change)."""

    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("agent_run_id", "seq", name="uq_agent_steps_run_seq"),)

    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_usage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tool_invocation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class AgentMessage(IdMixin, CreatedMixin, OrgMixin, Base):
    """Signed inter-agent message envelope. Sender identity is stamped by the runtime, never the payload."""

    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_receiver", "receiver_run_id", "status"),)

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sender_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    sender_role: Mapped[str] = mapped_column(String(48))
    receiver_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    receiver_role: Mapped[str | None] = mapped_column(String(48))
    message_type: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    auth_context: Mapped[dict[str, Any]] = mapped_column(default=dict)
    signature: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="delivered")  # delivered|consumed|rejected
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class PromptTemplate(IdMixin, TimestampMixin, Base):
    """Versioned prompt templates. ``organization_id`` NULL = system template shipped with the platform.

    Template text is immutable once created (trigger); only ``status`` may change. Agent runs store the
    template id, version and the hash of the rendered prompt.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key",
            "version",
            name="uq_prompt_templates_key_version",
            postgresql_nulls_not_distinct=True,
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)
    task_type: Mapped[str] = mapped_column(String(48))
    description: Mapped[str | None] = mapped_column(Text)
    system_template: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[Any]] = mapped_column(default=list)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # draft|active|deprecated
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
