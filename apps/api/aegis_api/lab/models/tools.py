"""Tool broker ledger and the MCP server/tool registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import money_column, user_fk
from engines.lab.states import MCPServerStatus, RiskLevel


class MCPServer(IdMixin, TimestampMixin, OrgMixin, Base):
    """An explicitly registered remote MCP server (Streamable HTTP). Never trusted automatically:
    new servers start ``PENDING_REVIEW`` and their tools are disabled until approved."""

    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_mcp_servers_org_name"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(String(1000))
    transport: Mapped[str] = mapped_column(String(24), default="streamable_http")
    auth_method: Mapped[str] = mapped_column(String(32), default="none")  # none|bearer|oauth_client_credentials
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    allowed_tools: Mapped[list[str]] = mapped_column(default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.MEDIUM)
    status: Mapped[str] = mapped_column(String(16), default=MCPServerStatus.PENDING_REVIEW)
    protocol_version: Mapped[str | None] = mapped_column(String(24))
    server_info: Mapped[dict[str, Any]] = mapped_column(default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_health_check_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_health_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    registered_by_id: Mapped[uuid.UUID | None] = user_fk()
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MCPTool(IdMixin, TimestampMixin, OrgMixin, Base):
    """A discovered MCP tool. ``schema_hash`` pins the reviewed schema: if the server later changes the
    tool definition the tool is disabled (``schema_changed``) until re-approved."""

    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),)

    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    annotations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    schema_hash: Mapped[str] = mapped_column(String(64))
    approved_schema_hash: Mapped[str | None] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.MEDIUM)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered|approved|disabled|schema_changed
    approved_by_id: Mapped[uuid.UUID | None] = user_fk()


class ToolInvocation(IdMixin, TimestampMixin, OrgMixin, Base):
    """Every tool request brokered for an agent — allowed, denied or awaiting approval."""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("ix_tool_invocations_org_created", "organization_id", "created_at"),
        Index("ix_tool_invocations_agent_run", "agent_run_id"),
        Index("ix_tool_invocations_mission", "mission_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(200))
    tool_source: Mapped[str] = mapped_column(String(16), default="builtin")  # builtin|mcp
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted
    input_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)  # sanitized + truncated
    output_hash: Mapped[str | None] = mapped_column(String(64))
    # requested|denied|approval_required|running|succeeded|failed|timeout
    status: Mapped[str] = mapped_column(String(24), default="requested")
    decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.LOW)
    injection_findings: Mapped[list[Any]] = mapped_column(default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal] = money_column()
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = user_fk()
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
