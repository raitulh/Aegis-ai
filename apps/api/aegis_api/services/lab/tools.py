"""ToolBroker: the only way an agent can act on the world.

For every call: (1) the tool must exist and be allowed for the agent role *and* the mission; (2) arguments are
validated against the tool's JSON schema; (3) the policy engine decides (high-risk and unregistered MCP tools
need approval/are denied); (4) the tool runs with a timeout and bounded output; (5) output is sanitized,
scored for prompt injection and returned to the model fenced as untrusted data; (6) the call is recorded in
``lab.tool_calls`` (redacted arguments, result hash, latency, decision) and emitted as a TOOL_CALLED event.

Agents never receive credentials: tools that need secrets resolve them server-side.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import jsonschema
import structlog
from sqlalchemy import select

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.infrastructure.llm.schemas import ToolDefinition
from aegis_api.infrastructure.observability import metrics
from aegis_api.logging import redact_data
from aegis_api.models.lab import Approval, Artifact, ArtifactVersion, Dataset, LabToolCall, Mission
from aegis_api.services.lab import approvals, events, knowledge
from aegis_api.services.lab import memory as memory_service
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.common import Actor, sha256_bytes, sha256_json
from engines.lab.enums import ApprovalStatus, LabEventType
from engines.lab.security.prompt_injection import detect, sanitize

log = structlog.get_logger("aegis.lab.tools")

MAX_RESULT_CHARS = 12_000
MAX_CALLS_PER_RUN = 24
APPROVAL_REUSE_WINDOW = timedelta(hours=24)


class ToolFailure(Exception):
    pass


@dataclass
class ToolContext:
    organization_id: uuid.UUID
    project_id: uuid.UUID | None
    mission_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    role: str
    actor: Actor
    allowed_tools: frozenset[str]
    trace_id: str | None = None
    calls: int = 0
    source_ids: list[str] = field(default_factory=list)


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    injection_score: float = 0.0
    record_id: uuid.UUID | None = None
    decision: str = "allow"
    approval_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: str
    handler: Callable[[ToolContext, dict[str, Any]], tuple[str, dict[str, Any]]]
    source: str = "builtin"
    network: bool = False

    def definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.description, parameters=self.parameters)


# --- built-in tools ----------------------------------------------------------------------------------------


def _paper_search(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from aegis_api.infrastructure.search import SearchError, search_providers

    query, limit = str(args["query"]), int(args.get("limit", 5))
    hits: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, provider in search_providers().items():
        try:
            hits.extend(h.to_dict() for h in provider.search(query, limit=limit))
        except SearchError as exc:
            errors.append(f"{name}: {exc}")
    if not hits and errors:
        raise ToolFailure("; ".join(errors))
    if ctx.project_id is None:
        raise ToolFailure("paper_search needs a project context to record sources")
    with session_scope(ctx.organization_id) as db:
        sources = knowledge.record_sources(
            db, organization_id=ctx.organization_id, project_id=ctx.project_id, hits=hits[: limit * 2]
        )
        rows = [knowledge.source_dict(s) for s in sources]
    ctx.source_ids.extend(r["id"] for r in rows)
    lines = [
        f"[source:{r['id']}] {r['title']} ({r['source_type']}; {r.get('publication_date') or 'n.d.'}; "
        f"{'doi:' + r['doi'] if r.get('doi') else r.get('url') or ''})\n{(r.get('snippet') or '')[:800]}"
        for r in rows
    ]
    return "\n\n".join(lines) or "No results.", {"source_ids": [r["id"] for r in rows], "errors": errors}


def _url_fetch(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from aegis_api.infrastructure.search import SearchError, fetch_url
    from engines.lab.knowledge.parsing import ParseError, detect_type, parse

    try:
        fetched = fetch_url(str(args["url"]), max_bytes=1_000_000)
        doc_type = detect_type(fetched.final_url, fetched.data, fetched.content_type)
        text = parse(doc_type, fetched.data).text
    except (SearchError, ParseError, ValueError) as exc:
        raise ToolFailure(str(exc)) from exc
    source_id = None
    if ctx.project_id is not None:
        with session_scope(ctx.organization_id) as db:
            (src,) = knowledge.record_sources(
                db,
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                hits=[
                    {
                        "source_type": "web",
                        "url": fetched.final_url,
                        "title": text.strip().splitlines()[0][:300] if text.strip() else fetched.final_url,
                        "snippet": text[:2000],
                        "checksum": fetched.sha256,
                        "retrieved_at": fetched.retrieved_at,
                    }
                ],
            )
            source_id = str(src.id)
            ctx.source_ids.append(source_id)
    return f"[source:{source_id}] {fetched.final_url}\n{text[:8000]}", {
        "source_id": source_id,
        "sha256": fetched.sha256,
        "truncated": fetched.truncated,
    }


def _memory_search(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    query = str(args["query"])
    vectors, model = knowledge.embed_texts(ctx.organization_id, [query])
    with session_scope(ctx.organization_id) as db:
        results = memory_service.search(
            db,
            ctx.organization_id,
            query,
            query_vector=vectors[0] if vectors else None,
            embedding_model=model,
            project_id=ctx.project_id,
            mission_id=ctx.mission_id,
            limit=int(args.get("limit", 5)),
        )
    lines = [
        f"[memory:{m['id']}] ({m['scope']}/{m['category']}, confidence {m['confidence']:.2f}) {m['content'][:600]}"
        for m in results
    ]
    return "\n".join(lines) or "No matching memories.", {"memory_ids": [m["id"] for m in results]}


def _file_search(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    query = str(args["query"])
    vectors, model = knowledge.embed_texts(ctx.organization_id, [query])
    with session_scope(ctx.organization_id) as db:
        results = knowledge.search_chunks(
            db,
            ctx.organization_id,
            query,
            project_ids=[ctx.project_id] if ctx.project_id else [],
            query_vector=vectors[0] if vectors else None,
            embedding_model=model,
            limit=int(args.get("limit", 5)),
        )
    lines = [
        f"[document:{r['document_id']}#{r['chunk_index']}] {r['document_title']}\n{r['text'][:900]}" for r in results
    ]
    return "\n\n".join(lines) or "No matching documents.", {"chunk_ids": [r["chunk_id"] for r in results]}


def _dataset_search(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    query = str(args.get("query") or "")
    with session_scope(ctx.organization_id) as db:
        stmt = select(Dataset).where(Dataset.organization_id == ctx.organization_id)
        if ctx.project_id:
            stmt = stmt.where(Dataset.project_id == ctx.project_id)
        if query:
            stmt = stmt.where(Dataset.name.ilike(f"%{query[:80]}%"))
        rows = [
            {
                "id": str(d.id),
                "name": d.name,
                "description": d.description,
                "current_version_id": str(d.current_version_id) if d.current_version_id else None,
                "license": d.license,
            }
            for d in db.scalars(stmt.limit(20)).all()
        ]
    return json.dumps(rows, indent=1)[:MAX_RESULT_CHARS], {"dataset_ids": [r["id"] for r in rows]}


def _object_storage(ctx: ToolContext, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from aegis_api.infrastructure.storage import get_storage

    try:
        artifact_id = uuid.UUID(str(args["artifact_id"]))
    except ValueError as exc:
        raise ToolFailure("invalid artifact id") from exc
    with session_scope(ctx.organization_id) as db:
        artifact = db.get(Artifact, artifact_id)
        if (
            artifact is None
            or artifact.organization_id != ctx.organization_id
            or (ctx.project_id is not None and artifact.project_id not in (None, ctx.project_id))
        ):
            raise ToolFailure("artifact not found")
        version = db.get(ArtifactVersion, artifact.current_version_id) if artifact.current_version_id else None
        if version is None or version.purged_at is not None:
            raise ToolFailure("artifact has no available content")
        if not (version.content_type.startswith("text/") or version.content_type in ("application/json",)):
            raise ToolFailure(f"artifact content type {version.content_type} is not readable as text")
        key, name = version.storage_key, artifact.name
    data = get_storage().get_bytes(key, max_bytes=256 * 1024)
    return f"[artifact:{artifact_id}] {name}\n" + data[:64_000].decode("utf-8", errors="replace"), {
        "artifact_id": str(artifact_id)
    }


def _q(max_len: int = 300) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": max_len}


BUILTIN_TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "paper_search",
            "Search scholarly literature (arXiv, Crossref). Results are recorded as citable sources.",
            {
                "type": "object",
                "properties": {"query": _q(), "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "low",
            _paper_search,
            network=True,
        ),
        ToolSpec(
            "url_fetch",
            "Fetch a public web page or document (SSRF-protected, size-limited). Content is untrusted data.",
            {
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2000}},
                "required": ["url"],
                "additionalProperties": False,
            },
            "medium",
            _url_fetch,
            network=True,
        ),
        ToolSpec(
            "memory_search",
            "Search the organization's scientific memory (active memories visible to this mission).",
            {
                "type": "object",
                "properties": {"query": _q(), "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "low",
            _memory_search,
        ),
        ToolSpec(
            "file_search",
            "Search ingested project documents (hybrid keyword + vector retrieval).",
            {
                "type": "object",
                "properties": {"query": _q(), "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "low",
            _file_search,
        ),
        ToolSpec(
            "dataset_search",
            "List registered datasets in the project (metadata only).",
            {
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 120}},
                "additionalProperties": False,
            },
            "low",
            _dataset_search,
        ),
        ToolSpec(
            "object_storage",
            "Read a small text artifact (read-only, project-scoped).",
            {
                "type": "object",
                "properties": {"artifact_id": {"type": "string", "minLength": 32, "maxLength": 36}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            "low",
            _object_storage,
        ),
    )
}


class ToolBroker:
    def tools_for(self, ctx: ToolContext) -> list[ToolSpec]:
        specs = [BUILTIN_TOOLS[n] for n in sorted(ctx.allowed_tools) if n in BUILTIN_TOOLS]
        if any(n.startswith("mcp:") for n in ctx.allowed_tools):
            specs.extend(self._mcp_specs(ctx))
        return specs

    def _mcp_specs(self, ctx: ToolContext) -> list[ToolSpec]:
        from aegis_api.services.lab import mcp

        out: list[ToolSpec] = []
        with session_scope(ctx.organization_id) as db:
            pairs = mcp.available_tools(db, ctx.organization_id, ctx.project_id)
            for server, tool in pairs:
                name = f"mcp:{server.name}.{tool.name}"[:64]
                if name not in ctx.allowed_tools and f"mcp:{server.name}.*" not in ctx.allowed_tools:
                    continue
                server_id, tool_name = server.id, tool.name

                def handler(
                    c: ToolContext, args: dict[str, Any], sid: uuid.UUID = server_id, tn: str = tool_name
                ) -> tuple[str, dict[str, Any]]:
                    from aegis_api.infrastructure.mcp.client import MCPError

                    try:
                        result = mcp.call(c.organization_id, sid, tn, args)
                    except MCPError as exc:
                        raise ToolFailure(str(exc)) from exc
                    if result.is_error:
                        raise ToolFailure(result.text[:2000] or "MCP tool reported an error")
                    return result.text, {"structured": result.structured, "content_types": result.content_types}

                out.append(
                    ToolSpec(
                        name=name,
                        description=(tool.description or "")[:1000],
                        parameters=tool.input_schema or {"type": "object"},
                        risk=tool.risk_level,
                        handler=handler,
                        source="mcp",
                        network=True,
                    )
                )
        return out

    def invoke(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        spec = next((s for s in self.tools_for(ctx) if s.name == name), None)
        if spec is None:
            return self._finish(
                ctx, name, arguments, None, "denied", "deny", f"tool '{name}' is not available", started
            )
        ctx.calls += 1
        if ctx.calls > MAX_CALLS_PER_RUN:
            return self._finish(ctx, name, arguments, spec, "denied", "deny", "tool call limit reached", started)
        try:
            jsonschema.Draft202012Validator(spec.parameters).validate(arguments)
        except jsonschema.ValidationError as exc:
            return self._finish(
                ctx, name, arguments, spec, "invalid", "allow", f"invalid arguments: {exc.message}", started
            )
        args_hash = sha256_json(arguments)
        resource_id = f"{name}:{args_hash[:40]}"
        with session_scope(ctx.organization_id) as db:
            mission = db.get(Mission, ctx.mission_id) if ctx.mission_id else None
            result = lab_policy.evaluate(
                db,
                organization_id=ctx.organization_id,
                action="tool.call",
                facts=lab_policy.base_facts(
                    db,
                    ctx.organization_id,
                    ctx.actor,
                    mission=mission,
                    extra={
                        "tool": {
                            "name": name,
                            "risk": spec.risk,
                            "source": spec.source,
                            "registered": True,
                            "network": spec.network,
                        },
                        "execution": {"network": "none", "secrets_requested": False},
                    },
                ),
                actor=ctx.actor,
                resource_type="tool_call",
                resource_id=resource_id,
            )
            approval_id: str | None = None
            if result.needs_approval:
                prior = db.scalar(
                    select(Approval).where(
                        Approval.organization_id == ctx.organization_id,
                        Approval.resource_type == "tool_call",
                        Approval.resource_id == resource_id,
                        Approval.status == ApprovalStatus.APPROVED,
                        Approval.decided_at >= utcnow() - APPROVAL_REUSE_WINDOW,
                    )
                )
                if prior is None:
                    approval = approvals.request(
                        db,
                        organization_id=ctx.organization_id,
                        kind=result.approval_kinds[0] if result.approval_kinds else "high_risk_tool",
                        resource_type="tool_call",
                        resource_id=resource_id,
                        title=f"Tool call {name} requested by {ctx.role} agent",
                        requester=ctx.actor,
                        details={
                            "tool": name,
                            "arguments": redact_data(arguments),
                            "agent_run_id": str(ctx.agent_run_id),
                        },
                        policy_result=result,
                        project_id=ctx.project_id,
                        mission_id=ctx.mission_id,
                    )
                    approval_id = str(approval.id)
        if result.denied:
            return self._finish(ctx, name, arguments, spec, "denied", "deny", "; ".join(result.reasons), started)
        if approval_id is not None:
            outcome = self._finish(
                ctx,
                name,
                arguments,
                spec,
                "approval_required",
                "require_approval",
                f"This call requires human approval (approval {approval_id}). Continue without it.",
                started,
                approval_id=approval_id,
            )
            return outcome
        try:
            text, data = spec.handler(ctx, arguments)
        except ToolFailure as exc:
            return self._finish(ctx, name, arguments, spec, "failed", str(result.decision), str(exc), started)
        except Exception as exc:
            log.exception("tool_crashed", tool=name)
            return self._finish(
                ctx, name, arguments, spec, "failed", str(result.decision), f"tool error: {type(exc).__name__}", started
            )
        return self._finish(ctx, name, arguments, spec, "succeeded", str(result.decision), text, started, data=data)

    def _finish(
        self,
        ctx: ToolContext,
        name: str,
        arguments: dict[str, Any],
        spec: ToolSpec | None,
        status: str,
        decision: str,
        text: str,
        started: float,
        *,
        data: dict[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> ToolOutcome:
        clean, truncated = sanitize(text, max_chars=MAX_RESULT_CHARS)
        injection = detect(clean) if status == "succeeded" else None
        latency = int((time.perf_counter() - started) * 1000)
        raw = clean.encode()
        metrics.TOOL_CALLS.labels(tool=name if spec and spec.source == "builtin" else "mcp", status=status).inc()
        record_id: uuid.UUID | None = None
        try:
            with session_scope(ctx.organization_id) as db:
                record = LabToolCall(
                    organization_id=ctx.organization_id,
                    project_id=ctx.project_id,
                    mission_id=ctx.mission_id,
                    agent_run_id=ctx.agent_run_id,
                    tool_id=name[:120],
                    status=status,
                    risk_level=spec.risk if spec else "unknown",
                    policy_decision=decision,
                    arguments=redact_data(arguments),
                    result_summary=clean[:500] if status == "succeeded" else None,
                    result_sha256=sha256_bytes(raw) if status == "succeeded" else None,
                    result_bytes=len(raw) if status == "succeeded" else None,
                    injection_score=injection.score if injection else 0.0,
                    latency_ms=latency,
                    approval_id=uuid.UUID(approval_id) if approval_id else None,
                    error=None if status == "succeeded" else clean[:2000],
                    actor=ctx.actor.label[:160],
                )
                db.add(record)
                db.flush()
                record_id = record.id
                if ctx.mission_id:
                    events.emit(
                        db,
                        organization_id=ctx.organization_id,
                        mission_id=ctx.mission_id,
                        project_id=ctx.project_id,
                        event_type=LabEventType.TOOL_CALLED,
                        message=f"{ctx.role} called {name}: {status}",
                        data={
                            "tool": name,
                            "status": status,
                            "agent_run_id": str(ctx.agent_run_id) if ctx.agent_run_id else None,
                            "latency_ms": latency,
                            "injection_score": injection.score if injection else 0.0,
                        },
                        actor=ctx.actor,
                        trace_id=ctx.trace_id,
                    )
        except Exception:
            log.exception("tool_call_record_failed", tool=name)
        return ToolOutcome(
            name=name,
            ok=status == "succeeded",
            text=clean + ("\n[output truncated]" if truncated else ""),
            data=data or {},
            injection_score=injection.score if injection else 0.0,
            record_id=record_id,
            decision=decision,
            approval_id=approval_id,
        )


_BROKER = ToolBroker()


def get_broker() -> ToolBroker:
    return _BROKER
