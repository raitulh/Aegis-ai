"""API contract for the tool broker and the MCP server/tool registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from aegis_api.schemas.common import ORMModel

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
RiskName = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ToolStatus = Literal["succeeded", "denied", "approval_required", "failed", "timeout"]
MCP_SERVER_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
MCP_TOOL_REF_PATTERN = r"^[A-Za-z0-9_.\-/*]{1,200}$"


# ---------------------------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------------------------
class ToolOut(BaseModel):
    name: str = Field(description="Name used by agents and the invoke endpoint (MCP tools: mcp.<server>.<tool>)")
    source: Literal["builtin", "mcp"]
    description: str
    category: str
    risk_level: str
    permissions: list[str] = Field(description="Permissions the calling actor must hold")
    input_schema: dict[str, Any]
    output_kind: str
    feature_flag: str | None = None
    network_hosts: list[str] = Field(default_factory=list)
    rate_limit_per_min: int
    timeout_seconds: float
    available: bool = Field(description="Whether the caller could invoke it now (permissions and feature flags)")
    mcp_server_id: str | None = None
    mcp_tool_id: str | None = None


class ToolInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultOut(BaseModel):
    """The broker's answer. ``output`` is UNTRUSTED data: sanitized, scanned for prompt injection, truncated."""

    status: ToolStatus
    tool_name: str
    output: Any = None
    output_kind: str = "json"
    untrusted: Literal[True] = True
    injection_findings: list[dict[str, Any]] = Field(default_factory=list)
    invocation_id: str | None = None
    approval_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    reused: bool = Field(default=False, description="True when a stored result of an identical call was returned")
    latency_ms: int | None = None
    cost_usd: float = 0.0


class ToolInvocationOut(ORMModel):
    id: str
    project_id: str | None = None
    mission_id: str | None = None
    agent_run_id: str | None = None
    tool_name: str
    tool_source: str
    mcp_server_id: str | None = None
    input: dict[str, Any] = Field(description="Arguments with redacted fields and truncated long values")
    input_hash: str
    output: dict[str, Any] = Field(description="Sanitized, truncated output (untrusted data)")
    output_hash: str | None = None
    status: str
    decision: dict[str, Any]
    risk_level: str
    injection_findings: list[Any]
    latency_ms: int | None = None
    cost_usd: Money
    error: str | None = None
    approval_id: str | None = None
    requested_by_id: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------------------------
class MCPServerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=MCP_SERVER_NAME_PATTERN, description="Short unique name; tools become mcp.<name>.<tool>")
    description: str | None = Field(default=None, max_length=2000)
    endpoint: str = Field(min_length=8, max_length=1000, description="Streamable HTTP endpoint (https)")
    project_id: uuid.UUID | None = Field(default=None, description="Restrict the server to one project")
    auth_method: Literal["none", "bearer"] = "none"
    bearer_token: str | None = Field(
        default=None, min_length=8, max_length=4096, description="Stored encrypted; never returned", repr=False
    )
    allowed_tools: list[Annotated[str, Field(pattern=MCP_TOOL_REF_PATTERN)]] = Field(
        default_factory=list, max_length=200, description="If non-empty, only these tool names are invokable"
    )
    risk_level: RiskName = "MEDIUM"

    @field_validator("allowed_tools")
    @classmethod
    def _dedupe(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class MCPServerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=2000)
    endpoint: str | None = Field(default=None, min_length=8, max_length=1000)
    auth_method: Literal["none", "bearer"] | None = None
    bearer_token: str | None = Field(default=None, min_length=8, max_length=4096, repr=False)
    allowed_tools: list[Annotated[str, Field(pattern=MCP_TOOL_REF_PATTERN)]] | None = Field(default=None, max_length=200)
    risk_level: RiskName | None = None


class MCPServerOut(ORMModel):
    id: str
    name: str
    description: str | None = None
    endpoint: str
    transport: str
    auth_method: str
    has_secret: bool
    project_id: str | None = None
    allowed_tools: list[str]
    risk_level: str
    status: str
    protocol_version: str | None = None
    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    last_health_check_at: datetime | None = None
    last_health_status: str | None = None
    last_error: str | None = None
    registered_by_id: str | None = None
    approved_by_id: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MCPToolOut(ORMModel):
    id: str
    server_id: str
    name: str
    full_name: str = Field(description="mcp.<server>.<tool>")
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(description="Server-provided hints (untrusted)")
    schema_hash: str
    approved_schema_hash: str | None = None
    risk_level: str
    enabled: bool
    status: str
    invokable: bool
    approved_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MCPToolApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskName | None = Field(
        default=None, description="Override the default risk (server risk raised by destructive/open-world hints)"
    )


class MCPServerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class MCPDiscoveryOut(BaseModel):
    server_id: str
    era: str | None
    protocol_version: str | None
    discovered: int
    new: list[str]
    updated: list[str]
    schema_changed: list[str]
    removed: list[str]
    skipped: list[str]
    tools: list[MCPToolOut]


class MCPHealthOut(BaseModel):
    server_id: str
    status: str
    healthy: bool
    last_health_status: str | None
    checked_at: datetime
    latency_ms: int
    tool_count: int | None = None
    error: str | None = None
