"""API contract for identity: organizations, hierarchy, teams, users, RBAC, service accounts, tokens, SSO."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

from aegis_api.schemas.common import ORMModel
from engines.lab.states import AutonomyLevel

Visibility = Literal["organization", "restricted"]
ApiScope = Literal["read", "write", "run", "ingest"]
ExecutionBackend = Literal["local_docker", "kubernetes"]
LLMProviderName = Literal["gemini", "openai", "anthropic", "ollama"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- organizations -------------------------------------------------------------------------------
class OrganizationCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)


class OrganizationUpdate(_Strict):
    name: str = Field(min_length=1, max_length=120)


class OrganizationSummaryOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    role: str
    membership_status: str
    is_current: bool
    suspended: bool
    is_demo: bool
    is_sandbox: bool
    created_at: datetime


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    is_demo: bool
    is_sandbox: bool
    suspended: bool
    member_count: int
    role: str
    created_at: datetime
    updated_at: datetime


class ExecutionPolicyIn(_Strict):
    allowed_images: list[str] | None = Field(default=None, description="Exact image refs or 'prefix*' patterns")
    default_backend: ExecutionBackend | None = None
    gpu_enabled: bool | None = None
    max_timeout_seconds: int | None = Field(default=None, ge=1)


class DataProcessingIn(_Strict):
    allow_external_llm: bool | None = Field(
        default=None, description="Consent to send organization data to hosted model providers"
    )
    allowed_providers: list[LLMProviderName] | None = None


class OrgSettingsUpdate(_Strict):
    max_autonomy_level: AutonomyLevel | None = None
    default_autonomy_level: AutonomyLevel | None = None
    retention: dict[str, int | None] | None = Field(
        default=None, description="Days per data class (null = keep indefinitely); merged into current values"
    )
    egress_allowlist: list[str] | None = Field(default=None, description="Hostnames or leading-dot suffixes")
    execution_policy: ExecutionPolicyIn | None = None
    data_processing: DataProcessingIn | None = None
    sso_enforced: bool | None = None
    allow_self_approval: bool | None = Field(
        default=None,
        description="Let a requester decide their own approval requests (weakens separation of duties; default false)",
    )
    lock_version: int | None = Field(default=None, description="Optimistic concurrency: expected current version")


class OrgSettingsOut(ORMModel):
    organization_id: str
    max_autonomy_level: str
    default_autonomy_level: str
    quotas: dict[str, Any] = Field(description="Read-only here; managed by governance")
    retention: dict[str, Any]
    egress_allowlist: list[str]
    execution_policy: dict[str, Any]
    data_processing: dict[str, Any]
    sso_enforced: bool
    allow_self_approval: bool = Field(
        default=False, description="Separation of duties for approvals is relaxed when true (governance)"
    )
    lock_version: int
    updated_at: datetime


class FeatureFlagOut(BaseModel):
    key: str
    enabled: bool
    default: bool
    source: Literal["organization", "global", "default"]


class FeatureFlagUpdate(_Strict):
    enabled: bool


# --- workspaces ----------------------------------------------------------------------------------
class WorkspaceCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    settings: dict[str, Any] | None = None


class WorkspaceOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None
    settings: dict[str, Any]
    created_by_id: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberCreate(_Strict):
    user_id: uuid.UUID
    role: str = "researcher"


class WorkspaceMemberOut(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    role: str
    email: str | None
    full_name: str | None
    created_at: datetime


# --- projects ------------------------------------------------------------------------------------
class ProjectBudget(_Strict):
    max_total_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    max_llm_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    max_compute_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)


class ProjectCreate(_Strict):
    workspace_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=10000)
    visibility: Visibility = "organization"
    max_autonomy_level: AutonomyLevel | None = None
    budget: ProjectBudget | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10000)
    visibility: Visibility | None = None
    max_autonomy_level: AutonomyLevel | None = None
    budget: ProjectBudget | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(ORMModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    description: str | None
    visibility: str
    max_autonomy_level: str | None
    budget: dict[str, Any]
    settings: dict[str, Any]
    created_by_id: str | None
    archived_at: datetime | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class ProjectDetailOut(ProjectOut):
    effective_permissions: list[str] = Field(description="Lab permissions the caller holds inside this project")


class ProjectMemberCreate(_Strict):
    user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    role: str

    @model_validator(mode="after")
    def _one_subject(self) -> ProjectMemberCreate:
        if (self.user_id is None) == (self.team_id is None):
            raise ValueError("Provide exactly one of user_id or team_id")
        return self


class ProjectMemberUpdate(_Strict):
    role: str


class ProjectMemberOut(BaseModel):
    id: str
    project_id: str
    user_id: str | None
    team_id: str | None
    role: str
    email: str | None = None
    full_name: str | None = None
    team_name: str | None = None
    created_at: datetime
    updated_at: datetime


# --- teams ---------------------------------------------------------------------------------------
class TeamCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)


class TeamUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)


class TeamOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_by_id: str | None
    member_count: int
    created_at: datetime
    updated_at: datetime


class TeamMemberCreate(_Strict):
    user_id: uuid.UUID


class TeamMemberOut(BaseModel):
    id: str
    team_id: str
    user_id: str
    email: str | None
    full_name: str | None
    created_at: datetime


# --- users ---------------------------------------------------------------------------------------
class MembershipSummary(BaseModel):
    organization_id: str
    organization_name: str
    organization_slug: str
    role: str
    status: str


class UserProfileOut(ORMModel):
    id: str
    email: str
    full_name: str | None
    auth_provider: str
    email_verified: bool
    is_guest: bool
    created_at: datetime
    last_active_at: datetime | None = None


class MeOut(BaseModel):
    kind: str = Field(description="user | api_key | service_account")
    user: UserProfileOut | None
    organization_id: str
    role: str
    auth_method: str
    permissions: list[str]
    lab_permissions: list[str]
    is_platform_admin: bool
    service_account_id: str | None = None
    api_key_id: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    memberships: list[MembershipSummary] = Field(default_factory=list)


class OrgUserOut(BaseModel):
    user_id: str
    membership_id: str
    email: str
    full_name: str | None
    role: str
    status: str
    email_verified: bool
    joined_at: datetime
    last_active_at: datetime | None


# --- RBAC catalog --------------------------------------------------------------------------------
class RoleOut(BaseModel):
    key: str
    name: str
    description: str | None
    is_system: bool
    rank: int
    assignable: bool
    permissions: list[str]


class PermissionOut(BaseModel):
    key: str
    category: str
    description: str
    family: Literal["lab", "core"]
    human_only: bool


# --- service accounts & API keys -----------------------------------------------------------------
def _default_scopes() -> list[ApiScope]:
    return ["read"]


class ServiceAccountCreate(_Strict):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    description: str | None = Field(default=None, max_length=2000)
    role: str = "researcher"
    scopes: list[ApiScope] = Field(default_factory=_default_scopes, min_length=1)
    project_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=200, description="Restrict to these projects (empty = no restriction)"
    )


class ServiceAccountUpdate(_Strict):
    description: str | None = Field(default=None, max_length=2000)
    role: str | None = None
    scopes: list[ApiScope] | None = Field(default=None, min_length=1)
    project_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class ServiceAccountOut(ORMModel):
    id: str
    name: str
    description: str | None
    role: str
    scopes: list[str]
    project_ids: list[str]
    created_by_id: str | None
    disabled_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ServiceAccountKeyCreate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class LabApiKeyOut(ORMModel):
    id: str
    name: str
    prefix: str
    role: str
    scopes: list[str]
    service_account_id: str | None
    rotated_from_id: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyIssuedOut(BaseModel):
    api_key: LabApiKeyOut
    plaintext: str = Field(description="Shown once. Store it securely; it cannot be retrieved again.")


class ApiKeyRotate(_Strict):
    grace_seconds: int = Field(
        default=0, ge=0, le=86400, description="How long the previous key keeps working (0 = revoke immediately)"
    )


class ApiKeyRotatedOut(BaseModel):
    api_key: LabApiKeyOut
    plaintext: str = Field(description="Shown once. Store it securely; it cannot be retrieved again.")
    previous_key_id: str
    previous_key_expires_at: datetime | None
    previous_key_revoked: bool


# --- tokens & credentials --------------------------------------------------------------------------
class TokenRequest(_Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    organization_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_token: str = Field(description="Opaque, single-use; rotate it with POST /auth/token/refresh")
    refresh_token_expires_at: datetime
    organization_id: str


class RefreshRequest(_Strict):
    refresh_token: str = Field(min_length=1, max_length=512)


class PasswordResetRequest(_Strict):
    email: EmailStr


class PasswordResetConfirm(_Strict):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class EmailVerifyConfirm(_Strict):
    token: str = Field(min_length=1, max_length=512)


# --- SSO -----------------------------------------------------------------------------------------
class SSOProviderCreate(_Strict):
    kind: Literal["oidc", "saml"] = "oidc"
    name: str = Field(min_length=1, max_length=120)
    issuer: str | None = Field(default=None, max_length=500, description="Expected issuer (defaults to discovery)")
    discovery_url: str | None = Field(default=None, max_length=1000)
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: SecretStr | None = Field(default=None, description="Stored encrypted; never returned")
    email_domains: list[str] = Field(min_length=1, max_length=50)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    jit_provisioning: bool = False
    default_role: str = "viewer"
    enabled: bool = False


class SSOProviderUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    issuer: str | None = Field(default=None, max_length=500)
    discovery_url: str | None = Field(default=None, max_length=1000)
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: SecretStr | None = None
    email_domains: list[str] | None = Field(default=None, min_length=1, max_length=50)
    scopes: list[str] | None = None
    jit_provisioning: bool | None = None
    default_role: str | None = None
    enabled: bool | None = None


class SSOProviderOut(BaseModel):
    id: str
    kind: str
    name: str
    issuer: str | None
    discovery_url: str | None
    client_id: str | None
    has_client_secret: bool
    email_domains: list[str]
    scopes: list[str]
    jit_provisioning: bool
    default_role: str
    enabled: bool
    redirect_uri: str
    created_at: datetime
    updated_at: datetime


class SSOStartOut(BaseModel):
    authorization_url: str
    expires_at: datetime
