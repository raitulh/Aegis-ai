"""API contract for governance: policies, policy evaluation, approvals, budgets, quotas and the audit log."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from aegis_api.schemas.common import ORMModel

# Money is Decimal internally (Numeric(18,6)) and a JSON number on the wire.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
Amount = Annotated[
    Decimal | int, PlainSerializer(lambda v: float(v) if isinstance(v, Decimal) else v, when_used="json")
]

_POLICY_KEY = r"^[a-z0-9][a-z0-9_.\-]{1,79}$"


# ---------------------------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------------------------
class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_POLICY_KEY, description="Stable identifier, unique per organization")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: str | None = Field(default=None, description="Scope the policy to one project (default: org-wide)")
    rules: list[dict[str, Any]] = Field(
        default_factory=list, max_length=200, description="Policy rules (see GET /governance/baseline for the DSL)"
    )
    change_note: str | None = Field(default=None, max_length=2000)


class PolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[dict[str, Any]] = Field(max_length=200)
    change_note: str | None = Field(default=None, max_length=2000)


class PolicyStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"]


class PolicyVersionOut(ORMModel):
    id: str
    policy_id: str
    version: int
    rules: list[dict[str, Any]]
    content_hash: str
    change_note: str | None = None
    created_by_id: str | None = None
    created_at: datetime


class PolicyOut(ORMModel):
    id: str
    key: str
    name: str
    description: str | None = None
    project_id: str | None = None
    status: str
    current_version_id: str | None = None
    current_version: int | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class PolicyDetailOut(PolicyOut):
    rules: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = None


class BaselineOut(BaseModel):
    version: str
    rules: list[dict[str, Any]]
    default_effects: dict[str, str]
    actions: list[str]
    context_keys: list[str]
    combining: str = (
        "deny > require_approval > allow; the baseline is evaluated first and organization rules can only add "
        "restrictions; restrictive defaults are relaxed only by an organization allow rule naming the action exactly"
    )


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=3, max_length=64)
    context: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    mission_id: str | None = None

    @field_validator("context")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("context may contain at most 64 keys")
        return value


class DecisionOut(BaseModel):
    action: str
    effect: Literal["allow", "deny", "require_approval"]
    reasons: list[str]
    matched_rules: list[str]
    policy_versions: list[str]
    obligations: dict[str, Any]
    approver_permission: str | None = None
    default_applied: bool = False
    context: dict[str, Any] = Field(default_factory=dict, description="The enriched context that was evaluated")


# ---------------------------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------------------------
class ApprovalOut(ORMModel):
    id: str
    project_id: str | None = None
    workspace_id: str | None = None
    mission_id: str | None = None
    action: str
    subject_type: str
    subject_id: str
    title: str
    request_payload: dict[str, Any]
    risk_level: str
    estimated_cost_usd: Money
    requested_by_type: str
    requested_by_id: str | None = None
    requested_by_agent_run_id: str | None = None
    requested_by_label: str | None = None
    policy_decision: dict[str, Any]
    required_permission: str
    status: str
    decided_by_id: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    workflow_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason must contain at least 3 non-blank characters")
        return value


class ApprovalCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------------------------
class BudgetDimensionOut(BaseModel):
    dimension: str
    limit: Amount | None = None
    spent: Amount
    remaining: Amount | None = None
    utilization: float | None = None
    status: Literal["unlimited", "ok", "warning", "exceeded"]


class ProjectBudgetOut(BaseModel):
    project_id: str
    limits: dict[str, float]
    spent_usd: dict[str, float]
    exceeded: list[str]


class BudgetStateOut(BaseModel):
    mission_id: str
    mission_status: str
    ok: bool
    reason: str | None = None
    warnings: list[str]
    exceeded: list[str]
    dimensions: list[BudgetDimensionOut]
    time_budget_seconds: int | None = None
    deadline: datetime | None = None
    started_at: datetime | None = None
    elapsed_seconds: float | None = None
    time_remaining_seconds: float | None = None
    time_exceeded: bool = False
    deadline_passed: bool = False
    project: ProjectBudgetOut | None = None


# ---------------------------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------------------------
class QuotaOut(BaseModel):
    key: str
    unit: Literal["count", "usd", "bytes"]
    window: Literal["current", "calendar_month", "total"]
    limit: float | None = None
    current: float
    remaining: float | None = None
    utilization: float | None = None
    sources: dict[str, float | None] = Field(
        default_factory=dict, description="Limits by source (organization, plan, override); the lowest wins"
    )


class QuotasOut(BaseModel):
    plan_key: str | None = None
    items: list[QuotaOut]


class QuotasUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quotas: dict[str, float | int | None] = Field(
        description="Organization quota limits; null restores the platform default for that key"
    )


# ---------------------------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------------------------
class AuditEntryOut(ORMModel):
    id: str
    created_at: datetime
    user_id: str | None = None
    actor_type: str
    actor_label: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    request_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
