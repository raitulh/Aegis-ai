"""OpenTelemetry tracing + trace-aware logging.

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, spans are exported over OTLP/HTTP and FastAPI, SQLAlchemy and HTTPX
are auto-instrumented. Without it, the OpenTelemetry API is a no-op but W3C ``traceparent`` headers are still
honoured so every request log line carries a ``trace_id`` for correlation.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.engine import Engine

from aegis_api.config import get_settings
from aegis_api.infrastructure.observability import metrics

log = structlog.get_logger("aegis.telemetry")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
_configured = False


def configure_tracing(app: Any) -> None:
    global _configured
    settings = get_settings()
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": settings.otel_service_name, "deployment.environment": settings.environment}
            ),
            sampler=ParentBasedTraceIdRatio(settings.otel_traces_sampler_ratio),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")
            )
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
        HTTPXClientInstrumentor().instrument()
        from aegis_api.db.session import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine())
        _configured = True
        log.info("tracing_enabled", endpoint=settings.otel_exporter_otlp_endpoint)
    except Exception as exc:  # observability must never take the service down
        log.warning("tracing_setup_failed", error=type(exc).__name__)


def current_trace_ids(headers: dict[str, str]) -> tuple[str, str]:
    """(trace_id, span_id): active OTel span if any, else incoming traceparent, else newly generated."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
    except Exception:  # OpenTelemetry API unavailable/misconfigured → fall back to traceparent
        log.debug("otel_context_unavailable")
    match = _TRACEPARENT.match(headers.get("traceparent", ""))
    if match and match.group(1) != "0" * 32:
        return match.group(1), secrets.token_hex(8)
    return secrets.token_hex(16), secrets.token_hex(8)


def tracer(name: str) -> Any:
    from opentelemetry import trace

    return trace.get_tracer(name)


def instrument_engine_metrics(engine: Engine) -> None:
    """Record DB statement latency into Prometheus (bounded label: SQL verb)."""
    if getattr(engine, "_aegis_metrics", False):
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        conn.info.setdefault("_aegis_t", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        stack = conn.info.get("_aegis_t") or []
        if stack:
            verb = (statement.lstrip().split(" ", 1)[0] or "other").lower()[:12]
            metrics.DB_LATENCY.labels(
                operation=verb if verb in ("select", "insert", "update", "delete", "with") else "other"
            ).observe(time.perf_counter() - stack.pop())

    engine._aegis_metrics = True  # type: ignore[attr-defined]
