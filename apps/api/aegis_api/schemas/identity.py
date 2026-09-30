"""Identity & access management schemas: roles, service accounts, SSO, quotas, feature flags."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from aegis_api.schemas.auth import ApiKeyOut
from aegis_api.schemas.common import ORMModel


class PermissionOut(ORMModel):
    key: str
    category: str
    description: str


class RoleCreate(BaseModel):
    key: str = Field(min_length=3, max_length=48, pattern=r"^[a-z][a-z0-9_]{2,47}$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] = Field(min_length=1, max_length=200)


class RoleOut(BaseModel):
    id: str
    key: str
    name: str
    description: str | None = None
    is_system: bool
    permissions: list[str]


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    role: str = Field(default="researcher", max_length=48)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    project_ids: list[str] = Field(default_factory=list, max_length=100)


class ServiceAccountUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    disabled: bool | None = None
    project_ids: list[str] | None = Field(default=None, max_length=100)


class ServiceAccountOut(ORMModel):
    id: str
    name: str
    description: str | None = None
    role: str
    scopes: list[str]
    project_ids: list[str]
    disabled_at: datetime | None = None
    created_at: datetime


class ServiceAccountKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_in_days: int | None = Field(default=90, ge=1, le=730)


class ServiceAccountKeyCreated(BaseModel):
    api_key: ApiKeyOut
    plaintext: str = Field(description="Shown exactly once")


class SSOConnectionCreate(BaseModel):
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=2000, description="Stored encrypted; never returned")
    email_domains: list[str] = Field(min_length=1, max_length=50)
    default_role: str = Field(default="viewer", max_length=48)
    enabled: bool = False

    @field_validator("issuer")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("issuer must be an https URL")
        return value.rstrip("/")


class SSOConnectionOut(ORMModel):
    id: str
    protocol: str
    issuer: str
    client_id: str
    email_domains: list[str]
    default_role: str
    enabled: bool
    has_client_secret: bool = False
    created_at: datetime


class QuotaOut(ORMModel):
    max_agents: int | None = None
    max_concurrent_experiments: int | None = None
    max_llm_spend_usd: float | None = None
    max_compute_spend_usd: float | None = None
    max_storage_bytes: int | None = None
    max_research_jobs: int | None = None
    max_autonomy_level: str
    expensive_compute_threshold_usd: float
    allow_auto_strategy_promotion: bool
    allow_external_models: bool
    retention: dict[str, int] = Field(default_factory=dict)


class QuotaUpdate(BaseModel):
    max_agents: int | None = Field(default=None, ge=0)
    max_concurrent_experiments: int | None = Field(default=None, ge=0, le=1000)
    max_llm_spend_usd: float | None = Field(default=None, ge=0)
    max_compute_spend_usd: float | None = Field(default=None, ge=0)
    max_storage_bytes: int | None = Field(default=None, ge=0)
    max_research_jobs: int | None = Field(default=None, ge=0)
    max_autonomy_level: str | None = None
    expensive_compute_threshold_usd: float | None = Field(default=None, ge=0)
    allow_auto_strategy_promotion: bool | None = None
    allow_external_models: bool | None = None
    retention: dict[str, int] | None = Field(default=None, description="Retention days per data class")


class FeatureFlagUpdate(BaseModel):
    enabled: bool
