"""Schemas for the runtime guard, policy studio, continuous assurance, billing, webhooks and public endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from aegis_api.schemas.common import ORMModel
from engines.runtime.schema import RuntimeEventIn

# --- runtime ---------------------------------------------------------------------------------------


class RuntimeIngest(BaseModel):
    events: list[RuntimeEventIn] = Field(min_length=1, max_length=500)


class RuntimeDecisionOut(BaseModel):
    id: str
    event_id: str
    system_id: str
    event_type: str
    mode: str
    decision: str
    effective_decision: str
    allowed: bool
    reason: str | None
    matches: list[dict[str, Any]]
    risk_level: str | None
    approval_id: str | None
    finding_id: str | None
    evidence_id: str | None
    duplicate: bool = False


class RuntimeIngestOut(BaseModel):
    accepted: int
    duplicates: int
    decisions: list[RuntimeDecisionOut]


class RuntimeEventOut(ORMModel):
    id: str
    event_id: str
    system_id: str
    schema_version: str
    event_type: str
    source: str
    environment: str | None
    agent_name: str | None
    actor: str | None
    session_id: str | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    tool_name: str | None
    occurred_at: datetime
    payload: dict[str, Any]
    signals: dict[str, Any]
    mode: str
    decision: str
    effective_decision: str
    decision_reason: str | None
    policy_matches: list[dict[str, Any]]
    risk_level: str | None
    finding_id: str | None
    approval_id: str | None
    evidence_id: str | None


class ApprovalOut(ORMModel):
    id: str
    system_id: str
    runtime_event_id: str
    status: str
    summary: str
    rule_ref: str | None
    request: dict[str, Any]
    expires_at: datetime
    decided_by_label: str | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class ApprovalDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class RuntimeModeUpdate(BaseModel):
    mode: Literal["observe", "audit", "enforce"]


# --- policy studio ---------------------------------------------------------------------------------


class RuntimePolicyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    key: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    source_yaml: str | None = Field(default=None, max_length=65536)
    template_key: str | None = Field(default=None, max_length=80)


class RuntimePolicyVersionCreate(BaseModel):
    source_yaml: str = Field(min_length=1, max_length=65536)
    change_note: str | None = Field(default=None, max_length=500)


class RuntimePolicyOut(ORMModel):
    id: str
    key: str
    name: str
    description: str | None
    category: str | None
    status: str
    published_version_id: str | None
    latest_version: int
    template_key: str | None
    created_at: datetime
    updated_at: datetime


class RuntimePolicyVersionOut(ORMModel):
    id: str
    policy_id: str
    version: int
    source_yaml: str
    compiled: dict[str, Any]
    checksum: str
    status: str
    change_note: str | None
    published_at: datetime | None
    created_at: datetime


class RuntimePolicyAssignmentCreate(BaseModel):
    scope_type: Literal["organization", "environment", "system"]
    system_id: str | None = None
    environment: str | None = None


class RuntimePolicyAssignmentOut(ORMModel):
    id: str
    policy_id: str
    scope_type: str
    scope_key: str
    system_id: str | None
    enabled: bool
    created_at: datetime


class PublishRequest(BaseModel):
    version: int = Field(ge=1)


class PolicySourceRequest(BaseModel):
    source_yaml: str = Field(min_length=1, max_length=65536)


class PolicyTestRequest(BaseModel):
    source_yaml: str = Field(min_length=1, max_length=65536)
    event: RuntimeEventIn


class SimulationRequest(BaseModel):
    source_yaml: str | None = Field(default=None, max_length=65536)
    policy_id: str | None = None
    version: int | None = None
    system_ids: list[str] = Field(default_factory=list, max_length=50)
    days: int = Field(default=7, ge=1, le=90)


# --- continuous assurance ----------------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    system_id: str
    name: str | None = Field(default=None, max_length=200)
    interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    trigger_on: list[str] = Field(default_factory=list, max_length=10)
    categories: list[str] = Field(default_factory=list, max_length=12)
    intensity: Literal["quick", "standard", "deep", "adversarial"] = "quick"
    policy_version_ids: list[str] = Field(default_factory=list, max_length=20)


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    trigger_on: list[str] | None = None
    categories: list[str] | None = None
    intensity: Literal["quick", "standard", "deep", "adversarial"] | None = None


class ScheduleOut(ORMModel):
    id: str
    system_id: str
    name: str
    enabled: bool
    interval_hours: int | None
    trigger_on: list[str]
    categories: list[str]
    intensity: str
    policy_version_ids: list[str]
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_audit_id: str | None
    created_at: datetime


class TriggerCreate(BaseModel):
    system_id: str
    event_type: str = Field(max_length=32)
    ref: str = Field(min_length=1, max_length=200, description="Commit SHA, PR number, release tag, version …")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerOut(ORMModel):
    id: str
    system_id: str
    schedule_id: str | None
    event_type: str
    ref: str
    source: str
    categories: list[str]
    selection_reason: str | None
    audit_id: str | None
    created_at: datetime


class BaselineUpdate(BaseModel):
    audit_id: str


# --- webhooks ------------------------------------------------------------------------------------------

WEBHOOK_EVENTS = (
    "audit.completed",
    "finding.created",
    "finding.resolved",
    "critical_risk.detected",
    "policy.violation",
    "runtime.approval_decided",
    "regression.detected",
    "monitoring.alert",
    "usage.threshold",
)


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    description: str | None = Field(default=None, max_length=300)
    events: list[str] = Field(default_factory=list, max_length=len(WEBHOOK_EVENTS))


class WebhookUpdate(BaseModel):
    active: bool | None = None
    events: list[str] | None = None
    description: str | None = Field(default=None, max_length=300)


class WebhookOut(ORMModel):
    id: str
    url: str
    description: str | None
    events: list[str]
    active: bool
    failure_count: int
    last_delivery_at: datetime | None
    created_at: datetime


class WebhookCreated(BaseModel):
    webhook: WebhookOut
    signing_secret: str


class WebhookDeliveryOut(ORMModel):
    id: str
    event_type: str
    event_id: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    response_status: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime


# --- organization & billing ----------------------------------------------------------------------------


class OrganizationSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    runtime_events_retention_days: int | None = Field(default=None, ge=1, le=3650)
    finding_sla_days: dict[str, int] | None = None


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "business"]


# --- findings workflow ------------------------------------------------------------------------------


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class CommentOut(ORMModel):
    id: str
    finding_id: str
    author_label: str | None
    body: str
    created_at: datetime


class RiskAcceptanceIn(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime
    owner_id: str | None = None


class BulkFindingUpdate(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    status: str | None = None
    assignee_id: str | None = None
    priority: str | None = None
    tags: list[str] | None = None
    note: str | None = Field(default=None, max_length=2000)


# --- public ----------------------------------------------------------------------------------------


class ContactCreate(BaseModel):
    kind: Literal["sales", "demo", "security_review", "private_deployment"]
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    company: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=5000)
