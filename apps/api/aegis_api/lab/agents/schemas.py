"""API contract for agents, immutable agent versions, agent runs, steps and inter-agent messages."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from aegis_api.schemas.common import ORMModel
from engines.lab.states import AgentRole, AutonomyLevel

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
MAX_INPUT_BYTES = 64 * 1024
_KEY = r"^[a-z][a-z0-9_.\-]{1,119}$"


class ModelPolicy(BaseModel):
    """How the agent's model calls are routed. Provider/model are optional pins (else the gateway routes)."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    task_type: str | None = Field(default=None, max_length=48)
    tier: Literal["fast", "default", "reasoning"] = "default"
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@\-]*$")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=64, le=1_000_000)


class AgentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=8, ge=1, le=50, description="Model calls per run")
    max_tool_calls: int = Field(default=20, ge=0, le=200)
    max_cost_usd: float = Field(default=2.0, ge=0, le=1000, description="Model spend per run (USD)")
    max_tokens: int | None = Field(default=None, ge=1000, le=100_000_000)


class AgentVersionCreate(BaseModel):
    """A new immutable configuration. Omitted fields default to the role's defaults."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_policy: ModelPolicy | None = None
    prompt_key: str | None = Field(default=None, pattern=_KEY)
    prompt_version: int | None = Field(default=None, ge=1, description="Pin a prompt template version (null = latest)")
    tools: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(default=None, max_length=50)
    permissions: list[Annotated[str, Field(min_length=3, max_length=64)]] | None = Field(default=None, max_length=200)
    budget: AgentBudget | None = None
    timeout_seconds: int | None = Field(default=None, ge=10, le=24 * 3600)
    max_autonomy_level: AutonomyLevel | None = None
    memory_scope: dict[str, Any] | None = None
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=10_000)
    evaluation_policy: dict[str, Any] | None = None
    strategy_kind: str | None = Field(default=None, max_length=32)

    @field_validator("tools", "permissions")
    @classmethod
    def _dedupe(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(value))


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    config: AgentVersionCreate = Field(default_factory=AgentVersionCreate)


class AgentVersionOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    agent_id: str
    version: int
    model_policy: dict[str, Any]
    prompt_key: str
    prompt_version: int | None = None
    strategy_kind: str | None = None
    tools: list[str]
    memory_scope: dict[str, Any]
    permissions: list[str]
    budget: dict[str, Any]
    rate_limit_per_min: int
    timeout_seconds: int
    max_autonomy_level: str
    evaluation_policy: dict[str, Any]
    config_hash: str
    created_by_id: str | None = None
    created_at: datetime


class AgentOut(ORMModel):
    id: str
    role: str
    name: str
    description: str | None = None
    status: str
    is_system: bool
    current_version_id: str | None = None
    current_version: AgentVersionOut | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    project_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = Field(default=None, description="Run a specific agent (default: the role's system agent)")
    parent_run_id: uuid.UUID | None = None
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_INPUT_BYTES:
            raise ValueError(f"input must serialize to at most {MAX_INPUT_BYTES} bytes")
        return value


class ModelProvenance(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    prompt_template_id: str | None = None
    prompt_key: str | None = None
    prompt_version: int | None = None
    prompt_hash: str | None = None
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    model_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    strategy_version_id: str | None = None
    agent_version_id: str | None = None


class AgentRunOut(BaseModel):
    id: str
    agent_id: str
    agent_version_id: str
    role: str
    project_id: str | None = None
    mission_id: str | None = None
    parent_run_id: str | None = None
    workflow_run_id: str | None = None
    status: str
    status_reason: str | None = None
    triggered_by: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    granted_permissions: list[str]
    autonomy_level: str | None = None
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: Money
    step_count: int
    tool_call_count: int
    deadline_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    trace_id: str | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime
    provenance: ModelProvenance


class AgentRunStartOut(AgentRunOut):
    dispatched: bool = Field(description="Whether the run was handed to the workflow engine")
    dispatch_detail: str


class AgentStepOut(ORMModel):
    id: str
    agent_run_id: str
    seq: int
    kind: str
    summary: str
    data: dict[str, Any]
    model_usage_id: str | None = None
    tool_invocation_id: str | None = None
    created_at: datetime


class AgentMessageOut(ORMModel):
    id: str
    mission_id: str | None = None
    sender_run_id: str
    sender_role: str
    receiver_run_id: str | None = None
    receiver_role: str | None = None
    message_type: str
    schema_version: str
    trace_id: str | None = None
    payload: dict[str, Any] = Field(description="Validated payload (content produced by an agent: untrusted)")
    auth_context: dict[str, Any]
    status: str
    rejection_reason: str | None = None
    created_at: datetime
