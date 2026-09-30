"""MCP server registry.

Lifecycle: register (``pending_review``) → human approval (``active``) → tool discovery. Every discovered tool
starts unapproved; a tool whose input schema changes is automatically un-approved (protects against a server
silently changing what a tool does). Tool calls go through the ToolBroker, which only exposes approved tools
of active servers that are in scope for the calling project.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import InvalidState, ValidationFailed
from aegis_api.infrastructure.mcp.client import MCPCallResult, MCPClient, MCPError
from aegis_api.models.lab import MCPServer, MCPTool
from aegis_api.security.context import Principal
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services import audit_log, secrets_service
from aegis_api.services.lab.access import get_scoped
from aegis_api.services.lab.common import sha256_json
from engines.lab.enums import RiskLevel

MCP_TIMEOUT_SECONDS = 30.0


def register(
    db: Session,
    principal: Principal,
    *,
    name: str,
    endpoint: str,
    auth_method: str = "none",
    auth_header: str | None = None,
    secret_value: str | None = None,
    risk_level: str = "high",
    allowed_tools: list[str] | None = None,
    project_ids: list[str] | None = None,
) -> MCPServer:
    principal.require("mcp:manage")
    validate_outbound_url(endpoint)
    if auth_method not in ("none", "bearer", "header"):
        raise ValidationFailed("auth_method must be none, bearer or header")
    if auth_method != "none" and not secret_value:
        raise ValidationFailed("A credential is required for bearer/header authentication")
    if auth_method == "header" and not auth_header:
        raise ValidationFailed("auth_header is required for header authentication")
    RiskLevel(risk_level)
    server = MCPServer(
        organization_id=principal.organization_id,
        name=name[:120],
        endpoint=endpoint[:1000],
        transport="streamable_http",
        auth_method=auth_method,
        auth_header=auth_header,
        allowed_tools=list(allowed_tools or []),
        project_ids=[str(p) for p in (project_ids or [])],
        risk_level=risk_level,
        status="pending_review",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(server)
    db.flush()
    if secret_value:
        secret = secrets_service.create_secret(
            db,
            organization_id=principal.organization_id,
            name=f"mcp:{server.id}",
            value=secret_value,
            kind="mcp_credential",
            created_by_id=principal.user_id if principal.is_human else None,
        )
        server.secret_id = secret.id
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.mcp.registered",
        resource_type="mcp_server",
        resource_id=server.id,
        principal=principal,
        after={"name": name, "endpoint": endpoint, "risk_level": risk_level},
    )
    return server


def review_server(db: Session, principal: Principal, server_id: uuid.UUID | str, *, approve: bool) -> MCPServer:
    principal.require_human("MCP server approval")
    principal.require("mcp:manage")
    server = get_scoped(db, principal, MCPServer, server_id, label="MCP server")
    if approve and server.status not in ("pending_review", "disabled"):
        raise InvalidState(f"Server is {server.status}")
    server.status = "active" if approve else "rejected"
    server.approved_by_id = principal.user_id if approve else None
    audit_log.record(
        db,
        organization_id=server.organization_id,
        action="lab.mcp.approved" if approve else "lab.mcp.rejected",
        resource_type="mcp_server",
        resource_id=server.id,
        principal=principal,
    )
    return server


def set_disabled(db: Session, principal: Principal, server_id: uuid.UUID | str) -> MCPServer:
    principal.require("mcp:manage")
    server = get_scoped(db, principal, MCPServer, server_id, label="MCP server")
    server.status = "disabled"
    audit_log.record(
        db,
        organization_id=server.organization_id,
        action="lab.mcp.disabled",
        resource_type="mcp_server",
        resource_id=server.id,
        principal=principal,
    )
    return server


def approve_tool(
    db: Session, principal: Principal, tool_id: uuid.UUID | str, *, approve: bool, risk_level: str | None = None
) -> MCPTool:
    principal.require_human("MCP tool approval")
    principal.require("mcp:manage")
    tool = get_scoped(db, principal, MCPTool, tool_id, label="MCP tool")
    if risk_level:
        RiskLevel(risk_level)
        tool.risk_level = risk_level
    tool.approved = approve
    tool.approved_by_id = principal.user_id if approve else None
    audit_log.record(
        db,
        organization_id=tool.organization_id,
        action="lab.mcp.tool_approved" if approve else "lab.mcp.tool_revoked",
        resource_type="mcp_tool",
        resource_id=tool.id,
        principal=principal,
        after={"name": tool.name, "risk_level": tool.risk_level, "schema_sha256": tool.schema_sha256},
    )
    return tool


def _client(db: Session, server: MCPServer) -> MCPClient:
    headers: dict[str, str] = {}
    if server.auth_method != "none" and server.secret_id:
        credential = secrets_service.reveal_secret(db, server.secret_id, server.organization_id)
        if server.auth_method == "bearer":
            headers["Authorization"] = f"Bearer {credential}"
        elif server.auth_header:
            headers[server.auth_header] = credential
    return MCPClient(server.endpoint, headers=headers, timeout=MCP_TIMEOUT_SECONDS)


def discover(organization_id: uuid.UUID, server_id: uuid.UUID) -> dict[str, Any]:
    """Connect, initialize and list tools (network outside the DB transaction)."""
    with session_scope(organization_id) as db:
        server = db.get(MCPServer, server_id)
        if server is None or server.organization_id != organization_id:
            raise ValidationFailed("MCP server not found")
        if server.status != "active":
            raise InvalidState("Only approved (active) MCP servers can be connected")
        client = _client(db, server)
    try:
        with client:
            tools = client.list_tools()
            info, protocol = client.server_info, client.protocol_version
        health = "ok"
    except MCPError as exc:
        tools, info, protocol, health = [], {}, None, f"error: {exc}"[:160]
    with session_scope(organization_id) as db:
        server = db.get(MCPServer, server_id)
        assert server is not None
        server.last_health_check_at = utcnow()
        server.last_health_status = health
        if info:
            server.server_info = info
            server.protocol_version = protocol
        existing = {t.name: t for t in db.scalars(select(MCPTool).where(MCPTool.server_id == server.id)).all()}
        changed: list[str] = []
        for t in tools:
            digest = sha256_json({"schema": t.input_schema, "description": t.description})
            row = existing.get(t.name)
            if row is None:
                db.add(
                    MCPTool(
                        organization_id=organization_id,
                        server_id=server.id,
                        name=t.name[:160],
                        description=(t.description or "")[:4000],
                        input_schema=t.input_schema,
                        schema_sha256=digest,
                        risk_level=_risk_from_annotations(t.annotations, server.risk_level),
                        approved=False,
                        last_seen_at=utcnow(),
                    )
                )
            else:
                if row.schema_sha256 != digest:
                    row.approved = False  # definition changed: require re-approval
                    row.schema_sha256 = digest
                    row.input_schema = t.input_schema
                    row.description = (t.description or "")[:4000]
                    changed.append(t.name)
                row.last_seen_at = utcnow()
        return {"health": health, "tools": [t.name for t in tools], "schema_changed": changed}


def _risk_from_annotations(annotations: dict[str, Any], default: str) -> str:
    if annotations.get("destructiveHint") or annotations.get("openWorldHint"):
        return "high"
    if annotations.get("readOnlyHint"):
        return "medium" if default in ("high", "critical") else default
    return default


def call(organization_id: uuid.UUID, server_id: uuid.UUID, tool_name: str, arguments: dict[str, Any]) -> MCPCallResult:
    with session_scope(organization_id) as db:
        server = db.get(MCPServer, server_id)
        if server is None or server.status != "active":
            raise MCPError("MCP server is not active")
        client = _client(db, server)
    with client:
        return client.call_tool(tool_name, arguments)


def available_tools(
    db: Session, organization_id: uuid.UUID, project_id: uuid.UUID | None
) -> list[tuple[MCPServer, MCPTool]]:
    rows = db.execute(
        select(MCPServer, MCPTool)
        .join(MCPTool, MCPTool.server_id == MCPServer.id)
        .where(MCPServer.organization_id == organization_id, MCPServer.status == "active", MCPTool.approved.is_(True))
    ).all()
    out = []
    for server, tool in rows:
        if server.project_ids and (project_id is None or str(project_id) not in server.project_ids):
            continue
        if server.allowed_tools and tool.name not in server.allowed_tools:
            continue
        out.append((server, tool))
    return out
