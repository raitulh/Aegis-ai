"""Tool definitions and the registry of built-in tools.

A :class:`ToolDefinition` declares everything the broker needs to govern a tool *before* running it: a pydantic
input model (→ JSON Schema for the model and strict argument validation), the risk level (drives the
``tool.invoke`` policy), the permissions the calling actor must hold, an optional feature flag, the egress hosts
it may reach, a per-organization rate limit, a timeout and the input fields to redact in the ledger/audit log.

Handlers run **outside** any database transaction (on a broker worker thread, bounded by the timeout); when
they need the database they open short ``tenant_uow(ctx.actor)`` units of work themselves. Everything a handler
returns is treated as UNTRUSTED data by the broker (sanitized, injection-scanned, truncated).

Built-in tools register themselves in ``aegis_api.lab.tools.builtin.*`` via :func:`register_tool`; MCP tools
are resolved dynamically by the broker (``mcp.<server>.<tool>``).
"""

from __future__ import annotations

import importlib
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from aegis_api.config import get_settings
from aegis_api.lab.core.actor import Actor
from engines.lab.states import RISK_RANK, RiskLevel

log = structlog.get_logger("aegis.lab.tools")

OutputKind = Literal["text", "json"]
ToolSource = Literal["builtin", "mcp"]

TOOL_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$"
_TOOL_NAME_RE = re.compile(TOOL_NAME_PATTERN)
MCP_PREFIX = "mcp."
BUILTIN_MODULES: tuple[str, ...] = (
    "aegis_api.lab.tools.builtin.web",
    "aegis_api.lab.tools.builtin.papers",
    "aegis_api.lab.tools.builtin.knowledge",
    "aegis_api.lab.tools.builtin.execution",
    "aegis_api.lab.tools.builtin.storage",
    "aegis_api.lab.tools.builtin.query",
    "aegis_api.lab.tools.builtin.git",
)


# ---------------------------------------------------------------------------------------------
# Errors raised by handlers (mapped onto ToolResult statuses by the broker)
# ---------------------------------------------------------------------------------------------
class ToolError(Exception):
    """A tool failed in a way that is safe to report to the caller (message never contains secrets)."""

    code = "tool_failed"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = dict(details or {})


class ToolInputError(ToolError):
    """The arguments are syntactically valid but unusable (e.g. an unknown entity or a disallowed host)."""

    code = "invalid_arguments"


class ToolUnavailable(ToolError):
    """The tool's backing service is not installed/configured in this deployment."""

    code = "tool_unavailable"


class ToolTimeout(ToolError):
    """The tool did not finish before its deadline."""

    code = "timeout"


class ToolDenied(ToolError):
    """The tool refused the request for a security reason (egress allowlist, SSRF, permissions)."""

    code = "denied"


# ---------------------------------------------------------------------------------------------
# Execution context and output
# ---------------------------------------------------------------------------------------------
@dataclass
class ToolExecutionContext:
    """What a handler knows about the call. Never contains secrets (MCP credentials are bound in the handler)."""

    actor: Actor
    organization_id: uuid.UUID
    project_id: uuid.UUID
    mission_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    invocation_id: uuid.UUID
    timeout_seconds: float
    egress_allowlist: tuple[str, ...] = ()
    started_at: float = field(default_factory=time.monotonic)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def deadline(self) -> float:
        return self.started_at + self.timeout_seconds

    def remaining(self) -> float:
        return max(self.deadline - time.monotonic(), 0.0)

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set() or self.remaining() <= 0

    def check(self) -> None:
        """Raise :class:`ToolTimeout` once the deadline passed or the broker gave up on the call."""
        if self.is_cancelled():
            raise ToolTimeout("The tool call exceeded its time limit")

    def sleep(self, seconds: float) -> None:
        """Sleep up to ``seconds`` (returns early when cancelled); raises when the deadline passed."""
        self._cancel.wait(max(min(seconds, self.remaining()), 0.0))
        self.check()


@dataclass
class ToolOutput:
    """A handler result. ``content`` is text (``kind='text'``) or JSON-serializable data (``kind='json'``)."""

    content: Any
    kind: OutputKind = "json"
    cost_usd: Decimal = Decimal("0")
    is_error: bool = False
    # Non-sensitive references for the ledger (job ids, artifact ids, final URL…), never raw content.
    metadata: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[ToolExecutionContext, Any], ToolOutput]
