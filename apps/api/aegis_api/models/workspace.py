"""Platform tenancy extensions: workspaces, projects, teams, service accounts, custom roles, quotas, refresh
tokens, idempotency keys and enterprise SSO connections (public schema)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OptimisticLockMixin, OrgMixin, TimestampMixin


class Workspace(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_workspaces_org_slug"),)

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WorkspaceMember(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_ws_user"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24), default="member")  # admin | member | viewer


class Project(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
        Index("ix_projects_org_created", "organization_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(48), default="general")
    # "organization": every org member with lab permissions; "private": project members + org admins only.
    visibility: Mapped[str] = mapped_column(String(16), default="organization")
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    verification_criteria: Mapped[dict[str, Any]] = mapped_column(default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ProjectMember(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24), default="member")  # lead | member | reviewer | viewer


class Team(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)


class TeamMember(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class ServiceAccount(IdMixin, TimestampMixin, OrgMixin, Base):
    """Non-human principal for enterprise automation. Authenticates with API keys it owns; never with a person's
    credentials. Its role/scopes bound every key issued to it."""

    __tablename__ = "service_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_service_accounts_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32), default="researcher")
    scopes: Mapped[list[str]] = mapped_column(default=list)
    project_ids: Mapped[list[str]] = mapped_column(default=list, doc="Optional project restriction (empty = all)")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Permission(Base):
    """Global permission catalogue (reference data, seeded from code)."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(300), default="")


class RoleDefinition(IdMixin, TimestampMixin, Base):
    """System roles (organization_id NULL, immutable) and organization-defined custom roles."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_roles_org_key"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(48))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)


class RolePermission(IdMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_key", name="uq_role_permissions_role_perm"),)

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    permission_key: Mapped[str] = mapped_column(ForeignKey("permissions.key", ondelete="CASCADE"), index=True)


class OrganizationQuota(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "organization_quotas"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_organization_quotas_org"),)

    max_agents: Mapped[int | None] = mapped_column(Integer, default=50)
    max_concurrent_experiments: Mapped[int | None] = mapped_column(Integer, default=4)
    max_llm_spend_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_compute_spend_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_storage_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_research_jobs: Mapped[int | None] = mapped_column(Integer, default=10)
    max_autonomy_level: Mapped[str] = mapped_column(String(40), default="L3_AUTOMATED_EXECUTION")
    expensive_compute_threshold_usd: Mapped[float] = mapped_column(Float, default=5.0)
    allow_auto_strategy_promotion: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_external_models: Mapped[bool] = mapped_column(Boolean, default=False)
    retention: Mapped[dict[str, Any]] = mapped_column(default=dict)


class RefreshToken(IdMixin, CreatedMixin, Base):
    """Rotating refresh tokens. Each use issues a new token in the same family; presenting an already-used
    token (replay) revokes the entire family. Identity-layer table (resolved before a tenant exists)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column()
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class IdempotencyKey(IdMixin, CreatedMixin, OrgMixin, Base):
    """Replay protection for expensive, externally-triggered writes (``Idempotency-Key`` header)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("organization_id", "principal_key", "key", name="uq_idempotency_keys_scope"),
        Index("ix_idempotency_keys_expires", "expires_at"),
    )

    principal_key: Mapped[str] = mapped_column(String(96))
    key: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(500))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress | completed
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime]


class SSOConnection(IdMixin, TimestampMixin, OrgMixin, Base):
    """Enterprise SSO integration point (OIDC implemented; SAML reserved for an adapter)."""

    __tablename__ = "sso_connections"
    __table_args__ = (UniqueConstraint("organization_id", "issuer", name="uq_sso_connections_org_issuer"),)

    protocol: Mapped[str] = mapped_column(String(8), default="oidc")  # oidc | saml
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    client_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True
    )
    email_domains: Mapped[list[str]] = mapped_column(default=list)
    default_role: Mapped[str] = mapped_column(String(32), default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
