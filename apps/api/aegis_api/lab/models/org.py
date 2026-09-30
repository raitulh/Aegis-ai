"""Organization hierarchy (workspaces → projects), teams, service accounts, RBAC catalog, tokens, SSO."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import user_fk
from engines.lab.states import AutonomyLevel


class OrganizationSettings(IdMixin, TimestampMixin, OrgMixin, Base):
    """Typed per-organization lab configuration: autonomy ceiling, quotas, retention, egress, execution."""

    __tablename__ = "organization_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_organization_settings_org"),)

    max_autonomy_level: Mapped[str] = mapped_column(String(40), default=AutonomyLevel.L3_AUTOMATED_EXECUTION)
    default_autonomy_level: Mapped[str] = mapped_column(String(40), default=AutonomyLevel.L1_RESEARCH_AUTOMATION)
    # max_agents, max_concurrent_experiments, max_llm_spend_usd, max_compute_spend_usd, max_storage_bytes,
    # max_research_jobs, max_missions (per calendar month for spend quotas).
    quotas: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Retention in days per data class: agent_logs, research_events, artifacts, raw_outputs, audit_logs,
    # llm_metadata. ``None`` means keep indefinitely. Evidence required for reproducibility is never purged.
    retention: Mapped[dict[str, Any]] = mapped_column(default=dict)
    egress_allowlist: Mapped[list[str]] = mapped_column(default=list)
    # allowed_images, default_backend, gpu_enabled, max_timeout_seconds
    execution_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # allow_external_llm (consent to send org data to hosted model providers), allowed_providers
    data_processing: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sso_enforced: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class Workspace(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_workspaces_org_slug"),
        UniqueConstraint("id", "organization_id", name="uq_workspaces_scope"),
    )

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WorkspaceMember(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_ws_user"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))


class Project(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_projects_ws_slug"),
        UniqueConstraint("id", "workspace_id", "organization_id", name="uq_projects_scope"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name="fk_projects_workspace_scope",
        ),
        CheckConstraint("visibility in ('organization','restricted')", name="visibility"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    # organization: every org member with the permission may access; restricted: explicit members only.
    visibility: Mapped[str] = mapped_column(String(16), default="organization")
    max_autonomy_level: Mapped[str | None] = mapped_column(String(40))
    # Project-level budget ceilings (max_total_cost_usd, max_llm_cost_usd, max_compute_cost_usd).
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Team(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class TeamMember(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class ProjectMember(IdMixin, TimestampMixin, OrgMixin, Base):
    """Grants a user or a team a project-level role (in addition to their organization role)."""

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        UniqueConstraint("project_id", "team_id", name="uq_project_members_project_team"),
        CheckConstraint("(user_id IS NOT NULL) <> (team_id IS NOT NULL)", name="member_subject"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32))


class ServiceAccount(IdMixin, TimestampMixin, OrgMixin, Base):
    """Non-human identity for enterprise automation. Authenticates with API keys bound to it."""

    __tablename__ = "service_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_service_accounts_org_name"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    scopes: Mapped[list[str]] = mapped_column(default=list)
    # Optional restriction to specific projects (empty = all projects the role can access).
    project_ids: Mapped[list[str]] = mapped_column(default=list)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RoleRecord(IdMixin, TimestampMixin, Base):
    """Role catalog. ``organization_id`` NULL = built-in system role (readable by every tenant)."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_roles_org_key", postgresql_nulls_not_distinct=True),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(48))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class PermissionRecord(IdMixin, CreatedMixin, Base):
    """Global permission catalog (e.g. ``mission:create``)."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(48))
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(IdMixin, CreatedMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_perm"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), index=True)


class RefreshToken(IdMixin, CreatedMixin, Base):
    """Rotating refresh tokens (identity layer; not RLS-scoped, like ``auth_sessions``).

    Each refresh rotates the token within a *family*. Presenting an already-rotated token is treated as
    theft: the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    rotated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class IdentityProvider(IdMixin, TimestampMixin, OrgMixin, Base):
    """Enterprise SSO connection (OIDC implemented; SAML is a declared integration point)."""

    __tablename__ = "identity_providers"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_identity_providers_org_name"),
        CheckConstraint("kind in ('oidc','saml')", name="kind"),
    )

    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(120))
    issuer: Mapped[str | None] = mapped_column(String(500))
    discovery_url: Mapped[str | None] = mapped_column(String(1000))
    client_id: Mapped[str | None] = mapped_column(String(300))
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    email_domains: Mapped[list[str]] = mapped_column(default=list)
    scopes: Mapped[list[str]] = mapped_column(default=list)
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, default=False)
    default_role: Mapped[str] = mapped_column(String(32), default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
