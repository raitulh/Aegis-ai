"""Telemetry configuration, trace-aware logging, DB statement classification and health helpers."""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_tracing():
    from aegis_api.lab.observability import telemetry

    telemetry.uninstall_test_tracing()
    telemetry.shutdown_telemetry()
    yield telemetry
    telemetry.uninstall_test_tracing()
    telemetry.shutdown_telemetry()


def test_configure_without_endpoint_is_noop_and_idempotent(clean_tracing, monkeypatch):
    from opentelemetry import trace

    from aegis_api.config import get_settings

    monkeypatch.setattr(get_settings(), "otel_exporter_otlp_endpoint", None)
    assert clean_tracing.configure_telemetry() is False
    assert clean_tracing.configure_telemetry() is False
    assert clean_tracing.tracing_configured() is False
    with trace.get_tracer("t").start_as_current_span("noop") as span:
        assert not span.is_recording()


def test_configure_with_endpoint_installs_sdk_once(clean_tracing, monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from aegis_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", "http://127.0.0.1:4318/")
    monkeypatch.setattr(settings, "otel_traces_sampler_ratio", 0.25)
    assert clean_tracing.configure_telemetry("svc-test") is True
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    assert clean_tracing.configure_telemetry("svc-test") is True  # idempotent
    assert trace.get_tracer_provider() is provider
    assert provider.resource.attributes["service.name"] == "svc-test"
    assert provider.resource.attributes["deployment.environment"] == settings.environment
    assert "TraceIdRatioBased" in provider.sampler.get_description()
    assert clean_tracing.tracing_configured() is True
    clean_tracing.shutdown_telemetry()
    assert clean_tracing.tracing_configured() is False
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_traces_endpoint_suffix():
    from aegis_api.lab.observability.telemetry import traces_endpoint

    assert traces_endpoint("http://collector:4318") == "http://collector:4318/v1/traces"
    assert traces_endpoint("http://collector:4318/") == "http://collector:4318/v1/traces"
    assert traces_endpoint("http://collector:4318/v1/traces") == "http://collector:4318/v1/traces"


def test_test_tracing_records_spans_and_uninstalls(clean_tracing):
    from aegis_api.lab.observability.tracing import current_trace_id, span

    exporter = clean_tracing.install_test_tracing()
    assert clean_tracing.install_test_tracing() is exporter  # idempotent (and cleared)
    with span("unit.work", answer=42, skipped=None):
        assert current_trace_id() is not None
    names = [s.name for s in exporter.get_finished_spans()]
    assert names == ["unit.work"]
    assert exporter.get_finished_spans()[0].attributes["answer"] == 42
    clean_tracing.uninstall_test_tracing()
    assert clean_tracing.tracing_configured() is False
    with span("after"):
        assert current_trace_id() is None


def test_logging_processor_adds_trace_context(clean_tracing):
    from aegis_api.lab.observability.tracing import span
    from aegis_api.logging import add_trace_context, redact_processor

    assert add_trace_context(None, "info", {"event": "x"}) == {"event": "x"}
    clean_tracing.install_test_tracing()
    with span("logged") as s:
        ctx = s.get_span_context()
        out = add_trace_context(None, "info", {"event": "x"})
        assert out["trace_id"] == format(ctx.trace_id, "032x")
        assert out["span_id"] == format(ctx.span_id, "016x")
        # a trace id bound by the request middleware wins
        assert add_trace_context(None, "info", {"trace_id": "bound"})["trace_id"] == "bound"
    # redaction still applies and leaves correlation ids alone
    redacted = redact_processor(None, "info", {"event": "e", "trace_id": out["trace_id"], "api_key": "sk-123"})
    assert redacted["trace_id"] == out["trace_id"] and redacted["api_key"] == "[REDACTED]"


def test_json_log_line_carries_context_ids(clean_tracing):
    """The configured JSON pipeline emits request/tenant/user/trace ids and still redacts secrets."""
    import json

    import structlog

    from aegis_api.lab.observability.tracing import span
    from aegis_api.logging import configure_logging

    previous = structlog.get_config()
    configure_logging("INFO", json_logs=True)
    processors = structlog.get_config()["processors"]
    clean_tracing.install_test_tracing()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req_abc", tenant_id="org-1", user_id="user-1")
    try:
        with span("json-log"):
            rendered: object = {"event": "hello", "authorization": "Bearer secret-token"}
            for processor in processors:
                rendered = processor(None, "info", rendered)
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.configure(**previous)
    record = json.loads(str(rendered))
    assert record["event"] == "hello"
    assert record["request_id"] == "req_abc"
    assert record["tenant_id"] == "org-1" and record["user_id"] == "user-1"
    assert len(record["trace_id"]) == 32 and len(record["span_id"]) == 16
    assert record["authorization"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT 1", "select"),
        ("  select * from events", "select"),
        ("/* comment */ INSERT INTO x VALUES (1)", "insert"),
        ("-- c\nUPDATE t SET a = 1", "update"),
        ("DELETE FROM t", "delete"),
        ("WITH q AS (SELECT 1) SELECT * FROM q", "select"),
        ("WITH moved AS (DELETE FROM a RETURNING *) INSERT INTO b SELECT * FROM moved", "delete"),
        ("BEGIN", "other"),
        ("select set_config('app.current_org_id', '', true)", "select"),
        ("", "other"),
    ],
)
def test_classify_statement(statement, expected):
    from aegis_api.lab.observability.db_metrics import classify_statement

    assert classify_statement(statement) == expected


def test_install_db_metrics_is_idempotent():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from aegis_api.lab.observability import db_metrics

    db_metrics.install_db_metrics()
    db_metrics.install_db_metrics()
    assert event.contains(Engine, "before_cursor_execute", db_metrics._before_cursor_execute)


def test_route_template_and_method_folding():
    from aegis_api.lab.observability.middleware import UNMATCHED_ROUTE, route_template

    class _Route:
        path = "/api/v1/missions/{mission_id}"

    assert route_template({"route": _Route()}) == "/api/v1/missions/{mission_id}"
    assert route_template({}) == UNMATCHED_ROUTE


def test_parse_host_port():
    from aegis_api.lab.observability.health import parse_host_port

    assert parse_host_port("localhost:7233", 1) == ("localhost", 7233)
    assert parse_host_port("temporal.internal", 7233) == ("temporal.internal", 7233)
    assert parse_host_port("[::1]:7000", 1) == ("::1", 7000)
    assert parse_host_port("https://tmp.example:443", 1) == ("tmp.example", 443)
    with pytest.raises(ValueError):
        parse_host_port(":7233", 1)
