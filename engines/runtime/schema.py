"""The common runtime event schema (``aegis.runtime.v1``) and deterministic normalization.

Every adapter (SDK, MCP, OpenTelemetry, LangGraph, CrewAI, plain HTTP) maps its telemetry to this one shape so
policies are written once and evaluated identically regardless of the agent framework.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from engines.privacy.detectors import DEFAULT_DETECTOR, redact

SCHEMA_VERSION = "aegis.runtime.v1"

EVENT_TYPES: dict[str, str] = {
    "agent.start": "An agent run began",
    "agent.step": "An agent planning/execution step",
    "agent.stop": "An agent run ended",
    "tool.call": "The agent invoked a tool",
    "tool.result": "A tool returned a result",
    "model.request": "A model request was sent",
    "model.response": "A model response was received",
    "policy.check": "An explicit policy check requested by the agent",
    "permission.request": "The agent requested a permission",
    "network.request": "An outbound network request",
    "file.read": "A file was read",
    "file.write": "A file was written",
    "database.query": "A database query was executed",
    "mcp.tool.call": "An MCP tool was invoked",
    "human.approval": "A human approved or rejected an action",
}
ALIASES = {"external.network.request": "network.request", "mcp.call": "mcp.tool.call"}
SOURCES = {"sdk", "mcp", "otel", "langgraph", "crewai", "http", "browser", "custom"}
CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
SENSITIVE_KEY = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)", re.I
)
MAX_STRING = 2000
MAX_DEPTH = 6


class RuntimeEventIn(BaseModel):
    """Inbound runtime event. Unknown payload keys are kept (redacted); the envelope is strict."""

    event_id: str | None = Field(default=None, max_length=80)
    event_type: str = Field(max_length=48)
    system_id: str
    timestamp: datetime | None = None
    source: str = "sdk"
    environment: str | None = Field(default=None, max_length=24)
    agent: str | None = Field(default=None, max_length=160)
    actor: str | None = Field(default=None, max_length=160)
    session_id: str | None = Field(default=None, max_length=120)
    trace_id: str | None = Field(default=None, max_length=80)
    span_id: str | None = Field(default=None, max_length=64)
    parent_span_id: str | None = Field(default=None, max_length=64)
    tool: str | None = Field(default=None, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        value = ALIASES.get(value, value)
        if value not in EVENT_TYPES:
            raise ValueError(f"unknown event_type '{value}' (allowed: {sorted(EVENT_TYPES)})")
        return value

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        return value if value in SOURCES else "custom"


def _text_fields(payload: Any, depth: int = 0) -> list[str]:
    if depth > MAX_DEPTH:
        return []
    if isinstance(payload, str):
        return [payload[:MAX_STRING]]
    if isinstance(payload, dict):
        return [t for v in payload.values() for t in _text_fields(v, depth + 1)]
    if isinstance(payload, list):
        return [t for v in payload[:50] for t in _text_fields(v, depth + 1)]
    return []


def redact_payload(payload: Any, depth: int = 0) -> Any:
    """Mask credentials by key, mask detected PII/secrets in strings, bound size and depth."""
    if depth > MAX_DEPTH:
        return "[truncated]"
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for i, (key, value) in enumerate(payload.items()):
            if i >= 100:
                out["_truncated_keys"] = len(payload) - 100
                break
            k = str(key)[:80]
            out[k] = "[REDACTED]" if SENSITIVE_KEY.search(k) else redact_payload(value, depth + 1)
        return out
    if isinstance(payload, list):
        return [redact_payload(v, depth + 1) for v in payload[:50]]
    if isinstance(payload, str):
        return redact(payload[:MAX_STRING])
    if isinstance(payload, int | float | bool) or payload is None:
        return payload
    return str(payload)[:200]


def _host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value if "://" in value else f"https://{value}")
    except ValueError:
        return None
    return (parts.hostname or "").lower() or None


def derive_signals(event: RuntimeEventIn, *, default_classification: str | None = None) -> dict[str, Any]:
    """Deterministic facts extracted from the raw (unredacted) event — computed before redaction so
    policies can match on what really happened, while only redacted values are ever stored."""
    payload = event.payload or {}
    texts = _text_fields(payload)
    matches = [m for text in texts for m in DEFAULT_DETECTOR.detect(text)]
    pii = sorted({m.type for m in matches if m.category != "secret"})
    secrets = sorted({m.type for m in matches if m.category == "secret"})
    url = payload.get("url") or payload.get("endpoint") or payload.get("host")
    host = _host(str(url)) if url else None
    recipients = payload.get("to") or payload.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    recipient_domains = sorted(
        {str(r).rsplit("@", 1)[-1].lower() for r in recipients if isinstance(r, str) and "@" in r}
    )
    classification = str(payload.get("data_classification") or default_classification or "internal").lower()
    statement = str(payload.get("query") or payload.get("statement") or "").strip()
    statement_kind = statement.split(None, 1)[0].lower() if statement else None
    return {
        "host": host,
        "recipient_domains": recipient_domains,
        "pii_types": pii,
        "secret_types": secrets,
        "contains_pii": bool(pii),
        "contains_secret": bool(secrets),
        "data_classification": classification if classification in CLASSIFICATION_RANK else "internal",
        "statement_kind": statement_kind,
        "path": payload.get("path"),
        "approved": bool(payload.get("approved") or payload.get("human_approved")),
    }


def normalize(event: RuntimeEventIn, *, default_classification: str | None = None) -> dict[str, Any]:
    """Return the stored representation: envelope, redacted payload and derived signals."""
    occurred = event.timestamp or datetime.now(UTC)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    tool = event.tool or (event.payload or {}).get("tool") or (event.payload or {}).get("name")
    return {
        "event_id": event.event_id or uuid.uuid4().hex,
        "schema_version": SCHEMA_VERSION,
        "event_type": event.event_type,
        "source": event.source,
        "environment": event.environment,
        "agent_name": event.agent,
        "actor": event.actor,
        "session_id": event.session_id,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "tool_name": str(tool)[:160]
        if tool and event.event_type in ("tool.call", "tool.result", "mcp.tool.call")
        else None,
        "occurred_at": occurred,
        "payload": redact_payload(event.payload or {}),
        "signals": derive_signals(event, default_classification=default_classification),
    }
