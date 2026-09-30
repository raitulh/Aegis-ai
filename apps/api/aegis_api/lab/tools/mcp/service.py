"""MCP network operations: discovery, health checks and tool calls.

Every function here talks to a remote server and therefore manages its own SHORT tenant transactions
(read server + credentials → close the transaction → network call → new transaction to record the outcome);
no database transaction is ever held open across an MCP request.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import ServiceUnavailable
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.features import ensure_feature
from aegis_api.lab.models import MCPServer
from aegis_api.lab.tools.mcp import registry
from aegis_api.lab.tools.mcp.client import (
    LEGACY_VERSIONS,
    Era,
    MCPClient,
    MCPError,
    is_modern_version,
)
from aegis_api.lab.tools.schemas import MCPDiscoveryOut, MCPHealthOut
from engines.lab.prompt_security import sanitize_untrusted
from engines.lab.states import MCPServerStatus

log = structlog.get_logger("aegis.lab.mcp")

HEALTH_TIMEOUT_SECONDS = 5.0
MAX_INFO_BYTES = 8 * 1024


class MCPUnavailable(ServiceUnavailable):
    """The MCP server could not be reached or answered incorrectly."""

    code = "mcp_unavailable"


@dataclass(frozen=True)
class ServerConnection:
    """Everything needed to call a server. The token is decrypted in memory only and never logged."""

    server_id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    endpoint: str
    protocol_version: str | None
    token: str | None = field(default=None, repr=False)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


def connection_for(db: Any, server: MCPServer) -> ServerConnection:
    return ServerConnection(
        server_id=server.id,
        organization_id=server.organization_id,
        name=server.name,
        endpoint=server.endpoint,
        protocol_version=server.protocol_version,
        token=registry.server_token(db, server),
    )


def era_hint(protocol_version: str | None) -> Era | None:
    if protocol_version in LEGACY_VERSIONS:
        return "legacy"
    if is_modern_version(protocol_version):
        return "modern"
    return None


def build_client(connection: ServerConnection, *, timeout: float | None = None) -> MCPClient:
    """A client for one server (cached era/version from the registry). Tests substitute a mock transport here."""
    era = era_hint(connection.protocol_version)
    return MCPClient(
        connection.endpoint,
        headers=connection.headers(),
        timeout=timeout,
        era=era,
        legacy_version=connection.protocol_version if era == "legacy" else None,
    )


def safe_error(exc: MCPError) -> str:
    """A short, credential-free description of an MCP failure for storage and API responses."""
    status = f" (HTTP {exc.status})" if exc.status else ""
    return sanitize_untrusted(f"{exc.kind}: {exc.message}{status}", 500)


def _bounded_info(value: dict[str, Any]) -> dict[str, Any]:
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) > MAX_INFO_BYTES:
        return {"truncated": True}
    return {str(k)[:64]: (sanitize_untrusted(v, 300) if isinstance(v, str) else v) for k, v in value.items()}


def _record_failure(organization_id: uuid.UUID, server_id: uuid.UUID, error: str, *, mark_error: bool) -> None:
    with tenant_uow(organization_id) as db:
        server = registry.get_server_or_404(db, organization_id, server_id)
        server.last_health_check_at = utcnow()
        server.last_health_status = "unhealthy"
        server.last_error = error
        if mark_error and server.status == MCPServerStatus.ACTIVE:
            server.status = MCPServerStatus.ERROR


# ---------------------------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------------------------
def discover_tools(actor: Actor, server_id: uuid.UUID | str) -> MCPDiscoveryOut:
    """``tools/list`` → upsert ``mcp_tools`` (new tools disabled; changed approved tools → ``schema_changed``)."""
    actor.require("mcp:manage")
    actor.require_human("discovering MCP tools")
    with tenant_uow(actor) as db:
        ensure_feature(db, actor.organization_id, "mcp")
        server = registry.get_server(db, actor, server_id)
        registry.ensure_not_disabled(server)
        connection = connection_for(db, server)
    client = build_client(connection)
    try:
        tools = client.list_tools()
        description = client.describe()
    except MCPError as exc:
        error = safe_error(exc)
        _record_failure(actor.organization_id, connection.server_id, error, mark_error=False)
        raise MCPUnavailable(f"MCP discovery failed: {error}") from None
    finally:
        client.close()
    with tenant_uow(actor) as db:
        server = registry.get_server(db, actor, connection.server_id)
        server.protocol_version = description.protocol_version
        server.server_info = _bounded_info(description.server_info)
        server.capabilities = _bounded_info(description.capabilities)
        server.last_health_check_at = utcnow()
        server.last_health_status = "healthy"
        server.last_error = None
        summary = registry.upsert_discovered_tools(db, actor, server, tools)
        tool_views = registry.list_server_tools(db, actor, server.id)
        return MCPDiscoveryOut(
            server_id=str(server.id),
            era=description.era,
            protocol_version=description.protocol_version,
            discovered=len(tools) - len(summary.skipped),
            new=summary.new,
            updated=summary.updated,
            schema_changed=summary.schema_changed,
            removed=summary.removed,
            skipped=summary.skipped,
            tools=tool_views,
        )


# ---------------------------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------------------------
def health_check(organization_id: uuid.UUID, server_id: uuid.UUID | str) -> MCPHealthOut:
    """``tools/list`` with a short timeout → ``last_health_status``. Non-transient failures (credentials,
    protocol) move an ACTIVE server to ERROR; a later healthy check restores ACTIVE."""
    sid = server_id if isinstance(server_id, uuid.UUID) else uuid.UUID(str(server_id))
    with tenant_uow(organization_id) as db:
        server = registry.get_server_or_404(db, organization_id, sid)
        connection = connection_for(db, server)
    started = time.perf_counter()
    client = build_client(connection, timeout=min(HEALTH_TIMEOUT_SECONDS, get_settings().mcp_timeout_seconds))
    error: MCPError | None = None
    tool_count: int | None = None
    try:
        tool_count = len(client.list_tools())
    except MCPError as exc:
        error = exc
    finally:
        client.close()
    latency_ms = int((time.perf_counter() - started) * 1000)
    now = utcnow()
    with tenant_uow(organization_id) as db:
        server = registry.get_server_or_404(db, organization_id, sid)
        server.last_health_check_at = now
        if error is None:
            server.last_health_status = "healthy"
            server.last_error = None
            if client.negotiated_version:
                server.protocol_version = client.negotiated_version
            if server.status == MCPServerStatus.ERROR:
                server.status = MCPServerStatus.ACTIVE
        else:
            server.last_health_status = "unhealthy"
            server.last_error = safe_error(error)
            if not error.retryable and server.status == MCPServerStatus.ACTIVE:
                server.status = MCPServerStatus.ERROR
        return MCPHealthOut(
            server_id=str(server.id),
            status=server.status,
            healthy=error is None,
            last_health_status=server.last_health_status,
            checked_at=now,
            latency_ms=latency_ms,
            tool_count=tool_count,
            error=server.last_error,
        )


def health_check_for(actor: Actor, server_id: uuid.UUID | str) -> MCPHealthOut:
    """Human-triggered health check (``mcp:manage``)."""
    actor.require("mcp:manage")
    with tenant_uow(actor) as db:
        ensure_feature(db, actor.organization_id, "mcp")
        server = registry.get_server(db, actor, server_id)
        sid = server.id
    return health_check(actor.organization_id, sid)


# ---------------------------------------------------------------------------------------------
# Invocation (called by the broker outside any transaction)
# ---------------------------------------------------------------------------------------------
def call_tool(
    connection: ServerConnection, tool_name: str, arguments: dict[str, Any], *, timeout: float | None = None
) -> dict[str, Any]:
    client = build_client(connection, timeout=timeout)
    try:
        return client.call_tool(tool_name, arguments)
    finally:
        client.close()
