"""Agent trace ingestion and tool-action auditing (no chain-of-thought is stored)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound
from aegis_api.models import AgentTrace, AISystem, ToolCall, TraceEvent
from engines.agent.policy_checks import evaluate_tool_call
from engines.common.text import truncate
from engines.evaluation.base import SystemInvocation, ToolCallRecord
from engines.privacy.detectors import redact

SAFE_EVENT_KINDS = {
    "user_input",
    "agent",
    "llm",
    "retrieval",
    "tool_call",
    "tool_result",
    "action",
    "guardrail",
    "final_output",
}


def record_invocation_trace(
    session: Session,
    system: AISystem,
    inv: SystemInvocation,
    *,
    audit_id: uuid.UUID | None,
    name: str,
    source: str = "audit",
) -> AgentTrace:
    """Persist an observable trace from a system invocation (used by audits for agent systems)."""
    tool_policies = (system.config or {}).get("tools", {})
    trace = AgentTrace(
        organization_id=system.organization_id,
        system_id=system.id,
        audit_id=audit_id,
        trace_id=f"tr-{uuid.uuid4().hex[:16]}",
        name=name,
        status="ok" if inv.ok else "error",
        model=inv.model,
        source=source,
        started_at=utcnow(),
        ended_at=utcnow(),
        input_summary=truncate(redact(inv.prompt), 300),
        output_summary=truncate(redact(inv.output), 300),
    )
    session.add(trace)
    session.flush()
    violations = 0
    for seq, event in enumerate(inv.trace):
        if event.kind not in SAFE_EVENT_KINDS:
            continue
        session.add(
            TraceEvent(
                organization_id=system.organization_id,
                trace_pk=trace.id,
                span_id=f"sp-{seq}",
                seq=seq,
                kind=event.kind,
                name=event.name,
                timestamp=utcnow(),
                duration_ms=event.duration_ms,
                model=event.attributes.get("model") if event.attributes else None,
                attributes=event.attributes,
                input_preview=truncate(redact(event.input or ""), 300) if event.input else None,
                output_preview=truncate(redact(event.output or ""), 300) if event.output else None,
            )
        )
    for call in inv.tool_calls:
        decision = evaluate_tool_call(call, tool_policies.get(call.name, {}))
        if not decision["allowed"]:
            violations += 1
        session.add(
            ToolCall(
                organization_id=system.organization_id,
                trace_pk=trace.id,
                tool_name=call.name,
                arguments={k: redact(str(v)) if isinstance(v, str) else v for k, v in call.arguments.items()},
                result_metadata={"status": (call.result or {}).get("status", "ok")} if call.result else {},
                authorization=decision["authorization"],
                requires_human_approval=decision["requires_human_approval"],
                human_approved=decision["human_approved"],
                sensitive_data_detected=decision["sensitive_data_detected"],
                sensitive_types=decision["sensitive_types"],
                allowed=decision["allowed"],
                risk_level=decision["risk_level"],
                violations=decision["violations"],
            )
        )
    trace.span_count = len(inv.trace)
    trace.tool_call_count = len(inv.tool_calls)
    trace.violation_count = violations
    trace.risk_level = "high" if violations else "low"
    return trace


def ingest_trace(session: Session, organization_id: uuid.UUID, data: Any) -> AgentTrace:
    system = session.get(AISystem, uuid.UUID(data.system_id))
    if system is None or system.organization_id != organization_id:
        raise NotFound("System not found")
    tool_policies = (system.config or {}).get("tools", {})
    trace = AgentTrace(
        organization_id=organization_id,
        system_id=system.id,
        trace_id=data.trace_id or f"tr-{uuid.uuid4().hex[:16]}",
        name=data.name,
        status="ok",
        model=system.model_name,
        source="sdk",
        started_at=utcnow(),
        ended_at=utcnow(),
    )
    session.add(trace)
    session.flush()
    tool_calls = 0
    violations = 0
    for seq, raw in enumerate(data.events):
        kind = raw.get("kind", "agent")
        if kind not in SAFE_EVENT_KINDS:
            continue
        session.add(
            TraceEvent(
                organization_id=organization_id,
                trace_pk=trace.id,
                span_id=raw.get("span_id", f"sp-{seq}"),
                seq=seq,
                kind=kind,
                name=raw.get("name", kind),
                timestamp=utcnow(),
                duration_ms=raw.get("duration_ms"),
                input_preview=truncate(redact(str(raw.get("input", ""))), 300) or None,
                output_preview=truncate(redact(str(raw.get("output", ""))), 300) or None,
                attributes=raw.get("attributes", {}),
            )
        )
        if kind == "tool_call":
            call = ToolCallRecord(
                name=raw.get("name", "tool"),
                arguments=raw.get("arguments", {}),
                approved_by_human=raw.get("approved", False),
            )
            decision = evaluate_tool_call(call, tool_policies.get(call.name, {}))
            tool_calls += 1
            if not decision["allowed"]:
                violations += 1
            session.add(
                ToolCall(
                    organization_id=organization_id,
                    trace_pk=trace.id,
                    tool_name=call.name,
                    arguments={k: redact(str(v)) if isinstance(v, str) else v for k, v in call.arguments.items()},
                    authorization=decision["authorization"],
                    requires_human_approval=decision["requires_human_approval"],
                    human_approved=decision["human_approved"],
                    sensitive_data_detected=decision["sensitive_data_detected"],
                    sensitive_types=decision["sensitive_types"],
                    allowed=decision["allowed"],
                    risk_level=decision["risk_level"],
                    violations=decision["violations"],
                )
            )
    trace.span_count = len(data.events)
    trace.tool_call_count = tool_calls
    trace.violation_count = violations
    trace.risk_level = "high" if violations else "low"
    return trace


def get_trace_detail(
    session: Session, trace_id: uuid.UUID, organization_id: uuid.UUID
) -> tuple[AgentTrace, list[TraceEvent], list[ToolCall]]:
    trace = session.get(AgentTrace, trace_id)
    if trace is None or trace.organization_id != organization_id:
        raise NotFound("Trace not found")
    events = session.scalars(select(TraceEvent).where(TraceEvent.trace_pk == trace.id).order_by(TraceEvent.seq)).all()
    tools = session.scalars(select(ToolCall).where(ToolCall.trace_pk == trace.id)).all()
    return trace, list(events), list(tools)


def ingest_opentelemetry_trace(
    session: Session,
    organization_id: uuid.UUID,
    system_id: uuid.UUID,
    otel_payload: dict[str, Any],
) -> AgentTrace:
    """Ingest OpenTelemetry-formatted traces (from LangChain, LlamaIndex, CrewAI, etc.)."""
    system = session.get(AISystem, system_id)
    if system is None or system.organization_id != organization_id:
        raise NotFound("System not found")

    tool_policies = (system.config or {}).get("tools", {})
    spans: list[dict[str, Any]] = []

    # Parse OTLP standard resourceSpans or direct span list
    if "resourceSpans" in otel_payload:
        for rs in otel_payload.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    # Flatten OTel attribute key-value pairs
                    attrs: dict[str, Any] = {}
                    for a in s.get("attributes", []):
                        k = a.get("key", "")
                        v_obj = a.get("value", {})
                        val = next(iter(v_obj.values())) if isinstance(v_obj, dict) and v_obj else str(v_obj)
                        attrs[k] = val
                    spans.append(
                        {
                            "trace_id": s.get("traceId", s.get("trace_id")),
                            "span_id": s.get("spanId", s.get("span_id")),
                            "name": s.get("name", "span"),
                            "attributes": attrs,
                        }
                    )
    elif "spans" in otel_payload:
        spans = otel_payload.get("spans", [])
    elif isinstance(otel_payload, list):
        spans = otel_payload

    trace_id = spans[0].get("trace_id") if spans else None
    trace = AgentTrace(
        organization_id=organization_id,
        system_id=system.id,
        trace_id=trace_id or f"tr-{uuid.uuid4().hex[:16]}",
        name=spans[0].get("name", "OTel Agent Trace") if spans else "OTel Trace",
        status="ok",
        model=system.model_name,
        source="opentelemetry",
        started_at=utcnow(),
        ended_at=utcnow(),
    )
    session.add(trace)
    session.flush()

    tool_calls = 0
    violations = 0
    for seq, span in enumerate(spans):
        attrs = span.get("attributes", {})
        span_name = span.get("name", "span").lower()
        is_tool = "tool" in span_name or "tool.name" in attrs

        kind = "tool_call" if is_tool else "llm" if ("llm" in span_name or "gen_ai" in str(attrs)) else "agent"
        session.add(
            TraceEvent(
                organization_id=organization_id,
                trace_pk=trace.id,
                span_id=span.get("span_id", f"sp-{seq}"),
                seq=seq,
                kind=kind,
                name=span.get("name", kind),
                timestamp=utcnow(),
                attributes=attrs,
            )
        )

        if is_tool:
            tool_name = attrs.get("tool.name", span.get("name", "tool"))
            tool_args = attrs.get("tool.arguments", attrs.get("tool.input", {}))
            if isinstance(tool_args, str):
                tool_args = {"input": tool_args}

            call = ToolCallRecord(
                name=tool_name,
                arguments=tool_args if isinstance(tool_args, dict) else {},
            )
            decision = evaluate_tool_call(call, tool_policies.get(tool_name, {}))
            tool_calls += 1
            if not decision["allowed"]:
                violations += 1

            session.add(
                ToolCall(
                    organization_id=organization_id,
                    trace_pk=trace.id,
                    tool_name=tool_name,
                    arguments={k: redact(str(v)) if isinstance(v, str) else v for k, v in call.arguments.items()},
                    authorization=decision["authorization"],
                    requires_human_approval=decision["requires_human_approval"],
                    human_approved=decision["human_approved"],
                    sensitive_data_detected=decision["sensitive_data_detected"],
                    sensitive_types=decision["sensitive_types"],
                    allowed=decision["allowed"],
                    risk_level=decision["risk_level"],
                    violations=decision["violations"],
                )
            )

    trace.span_count = len(spans)
    trace.tool_call_count = tool_calls
    trace.violation_count = violations
    trace.risk_level = "high" if violations else "low"
    return trace
