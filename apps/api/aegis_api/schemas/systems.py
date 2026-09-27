"""AI system and provider schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aegis_api.models.enums import (
    DataClassification,
    Environment,
    ProviderKind,
    RiskTier,
    SystemType,
)
from aegis_api.schemas.common import ORMModel


class ProviderCreate(BaseModel):
    kind: ProviderKind
    name: str = Field(min_length=1, max_length=120)
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = Field(default=None, description="Stored encrypted; never returned")
    allow_data_processing: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None
    allow_data_processing: bool | None = None
    settings: dict[str, Any] | None = None


class ProviderOut(ORMModel):
    id: str
    kind: str
    name: str
    base_url: str | None
    default_model: str | None
    allow_data_processing: bool
    status: str
    last_checked_at: datetime | None = None
    last_error: str | None = None
    has_credentials: bool = False


class ConnectionTestResult(BaseModel):
    ok: bool
    status: str
    detail: str
    models: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    data_leaves_organization: bool = False


class SystemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    system_type: SystemType = SystemType.LLM
    environment: Environment = Environment.DEVELOPMENT
    owner_name: str | None = None
    business_purpose: str | None = None
    risk_tier: RiskTier = RiskTier.LIMITED
    provider_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    endpoint_url: str | None = None
    endpoint_auth_secret: str | None = Field(
        default=None, description="Bearer token for the endpoint; stored encrypted"
    )
    system_instructions: str | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
    config: dict[str, Any] = Field(default_factory=dict)


class SystemUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = None
    environment: Environment | None = None
    owner_name: str | None = None
    business_purpose: str | None = None
    risk_tier: RiskTier | None = None
    provider_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    endpoint_url: str | None = None
    system_instructions: str | None = None
    data_classification: DataClassification | None = None
    config: dict[str, Any] | None = None
    change_note: str | None = None


class SystemOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None
    system_type: str
    environment: str
    owner_name: str | None
    business_purpose: str | None
    risk_tier: str
    provider_id: str | None
    model_name: str | None
    model_version: str | None
    data_classification: str
    version: str
    config: dict[str, Any]
    is_demo: bool
    status: str
    created_at: datetime
    updated_at: datetime


class SystemSummary(ORMModel):
    id: str
    name: str
    slug: str
    system_type: str
    environment: str
    risk_tier: str
    model_name: str | None
    is_demo: bool
    version: str


class SystemVersionOut(ORMModel):
    id: str
    version: str
    model_name: str | None
    model_version: str | None
    prompt_version: str | None
    changed_fields: list[str]
    change_summary: str | None
    created_at: datetime


class ChangeImpact(BaseModel):
    changed_fields: list[str]
    affected_controls: int
    affected_tests: int
    recommended_regression_tests: int
    notes: list[str]


class SystemEventOut(ORMModel):
    id: str
    type: str
    title: str
    description: str | None
    occurred_at: datetime
    data: dict[str, Any]