# Hosts a specific call will reach (for the egress policy context), derived from validated arguments.
EgressResolver = Callable[[Any], list[str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    output_kind: OutputKind = "json"
    risk_level: str = RiskLevel.LOW
    permissions: frozenset[str] = frozenset()
    feature_flag: str | None = None
    network_hosts: tuple[str, ...] = ()
    egress_resolver: EgressResolver | None = None
    rate_limit_per_min: int = 60
    timeout_seconds: float | None = None
    audit_redact_fields: tuple[str, ...] = ()
    source: ToolSource = "builtin"
    category: str = "general"

    def __post_init__(self) -> None:
        if not _TOOL_NAME_RE.match(self.name) or self.name.startswith(MCP_PREFIX):
            raise ValueError(f"invalid built-in tool name '{self.name}'")
        if self.risk_level not in RISK_RANK:
            raise ValueError(f"invalid risk level '{self.risk_level}' for tool '{self.name}'")
        if self.rate_limit_per_min < 1:
            raise ValueError("rate_limit_per_min must be >= 1")

    @property
    def tool_id(self) -> str:
        return f"builtin:{self.name}"

    @property
    def effective_timeout(self) -> float:
        return float(self.timeout_seconds or get_settings().tool_default_timeout_seconds)

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def egress_hosts(self, arguments: Any) -> list[str]:
        if self.egress_resolver is not None:
            try:
                return sorted({h.lower() for h in self.egress_resolver(arguments) if h})
            except Exception:  # the handler re-validates; the policy context is best effort
                log.debug("tool_egress_resolver_failed", tool=self.name, exc_info=True)
        return sorted(self.network_hosts)


BUILTIN_TOOLS: dict[str, ToolDefinition] = {}
_loaded = False
_load_lock = threading.Lock()


def register_tool(definition: ToolDefinition) -> ToolDefinition:
    existing = BUILTIN_TOOLS.get(definition.name)
    if existing is not None and existing is not definition:
        raise ValueError(f"Tool '{definition.name}' is already registered")
    BUILTIN_TOOLS[definition.name] = definition
    return definition


def load_builtin_tools() -> dict[str, ToolDefinition]:
    """Import every built-in tool module (idempotent)."""
    global _loaded
    if not _loaded:
        with _load_lock:
            if not _loaded:
                for module in BUILTIN_MODULES:
                    importlib.import_module(module)
                _loaded = True
    return BUILTIN_TOOLS


def builtin_tools() -> list[ToolDefinition]:
    return sorted(load_builtin_tools().values(), key=lambda d: d.name)


def get_builtin_tool(name: str) -> ToolDefinition | None:
    return load_builtin_tools().get(name)


def is_known_tool_name(name: str) -> bool:
    """Built-in tool names, or a syntactically valid MCP tool reference (``mcp.<server>.<tool>``/wildcards)."""
    if name in load_builtin_tools():
        return True
    return parse_mcp_reference(name) is not None


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{MCP_PREFIX}{server_name}.{tool_name}"


_MCP_REF_RE = re.compile(r"^mcp\.([a-z0-9][a-z0-9_-]{0,39})\.(.{1,200})$")


def parse_mcp_reference(name: str) -> tuple[str, str] | None:
    """``mcp.<server>.<tool>`` → (server, tool). ``mcp.<server>.*`` → (server, '*')."""
    match = _MCP_REF_RE.match(name or "")
    if match is None:
        return None
    return match.group(1), match.group(2)


def name_allowed(name: str, allowed: list[str] | tuple[str, ...] | frozenset[str] | set[str]) -> bool:
    """Whether a tool name is covered by an allowlist that may contain ``mcp.<server>.*`` / ``mcp.*`` wildcards."""
    if name in allowed:
        return True
    if name.startswith(MCP_PREFIX):
        if "mcp.*" in allowed:
            return True
        parsed = parse_mcp_reference(name)
        if parsed is not None and f"{MCP_PREFIX}{parsed[0]}.*" in allowed:
            return True
    return False


def max_risk(*levels: str | None) -> str:
    known = [level for level in levels if level in RISK_RANK]
    if not known:
        return RiskLevel.MEDIUM
    return max(known, key=lambda level: RISK_RANK[level])


def host_allowed(host: str, allowlist: tuple[str, ...] | list[str]) -> bool:
    """Exact host match, or a leading-dot suffix entry (``.wikipedia.org`` matches ``en.wikipedia.org``)."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for entry in allowlist:
        item = (entry or "").strip().lower().rstrip(".")
        if not item:
            continue
        if item.startswith("."):
            if host.endswith(item) or host == item[1:]:
                return True
        elif host == item:
            return True
    return False
