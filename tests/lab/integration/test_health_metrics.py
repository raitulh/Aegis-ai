"""Liveness/readiness probes, Prometheus metrics, HTTP tracing (X-Trace-ID) and platform info."""

from __future__ import annotations

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


@pytest.fixture(autouse=True)
def _fresh_readiness():
    from aegis_api.lab.observability.health import reset_readiness_cache

    reset_readiness_cache()
    yield
    reset_readiness_cache()


def test_liveness(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] and body["service"]
    assert body["uptime_seconds"] >= 0


def test_readiness_ok_with_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["database"]["required"] is True
    # REDIS_URL is empty in tests; Temporal is not the workflow engine → not required
    assert body["checks"]["redis"] == {"status": "not_configured", "required": False}
    assert body["checks"]["temporal"]["status"] == "not_configured"
    assert body["checks"]["storage"]["status"] in ("ok", "unknown")
    assert set(body["providers"]) == {"gemini", "openai", "anthropic", "ollama"}
    # core probes are untouched
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_readiness_503_when_database_fails_without_leaking_details(client, monkeypatch):
    from aegis_api.lab.observability import health

    class OperationalError(Exception):
        pass

    def broken() -> None:
        raise OperationalError("could not connect to server at 10.1.2.3 password=hunter2")

    monkeypatch.setattr(health, "_check_database", broken)
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == {"status": "error", "required": True, "error": "OperationalError"}
    assert "10.1.2.3" not in r.text and "hunter2" not in r.text


def test_readiness_is_cached_and_redis_checked_when_configured(client, monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.lab.observability import health

    calls = {"n": 0}

    def counting() -> None:
        calls["n"] += 1

    monkeypatch.setattr(health, "_check_database", counting)
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")  # unreachable
    r1 = client.get("/health/ready")
    r2 = client.get("/health/ready")
    assert calls["n"] == 1  # second probe served from the 5 s cache
    assert r1.status_code == r2.status_code == 503
    assert r1.json()["checks"]["redis"]["status"] in ("error", "timeout")
    assert r1.json()["checks"]["redis"]["required"] is True


def test_readiness_times_out_slow_checks(client, monkeypatch):
    import time

    from aegis_api.lab.observability import health

    monkeypatch.setattr(health, "DEFAULT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(health, "_check_database", lambda: time.sleep(1))
    started = time.perf_counter()
    r = client.get("/health/ready")
    assert time.perf_counter() - started < 1
    assert r.status_code == 503
    assert r.json()["checks"]["database"]["status"] == "timeout"


def test_metrics_exposition(client, lab):
    lab.get(f"/api/v1/events/{2**40}")  # 404, labelled with the route template
    client.get("/api/v1/definitely-not-a-route")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    text = r.text
    assert "aegis_http_requests_total" in text
    assert 'route="/api/v1/events/{event_id}"' in text
    assert 'route="unmatched"' in text
    assert "definitely-not-a-route" not in text  # raw paths never become labels
    assert 'aegis_db_query_duration_seconds_count{operation="select"}' in text
    assert "process_cpu_seconds_total" in text or "python_info" in text


def test_metrics_token_and_disable(client, monkeypatch):
    from aegis_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "metrics_token", "scrape-token-123")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Basic scrape-token-123"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"})
    assert ok.status_code == 200 and "aegis_http_requests_total" in ok.text
    monkeypatch.setattr(settings, "metrics_enabled", False)
    r = client.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_trace_id_propagated_from_traceparent_with_in_memory_exporter(lab):
    from opentelemetry.trace import SpanKind

    from aegis_api.lab.observability.telemetry import install_test_tracing, uninstall_test_tracing

    exporter = install_test_tracing()
    try:
        r = lab.get("/api/v1/system/info", headers={"traceparent": TRACEPARENT})
        assert r.status_code == 200, r.text
        assert r.headers["x-trace-id"] == TRACE_ID
        assert r.headers["x-request-id"]
        spans = exporter.get_finished_spans()
        server = [s for s in spans if s.kind == SpanKind.SERVER]
        assert len(server) == 1
        span = server[0]
        assert span.name == "HTTP GET /api/v1/system/info"
        assert format(span.context.trace_id, "032x") == TRACE_ID
        assert span.attributes["http.route"] == "/api/v1/system/info"
        assert span.attributes["http.response.status_code"] == 200
        assert span.attributes["http.request.method"] == "GET"
        db_spans = [s for s in spans if s.name.startswith("db.")]
        assert db_spans, "statement spans are recorded while tracing is configured"
        for s in db_spans:
            assert set(s.attributes) == {"db.system", "db.operation"}  # never SQL text or parameters
            assert format(s.context.trace_id, "032x") == TRACE_ID
    finally:
        uninstall_test_tracing()


def test_trace_id_echoed_without_sdk(client):
    r = client.get("/api/v1/definitely-not-a-route", headers={"traceparent": TRACEPARENT})
    assert r.headers.get("x-trace-id") == TRACE_ID
    assert "x-trace-id" not in client.get("/api/v1/definitely-not-a-route").headers  # no SDK, no parent


def test_system_info(lab, client):
    client.cookies.clear()  # the signup cookie lives in the shared client's jar
    assert client.get("/api/v1/system/info").status_code == 401
    r = lab.get("/api/v1/system/info")
    assert r.status_code == 200
    body = r.json()
    for key in ("version", "environment", "workflow_engine", "execution_backend", "event_bus", "storage_backend"):
        assert body[key]
    assert body["event_bus"] == "memory"
    assert isinstance(body["features"], dict) and "evolution" in body["features"]
