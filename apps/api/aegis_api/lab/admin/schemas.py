"""API contract for the platform-admin (cross-tenant operator) API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis_api.schemas.common import ORMModel


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminOrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    subscription_plan: str | None
    member_count: int
    suspended: bool
    suspended_reason: str | None = None
    suspended_at: str | None = None
    is_demo: bool
    is_sandbox: bool
    created_at: datetime


class SuspendRequest(_Strict):
    reason: str = Field(min_length=3, max_length=500, description="Recorded in the organization's audit log")


class UnsuspendRequest(_Strict):
    reason: str | None = Field(default=None, max_length=500)


class AdminJobOut(ORMModel):
    id: str
    organization_id: str
    project_id: str | None
    mission_id: str | None
    kind: str
    subject_type: str
    subject_id: str
    engine: str
    status: str
    attempt: int
    error: str | None
    cancel_requested: bool
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminComputeJobOut(ORMModel):
    id: str
    organization_id: str
    project_id: str
    mission_id: str | None
    purpose: str
    backend: str
    image: str
    status: str
    status_reason: str | None
    attempt: int
    exit_code: int | None
    timeout_seconds: int
    estimated_cost_usd: Decimal
    cost_usd: Decimal
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime


class UsageTotals(BaseModel):
    llm_cost_usd: Decimal
    llm_calls: int
    llm_failed_calls: int
    input_tokens: int
    output_tokens: int
    compute_cost_usd: Decimal
    compute_jobs: int
    cpu_seconds: float
    gpu_seconds: float
    wall_seconds: float
    total_cost_usd: Decimal


class OrganizationUsage(BaseModel):
    organization_id: str
    organization_name: str | None
    llm_cost_usd: Decimal
    compute_cost_usd: Decimal
    total_cost_usd: Decimal
    llm_calls: int
    compute_jobs: int


class AdminUsageOut(BaseModel):
    start: datetime
    end: datetime
    totals: UsageTotals
    by_organization: list[OrganizationUsage] = Field(description="Top organizations by total cost")


class AdminFeatureFlagOut(BaseModel):
    key: str
    default: bool
    global_override: bool | None = Field(description="Platform-wide override (null = no override)")
    effective_default: bool = Field(description="What organizations without their own override get")
    organization_overrides: int


class AdminFeatureFlagUpdate(_Strict):
    enabled: bool | None = Field(description="true/false sets a platform-wide override; null removes it")


class AdminFeatureFlagsBulkUpdate(_Strict):
    flags: dict[str, bool | None] = Field(min_length=1)


class ModelProvidersOut(BaseModel):
    default_provider: str
    embedding_provider: str
    providers: dict[str, bool] = Field(description="Provider → configured (credentials/base URL present)")


class SystemInfoOut(BaseModel):
    app_name: str
    app_version: str
    environment: str
    python_version: str
    database_version: str | None
    schema_revision: str | None
    library_versions: dict[str, str]
    workflow_engine: str
    execution_backend: str
    event_bus: str
    job_backend: str
    object_storage_backend: str
    malware_scanner: str
    redis_configured: bool
    temporal_configured: bool
    otel_configured: bool
    dev_secrets: bool
    features: dict[str, Any]
