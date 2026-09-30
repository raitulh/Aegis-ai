"""MCP server & tool registry (database side): registration, review, approval, rug-pull defence, invokability.

Nothing from a remote MCP server is trusted automatically:

* servers are registered explicitly by a human with ``mcp:manage`` (endpoint SSRF-validated, https in
  production, credentials stored encrypted) and start ``PENDING_REVIEW``;
* discovered tools start ``discovered``/disabled; a human approves each one, which pins its ``schema_hash``
  (sha256 of the canonical ``{name, description, inputSchema, outputSchema, annotations}``);
* on rediscovery a previously approved tool whose definition changed becomes ``schema_changed`` and disabled
  until re-approved (rug-pull defence); tools that disappeared are disabled;
* annotations (``readOnlyHint``, ``destructiveHint``…) are untrusted hints that can only *raise* the default risk.

A tool is invokable only when the ``mcp`` feature is enabled, its server is ``ACTIVE`` (and scoped to the
invocation's project, if restricted), the tool is enabled and approved with an unchanged schema, and it is in
the server's ``allowed_tools`` when that list is non-empty.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.models import MCPServer, MCPTool
from aegis_api.lab.tools.registry import max_risk, mcp_tool_name
from aegis_api.lab.tools.schemas import (
    MCPServerCreate,
    MCPServerOut,
    MCPServerUpdate,
    MCPToolOut,
)
from aegis_api.models import Secret
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services import secrets_service
from engines.lab.states import MCPServerStatus, RiskLevel

log = structlog.get_logger("aegis.lab.mcp")

TOOL_DISCOVERED = "discovered"
TOOL_APPROVED = "approved"
TOOL_DISABLED = "disabled"
TOOL_SCHEMA_CHANGED = "schema_changed"
MAX_TOOL_NAME = 128
MAX_DESCRIPTION_CHARS = 8000
MAX_SCHEMA_BYTES = 64 * 1024
SECRET_KIND = "mcp_bearer"
_TOOL_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./")


class AuditActions:
    MCP_SERVER_UPDATED = "MCP_SERVER_UPDATED"
    MCP_SERVER_DISABLED = "MCP_SERVER_DISABLED"
    MCP_TOOLS_DISCOVERED = "MCP_TOOLS_DISCOVERED"
    MCP_TOOL_APPROVED = "MCP_TOOL_APPROVED"
    MCP_TOOL_DISABLED = "MCP_TOOL_DISABLED"
    MCP_TOOL_SCHEMA_CHANGED = "MCP_TOOL_SCHEMA_CHANGED"


class MCPToolNotInvokable(Exception):
    def __init__(self, reason: str, code: str = "mcp_not_invokable") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


# ---------------------------------------------------------------------------------------------
# Hashing & risk
# ---------------------------------------------------------------------------------------------
def canonical_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "inputSchema": tool.get("inputSchema"),
        "outputSchema": tool.get("outputSchema"),
        "annotations": tool.get("annotations"),
    }


def schema_hash(tool: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of ``{name, description, inputSchema, outputSchema, annotations}``."""
    canonical = json.dumps(canonical_tool(tool), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_tool_risk(server_risk: str | None, annotations: dict[str, Any] | None) -> str:
    """Server risk, raised (never lowered) by the tool's hints: destructive → HIGH, open-world → ≥ MEDIUM.

    Per the MCP spec the defaults are ``readOnlyHint=false``, ``destructiveHint=true``, ``openWorldHint=true``.
    ``readOnlyHint`` is untrusted and therefore never lowers the risk.
    """
    hints = annotations if isinstance(annotations, dict) else {}
    read_only = hints.get("readOnlyHint") is True
    destructive = (not read_only) and hints.get("destructiveHint", True) is not False
    open_world = hints.get("openWorldHint", True) is not False
    raised: list[str | None] = [server_risk or RiskLevel.MEDIUM]
    if destructive:
        raised.append(RiskLevel.HIGH)
    if open_world:
        raised.append(RiskLevel.MEDIUM)
    return max_risk(*raised)


def valid_tool_name(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= MAX_TOOL_NAME and set(name) <= _TOOL_NAME_CHARS


# ---------------------------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------------------------
def server_out(server: MCPServer) -> MCPServerOut:
    return MCPServerOut(
        id=str(server.id),
        name=server.name,
        description=server.description,
        endpoint=server.endpoint,
        transport=server.transport,
        auth_method=server.auth_method,
        has_secret=server.secret_id is not None,
        project_id=str(server.project_id) if server.project_id else None,
        allowed_tools=list(server.allowed_tools or []),
        risk_level=server.risk_level,
        status=server.status,
        protocol_version=server.protocol_version,
        server_info=dict(server.server_info or {}),
        capabilities=dict(server.capabilities or {}),
        last_health_check_at=server.last_health_check_at,
        last_health_status=server.last_health_status,
        last_error=server.last_error,
        registered_by_id=str(server.registered_by_id) if server.registered_by_id else None,
        approved_by_id=str(server.approved_by_id) if server.approved_by_id else None,
        approved_at=server.approved_at,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def tool_is_invokable(server: MCPServer, tool: MCPTool) -> bool:
    return not _blocking_reason(server, tool)


def tool_out(server: MCPServer, tool: MCPTool) -> MCPToolOut:
    return MCPToolOut(
        id=str(tool.id),
        server_id=str(tool.server_id),
        name=tool.name,
        full_name=mcp_tool_name(server.name, tool.name),
        title=tool.title,
        description=tool.description,
        input_schema=dict(tool.input_schema or {}),
        output_schema=tool.output_schema,
        annotations=dict(tool.annotations or {}),
        schema_hash=tool.schema_hash,
        approved_schema_hash=tool.approved_schema_hash,
        risk_level=tool.risk_level,
        enabled=tool.enabled,
        status=tool.status,
        invokable=tool_is_invokable(server, tool),
        approved_by_id=str(tool.approved_by_id) if tool.approved_by_id else None,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


# ---------------------------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------------------------
def _require_manager(actor: Actor, action: str) -> None:
    actor.require("mcp:manage")
    actor.require_human(action)


def validate_endpoint(endpoint: str) -> str:
    schemes = frozenset({"https"}) if get_settings().is_production else frozenset({"https", "http"})
    return validate_outbound_url(endpoint.strip(), allowed_schemes=schemes)


def _store_secret(db: Session, actor: Actor, server_id: uuid.UUID, token: str) -> uuid.UUID:
    secret = secrets_service.create_secret(
        db,
        organization_id=actor.organization_id,
        name=f"mcp-server:{server_id}",
        value=token,
        kind=SECRET_KIND,
        created_by_id=actor.user_id,
    )
    return secret.id


def register_server(db: Session, actor: Actor, data: MCPServerCreate) -> MCPServer:
    """Register a server (``PENDING_REVIEW``). Requires a human with ``mcp:manage``."""
    _require_manager(actor, "registering an MCP server")
    project_id = load_project(db, actor, data.project_id).id if data.project_id else None
    endpoint = validate_endpoint(data.endpoint)
    if data.auth_method == "bearer" and not data.bearer_token:
        raise ValidationFailed("bearer_token is required when auth_method is 'bearer'")
    if data.auth_method == "none" and data.bearer_token:
        raise ValidationFailed("bearer_token requires auth_method 'bearer'")
    exists = db.scalar(
        select(MCPServer.id).where(MCPServer.organization_id == actor.organization_id, MCPServer.name == data.name)
    )
    if exists is not None:
        raise Conflict(f"An MCP server named '{data.name}' already exists", code="mcp_server_exists")
    server = MCPServer(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        project_id=project_id,
        name=data.name,
        description=data.description,
        endpoint=endpoint,
        transport="streamable_http",
        auth_method=data.auth_method,
        allowed_tools=list(data.allowed_tools),
        risk_level=data.risk_level,
        status=MCPServerStatus.PENDING_REVIEW,
        server_info={},
        capabilities={},
        registered_by_id=actor.user_id,
    )
    if data.bearer_token:
        server.secret_id = _store_secret(db, actor, server.id, data.bearer_token)
    db.add(server)
    db.flush()
    audit(
        db,
        actor,
        AuditAction.MCP_SERVER_REGISTERED,
        "mcp_server",
        server.id,
        after={
            "name": server.name,
            "endpoint": server.endpoint,
            "auth_method": server.auth_method,
            "project_id": project_id,
            "allowed_tools": server.allowed_tools,
            "risk_level": server.risk_level,
        },
    )
    return server


def get_server(db: Session, actor: Actor, server_id: uuid.UUID | str) -> MCPServer:
    actor.require("mcp:read")
    server = get_owned(db, MCPServer, server_id, actor, label="MCP server")
    if server.project_id is not None:
        load_project(db, actor, server.project_id)
    return server


def list_servers(db: Session, actor: Actor, params: PageParams, *, status: str | None = None) -> Page[MCPServerOut]:
    actor.require("mcp:read")
    stmt = select(MCPServer).where(MCPServer.organization_id == actor.organization_id)
    if status:
        stmt = stmt.where(MCPServer.status == status.upper())
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(MCPServer.project_id.is_(None), MCPServer.project_id.in_(visible)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(MCPServer.created_at.desc(), MCPServer.id.desc()).limit(params.page_size).offset(params.offset)
    ).all()
    return Page.build([server_out(s) for s in rows], int(total), params)


def update_server(db: Session, actor: Actor, server_id: uuid.UUID | str, data: MCPServerUpdate) -> MCPServer:
    """Change a server. A new endpoint or credential sends the server back to review (``PENDING_REVIEW``)."""
    _require_manager(actor, "changing an MCP server")
    server = get_server(db, actor, server_id)
    before = {
        "endpoint": server.endpoint,
        "auth_method": server.auth_method,
        "allowed_tools": list(server.allowed_tools or []),
        "risk_level": server.risk_level,
        "status": server.status,
    }
    needs_review = False
    fields = data.model_fields_set
    if "description" in fields:
        server.description = data.description
    if data.endpoint is not None and data.endpoint.strip() != server.endpoint:
        server.endpoint = validate_endpoint(data.endpoint)
        server.protocol_version = None
        needs_review = True
    auth_method = data.auth_method or server.auth_method
    if auth_method == "none":
        if data.bearer_token:
            raise ValidationFailed("bearer_token requires auth_method 'bearer'")
        if server.auth_method != "none":
            server.secret_id = None
            needs_review = True
    elif data.bearer_token:
        server.secret_id = _store_secret(db, actor, server.id, data.bearer_token)
        needs_review = True
    elif server.secret_id is None:
        raise ValidationFailed("bearer_token is required when auth_method is 'bearer'")
    if auth_method != server.auth_method:
        needs_review = True
    server.auth_method = auth_method
    if data.allowed_tools is not None:
        server.allowed_tools = list(dict.fromkeys(data.allowed_tools))
    if data.risk_level is not None:
        server.risk_level = data.risk_level
    if needs_review and server.status in (MCPServerStatus.ACTIVE, MCPServerStatus.ERROR):
        server.status = MCPServerStatus.PENDING_REVIEW
        server.approved_at = None
        server.approved_by_id = None
    db.flush()
    audit(
        db,
        actor,
        AuditActions.MCP_SERVER_UPDATED,
        "mcp_server",
        server.id,
        before=before,
        after={
            "endpoint": server.endpoint,
            "auth_method": server.auth_method,
            "allowed_tools": list(server.allowed_tools or []),
            "risk_level": server.risk_level,
            "status": server.status,
            "credential_rotated": bool(data.bearer_token),
        },
    )
    return server


def approve_server(db: Session, actor: Actor, server_id: uuid.UUID | str) -> MCPServer:
    """Human review of a server → ``ACTIVE`` (its tools still need individual approval)."""
    _require_manager(actor, "approving an MCP server")
    server = get_server(db, actor, server_id)
    if server.status == MCPServerStatus.ACTIVE:
        return server
    before = server.status
    server.status = MCPServerStatus.ACTIVE
    server.approved_by_id = actor.user_id
    server.approved_at = utcnow()
    db.flush()
    audit(
        db,
        actor,
        AuditAction.MCP_SERVER_APPROVED,
        "mcp_server",
        server.id,
        before={"status": before},
        after={"status": server.status, "endpoint": server.endpoint},
    )
    return server


def disable_server(db: Session, actor: Actor, server_id: uuid.UUID | str, *, reason: str | None = None) -> MCPServer:
    """Disable a server: none of its tools can be invoked until it is approved again."""
    _require_manager(actor, "disabling an MCP server")
    server = get_server(db, actor, server_id)
    if server.status == MCPServerStatus.DISABLED:
        return server
    before = server.status
    server.status = MCPServerStatus.DISABLED
    db.flush()
    audit(
        db,
        actor,
        AuditActions.MCP_SERVER_DISABLED,
        "mcp_server",
        server.id,
        before={"status": before},
        after={"status": server.status, "reason": (reason or "")[:500] or None},
    )
    return server


# ---------------------------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------------------------
def list_server_tools(db: Session, actor: Actor, server_id: uuid.UUID | str) -> list[MCPToolOut]:
    server = get_server(db, actor, server_id)
    rows = db.scalars(select(MCPTool).where(MCPTool.server_id == server.id).order_by(MCPTool.name)).all()
    return [tool_out(server, t) for t in rows]


def _tool_with_server(db: Session, actor: Actor, tool_id: uuid.UUID | str) -> tuple[MCPServer, MCPTool]:
    tool = get_owned(db, MCPTool, tool_id, actor, label="MCP tool")
    server = get_server(db, actor, tool.server_id)
    return server, tool


def approve_tool(
    db: Session, actor: Actor, tool_id: uuid.UUID | str, *, risk_level: str | None = None
) -> tuple[MCPServer, MCPTool]:
    """Approve the tool's CURRENT definition (pins ``approved_schema_hash``) and enable it."""
    _require_manager(actor, "approving an MCP tool")
    server, tool = _tool_with_server(db, actor, tool_id)
    before = {
        "status": tool.status,
        "enabled": tool.enabled,
        "risk_level": tool.risk_level,
        "approved_schema_hash": tool.approved_schema_hash,
    }
    tool.risk_level = risk_level or default_tool_risk(server.risk_level, tool.annotations)
    tool.approved_schema_hash = tool.schema_hash
    tool.enabled = True
    tool.status = TOOL_APPROVED
    tool.approved_by_id = actor.user_id
    db.flush()
    audit(
        db,
        actor,
        AuditActions.MCP_TOOL_APPROVED,
        "mcp_tool",
        tool.id,
        before=before,
        after={
            "name": mcp_tool_name(server.name, tool.name),
            "schema_hash": tool.schema_hash,
            "risk_level": tool.risk_level,
        },
    )
    return server, tool


def disable_tool(db: Session, actor: Actor, tool_id: uuid.UUID | str) -> tuple[MCPServer, MCPTool]:
    _require_manager(actor, "disabling an MCP tool")
    server, tool = _tool_with_server(db, actor, tool_id)
    before = {"status": tool.status, "enabled": tool.enabled}
    tool.enabled = False
    tool.status = TOOL_DISABLED
    db.flush()
    audit(
        db,
        actor,
        AuditActions.MCP_TOOL_DISABLED,
        "mcp_tool",
        tool.id,
        before=before,
        after={"status": tool.status, "name": mcp_tool_name(server.name, tool.name)},
    )
    return server, tool


@dataclass
class DiscoverySummary:
    new: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    schema_changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _bounded_schema(value: Any, *, default: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return default
    if len(json.dumps(value, default=str)) > MAX_SCHEMA_BYTES:
        raise ValidationFailed("tool schema too large")
    return value


def upsert_discovered_tools(
    db: Session,
    actor: Actor,
    server: MCPServer,
    tools: list[dict[str, Any]],
) -> DiscoverySummary:
    """Apply a ``tools/list`` result to the registry (rug-pull defence on changed approved tools)."""
    summary = DiscoverySummary()
    existing = {t.name: t for t in db.scalars(select(MCPTool).where(MCPTool.server_id == server.id)).all()}
    seen: set[str] = set()
    for item in tools:
        name = item.get("name")
        if not valid_tool_name(name) or name in seen:
            summary.skipped.append(str(name)[:MAX_TOOL_NAME])
            continue
        try:
            input_schema = _bounded_schema(item.get("inputSchema"), default={"type": "object", "properties": {}})
            output_schema = _bounded_schema(item.get("outputSchema"), default=None)
        except ValidationFailed:
            summary.skipped.append(name)
            continue
        seen.add(name)
        annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        description = item.get("description") if isinstance(item.get("description"), str) else None
        title = item.get("title") if isinstance(item.get("title"), str) else None
        digest = schema_hash(item)
        row = existing.get(name)
        if row is None:
            db.add(
                MCPTool(
                    organization_id=server.organization_id,
                    server_id=server.id,
                    name=name,
                    title=(title or "")[:300] or None,
                    description=(description or "")[:MAX_DESCRIPTION_CHARS] or None,
                    input_schema=input_schema or {},
                    output_schema=output_schema,
                    annotations=annotations,
                    schema_hash=digest,
                    approved_schema_hash=None,
                    risk_level=default_tool_risk(server.risk_level, annotations),
                    enabled=False,
                    status=TOOL_DISCOVERED,
                )
            )
            summary.new.append(name)
            continue
        if row.schema_hash == digest:
            if row.status == TOOL_DISABLED and row.approved_schema_hash is None and not row.enabled:
                row.status = TOOL_DISCOVERED  # reappeared before ever being approved
            continue
        row.title = (title or "")[:300] or None
        row.description = (description or "")[:MAX_DESCRIPTION_CHARS] or None
        row.input_schema = input_schema or {}
        row.output_schema = output_schema
        row.annotations = annotations
        row.schema_hash = digest
        if row.approved_schema_hash is not None and row.approved_schema_hash != digest:
            before = {"status": row.status, "enabled": row.enabled}
            row.status = TOOL_SCHEMA_CHANGED
            row.enabled = False
            summary.schema_changed.append(name)
            audit(
                db,
                actor,
                AuditActions.MCP_TOOL_SCHEMA_CHANGED,
                "mcp_tool",
                row.id,
                before=before,
                after={
                    "name": mcp_tool_name(server.name, name),
                    "approved_schema_hash": row.approved_schema_hash,
                    "schema_hash": digest,
                    "status": row.status,
                },
            )
        else:
            # Never approved, or reverted to the approved definition: a schema_changed tool still needs a human.
            summary.updated.append(name)
    for name, row in existing.items():
        if name not in seen and row.status != TOOL_DISABLED:
            row.enabled = False
            row.status = TOOL_DISABLED
            summary.removed.append(name)
    db.flush()
    audit(
        db,
        actor,
        AuditActions.MCP_TOOLS_DISCOVERED,
        "mcp_server",
        server.id,
        after={
            "new": summary.new[:50],
            "schema_changed": summary.schema_changed[:50],
            "removed": summary.removed[:50],
            "skipped": summary.skipped[:50],
            "total": len(seen),
        },
    )
    return summary


# ---------------------------------------------------------------------------------------------
# Invokability (used by the broker)
# ---------------------------------------------------------------------------------------------
def _blocking_reason(server: MCPServer, tool: MCPTool, project_id: uuid.UUID | None = None) -> str | None:
    if server.status != MCPServerStatus.ACTIVE:
        return f"MCP server '{server.name}' is {server.status}, not ACTIVE"
    if server.project_id is not None and project_id is not None and server.project_id != project_id:
        return f"MCP server '{server.name}' is restricted to another project"
    if server.allowed_tools and tool.name not in server.allowed_tools:
        return f"MCP tool '{tool.name}' is not in the server's allowed tools"
    if tool.status != TOOL_APPROVED or not tool.enabled:
        return f"MCP tool '{tool.name}' is not approved/enabled (status {tool.status})"
    if tool.approved_schema_hash is None or tool.schema_hash != tool.approved_schema_hash:
        return f"MCP tool '{tool.name}' changed since it was approved"
    return None


def resolve_invokable_tool(
    db: Session, organization_id: uuid.UUID, server_name: str, tool_name: str, project_id: uuid.UUID | None
) -> tuple[MCPServer, MCPTool]:
    """The (server, tool) pair for ``mcp.<server>.<tool>`` or :class:`MCPToolNotInvokable` with the reason."""
    if not feature_enabled(db, organization_id, "mcp"):
        raise MCPToolNotInvokable("The 'mcp' feature is disabled for this organization", code="feature_disabled")
    server = db.scalar(
        select(MCPServer).where(MCPServer.organization_id == organization_id, MCPServer.name == server_name)
    )
    if server is None:
        raise MCPToolNotInvokable(f"MCP server '{server_name}' is not registered", code="mcp_unregistered")
    tool = db.scalar(select(MCPTool).where(MCPTool.server_id == server.id, MCPTool.name == tool_name))
    if tool is None:
        raise MCPToolNotInvokable(f"MCP tool '{tool_name}' is not registered on '{server_name}'", code="mcp_unregistered")
    reason = _blocking_reason(server, tool, project_id)
    if reason is not None:
        raise MCPToolNotInvokable(reason)
    return server, tool


def invokable_tools(
    db: Session, organization_id: uuid.UUID, project_id: uuid.UUID | None
) -> list[tuple[MCPServer, MCPTool]]:
    if not feature_enabled(db, organization_id, "mcp"):
        return []
    rows = db.execute(
        select(MCPServer, MCPTool)
        .join(MCPTool, MCPTool.server_id == MCPServer.id)
        .where(
            MCPServer.organization_id == organization_id,
            MCPServer.status == MCPServerStatus.ACTIVE,
            MCPTool.enabled.is_(True),
            MCPTool.status == TOOL_APPROVED,
        )
        .order_by(MCPServer.name, MCPTool.name)
    ).all()
    return [(s, t) for s, t in rows if _blocking_reason(s, t, project_id) is None]


def server_token(db: Session, server: MCPServer) -> str | None:
    """Decrypt the server's bearer token (never log it)."""
    if server.auth_method != "bearer" or server.secret_id is None:
        return None
    secret = db.get(Secret, server.secret_id)
    if secret is None or secret.organization_id != server.organization_id:
        return None
    return secrets_service.reveal_secret(db, secret.id, server.organization_id)


def ensure_not_disabled(server: MCPServer) -> None:
    if server.status == MCPServerStatus.DISABLED:
        raise Conflict("The MCP server is disabled; approve it again first", code="mcp_server_disabled")


def get_server_or_404(db: Session, organization_id: uuid.UUID, server_id: uuid.UUID) -> MCPServer:
    server = db.get(MCPServer, server_id)
    if server is None or server.organization_id != organization_id:
        raise NotFound("MCP server not found")
    return server


# ---------------------------------------------------------------------------------------------
# Scheduler entry point
# ---------------------------------------------------------------------------------------------
def run_health_checks(organization_id: uuid.UUID) -> dict[str, Any]:
    """Health-check every ACTIVE/ERROR server of one organization (short transactions; never raises)."""
    from aegis_api.lab.tools.mcp.service import health_check

    with tenant_uow(organization_id) as db:
        server_ids = list(
            db.scalars(
                select(MCPServer.id).where(
                    MCPServer.organization_id == organization_id,
                    MCPServer.status.in_((MCPServerStatus.ACTIVE, MCPServerStatus.ERROR)),
                )
            ).all()
        )
    healthy = 0
    failures = 0
    for server_id in server_ids:
        try:
            result = health_check(organization_id, server_id)
        except Exception:
            log.warning("mcp_health_check_failed", server_id=str(server_id), exc_info=True)
            failures += 1
            continue
        if result.healthy:
            healthy += 1
        else:
            failures += 1
    return {"checked": len(server_ids), "healthy": healthy, "unhealthy": failures}
