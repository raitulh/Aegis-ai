"""The tool broker: the only way agents (and humans, via the API) invoke tools.

Pipeline for every call (:func:`invoke`):

1. **resolve** the tool (built-in registry, or ``mcp.<server>.<tool>`` from the MCP registry);
2. **authorize**: the actor holds the tool's permissions in the project; for agent runs the tool is in the agent
   version's allowlist and the call is made by that run's own actor; the mission's ``allowed_tools`` (when set);
   feature flags; MCP tools must be registered, approved, schema-pinned and invokable;
3. **validate** arguments (pydantic for built-ins, JSON Schema Draft 2020-12 for MCP ``inputSchema``);
4. **policy** ``tool.invoke`` / ``mcp.invoke`` (tool risk, autonomy, egress hosts…): ``deny`` → ``denied`` (audit
   ``TOOL_DENIED``); ``require_approval`` → a human approval is requested and the call is NOT performed
   (``approval_required``); the mission budget must not be exhausted;
5. per-(organization, tool) **rate limit**;
6. **execute** outside any transaction on a worker thread, bounded by the tool timeout;
7. **sanitize** the output (hidden characters stripped, trust delimiters neutralised, prompt-injection scan;
   quarantine-level findings replace the output with a notice) — outputs are always UNTRUSTED data;
8. **persist** the ``tool_invocations`` row (redacted input, hashes, sanitized/truncated output, latency, cost,
   status, decision), audit ``TOOL_CALLED``/``MCP_CALLED``, event, metrics and mission tool spend.

All database work happens in short units of work; tool execution never holds a transaction. Identical calls
within one agent run (same ``input_hash``) return the stored result instead of executing again, and an
approval-gated call proceeds once its approval is granted.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import jsonschema
import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import AppError, Forbidden, NotFound, RateLimited, ValidationFailed
from aegis_api.lab.core.access import effective_permissions, get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import ApprovalRequired, BudgetExceeded, PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.core.org_settings import get_org_settings
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.llm.schemas import ToolSpec
from aegis_api.lab.models import AgentRun, AgentVersion, Approval, MCPServer, Mission, Project, ToolInvocation
from aegis_api.lab.observability import metrics
from aegis_api.lab.observability.tracing import span
from aegis_api.lab.tools import registry as tool_registry
from aegis_api.lab.tools.mcp import registry as mcp_registry
from aegis_api.lab.tools.mcp import service as mcp_service
from aegis_api.lab.tools.mcp.client import MCPError, MCPInputRequired, MCPResponseTooLarge
from aegis_api.lab.tools.registry import (
    MCP_PREFIX,
    TOOL_NAME_PATTERN,
    ToolDefinition,
    ToolDenied,
    ToolError,
    ToolExecutionContext,
    ToolInputError,
    ToolOutput,
    ToolTimeout,
    ToolUnavailable,
    mcp_tool_name,
    name_allowed,
    parse_mcp_reference,
)
from aegis_api.lab.tools.schemas import ToolInvocationOut, ToolOut, ToolResultOut
from aegis_api.ratelimit import get_limiter
from engines.lab.prompt_security import sanitize_untrusted, scan_for_injection
from engines.lab.states import ApprovalStatus, RiskLevel

log = structlog.get_logger("aegis.lab.tools")

ToolResult = ToolResultOut

MAX_OUTPUT_CHARS = 20_000
MAX_STORED_INPUT_CHARS = 2_000
MAX_STORED_INPUT_BYTES = 16 * 1024
MAX_FINDINGS = 20
MCP_PERMISSIONS = frozenset({"tool:invoke"})
MCP_TIMEOUT_GRACE_SECONDS = 1.0
SECRET_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization", "credential")
_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="aegis-tool")

S_SUCCEEDED = "succeeded"
S_DENIED = "denied"
S_APPROVAL = "approval_required"
S_FAILED = "failed"
S_TIMEOUT = "timeout"
S_RUNNING = "running"


@dataclass(frozen=True)
class ToolCallRequest:
    actor: Actor
    tool_name: str
    arguments: dict[str, Any]
    project_id: uuid.UUID | str | None = None
    mission_id: uuid.UUID | str | None = None
    agent_run_id: uuid.UUID | str | None = None
    call_id: str | None = None


@dataclass
class _Resolved:
    name: str
    source: str  # builtin | mcp
    description: str
    risk_level: str
    permissions: frozenset[str]
    input_schema: dict[str, Any]
    timeout: float
    rate_limit_per_min: int
    redact: tuple[str, ...] = ()
    feature_flag: str | None = None
    definition: ToolDefinition | None = None
    mcp_server_id: uuid.UUID | None = None
    mcp_tool: str | None = None
    mcp_output_schema: dict[str, Any] | None = None
    connection: mcp_service.ServerConnection | None = field(default=None, repr=False)
    block_reason: str | None = None
    block_code: str | None = None


@dataclass
class _Prepared:
    request: ToolCallRequest
    resolved: _Resolved
    arguments: Any
    invocation_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    mission_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    egress_allowlist: tuple[str, ...]
    decision: dict[str, Any]


@dataclass
class SanitizedOutput:
    content: Any
    kind: str
    findings: list[dict[str, Any]]
    quarantined: bool
    truncated: bool


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def _uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{label} not found") from exc


def input_hash(tool_name: str, arguments: Any) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "arguments": arguments}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_arguments(arguments: Any, fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """Ledger copy of the arguments: redacted fields/secret-like keys replaced, long strings truncated."""

    def clean(value: Any, key: str | None, depth: int) -> Any:
        if key is not None:
            lowered = key.lower()
            if key in fields or any(marker in lowered for marker in SECRET_KEYS):
                return "[redacted]"
        if depth > 8:
            return "[…]"
        if isinstance(value, str):
            return value if len(value) <= MAX_STORED_INPUT_CHARS else value[:MAX_STORED_INPUT_CHARS] + "…[truncated]"
        if isinstance(value, dict):
            return {str(k)[:100]: clean(v, str(k), depth + 1) for k, v in list(value.items())[:100]}
        if isinstance(value, list | tuple):
            return [clean(v, None, depth + 1) for v in list(value)[:100]]
        if isinstance(value, bool | int | float) or value is None:
            return value
        return str(value)[:MAX_STORED_INPUT_CHARS]

    cleaned = clean(arguments if isinstance(arguments, dict) else {"value": arguments}, None, 0)
    if len(json.dumps(cleaned, default=str)) > MAX_STORED_INPUT_BYTES:
        return {"_truncated": True, "keys": sorted(str(k) for k in cleaned)[:50]}
    return cleaned


def _string_leaves(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 12 or len(out) > 5000:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            _string_leaves(v, out, depth + 1)
    elif isinstance(value, list | tuple):
        for v in value:
            _string_leaves(v, out, depth + 1)


def _sanitize_json(value: Any, max_chars: int, depth: int = 0) -> Any:
    if depth > 12:
        return "[…]"
    if isinstance(value, str):
        return sanitize_untrusted(value, max_chars)
    if isinstance(value, dict):
        return {sanitize_untrusted(str(k), 200): _sanitize_json(v, max_chars, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize_json(v, max_chars, depth + 1) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return sanitize_untrusted(str(value), max_chars)


def sanitize_output(content: Any, kind: str, *, max_chars: int = MAX_OUTPUT_CHARS) -> SanitizedOutput:
    """Sanitize + injection-scan untrusted tool output. Quarantine-level findings withhold the whole output."""
    if kind == "text" or isinstance(content, str):
        text = "" if content is None else str(content)
        scan = scan_for_injection(text)
        findings = [f.as_dict() for f in scan.findings][:MAX_FINDINGS]
        if scan.quarantine:
            return SanitizedOutput(_withheld(scan), "json", findings, True, False)
        cleaned = sanitize_untrusted(text, max_chars)
        return SanitizedOutput(cleaned, "text", findings, False, len(text) > max_chars)
    leaves: list[str] = []
    _string_leaves(content, leaves)
    scan = scan_for_injection("\n".join(leaves))
    findings = [f.as_dict() for f in scan.findings][:MAX_FINDINGS]
    if scan.quarantine:
        return SanitizedOutput(_withheld(scan), "json", findings, True, False)
    cleaned = _sanitize_json(content, max_chars)
    serialized = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(serialized) > max_chars:
        return SanitizedOutput(sanitize_untrusted(serialized, max_chars), "text", findings, False, True)
    return SanitizedOutput(cleaned, "json", findings, False, False)


def _withheld(scan: Any) -> dict[str, Any]:
    categories = sorted({f.category for f in scan.findings})
    return {
        "withheld": True,
        "notice": "[tool output withheld: possible prompt injection detected (" + ", ".join(categories) + ")]",
        "risk_score": scan.risk_score,
        "categories": categories,
    }


def _output_hash(stored: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(stored, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _metric_name(resolved_name: str) -> str:
    return "mcp" if resolved_name.startswith(MCP_PREFIX) else resolved_name[:64]


def _result_from_row(row: ToolInvocation, *, reused: bool = False) -> ToolResultOut:
    stored = row.output or {}
    status: Any = row.status if row.status in (S_SUCCEEDED, S_DENIED, S_APPROVAL, S_FAILED, S_TIMEOUT) else S_FAILED
    return ToolResultOut(
        status=status,
        tool_name=row.tool_name,
        output=stored.get("content"),
        output_kind=str(stored.get("kind") or "json"),
        injection_findings=list(row.injection_findings or []),
        invocation_id=str(row.id),
        approval_id=str(row.approval_id) if row.approval_id else None,
        error=row.error,
        error_code=(row.decision or {}).get("error_code"),
        reused=reused,
        latency_ms=row.latency_ms,
        cost_usd=float(row.cost_usd or 0),
    )


# ---------------------------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------------------------
def _resolve(db: Session, actor: Actor, name: str, project_id: uuid.UUID) -> _Resolved | None:
    definition = tool_registry.get_builtin_tool(name)
    if definition is not None:
        return _Resolved(
            name=name,
            source="builtin",
            description=definition.description,
            risk_level=definition.risk_level,
            permissions=definition.permissions,
            input_schema=definition.json_schema(),
            timeout=definition.effective_timeout,
            rate_limit_per_min=definition.rate_limit_per_min,
            redact=definition.audit_redact_fields,
            feature_flag=definition.feature_flag,
            definition=definition,
        )
    parsed = parse_mcp_reference(name)
    if parsed is None or parsed[1] == "*":
        return None
    server_name, tool_name = parsed
    resolved = _Resolved(
        name=name,
        source="mcp",
        description="",
        risk_level=RiskLevel.HIGH,
        permissions=MCP_PERMISSIONS,
        input_schema={},
        timeout=float(get_settings().mcp_timeout_seconds),
        rate_limit_per_min=60,
        feature_flag="mcp",
    )
    try:
        server, tool = mcp_registry.resolve_invokable_tool(db, actor.organization_id, server_name, tool_name, project_id)
    except mcp_registry.MCPToolNotInvokable as exc:
        server = db.scalar(
            select(MCPServer).where(MCPServer.organization_id == actor.organization_id, MCPServer.name == server_name)
        )
        resolved.mcp_server_id = server.id if server is not None else None
        resolved.block_reason = exc.reason
        resolved.block_code = exc.code
        return resolved
    resolved.description = tool.description or ""
    resolved.risk_level = tool.risk_level
    resolved.input_schema = dict(tool.input_schema or {})
    resolved.mcp_server_id = server.id
    resolved.mcp_tool = tool.name
    resolved.mcp_output_schema = tool.output_schema
    resolved.connection = mcp_service.connection_for(db, server)
    return resolved


# ---------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------
def _new_row(
    db: Session,
    actor: Actor,
    *,
    name: str,
    source: str,
    arguments: Any,
    digest: str,
    project: Project,
    mission_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    risk_level: str,
    redact: tuple[str, ...],
    mcp_server_id: uuid.UUID | None,
    status: str,
    decision: dict[str, Any],
    error: str | None = None,
) -> ToolInvocation:
    row = ToolInvocation(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        project_id=project.id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        tool_name=name[:200],
        tool_source=source,
        mcp_server_id=mcp_server_id,
        input=redact_arguments(arguments, redact),
        input_hash=digest,
        output={},
        status=status,
        decision=decision,
        risk_level=risk_level if risk_level in RiskLevel.__members__ else RiskLevel.MEDIUM,
        injection_findings=[],
        cost_usd=Decimal("0"),
        error=error,
        requested_by_id=actor.user_id,
    )
    if status in (S_DENIED, S_FAILED):
        row.completed_at = utcnow()
        row.latency_ms = 0
    db.add(row)
    db.flush()
    return row


def _terminal(
    db: Session,
    actor: Actor,
    *,
    request: ToolCallRequest,
    project: Project,
    mission_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    resolved: _Resolved | None,
    digest: str,
    status: str,
    error: str,
    error_code: str,
    decision: dict[str, Any] | None = None,
) -> ToolResultOut:
    """Persist a call that ends before execution (denied / invalid) and audit it."""
    name = resolved.name if resolved else request.tool_name
    source = resolved.source if resolved else ("mcp" if request.tool_name.startswith(MCP_PREFIX) else "builtin")
    full_decision = {**(decision or {}), "error_code": error_code, "reason": error}
    row = _new_row(
        db,
        actor,
        name=name,
        source=source,
        arguments=request.arguments,
        digest=digest,
        project=project,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        risk_level=resolved.risk_level if resolved else RiskLevel.MEDIUM,
        redact=resolved.redact if resolved else (),
        mcp_server_id=resolved.mcp_server_id if resolved else None,
        status=status,
        decision=full_decision,
        error=error[:2000],
    )
    if status == S_DENIED:
        audit(
            db,
            actor,
            AuditAction.TOOL_DENIED,
            "tool_invocation",
            row.id,
            after={"tool": name, "reason": error[:500], "code": error_code, "agent_run_id": agent_run_id},
        )
    metrics.TOOL_INVOCATIONS.labels(_metric_name(name), source, status).inc()
    return _result_from_row(row)


def _find_previous(
    db: Session,
    actor: Actor,
    *,
    name: str,
    digest: str,
    project_id: uuid.UUID,
    agent_run_id: uuid.UUID | None,
) -> ToolInvocation | None:
    stmt = select(ToolInvocation).where(
        ToolInvocation.organization_id == actor.organization_id,
        ToolInvocation.project_id == project_id,
        ToolInvocation.tool_name == name,
        ToolInvocation.input_hash == digest,
    )
    if agent_run_id is not None:
        stmt = stmt.where(
            ToolInvocation.agent_run_id == agent_run_id,
            ToolInvocation.status.in_((S_SUCCEEDED, S_APPROVAL, S_RUNNING)),
        )
    else:
        if actor.user_id is None:
            return None
        stmt = stmt.where(
            ToolInvocation.agent_run_id.is_(None),
            ToolInvocation.requested_by_id == actor.user_id,
            ToolInvocation.status == S_APPROVAL,
        )
    return db.scalars(stmt.order_by(ToolInvocation.created_at.desc()).limit(1)).first()


# ---------------------------------------------------------------------------------------------
# Phase A — authorize, validate, govern (one short transaction)
# ---------------------------------------------------------------------------------------------
def _prepare(request: ToolCallRequest) -> _Prepared | ToolResultOut:
    actor = request.actor
    if request.project_id is None:
        raise ValidationFailed("project_id is required to invoke a tool")
    if not isinstance(request.arguments, dict):
        raise ValidationFailed("arguments must be a JSON object")
    with tenant_uow(actor) as db:
        project = load_project(db, actor, request.project_id)
        run_id = _uuid(request.agent_run_id, "Agent run") or (actor.agent_run_id if actor.kind == "agent" else None)
        run: AgentRun | None = None
        version: AgentVersion | None = None
        if run_id is not None:
            if actor.kind != "agent" or actor.agent_run_id != run_id:
                raise Forbidden("Tools can only be invoked for an agent run by that run's own agent")
            run = get_owned(db, AgentRun, run_id, actor, label="Agent run")
            if run.project_id != project.id:
                raise ValidationFailed("The agent run belongs to a different project")
            version = db.get(AgentVersion, run.agent_version_id)
        mission_id = _uuid(request.mission_id, "Mission") or (run.mission_id if run is not None else None)
        mission: Mission | None = None
        if mission_id is not None:
            mission = get_owned(db, Mission, mission_id, actor, label="Mission")
            if mission.project_id != project.id:
                raise ValidationFailed("The mission belongs to a different project")
            if run is not None and run.mission_id not in (None, mission.id):
                raise ValidationFailed("The mission does not match the agent run's mission")
        digest = input_hash(request.tool_name, request.arguments)
        base: dict[str, Any] = {
            "request": request,
            "project": project,
            "mission_id": mission_id,
            "agent_run_id": run_id,
            "digest": digest,
        }

        # -- idempotency / approval continuation ---------------------------------------------
        previous = _find_previous(
            db, actor, name=request.tool_name, digest=digest, project_id=project.id, agent_run_id=run_id
        )
        resumed: ToolInvocation | None = None
        approval_note: dict[str, Any] | None = None
        if previous is not None:
            if previous.status == S_SUCCEEDED:
                return _result_from_row(previous, reused=True)
            if previous.status == S_APPROVAL and previous.approval_id is not None:
                approval = db.get(Approval, previous.approval_id)
                state = approval.status if approval is not None else ApprovalStatus.CANCELLED
                if state == ApprovalStatus.PENDING:
                    return _result_from_row(previous, reused=True)
                if state == ApprovalStatus.APPROVED:
                    resumed = previous
                    approval_note = {"approval_id": str(previous.approval_id), "status": state}
                elif state == ApprovalStatus.REJECTED:
                    previous.status = S_DENIED
                    previous.error = "The approval request for this call was rejected"
                    previous.completed_at = utcnow()
                    previous.decision = {**(previous.decision or {}), "error_code": "approval_rejected"}
                    db.flush()
                    metrics.TOOL_INVOCATIONS.labels(_metric_name(previous.tool_name), previous.tool_source, S_DENIED).inc()
                    return _result_from_row(previous)
            elif previous.status == S_RUNNING:
                resumed = previous  # abandoned by a crashed attempt of this run: re-run under the same id

        # -- resolve -------------------------------------------------------------------------
        resolved = _resolve(db, actor, request.tool_name, project.id)
        if resolved is None:
            return _terminal(
                db, actor, **base, resolved=None, status=S_DENIED,
                error=f"Unknown tool '{request.tool_name}'", error_code="unknown_tool",
            )

        # -- authorize -----------------------------------------------------------------------
        granted = effective_permissions(db, actor, project)
        missing = sorted(resolved.permissions - granted)
        if missing:
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_DENIED,
                error=f"Missing permission(s) for this tool: {', '.join(missing)}", error_code="permission_denied",
            )
        if version is not None and not name_allowed(resolved.name, list(version.tools or [])):
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_DENIED,
                error=f"Tool '{resolved.name}' is not allowed for this agent", error_code="tool_not_allowed",
            )
        if mission is not None and mission.allowed_tools and not name_allowed(resolved.name, list(mission.allowed_tools)):
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_DENIED,
                error=f"Tool '{resolved.name}' is not allowed by the mission", error_code="tool_not_allowed_by_mission",
            )
        if resolved.feature_flag and not feature_enabled(db, actor.organization_id, resolved.feature_flag):
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_DENIED,
                error=f"The '{resolved.feature_flag}' feature is disabled for this organization",
                error_code="feature_disabled",
            )
        if resolved.block_reason is not None:
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_DENIED,
                error=resolved.block_reason, error_code=resolved.block_code or "mcp_not_invokable",
            )

        # -- validate ------------------------------------------------------------------------
        arguments: Any
        if resolved.definition is not None:
            try:
                arguments = resolved.definition.input_model.model_validate(request.arguments)
            except ValidationError as exc:
                problems = [
                    f"{'.'.join(str(p) for p in err.get('loc', ())) or '(root)'}: {err.get('msg')}"
                    for err in exc.errors()[:5]
                ]
                return _terminal(
                    db, actor, **base, resolved=resolved, status=S_FAILED,
                    error="Invalid arguments: " + "; ".join(problems), error_code="invalid_arguments",
                )
        else:
            try:
                validator = jsonschema.Draft202012Validator(resolved.input_schema or {"type": "object"})
                problems = [
                    f"{'.'.join(str(p) for p in err.path) or '(root)'}: {err.message[:200]}"
                    for err in sorted(validator.iter_errors(request.arguments), key=lambda e: list(e.path))[:5]
                ]
            except jsonschema.SchemaError:
                return _terminal(
                    db, actor, **base, resolved=resolved, status=S_FAILED,
                    error="The MCP tool's input schema is not valid JSON Schema", error_code="invalid_tool_schema",
                )
            if problems:
                return _terminal(
                    db, actor, **base, resolved=resolved, status=S_FAILED,
                    error="Invalid arguments: " + "; ".join(problems), error_code="invalid_arguments",
                )
            arguments = dict(request.arguments)

        # -- governance ----------------------------------------------------------------------
        org = get_org_settings(db, actor.organization_id)
        from aegis_api.lab.governance.policies import egress_allowlist

        allowlist = tuple(egress_allowlist(org.egress_allowlist))
        egress_hosts = (
            resolved.definition.egress_hosts(arguments) if resolved.definition is not None else []
        )
        decision_doc: dict[str, Any] = {"approval": approval_note} if approval_note else {}
        if resumed is None or resumed.status == S_RUNNING:
            action = "mcp.invoke" if resolved.source == "mcp" else "tool.invoke"
            context: dict[str, Any] = {
                "tool_name": resolved.name,
                "tool_risk": resolved.risk_level,
                "tool_source": resolved.source,
                "actor_kind": actor.kind,
                "is_human": actor.is_human,
                "autonomy_level": actor.autonomy_level,
                "egress_hosts": egress_hosts,
                "estimated_cost_usd": 0.0,
            }
            if resolved.source == "mcp":
                context["mcp_registered"] = True
            from aegis_api.lab.governance.policies import evaluate_policy

            decision = evaluate_policy(
                db,
                actor,
                action,
                {k: v for k, v in context.items() if v is not None},
                project_id=project.id,
                mission=mission,
            )
            decision_doc["policy"] = decision.to_dict()
            if decision.effect == "deny" or decision.effect not in ("allow", "require_approval"):
                return _terminal(
                    db, actor, **base, resolved=resolved, status=S_DENIED,
                    error="Denied by governance policy: " + ("; ".join(decision.reasons) or "denied"),
                    error_code="policy_denied", decision=decision_doc,
                )
            if decision.effect == "require_approval" and resumed is None:
                return _request_tool_approval(db, actor, base, resolved, decision_doc, decision, mission)
        if mission is not None:
            from aegis_api.lab.governance.budgets import check_budget

            budget = check_budget(db, mission, "tool", estimated_usd=0)
            if not budget.ok:
                return _terminal(
                    db, actor, **base, resolved=resolved, status=S_DENIED,
                    error=f"The mission budget is exhausted ({budget.reason})", error_code="budget_exceeded",
                    decision=decision_doc,
                )
        try:
            get_limiter().check_custom(
                f"lab-tool-{_metric_name(resolved.name)}", f"org:{actor.organization_id}", resolved.rate_limit_per_min
            )
        except RateLimited:
            return _terminal(
                db, actor, **base, resolved=resolved, status=S_FAILED,
                error=f"Rate limit reached for tool '{resolved.name}' ({resolved.rate_limit_per_min}/min)",
                error_code="rate_limited", decision=decision_doc,
            )

        # -- record the running invocation ---------------------------------------------------
        if resumed is not None:
            row = resumed
            row.status = S_RUNNING
            row.decision = {**(row.decision or {}), **decision_doc}
        else:
            row = _new_row(
                db,
                actor,
                name=resolved.name,
                source=resolved.source,
                arguments=request.arguments,
                digest=digest,
                project=project,
                mission_id=mission_id,
                agent_run_id=run_id,
                risk_level=resolved.risk_level,
                redact=resolved.redact,
                mcp_server_id=resolved.mcp_server_id,
                status=S_RUNNING,
                decision=decision_doc,
            )
        db.flush()
        return _Prepared(
            request=request,
            resolved=resolved,
            arguments=arguments,
            invocation_id=row.id,
            project_id=project.id,
            workspace_id=project.workspace_id,
            mission_id=mission_id,
            agent_run_id=run_id,
            egress_allowlist=allowlist,
            decision=row.decision,
        )


def _request_tool_approval(
    db: Session,
    actor: Actor,
    base: dict[str, Any],
    resolved: _Resolved,
    decision_doc: dict[str, Any],
    decision: Any,
    mission: Mission | None,
) -> ToolResultOut:
    from aegis_api.lab.governance.approvals import request_approval

    request: ToolCallRequest = base["request"]
    project: Project = base["project"]
    row = _new_row(
        db,
        actor,
        name=resolved.name,
        source=resolved.source,
        arguments=request.arguments,
        digest=base["digest"],
        project=project,
        mission_id=base["mission_id"],
        agent_run_id=base["agent_run_id"],
        risk_level=resolved.risk_level,
        redact=resolved.redact,
        mcp_server_id=resolved.mcp_server_id,
        status=S_APPROVAL,
        decision={**decision_doc, "error_code": "approval_required"},
    )
    approval = request_approval(
        db,
        actor,
        action="mcp.invoke" if resolved.source == "mcp" else "tool.high_risk",
        subject_type="tool_invocation",
        subject_id=str(row.id),
        title=f"Allow tool call '{resolved.name}' ({resolved.risk_level} risk)",
        payload={
            "tool_name": resolved.name,
            "tool_source": resolved.source,
            "risk_level": resolved.risk_level,
            "arguments": row.input,
            "agent_run_id": str(base["agent_run_id"]) if base["agent_run_id"] else None,
            "reasons": list(decision.reasons),
        },
        risk_level=resolved.risk_level if resolved.risk_level in RiskLevel.__members__ else "HIGH",
        decision=decision.to_dict(),
        project_id=project.id,
        mission_id=mission.id if mission is not None else None,
        workflow_run_id=actor.workflow_run_id,
        required_permission=decision.approver_permission or "approval:decide",
    )
    row.approval_id = approval.id
    row.error = "Pending human approval; the tool was not executed"
    db.flush()
    metrics.TOOL_INVOCATIONS.labels(_metric_name(resolved.name), resolved.source, S_APPROVAL).inc()
    return _result_from_row(row)


# ---------------------------------------------------------------------------------------------
# Phase B — execute (no transaction)
# ---------------------------------------------------------------------------------------------
def _mcp_part(part: Any) -> dict[str, Any]:
    if not isinstance(part, dict):
        return {"type": "unknown"}
    kind = str(part.get("type") or "unknown")
    if kind == "text":
        return {"type": "text", "text": str(part.get("text") or "")}
    if kind in ("image", "audio"):
        return {"type": kind, "mimeType": part.get("mimeType"), "note": "binary content omitted"}
    if kind == "resource_link":
        return {"type": kind, "uri": part.get("uri"), "name": part.get("name"), "mimeType": part.get("mimeType")}
    if kind == "resource":
        resource = part.get("resource") if isinstance(part.get("resource"), dict) else {}
        view: dict[str, Any] = {"type": kind, "uri": resource.get("uri"), "mimeType": resource.get("mimeType")}
        if isinstance(resource.get("text"), str):
            view["text"] = resource["text"]
        else:
            view["note"] = "binary content omitted"
        return view
    return {"type": kind}


def _mcp_handler(prepared: _Prepared) -> Any:
    connection = prepared.resolved.connection
    tool = prepared.resolved.mcp_tool
    output_schema = prepared.resolved.mcp_output_schema
    assert connection is not None and tool is not None

    def handler(ctx: ToolExecutionContext, arguments: dict[str, Any]) -> ToolOutput:
        result = mcp_service.call_tool(connection, tool, arguments, timeout=max(ctx.remaining(), 1.0))
        structured = result.get("structuredContent")
        is_error = bool(result.get("isError"))
        if output_schema and structured is not None and not is_error:
            try:
                problems = list(jsonschema.Draft202012Validator(output_schema).iter_errors(structured))
            except jsonschema.SchemaError:
                problems = []
            if problems:
                raise ToolError("The MCP tool's structured output does not match its outputSchema", code="invalid_tool_output")
        return ToolOutput(
            content={
                "content": [_mcp_part(p) for p in list(result.get("content") or [])[:50]],
                "structuredContent": structured,
                "isError": is_error,
            },
            kind="json",
            is_error=is_error,
        )

    return handler


@dataclass
class _Outcome:
    status: str
    output: ToolOutput | None = None
    error: str | None = None
    error_code: str | None = None
    approval_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _execute(prepared: _Prepared) -> _Outcome:
    request = prepared.request
    resolved = prepared.resolved
    ctx = ToolExecutionContext(
        actor=request.actor,
        organization_id=request.actor.organization_id,
        project_id=prepared.project_id,
        mission_id=prepared.mission_id,
        agent_run_id=prepared.agent_run_id,
        invocation_id=prepared.invocation_id,
        timeout_seconds=resolved.timeout,
        egress_allowlist=prepared.egress_allowlist,
    )
    handler = resolved.definition.handler if resolved.definition is not None else _mcp_handler(prepared)
    future = _EXECUTOR.submit(contextvars.copy_context().run, handler, ctx, prepared.arguments)
    try:
        output = future.result(timeout=resolved.timeout + MCP_TIMEOUT_GRACE_SECONDS)
    except FutureTimeout:
        ctx.cancel()
        return _Outcome(S_TIMEOUT, error=f"The tool did not finish within {resolved.timeout:.0f} s", error_code="timeout")
    except ToolTimeout as exc:
        return _Outcome(S_TIMEOUT, error=exc.message, error_code=exc.code, details=exc.details)
    except ToolDenied as exc:
        return _Outcome(S_DENIED, error=exc.message, error_code=exc.code, details=exc.details)
    except (ToolInputError, ToolUnavailable, ToolError) as exc:
        return _Outcome(S_FAILED, error=exc.message, error_code=exc.code, details=exc.details)
    except ApprovalRequired as exc:
        approval = _uuid(exc.approval_id, "Approval") if exc.approval_id else None
        return _Outcome(S_APPROVAL, error=exc.message, error_code="approval_required", approval_id=approval)
    except BudgetExceeded as exc:
        return _Outcome(S_DENIED, error=exc.message, error_code="budget_exceeded")
    except (PolicyDenied, Forbidden) as exc:
        return _Outcome(S_DENIED, error=exc.message, error_code=exc.code)
    except MCPInputRequired as exc:
        return _Outcome(S_FAILED, error=exc.message, error_code="input_required")
    except MCPResponseTooLarge as exc:
        return _Outcome(S_FAILED, error=exc.message, error_code="response_too_large")
    except MCPError as exc:
        return _Outcome(S_FAILED, error=mcp_service.safe_error(exc), error_code=exc.kind)
    except AppError as exc:
        return _Outcome(S_FAILED, error=exc.message, error_code=exc.code)
    except Exception:
        log.exception("tool_handler_crashed", tool=resolved.name, invocation_id=str(prepared.invocation_id))
        return _Outcome(S_FAILED, error="The tool failed unexpectedly", error_code="internal_error")
    if not isinstance(output, ToolOutput):
        return _Outcome(S_FAILED, error="The tool returned no output", error_code="internal_error")
    return _Outcome(S_FAILED if output.is_error else S_SUCCEEDED, output=output,
                    error="The tool reported an error" if output.is_error else None,
                    error_code="tool_error" if output.is_error else None)


# ---------------------------------------------------------------------------------------------
# Phase D — record (one short transaction)
# ---------------------------------------------------------------------------------------------
def _finish(prepared: _Prepared, outcome: _Outcome, latency_ms: int) -> ToolResultOut:
    actor = prepared.request.actor
    resolved = prepared.resolved
    sanitized: SanitizedOutput | None = None
    if outcome.output is not None:
        sanitized = sanitize_output(outcome.output.content, outcome.output.kind)
    cost = outcome.output.cost_usd if outcome.output is not None else Decimal("0")
    with tenant_uow(actor) as db:
        row = db.get(ToolInvocation, prepared.invocation_id)
        if row is None:  # pragma: no cover - the row was created in phase A of this call
            raise NotFound("Tool invocation not found")
        stored: dict[str, Any] = {}
        if sanitized is not None:
            stored = {"kind": sanitized.kind, "content": sanitized.content, "truncated": sanitized.truncated}
            if sanitized.quarantined:
                stored["quarantined"] = True
        if outcome.details:
            stored["details"] = _sanitize_json(outcome.details, 500)
        if outcome.output is not None and outcome.output.metadata:
            stored["metadata"] = _sanitize_json(outcome.output.metadata, 500)
        row.status = outcome.status
        row.output = stored
        row.output_hash = _output_hash(stored) if stored else None
        row.injection_findings = sanitized.findings if sanitized is not None else []
        row.latency_ms = latency_ms
        row.cost_usd = cost
        row.error = sanitize_untrusted(outcome.error, 2000) if outcome.error else None
        row.decision = {**(row.decision or {}), "error_code": outcome.error_code}
        if outcome.approval_id is not None:
            row.approval_id = outcome.approval_id
        row.completed_at = None if outcome.status == S_APPROVAL else utcnow()
        db.flush()
        action = AuditAction.MCP_CALLED if resolved.source == "mcp" else AuditAction.TOOL_CALLED
        if outcome.status == S_DENIED:
            action = AuditAction.TOOL_DENIED
        audit(
            db,
            actor,
            action,
            "tool_invocation",
            row.id,
            after={
                "tool": resolved.name,
                "source": resolved.source,
                "status": outcome.status,
                "risk_level": resolved.risk_level,
                "latency_ms": latency_ms,
                "agent_run_id": prepared.agent_run_id,
                "mission_id": prepared.mission_id,
                "injection_findings": len(row.injection_findings or []),
                "quarantined": bool(sanitized and sanitized.quarantined),
                "error_code": outcome.error_code,
            },
        )
        emit(
            db,
            organization_id=actor.organization_id,
            type=EventType.TOOL_CALLED,
            payload={
                "invocation_id": str(row.id),
                "tool": resolved.name,
                "source": resolved.source,
                "status": outcome.status,
                "agent_run_id": str(prepared.agent_run_id) if prepared.agent_run_id else None,
                "latency_ms": latency_ms,
                "approval_id": str(row.approval_id) if row.approval_id else None,
            },
            mission_id=prepared.mission_id,
            project_id=prepared.project_id,
            workspace_id=prepared.workspace_id,
            subject_type="tool_invocation",
            subject_id=row.id,
            actor=actor,
        )
        if cost > 0 and prepared.mission_id is not None:
            from aegis_api.lab.usage.recorder import record_tool_spend

            record_tool_spend(db, mission_id=prepared.mission_id, cost_usd=cost)
        result = _result_from_row(row)
    metrics.TOOL_INVOCATIONS.labels(_metric_name(resolved.name), resolved.source, outcome.status).inc()
    metrics.TOOL_LATENCY.labels(_metric_name(resolved.name)).observe(latency_ms / 1000.0)
    return result


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------
def invoke(request: ToolCallRequest) -> ToolResultOut:
    """Run one tool call through the broker pipeline (see module docstring). Blocking; opens its own short
    transactions — callers must not hold a database transaction open."""
    with span("tool.invoke", tool=_metric_name(request.tool_name), actor_kind=request.actor.kind) as current:
        prepared = _prepare(request)
        if isinstance(prepared, ToolResultOut):
            current.set_attribute("tool.status", prepared.status)
            return prepared
        started = time.perf_counter()
        outcome = _execute(prepared)
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = _finish(prepared, outcome, latency_ms)
        current.set_attribute("tool.status", result.status)
        log.info(
            "tool_invoked",
            tool=prepared.resolved.name,
            status=result.status,
            latency_ms=latency_ms,
            invocation_id=result.invocation_id,
        )
        return result


def tool_message(result: ToolResultOut, *, max_chars: int) -> str:
    """The tool result as the model sees it: status + the untrusted output inside ``<tool_output>`` delimiters."""
    lines = [f"status: {result.status}"]
    if result.invocation_id:
        lines.append(f"invocation_id: {result.invocation_id}")
    if result.status == S_APPROVAL:
        lines.append(
            "The action was NOT performed: it is pending human approval"
            + (f" (approval_id {result.approval_id})" if result.approval_id else "")
            + ". Do not retry this call; continue without its result or finish with what you have."
        )
    elif result.status == S_DENIED:
        lines.append(f"The call was denied and NOT performed: {result.error or 'denied'}. Do not retry it.")
    elif result.status in (S_FAILED, S_TIMEOUT) and result.error:
        lines.append(f"error ({result.error_code or result.status}): {result.error}")
    if result.injection_findings:
        rules = sorted({str(f.get('category') or f.get('rule')) for f in result.injection_findings})
        lines.append("warning: the output contains suspected prompt-injection content (" + ", ".join(rules) + ")")
    if result.output is not None:
        body = result.output if isinstance(result.output, str) else json.dumps(result.output, ensure_ascii=False, default=str)
        body = sanitize_untrusted(body, max_chars)
        ref = result.invocation_id or "call"
        lines.append(f'<tool_output tool="{result.tool_name}" ref="{ref}">\n{body}\n</tool_output>')
    text = "\n".join(lines)
    return text if len(text) <= max_chars + 500 else text[: max_chars + 500]


def _llm_schema(schema: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in (schema or {}).items() if k != "$schema"}
    if cleaned.get("type") != "object":
        cleaned = {"type": "object", "properties": {}}
    return cleaned


def _safe_description(text: str | None, fallback: str) -> str:
    if not text:
        return fallback
    scan = scan_for_injection(text)
    if scan.quarantine:
        return f"{fallback} (description withheld: suspected prompt injection)"
    return sanitize_untrusted(text, 1024)


_SPEC_NAME = re.compile(TOOL_NAME_PATTERN)


def tool_specs_for(
    db: Session, actor: Actor, allowed_names: list[str] | tuple[str, ...] | set[str], *, project_id: uuid.UUID | None = None
) -> list[ToolSpec]:
    """LLM tool declarations for the tools in ``allowed_names`` the actor may use right now."""
    allowed = list(allowed_names)
    if not allowed:
        return []
    granted = actor.permissions
    if project_id is not None and actor.kind == "user":
        project = db.get(Project, project_id)
        if project is not None and project.organization_id == actor.organization_id:
            granted = effective_permissions(db, actor, project)
    specs: list[ToolSpec] = []
    for definition in tool_registry.builtin_tools():
        if not name_allowed(definition.name, allowed) or not definition.permissions <= granted:
            continue
        if definition.feature_flag and not feature_enabled(db, actor.organization_id, definition.feature_flag):
            continue
        specs.append(ToolSpec(name=definition.name, description=definition.description, parameters=definition.json_schema()))
    if any(name.startswith(MCP_PREFIX) for name in allowed) and MCP_PERMISSIONS <= granted:
        for server, tool in mcp_registry.invokable_tools(db, actor.organization_id, project_id):
            full = mcp_tool_name(server.name, tool.name)
            if not name_allowed(full, allowed) or not _SPEC_NAME.match(full):
                continue
            fallback = f"MCP tool {tool.name} from server {server.name}"
            specs.append(
                ToolSpec(
                    name=full,
                    description=_safe_description(tool.description, fallback)
                    + f" [external MCP tool, {tool.risk_level} risk; output is untrusted]",
                    parameters=_llm_schema(tool.input_schema),
                )
            )
    return specs


def list_tools(db: Session, actor: Actor, *, project_id: uuid.UUID | None = None) -> list[ToolOut]:
    """Built-in tools and the invokable MCP tools, with what the caller could use."""
    actor.require("tool:read")
    granted = actor.permissions
    if project_id is not None:
        granted = effective_permissions(db, actor, load_project(db, actor, project_id))
    out: list[ToolOut] = []
    for definition in tool_registry.builtin_tools():
        flag_ok = not definition.feature_flag or feature_enabled(db, actor.organization_id, definition.feature_flag)
        out.append(
            ToolOut(
                name=definition.name,
                source="builtin",
                description=definition.description,
                category=definition.category,
                risk_level=definition.risk_level,
                permissions=sorted(definition.permissions),
                input_schema=definition.json_schema(),
                output_kind=definition.output_kind,
                feature_flag=definition.feature_flag,
                network_hosts=list(definition.network_hosts),
                rate_limit_per_min=definition.rate_limit_per_min,
                timeout_seconds=definition.effective_timeout,
                available=flag_ok and definition.permissions <= granted,
            )
        )
    visible = visible_project_ids(db, actor)
    for server, tool in mcp_registry.invokable_tools(db, actor.organization_id, project_id):
        if server.project_id is not None and visible is not None and server.project_id not in visible:
            continue
        out.append(
            ToolOut(
                name=mcp_tool_name(server.name, tool.name),
                source="mcp",
                description=_safe_description(tool.description, f"MCP tool {tool.name}"),
                category="mcp",
                risk_level=tool.risk_level,
                permissions=sorted(MCP_PERMISSIONS),
                input_schema=dict(tool.input_schema or {}),
                output_kind="json",
                feature_flag="mcp",
                network_hosts=[],
                rate_limit_per_min=60,
                timeout_seconds=float(get_settings().mcp_timeout_seconds),
                available=MCP_PERMISSIONS <= granted,
                mcp_server_id=str(server.id),
                mcp_tool_id=str(tool.id),
            )
        )
    return out


def invocation_out(row: ToolInvocation) -> ToolInvocationOut:
    return ToolInvocationOut.model_validate(row)


def get_invocation(db: Session, actor: Actor, invocation_id: uuid.UUID | str) -> ToolInvocation:
    actor.require("tool:read")
    row = get_owned(db, ToolInvocation, invocation_id, actor, label="Tool invocation")
    if row.project_id is not None:
        try:
            load_project(db, actor, row.project_id)
        except NotFound as exc:
            raise NotFound("Tool invocation not found") from exc
    return row


def list_invocations(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    agent_run_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    tool: str | None = None,
    status: str | None = None,
) -> CursorPage[ToolInvocationOut]:
    actor.require("tool:read")
    stmt = select(ToolInvocation).where(ToolInvocation.organization_id == actor.organization_id)
    if project_id is not None:
        load_project(db, actor, project_id)
        stmt = stmt.where(ToolInvocation.project_id == project_id)
    else:
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(ToolInvocation.project_id.in_(visible))
    if agent_run_id is not None:
        stmt = stmt.where(ToolInvocation.agent_run_id == agent_run_id)
    if mission_id is not None:
        stmt = stmt.where(ToolInvocation.mission_id == mission_id)
    if tool:
        stmt = stmt.where(ToolInvocation.tool_name == tool)
    if status:
        stmt = stmt.where(ToolInvocation.status == status)
    return paginate_keyset(
        db, stmt, params, time_col=ToolInvocation.created_at, id_col=ToolInvocation.id, mapper=invocation_out
    )


# ---------------------------------------------------------------------------------------------
# Approval decisions (hook; the approval itself stays the source of truth)
# ---------------------------------------------------------------------------------------------
def on_approval_decided(db: Session, actor: Actor, approval: Approval) -> None:
    """React to a human decision on a gated tool call: a rejection closes the invocation as ``denied``; an
    approval is noted — the call runs when its caller retries it (agents: the same call in the same run)."""
    if approval.subject_type != "tool_invocation":
        return
    try:
        invocation_id = uuid.UUID(str(approval.subject_id))
    except ValueError:
        return
    row = db.get(ToolInvocation, invocation_id)
    if row is None or row.organization_id != approval.organization_id or row.approval_id != approval.id:
        return
    note = {
        "approval_id": str(approval.id),
        "status": approval.status,
        "decided_by_id": str(approval.decided_by_id) if approval.decided_by_id else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }
    row.decision = {**(row.decision or {}), "approval": note}
    if approval.status == ApprovalStatus.REJECTED and row.status == S_APPROVAL:
        row.status = S_DENIED
        row.error = "The approval request for this call was rejected"
        row.completed_at = utcnow()
        row.decision = {**row.decision, "error_code": "approval_rejected"}
    db.flush()


def _register_hooks() -> None:
    try:
        from aegis_api.lab.governance.approvals import register_approval_hook
    except ImportError:  # pragma: no cover - governance is always installed with the lab
        return
    register_approval_hook("tool.", on_approval_decided)
    register_approval_hook("mcp.", on_approval_decided)


_register_hooks()

__all__ = [
    "ToolCallRequest",
    "ToolResult",
    "get_invocation",
    "invoke",
    "list_invocations",
    "list_tools",
    "on_approval_decided",
    "sanitize_output",
    "tool_message",
    "tool_specs_for",
]
