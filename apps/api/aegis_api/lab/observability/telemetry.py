"""OpenTelemetry SDK configuration (traces).

``configure_telemetry()`` installs the SDK only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set: a
``TracerProvider`` with resource attributes (service name/version, deployment environment), a
parent-based ratio sampler and a batching OTLP/HTTP exporter. Without an endpoint the OTel API keeps its
no-op provider, so instrumented code (``tracing.span``, the HTTP middleware, DB spans) costs almost
nothing. The W3C ``traceparent`` propagator is always installed so incoming trace ids are honoured and
echoed back (``X-Trace-ID``) even when this process does not export spans.

Tests call ``install_test_tracing()`` to record spans in memory and ``uninstall_test_tracing()`` to
restore the no-op provider.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.telemetry")

_lock = threading.RLock()
_provider: TracerProvider | None = None  # installed by configure_telemetry (OTLP export)
_test_provider: TracerProvider | None = None  # installed by install_test_tracing
_test_exporter: InMemorySpanExporter | None = None


def _clamp_ratio(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def traces_endpoint(endpoint: str) -> str:
    """OTLP/HTTP traces URL for a collector base URL (``http://collector:4318`` → ``…/v1/traces``)."""
    base = endpoint.strip().rstrip("/")
    return base if base.endswith("/v1/traces") else f"{base}/v1/traces"


def _install_propagator() -> None:
    propagate.set_global_textmap(TraceContextTextMapPropagator())


def _set_global_provider(provider: TracerProvider | None) -> None:
    """Install (or, with ``None``, remove) the global tracer provider.

    The OTel API only allows setting the provider once per process; resetting the "set once" guard is
    what OpenTelemetry's own test utilities do and is needed so tests and repeated app lifespans can
    swap providers. Tracers must therefore be looked up per use (``trace.get_tracer``), never cached.
    """
    once_cls = getattr(getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None), "__class__", None)
    if once_cls is not None and hasattr(trace, "_TRACER_PROVIDER"):
        trace._TRACER_PROVIDER_SET_ONCE = once_cls()
        trace._TRACER_PROVIDER = None
    if provider is not None:
        trace.set_tracer_provider(provider)


def build_resource(service_name: str) -> Resource:
    settings = get_settings()
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.environment,
        }
    )


def configure_telemetry(service_name: str | None = None) -> bool:
    """Configure tracing for this process. Idempotent; returns True when spans are exported."""
    global _provider
    settings = get_settings()
    with _lock:
        _install_propagator()
        if _provider is not None or _test_provider is not None:
            return True
        endpoint = settings.otel_exporter_otlp_endpoint
        if not endpoint:
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(
            resource=build_resource(service_name or settings.otel_service_name),
            sampler=ParentBased(TraceIdRatioBased(_clamp_ratio(settings.otel_traces_sampler_ratio))),
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint(endpoint))))
        _set_global_provider(provider)
        _provider = provider
        log.info(
            "telemetry_configured",
            service=service_name or settings.otel_service_name,
            sampler_ratio=_clamp_ratio(settings.otel_traces_sampler_ratio),
        )
        return True


def shutdown_telemetry() -> None:
    """Flush and shut down the exporter configured by ``configure_telemetry`` (test tracing is untouched)."""
    global _provider
    with _lock:
        provider, _provider = _provider, None
        if provider is None:
            return
        try:
            provider.force_flush(timeout_millis=5000)
            provider.shutdown()
        except Exception as exc:  # never fail process shutdown because the collector is unreachable
            log.warning("telemetry_shutdown_failed", error=type(exc).__name__)
        if _test_provider is None:
            _set_global_provider(None)


def tracing_configured() -> bool:
    """True when spans are recorded (OTLP export or in-memory test tracing)."""
    return _provider is not None or _test_provider is not None


def install_test_tracing() -> InMemorySpanExporter:
    """Record every span in memory (tests only). Returns the (cleared) exporter; idempotent."""
    global _test_provider, _test_exporter
    with _lock:
        _install_propagator()
        if _test_provider is None or _test_exporter is None:
            exporter = InMemorySpanExporter()
            provider = TracerProvider(resource=build_resource("aegis-test"), sampler=ParentBased(ALWAYS_ON))
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            _set_global_provider(provider)
            _test_provider, _test_exporter = provider, exporter
        _test_exporter.clear()
        return _test_exporter


def uninstall_test_tracing() -> None:
    """Remove test tracing and restore the previous state (the OTLP provider, if any, else no-op)."""
    global _test_provider, _test_exporter
    with _lock:
        provider, _test_provider = _test_provider, None
        exporter, _test_exporter = _test_exporter, None
        if provider is None:
            return
        provider.shutdown()
        if exporter is not None:
            exporter.clear()
        _set_global_provider(_provider)


def current_span_ids() -> dict[str, Any]:
    """``{"trace_id", "span_id"}`` (hex) of the active span, or ``{}`` when there is none."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x")}
