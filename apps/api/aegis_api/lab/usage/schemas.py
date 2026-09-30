"""API contract for usage metering, cost aggregation and billing."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from aegis_api.schemas.common import ORMModel

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

GroupBy = Literal["mission", "experiment", "discovery", "agent", "model", "project", "organization"]
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------------------------------------
# Usage & costs
# ---------------------------------------------------------------------------------------------
class LLMUsageTotals(BaseModel):
    cost_usd: Money = Decimal("0")
    calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0


class ComputeUsageTotals(BaseModel):
    cost_usd: Money = Decimal("0")
    jobs: int = 0
    cpu_seconds: float = 0.0
    gpu_seconds: float = 0.0
    wall_seconds: float = 0.0


class ToolUsageTotals(BaseModel):
    cost_usd: Money = Decimal("0")
    invocations: int = 0


class StorageUsageTotals(BaseModel):
    bytes_stored: int = Field(0, description="Net stored bytes at the end of the period")
    bytes_delta: int = Field(0, description="Net change in stored bytes during the period")


class UsageSummaryOut(BaseModel):
    period_start: datetime
    period_end: datetime
    project_id: str | None = None
    currency: str
    llm: LLMUsageTotals
    compute: ComputeUsageTotals
    tools: ToolUsageTotals
    storage: StorageUsageTotals
    total_cost_usd: Money


class CostGroupOut(BaseModel):
    key: str | None = Field(
        description="Group id (mission/experiment/… id, 'provider:model', role); null = unattributed"
    )
    label: str
    llm_usd: Money = Decimal("0")
    compute_usd: Money = Decimal("0")
    tool_usd: Money = Decimal("0")
    total_usd: Money = Decimal("0")
    tokens: int = 0
    calls: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class CostBreakdownOut(BaseModel):
    group_by: GroupBy
    period_start: datetime
    period_end: datetime
    currency: str
    total_usd: Money
    items: list[CostGroupOut]


class ModelUsageOut(ORMModel):
    id: str
    created_at: datetime
    project_id: str | None = None
    mission_id: str | None = None
    agent_run_id: str | None = None
    research_task_id: str | None = None
    provider: str
    model: str
    model_version: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    task_type: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    latency_ms: int
    cost_usd: Money
    cost_estimated: bool
    cost_basis: str | None = None
    success: bool
    error_code: str | None = None
    retry_count: int
    route_reason: str | None = None


class ComputeUsageOut(ORMModel):
    id: str
    created_at: datetime
    project_id: str | None = None
    mission_id: str | None = None
    experiment_id: str | None = None
    compute_job_id: str
    backend: str
    cpu_seconds: float
    gpu_seconds: float
    gpu_type: str | None = None
    memory_mb_seconds: float
    wall_seconds: float
    cost_usd: Money
    cost_basis: str


class ToolCostOut(BaseModel):
    tool_name: str
    tool_source: str
    invocations: int
    succeeded: int
    failed: int
    denied: int
    cost_usd: Money
    avg_latency_ms: float | None = None


class ToolCostsOut(BaseModel):
    period_start: datetime
    period_end: datetime
    currency: str
    total_usd: Money
    items: list[ToolCostOut]


# ---------------------------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------------------------
class BillingPlanOut(ORMModel):
    key: str
    name: str
    description: str | None = None
    quotas: dict[str, Any]
    prices: dict[str, Any]
    is_active: bool


class SubscriptionOut(BaseModel):
    id: str | None = None
    plan_key: str
    plan: BillingPlanOut | None = None
    status: str
    provider: str
    external_id: str | None = None
    current_period_start: datetime
    current_period_end: datetime
    quotas_override: dict[str, Any] = Field(default_factory=dict)
    cancelled_at: datetime | None = None
    is_default: bool = Field(False, description="True when no subscription exists and the free plan applies")


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str = Field(min_length=1, max_length=48)


class UsageRecordOut(ORMModel):
    id: str
    meter: str
    period_start: datetime
    period_end: datetime
    quantity: Money
    cost_usd: Money
    source_counts: dict[str, Any]
    finalized: bool
    external_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class PeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str | None = Field(
        default=None, description="Calendar month 'YYYY-MM' (UTC). Defaults to the current month."
    )

    @field_validator("period")
    @classmethod
    def _period(cls, value: str | None) -> str | None:
        if value is not None and not _PERIOD_RE.match(value):
            raise ValueError("period must be a calendar month formatted 'YYYY-MM'")
        return value


class RollupOut(BaseModel):
    period_start: datetime
    period_end: datetime
    records: list[UsageRecordOut]


class InvoiceOut(ORMModel):
    id: str
    subscription_id: str | None = None
    provider: str
    external_invoice_id: str
    period_start: datetime
    period_end: datetime
    amount_usd: Money
    currency: str
    status: str
    url: str | None = None
    created_at: datetime
