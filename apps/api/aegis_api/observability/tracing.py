"""W3C Trace Context (``traceparent``) propagation.

Every request gets a trace id: taken from a valid incoming ``traceparent`` header (so Aegis joins the
caller's distributed trace) or generated. The trace id is bound to every log line of the request and echoed
back in ``traceparent``. This is the seam for an OpenTelemetry exporter: spans can be added without changing
call sites because the identifiers already flow through logs, jobs and outbound calls.
"""

from __future__ import annotations

import re
import secrets

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    if not header:
        return None
    match = _TRACEPARENT.match(header.strip().lower())
    if not match:
        return None
    trace_id, parent_id, _ = match.groups()
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return None
    return trace_id, parent_id


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"
