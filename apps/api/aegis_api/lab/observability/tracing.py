"""OpenTelemetry tracing helpers.

The OTel *API* is always importable; without SDK configuration (``OTEL_EXPORTER_OTLP_ENDPOINT`` unset)
spans are no-ops, so instrumented code costs almost nothing in development and tests.
``aegis_api.lab.observability.telemetry.configure_tracing()`` installs the SDK + OTLP exporter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

TRACER_NAME = "aegis.lab"


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Start a span; attributes with ``None`` values are skipped. Never pass secrets or document text."""
    with tracer().start_as_current_span(name) as s:
        for key, value in attributes.items():
            if value is None:
                continue
            s.set_attribute(key, value if isinstance(value, bool | int | float | str) else str(value))
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            raise


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")
