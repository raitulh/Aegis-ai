"""Finding, remediation, regression, red team, agent trace, monitoring schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from aegis_api.models.enums import FindingStatus, RemediationCategory
from aegis_api.schemas.common import ORMModel


class FindingOut(ORMModel):
    id: str
    number: int
    title: str
    category: str
    dimension: str
    severity: str
    status: str
    description: str
    system_id: str
    system_version: str | None
    model_version: str | None
    audit_id: str | None
    last_audit_id: str | None = None
    last_seen_at: datetime | None = None
    control_ref: str | None
    policy_id: str | None
    test_type: str | None
    evaluator_key: str | None
    evaluator_version: str | None
    confidence: float
    impact: str | None
    risk_level: str
    risk_score: float
    risk_reasons: list[str]
    risk_factors: list[dict[str, Any]]
    occurrences: int
    sample_size: int
    details: dict[str, Any]
    evidence_unavailable_reason: str | None
    assignee_id: str | None
    due_date: date | None
    source: str = "audit"
    tags: list[str] = []
    priority: str | None = None
    sla_due_at: datetime | None = None
    risk_acceptance: dict[str, Any] | None = None
    risk_accepted_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FindingSummary(ORMModel):
    id: str
    number: int
    title: str
    category: str
    dimension: str
    severity: str
    status: str
    risk_level: str
    risk_score: float
    system_id: str
    control_ref: str | None = None
    confidence: float
    occurrences: int
    sample_size: int
    audit_id: str | None = None
    last_seen_at: datetime | None = None
    source: str = "audit"
    tags: list[str] = []
    priority: str | None = None
    assignee_id: str | None = None
    sla_due_at: datetime | None = None
    created_at: datetime


class AuditFindingOut(FindingSummary):
    """A finding as observed by one specific audit."""

    observed_severity: str
    observed_risk_level: str | None
    observed_occurrences: int
    observed_sample_size: int
    first_detected_here: bool


class RiskAcceptanceUpdate(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime
    owner_id: str | None = None


class FindingUpdate(BaseModel):
    status: FindingStatus | None = None
    assignee_id: str | None = None
    due_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    priority: str | None = Field(default=None, pattern="^p[1-4]$")
    risk_acceptance: RiskAcceptanceUpdate | None = None


class FindingEventOut(ORMModel):
    id: str
    type: str
    actor_label: str | None
    from_status: str | None
    to_status: str | None
    note: str | None
    created_at: datetime


class RemediationRecommendationOut(BaseModel):
    category: str
    title: str
    description: str
    change: dict[str, Any]
    creates_regression_test: bool


class RemediationCreate(BaseModel):
    category: RemediationCategory
    title: str = Field(min_length=1, max_length=300)
    description: str
    change: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = None
    due_date: date | None = None


class RemediationOut(ORMModel):
    id: str
    finding_id: str
    category: str
    title: str
    description: str
    status: str
    source: str
    change: dict[str, Any]
    owner_id: str | None
    due_date: date | None
    approved_at: datetime | None
    applied_at: datetime | None
    verified_at: datetime | None
    created_at: datetime


class RegressionRunOut(ORMModel):
    id: str
    system_id: str
    audit_id: str | None
    status: str
    verdict: str | None
    system_version: str | None
    results: list[dict[str, Any]]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RedTeamRunCreate(BaseModel):
    system_id: str
    name: str | None = None
    corpus: list[dict[str, Any]] = Field(default_factory=list, description="Imported probe corpus records")
    corpus_name: str = "custom"
    max_probes: int = Field(default=100, ge=1, le=1000)
    max_depth: int = Field(default=0, ge=0, le=5)


class RedTeamRunOut(ORMModel):
    id: str
    system_id: str
    audit_id: str | None
    name: str
    status: str
    config: dict[str, Any]
    summary: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    created_at: datetime


class RedTeamProbeOut(ORMModel):
    id: str
    parent_id: str | None
    root_id: str | None
    depth: int
    probe_key: str
    category: str
    technique: str
    payload: str
    expected_behavior: str
    observed_behavior: str | None
    result: str
    severity: str
    confidence: float
    finding_id: str | None


class TraceEventOut(ORMModel):
    id: str
    span_id: str
    parent_span_id: str | None
    seq: int
    kind: str
    name: str
    timestamp: datetime
    duration_ms: int | None
    model: str | None
    input_preview: str | None
    output_preview: str | None
    policy_checks: list[dict[str, Any]]
    finding_ids: list[str]
    status: str


class ToolCallOut(ORMModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
    authorization: str
    requires_human_approval: bool
    human_approved: bool
    sensitive_data_detected: bool
    sensitive_types: list[str]
    allowed: bool
    risk_level: str | None
    control_ref: str | None
    violations: list[dict[str, Any]]


class AgentTraceOut(ORMModel):
    id: str
    trace_id: str
    system_id: str
    name: str | None
    status: str
    model: str | None
    source: str
    started_at: datetime
    ended_at: datetime | None
    span_count: int
    tool_call_count: int
    violation_count: int
    risk_level: str | None
    input_summary: str | None
    output_summary: str | None
    finding_ids: list[str]


class AgentTraceDetail(AgentTraceOut):
    events: list[TraceEventOut] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)


class TraceIngest(BaseModel):
    system_id: str
    trace_id: str | None = None
    name: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class AlertOut(ORMModel):
    id: str
    system_id: str | None
    severity: str
    title: str
    event_type: str
    control_ref: str | None
    metric: str | None
    observed: float | None
    threshold: float | None
    evidence_count: int
    action: str | None
    status: str
    triggered_at: datetime


class MonitorCreate(BaseModel):
    system_id: str
    name: str = Field(min_length=1, max_length=200)
    sampling_rate: float = Field(default=0.1, ge=0, le=1)
    evaluators: list[str] = Field(default_factory=list)
    alert_conditions: list[dict[str, Any]] = Field(default_factory=list)


class MonitorOut(ORMModel):
    id: str
    system_id: str
    name: str
    enabled: bool
    sampling_rate: float
    evaluators: list[str]
    alert_conditions: list[dict[str, Any]]


class MonitoringIngest(BaseModel):
    system_id: str
    request_id: str | None = None
    input: str
    output: str
    model_version: str | None = None
    latency_ms: int | None = None


class MonitoringOverview(BaseModel):
    window_days: int
    requests: int
    evaluated: int
    policy_violations: int
    hallucination_alerts: int
    safety_alerts: int
    pii_incidents: int
    risk_trend: list[dict[str, Any]]
    model_events: list[dict[str, Any]]
    recent_alerts: list[AlertOut]


class WebhookCreate(BaseModel):
    url: str
    description: str | None = None
    events: list[str] = Field(default_factory=list)


class WebhookOut(ORMModel):
    id: str
    url: str
    description: str | None
    events: list[str]
    active: bool
    failure_count: int
    last_delivery_at: datetime | None
    created_at: datetime


class IntegrationOut(ORMModel):
    id: str
    kind: str
    name: str
    status: str
    config: dict[str, Any]
    last_checked_at: datetime | None
    last_error: str | None
    connected_at: datetime | None


class NotificationOut(ORMModel):
    id: str
    type: str
    title: str
    body: str | None
    link: str | None
    severity: str | None
    read_at: datetime | None
    created_at: datetime


class OverviewStats(BaseModel):
    active_systems: int
    audits_today: int
    open_findings: int
    critical_risks: int
    policy_coverage: float
    posture: str
    dimensions: dict[str, Any]
    previous_dimensions: dict[str, Any]
    risk_trend: list[dict[str, Any]]
    findings_by_severity: dict[str, int]
    recent_audits: list[dict[str, Any]]
    recent_incidents: list[dict[str, Any]]
    test_volume: int
    is_demo: bool


class SearchResult(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str | None = None
    url: str
    score: float = 0.0


class SearchResponse(BaseModel):
    query: str
    groups: dict[str, list[SearchResult]]
    total: int
