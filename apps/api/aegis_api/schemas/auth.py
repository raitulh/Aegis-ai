"""Auth, organization, membership, user and API key schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from aegis_api.schemas.common import ORMModel


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    full_name: str | None = Field(default=None, max_length=160)
    organization_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SupabaseExchangeRequest(BaseModel):
    access_token: str


class UserOut(ORMModel):
    id: str
    email: str
    full_name: str | None
    is_guest: bool
    auth_provider: str


class OrganizationOut(ORMModel):
    id: str
    name: str
    slug: str
    plan: str
    is_demo: bool
    is_sandbox: bool
    expires_at: datetime | None = None


class MembershipOut(ORMModel):
    id: str
    role: str
    status: str
    user: UserOut


class SessionOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    permissions: list[str]
    is_guest: bool
    expires_at: datetime | None = None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = "analyst"
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_in_days: int | None = Field(default=None, ge=1, le=730)
    test: bool = False


class ApiKeyOut(ORMModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    role: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(BaseModel):
    api_key: ApiKeyOut
    plaintext: str = Field(description="Shown once. Store it securely; it cannot be retrieved again.")


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "viewer"


class RoleUpdate(BaseModel):
    role: str


class MemberOut(BaseModel):
    membership_id: str
    user_id: str
    name: str | None
    email: str
    role: str
    status: str
    last_active_at: datetime | None = None


class TokenRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    organization_id: str | None = Field(default=None, description="Workspace to bind the token to (default: primary)")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")
    refresh_token: str
    refresh_expires_in: int
    organization_id: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)


class ApiKeyRotated(BaseModel):
    api_key: ApiKeyOut
    plaintext: str = Field(description="Shown exactly once")
    revoked_key_id: str
