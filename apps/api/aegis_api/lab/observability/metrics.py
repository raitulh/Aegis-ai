"""Prometheus metrics (process-local registry; exposed at ``/metrics`` and on worker metric ports).

Import the metric objects from here — never create ad-hoc metrics elsewhere, so names stay consistent.
Label cardinality is bounded: no tenant ids, user ids or free text in labels.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_LONG_BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 21600)

HTTP_REQUESTS = Counter("aegis_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aegis_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
DB_QUERY_LATENCY = Histogram(
    "aegis_db_query_duration_seconds",
    "Database statement latency",
    ["operation"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
REDIS_LATENCY = Histogram(
    "aegis_redis_duration_seconds", "Redis command latency", ["operation"], buckets=_LATENCY_BUCKETS, registry=REGISTRY
)
LLM_REQUESTS = Counter(
    "aegis_llm_requests_total", "LLM requests", ["provider", "model", "task_type", "outcome"], registry=REGISTRY
)
LLM_LATENCY = Histogram(
    "aegis_llm_request_duration_seconds",
    "LLM request latency",
    ["provider", "task_type"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter("aegis_llm_tokens_total", "LLM tokens", ["provider", "direction"], registry=REGISTRY)
LLM_ERRORS = Counter("aegis_llm_errors_total", "LLM errors", ["provider", "error_class"], registry=REGISTRY)
LLM_COST = Counter("aegis_llm_cost_usd_total", "Estimated LLM cost (USD)", ["provider"], registry=REGISTRY)
AGENT_RUN_DURATION = Histogram(
    "aegis_agent_run_duration_seconds",
    "Agent run duration",
    ["role", "status"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
AGENT_RUNS = Counter("aegis_agent_runs_total", "Agent runs", ["role", "status"], registry=REGISTRY)
WORKFLOW_DURATION = Histogram(
    "aegis_workflow_duration_seconds",
    "Workflow duration",
    ["kind", "status"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
WORKFLOW_ACTIVITY_DURATION = Histogram(
    "aegis_workflow_activity_duration_seconds",
    "Workflow activity duration",
    ["activity", "outcome"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
TOOL_INVOCATIONS = Counter(
    "aegis_tool_invocations_total", "Brokered tool invocations", ["tool", "source", "status"], registry=REGISTRY
)
TOOL_LATENCY = Histogram(
    "aegis_tool_duration_seconds", "Tool latency", ["tool"], buckets=_LATENCY_BUCKETS, registry=REGISTRY
)
EXPERIMENT_DURATION = Histogram(
    "aegis_experiment_duration_seconds",
    "Sandboxed experiment wall time",
    ["backend", "status"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
COMPUTE_JOBS = Counter("aegis_compute_jobs_total", "Compute jobs", ["backend", "status"], registry=REGISTRY)
GPU_SECONDS = Counter("aegis_gpu_seconds_total", "GPU seconds consumed", ["gpu_type"], registry=REGISTRY)
CPU_SECONDS = Counter("aegis_cpu_seconds_total", "Sandbox CPU seconds consumed", ["backend"], registry=REGISTRY)
VERIFICATION_DURATION = Histogram(
    "aegis_verification_duration_seconds",
    "Verification duration",
    ["verdict"],
    buckets=_LONG_BUCKETS,
    registry=REGISTRY,
)
EVENTS_EMITTED = Counter("aegis_events_emitted_total", "Lab events emitted", ["type"], registry=REGISTRY)
SSE_CONNECTIONS = Gauge("aegis_sse_connections", "Open SSE connections", registry=REGISTRY)
APPROVALS = Counter("aegis_approvals_total", "Approval requests", ["action", "status"], registry=REGISTRY)
POLICY_DECISIONS = Counter("aegis_policy_decisions_total", "Policy decisions", ["action", "effect"], registry=REGISTRY)
BUDGET_EXCEEDED = Counter("aegis_budget_exceeded_total", "Budget limit hits", ["kind"], registry=REGISTRY)
WEBHOOK_DELIVERIES = Counter("aegis_webhook_deliveries_total", "Webhook deliveries", ["outcome"], registry=REGISTRY)
