"""Prometheus metrics (process-local registry; scrape ``/metrics``).

Labels are bounded (route templates, provider/model ids, statuses) — never tenant ids or user input — to keep
cardinality under control and avoid leaking tenant information through metrics.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

_LAT = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_LONG = (0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200)

HTTP_REQUESTS = Counter("aegis_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aegis_http_request_duration_seconds", "HTTP latency", ["method", "route"], buckets=_LAT, registry=REGISTRY
)
DB_LATENCY = Histogram(
    "aegis_db_query_duration_seconds", "Database statement latency", ["operation"], buckets=_LAT, registry=REGISTRY
)
REDIS_LATENCY = Histogram(
    "aegis_redis_op_duration_seconds", "Redis operation latency", ["op"], buckets=_LAT, registry=REGISTRY
)
LLM_LATENCY = Histogram(
    "aegis_llm_request_duration_seconds",
    "LLM request latency",
    ["provider", "model", "task_type"],
    buckets=_LONG,
    registry=REGISTRY,
)
LLM_REQUESTS = Counter(
    "aegis_llm_requests_total", "LLM requests", ["provider", "model", "task_type", "outcome"], registry=REGISTRY
)
LLM_TOKENS = Counter("aegis_llm_tokens_total", "LLM tokens", ["provider", "model", "direction"], registry=REGISTRY)
AGENT_DURATION = Histogram(
    "aegis_agent_run_duration_seconds", "Agent run duration", ["role", "status"], buckets=_LONG, registry=REGISTRY
)
WORKFLOW_DURATION = Histogram(
    "aegis_workflow_duration_seconds", "Workflow duration", ["workflow", "status"], buckets=_LONG, registry=REGISTRY
)
WORKFLOW_ACTIVITIES = Counter(
    "aegis_workflow_activities_total", "Workflow activities", ["activity", "outcome"], registry=REGISTRY
)
TOOL_CALLS = Counter("aegis_tool_calls_total", "Brokered tool calls", ["tool", "status"], registry=REGISTRY)
EXPERIMENT_DURATION = Histogram(
    "aegis_experiment_run_duration_seconds",
    "Sandboxed run duration",
    ["backend", "kind", "status"],
    buckets=_LONG,
    registry=REGISTRY,
)
GPU_SECONDS = Counter("aegis_gpu_seconds_total", "GPU seconds consumed", ["gpu_type"], registry=REGISTRY)
VERIFICATION_DURATION = Histogram(
    "aegis_verification_duration_seconds", "Verification duration", ["outcome"], buckets=_LONG, registry=REGISTRY
)
ACTIVE_STREAMS = Gauge("aegis_sse_streams_active", "Open SSE streams", registry=REGISTRY)
POLICY_DECISIONS = Counter(
    "aegis_policy_decisions_total", "Policy decisions", ["action", "decision"], registry=REGISTRY
)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


@contextmanager
def timed(histogram: Histogram, **labels: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(**labels).observe(time.perf_counter() - started)
