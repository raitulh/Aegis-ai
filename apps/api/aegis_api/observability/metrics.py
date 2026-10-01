"""Prometheus metrics for the API and workers.

Exposed at ``/metrics`` (bearer-token protected when ``METRICS_TOKEN`` is set; required in production).
Labels are low-cardinality by design: routes use the path *template* (``/api/v1/audits/{audit_id}``), never
raw paths, and no tenant identifiers are used as labels.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter("aegis_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aegis_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "aegis_job_duration_seconds",
    "Background job duration",
    ["job", "outcome"],
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 900, 3600),
    registry=REGISTRY,
)
SSE_CONNECTIONS = Gauge("aegis_sse_connections", "Open server-sent-event streams", registry=REGISTRY)
RUNTIME_EVENTS = Counter("aegis_runtime_events_total", "Runtime events ingested", ["event_type"], registry=REGISTRY)
RUNTIME_DECISIONS = Counter(
    "aegis_runtime_decisions_total", "Runtime guard decisions", ["decision", "mode"], registry=REGISTRY
)
AUDITS = Counter("aegis_audits_finished_total", "Audits reaching a terminal state", ["status"], registry=REGISTRY)
POLICY_VIOLATIONS = Counter("aegis_policy_violations_total", "Runtime policy violations", ["action"], registry=REGISTRY)
WEBHOOK_DELIVERIES = Counter(
    "aegis_webhook_deliveries_total", "Webhook delivery attempts", ["outcome"], registry=REGISTRY
)
QUEUE_DEPTH = Gauge("aegis_job_queue_depth", "Jobs queued or retrying in the ledger", ["status"], registry=REGISTRY)


def render() -> bytes:
    return generate_latest(REGISTRY)
