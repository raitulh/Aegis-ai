"""HTTP API for identity: organizations, workspaces, projects, teams, users, RBAC catalog, service accounts,
API-key rotation, JWT/refresh tokens, password reset, email verification and enterprise SSO.

Thin layer: authentication/authorization dependencies, request parsing and response mapping only.
Identity-layer endpoints that run before a tenant context exists (token grants, password reset, SSO
login, organization creation) use the owner session and commit explicitly; everything else uses the
RLS-scoped ``get_db`` session.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import _raw_session, get_current_principal, get_db
from aegis_api.errors import Forbidden, Unauthorized
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, require_actor
from aegis_api.lab.identity import credentials, organizations, service, service_accounts, sso, tokens
from aegis_api.lab.identity.schemas import (
    ApiKeyIssuedOut,
    ApiKeyRotate,
    ApiKeyRotatedOut,
    EmailVerifyConfirm,
    FeatureFlagOut,
    FeatureFlagUpdate,
    LabApiKeyOut,
    MeOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationSummaryOut,
    OrganizationUpdate,
    OrgSettingsOut,
    OrgSettingsUpdate,
    OrgUserOut,
    PasswordResetConfirm,
    PasswordResetRequest,
    PermissionOut,
    ProjectCreate,
    ProjectDetailOut,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectMemberUpdate,
    ProjectOut,
    ProjectUpdate,
    RefreshRequest,
    RoleOut,
    ServiceAccountCreate,
    ServiceAccountKeyCreate,
    ServiceAccountOut,
    ServiceAccountUpdate,
    SSOProviderCreate,
    SSOProviderOut,
    SSOProviderUpdate,
    SSOStartOut,
    TeamCreate,
    TeamMemberCreate,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
    TokenRequest,
    TokenResponse,
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberOut,
    WorkspaceOut,
    WorkspaceUpdate,
)
from aegis_api.models import User
from aegis_api.ratelimit import client_ip, public_rate_limit
from aegis_api.routers.auth import _session_out, _set_cookie
from aegis_api.schemas.auth import SessionOut
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.security.context import Principal

router = APIRouter(prefix="/api/v1", tags=["Organizations"])

AUTH: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required"},
    403: {"description": "Missing permission, suspended organization, or human-only action"},
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Not found (or owned by another organization)"}}
CONFLICT: dict[int | str, dict[str, Any]] = {409: {"description": "Conflicts with the current state"}}
INVALID: dict[int | str, dict[str, Any]] = {422: {"description": "Validation error"}}


def _client(request: Request) -> tokens.ClientInfo:
    return tokens.ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


def _token_response(pair: tokens.TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access.token,
        expires_in=pair.access.expires_in,
        refresh_token=pair.refresh_token,
        refresh_token_expires_at=pair.refresh_expires_at,
        organization_id=str(pair.membership.organization_id),
    )


# =====================================================================================================
# Organizations
# =====================================================================================================
@router.get(
    "/organizations",
    summary="List my organizations",
    description="Organizations the caller is a member of (API keys and service accounts see only their own).",
    response_model=list[OrganizationSummaryOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_organizations(
    actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> list[OrganizationSummaryOut]:
    return organizations.list_my_organizations(db, actor)


@router.post(
    "/organizations",
    summary="Create an organization",
    description="Create another organization; the calling user becomes its owner. Signed-in humans only.",
    response_model=OrganizationSummaryOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **INVALID, 429: {"description": "Rate limited"}},
)
def create_organization(
    body: OrganizationCreate, actor: Actor = Depends(get_actor), admin_db: Session = Depends(_raw_session)
) -> OrganizationSummaryOut:
    org, membership = organizations.create_organization(admin_db, actor, body.name)
    admin_db.commit()
    return organizations.organization_summary(org, membership.role, membership.status, current=False)


@router.get(
    "/organizations/current",
    summary="Get the current organization",
    description="The organization the caller is acting in, with member count and the caller's role.",
    response_model=OrganizationOut,
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def get_current_organization(
    actor: Actor = Depends(require_actor("org:read")), db: Session = Depends(get_db)
) -> OrganizationOut:
    return organizations.get_current_organization(db, actor)


@router.patch(
    "/organizations/current",
    summary="Rename the current organization",
    description="Requires `org:manage`. Recorded in the audit log.",
    response_model=OrganizationOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **INVALID},
)
def update_current_organization(
    body: OrganizationUpdate, actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> OrganizationOut:
    return organizations.update_current_organization(db, actor, name=body.name)


@router.get(
    "/organizations/current/settings",
    summary="Get organization lab settings",
    description="Autonomy ceiling, retention, egress allowlist, execution policy, data processing, SSO "
    "enforcement, approval self-approval policy and (read-only) quotas.",
    response_model=OrgSettingsOut,
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def get_org_settings(
    actor: Actor = Depends(require_actor("org:read")), db: Session = Depends(get_db)
) -> OrgSettingsOut:
    return organizations.settings_out(db, organizations.get_organization_settings(db, actor))


@router.patch(
    "/organizations/current/settings",
    summary="Update organization lab settings",
    description="Partial update (admins). Quotas are managed by governance and cannot be changed here. "
    "Pass `lock_version` for optimistic concurrency.",
    response_model=OrgSettingsOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **CONFLICT, **INVALID},
)
def update_org_settings(
    body: OrgSettingsUpdate, actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> OrgSettingsOut:
    return organizations.settings_out(db, organizations.update_organization_settings(db, actor, body))


@router.get(
    "/organizations/current/features",
    summary="List feature flags",
    description="Effective feature flags (organization override > global override > platform default).",
    response_model=list[FeatureFlagOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_features(
    actor: Actor = Depends(require_actor("org:read")), db: Session = Depends(get_db)
) -> list[FeatureFlagOut]:
    return organizations.list_features(db, actor)


@router.put(
    "/organizations/current/features/{key}",
    summary="Set a feature flag for the organization",
    description="Organization-level override for a known feature key (admins). Recorded as FEATURE_FLAG_CHANGED.",
    response_model=FeatureFlagOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def set_feature(
    key: str,
    body: FeatureFlagUpdate,
    actor: Actor = Depends(require_actor("org:manage")),
    db: Session = Depends(get_db),
) -> FeatureFlagOut:
    return organizations.set_feature(db, actor, key, enabled=body.enabled)


# =====================================================================================================
# Workspaces
# =====================================================================================================
@router.post(
    "/workspaces",
    summary="Create a workspace",
    description="The slug is derived from the name when omitted (unique within the organization).",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **CONFLICT, **INVALID},
)
def create_workspace(
    body: WorkspaceCreate, actor: Actor = Depends(require_actor("workspace:create")), db: Session = Depends(get_db)
) -> WorkspaceOut:
    return WorkspaceOut.model_validate(service.create_workspace(db, actor, body))


@router.get(
    "/workspaces",
    summary="List workspaces",
    description="Paginated; archived workspaces are excluded unless `include_archived=true`.",
    response_model=Page[WorkspaceOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_workspaces(
    params: PageParams = Depends(),
    include_archived: bool = Query(False, description="Include archived workspaces"),
    q: str | None = Query(None, max_length=200, description="Filter by name"),
    actor: Actor = Depends(require_actor("workspace:read")),
    db: Session = Depends(get_db),
) -> Page[WorkspaceOut]:
    return service.list_workspaces(db, actor, params, include_archived=include_archived, q=q)


@router.get(
    "/workspaces/{workspace_id}",
    summary="Get a workspace",
    description="Workspaces of other organizations answer 404.",
    response_model=WorkspaceOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def get_workspace(
    workspace_id: uuid.UUID, actor: Actor = Depends(require_actor("workspace:read")), db: Session = Depends(get_db)
) -> WorkspaceOut:
    return WorkspaceOut.model_validate(service.get_workspace(db, actor, workspace_id))


@router.patch(
    "/workspaces/{workspace_id}",
    summary="Update a workspace",
    description="Name, description and settings. Archived workspaces cannot be modified (409).",
    response_model=WorkspaceOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdate,
    actor: Actor = Depends(require_actor("workspace:manage")),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    return WorkspaceOut.model_validate(service.update_workspace(db, actor, workspace_id, body))


@router.post(
    "/workspaces/{workspace_id}/archive",
    summary="Archive a workspace",
    description="Archives the workspace and all of its active projects. Nothing is deleted.",
    response_model=WorkspaceOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def archive_workspace(
    workspace_id: uuid.UUID, actor: Actor = Depends(require_actor("workspace:manage")), db: Session = Depends(get_db)
) -> WorkspaceOut:
    return WorkspaceOut.model_validate(service.archive_workspace(db, actor, workspace_id))


@router.get(
    "/workspaces/{workspace_id}/members",
    summary="List workspace members",
    description="Members of the workspace with their workspace role.",
    response_model=list[WorkspaceMemberOut],
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def list_workspace_members(
    workspace_id: uuid.UUID, actor: Actor = Depends(require_actor("workspace:read")), db: Session = Depends(get_db)
) -> list[WorkspaceMemberOut]:
    return service.list_workspace_members(db, actor, workspace_id)


@router.post(
    "/workspaces/{workspace_id}/members",
    summary="Add a workspace member",
    description="The user must be an active member of the organization.",
    response_model=WorkspaceMemberOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def add_workspace_member(
    workspace_id: uuid.UUID,
    body: WorkspaceMemberCreate,
    actor: Actor = Depends(require_actor("workspace:manage")),
    db: Session = Depends(get_db),
) -> WorkspaceMemberOut:
    return service.add_workspace_member(db, actor, workspace_id, user_id=body.user_id, role=body.role)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    summary="Remove a workspace member",
    description="Signed-in humans with `workspace:manage` only.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def remove_workspace_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: Actor = Depends(require_actor("workspace:manage")),
    db: Session = Depends(get_db),
) -> Message:
    service.remove_workspace_member(db, actor, workspace_id, user_id)
    return Message(message="Workspace member removed")


# =====================================================================================================
# Projects
# =====================================================================================================
@router.post(
    "/projects",
    summary="Create a project",
    description="Creates a project in a workspace. The creator becomes a project member with the "
    "`research_lead` role. `max_autonomy_level` may not exceed the organization maximum.",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def create_project(
    body: ProjectCreate, actor: Actor = Depends(require_actor("project:create")), db: Session = Depends(get_db)
) -> ProjectOut:
    return ProjectOut.model_validate(service.create_project(db, actor, body))


@router.get(
    "/projects",
    summary="List projects",
    description="Only projects visible to the caller (restricted projects require membership).",
    response_model=Page[ProjectOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_projects(
    params: PageParams = Depends(),
    workspace_id: uuid.UUID | None = Query(None, description="Filter by workspace"),
    include_archived: bool = Query(False, description="Include archived projects"),
    q: str | None = Query(None, max_length=200, description="Filter by name"),
    actor: Actor = Depends(require_actor("project:read")),
    db: Session = Depends(get_db),
) -> Page[ProjectOut]:
    return service.list_projects(db, actor, params, workspace_id=workspace_id, include_archived=include_archived, q=q)


@router.get(
    "/projects/{project_id}",
    summary="Get a project",
    description="Includes the lab permissions the caller holds inside the project (org role ∪ project roles).",
    response_model=ProjectDetailOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def get_project(
    project_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ProjectDetailOut:
    project = service.get_project(db, actor, project_id)
    base = ProjectOut.model_validate(project).model_dump()
    return ProjectDetailOut(**base, effective_permissions=service.project_permissions(db, actor, project))


@router.patch(
    "/projects/{project_id}",
    summary="Update a project",
    description="Requires `project:manage` in the organization or through a project role.",
    response_model=ProjectOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ProjectOut:
    return ProjectOut.model_validate(service.update_project(db, actor, project_id, body))


@router.post(
    "/projects/{project_id}/archive",
    summary="Archive a project",
    description="Archived projects are read-only and hidden from default listings. Requires `project:manage`.",
    response_model=ProjectOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def archive_project(
    project_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ProjectOut:
    return ProjectOut.model_validate(service.archive_project(db, actor, project_id))


@router.get(
    "/projects/{project_id}/members",
    summary="List project members",
    description="Users and teams holding a project role.",
    response_model=list[ProjectMemberOut],
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def list_project_members(
    project_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> list[ProjectMemberOut]:
    return service.list_project_members(db, actor, project_id)


@router.post(
    "/projects/{project_id}/members",
    summary="Add a project member",
    description="Grant a user XOR a team a project role (never `owner`). You can only grant roles whose "
    "permissions you hold in the project.",
    response_model=ProjectMemberOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def add_project_member(
    project_id: uuid.UUID, body: ProjectMemberCreate, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ProjectMemberOut:
    member = service.add_project_member(
        db, actor, project_id, user_id=body.user_id, team_id=body.team_id, role=body.role
    )
    return service.project_member_out(db, member)


@router.patch(
    "/projects/{project_id}/members/{member_id}",
    summary="Change a project member's role",
    description="You can only change members whose current and new role permissions you hold. Audited as PROJECT_MEMBER_CHANGED.",
    response_model=ProjectMemberOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_project_member(
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    body: ProjectMemberUpdate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ProjectMemberOut:
    member = service.update_project_member(db, actor, project_id, member_id, role=body.role)
    return service.project_member_out(db, member)


@router.delete(
    "/projects/{project_id}/members/{member_id}",
    summary="Remove a project member",
    description="You can only remove members whose role permissions you hold. Audited as PROJECT_MEMBER_CHANGED.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def remove_project_member(
    project_id: uuid.UUID, member_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> Message:
    service.remove_project_member(db, actor, project_id, member_id)
    return Message(message="Project member removed")


# =====================================================================================================
# Teams
# =====================================================================================================
@router.post(
    "/teams",
    summary="Create a team",
    description="Teams group users; granting a team a project role grants it to every member. Signed-in humans with `team:manage` only.",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **CONFLICT, **INVALID},
)
def create_team(
    body: TeamCreate, actor: Actor = Depends(require_actor("team:manage")), db: Session = Depends(get_db)
) -> TeamOut:
    return service.team_out(db, service.create_team(db, actor, body))


@router.get(
    "/teams",
    summary="List teams",
    description="Paginated, ordered by name.",
    response_model=Page[TeamOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_teams(
    params: PageParams = Depends(),
    q: str | None = Query(None, max_length=200, description="Filter by name"),
    actor: Actor = Depends(require_actor("team:read")),
    db: Session = Depends(get_db),
) -> Page[TeamOut]:
    return service.list_teams(db, actor, params, q=q)


@router.get(
    "/teams/{team_id}",
    summary="Get a team",
    description="Teams of other organizations answer 404.",
    response_model=TeamOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def get_team(
    team_id: uuid.UUID, actor: Actor = Depends(require_actor("team:read")), db: Session = Depends(get_db)
) -> TeamOut:
    return service.team_out(db, service.get_team(db, actor, team_id))


@router.patch(
    "/teams/{team_id}",
    summary="Update a team",
    description="Rename or re-describe a team (`team:manage`).",
    response_model=TeamOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    actor: Actor = Depends(require_actor("team:manage")),
    db: Session = Depends(get_db),
) -> TeamOut:
    return service.team_out(db, service.update_team(db, actor, team_id, body))


@router.delete(
    "/teams/{team_id}",
    summary="Delete a team",
    description="Removes the team and every project role granted through it.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def delete_team(
    team_id: uuid.UUID, actor: Actor = Depends(require_actor("team:manage")), db: Session = Depends(get_db)
) -> Message:
    service.delete_team(db, actor, team_id)
    return Message(message="Team deleted")


@router.get(
    "/teams/{team_id}/members",
    summary="List team members",
    description="Users in the team.",
    response_model=list[TeamMemberOut],
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def list_team_members(
    team_id: uuid.UUID, actor: Actor = Depends(require_actor("team:read")), db: Session = Depends(get_db)
) -> list[TeamMemberOut]:
    return service.list_team_members(db, actor, team_id)


@router.post(
    "/teams/{team_id}/members",
    summary="Add a team member",
    description="The user must be an active organization member. Non-admins cannot join users to teams whose project roles exceed their own permissions.",
    response_model=TeamMemberOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberCreate,
    actor: Actor = Depends(require_actor("team:manage")),
    db: Session = Depends(get_db),
) -> TeamMemberOut:
    return service.add_team_member(db, actor, team_id, user_id=body.user_id)


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    summary="Remove a team member",
    description="The user loses every project role granted through the team.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: Actor = Depends(require_actor("team:manage")),
    db: Session = Depends(get_db),
) -> Message:
    service.remove_team_member(db, actor, team_id, user_id)
    return Message(message="Team member removed")


# =====================================================================================================
# Users & RBAC catalog
# =====================================================================================================
@router.get(
    "/users/me",
    summary="Who am I",
    description="Profile, organization memberships, current role, effective permissions and platform-admin flag.",
    response_model=MeOut,
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def get_me(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> MeOut:
    return service.get_me(db, actor)


@router.get(
    "/users",
    summary="List organization members",
    description="Requires `team:read` or `project:read`.",
    response_model=Page[OrgUserOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_users(
    params: PageParams = Depends(),
    q: str | None = Query(None, max_length=200, description="Filter by email or name"),
    role: str | None = Query(None, max_length=32),
    membership_status: str | None = Query(None, alias="status", max_length=16),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Page[OrgUserOut]:
    return service.list_users(db, actor, params, q=q, role=role, status=membership_status)


@router.get(
    "/roles",
    summary="List roles",
    description="Built-in roles with descriptions and their permissions.",
    response_model=list[RoleOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_roles(actor: Actor = Depends(get_actor)) -> list[RoleOut]:
    return service.role_catalog()


@router.get(
    "/permissions",
    summary="List permissions",
    description="The lab permission catalog plus the core assurance permissions.",
    response_model=list[PermissionOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_permissions(actor: Actor = Depends(get_actor)) -> list[PermissionOut]:
    return service.permission_catalog()


# =====================================================================================================
# Service accounts & API key rotation
# =====================================================================================================
@router.post(
    "/service-accounts",
    summary="Create a service account",
    description="Non-human identity for automation. Human-only permissions are never granted to it.",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def create_service_account(
    body: ServiceAccountCreate,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    return ServiceAccountOut.model_validate(service_accounts.create_service_account(db, actor, body))


@router.get(
    "/service-accounts",
    summary="List service accounts",
    description="Paginated; includes disabled accounts unless `include_disabled=false`.",
    response_model=Page[ServiceAccountOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_service_accounts(
    params: PageParams = Depends(),
    include_disabled: bool = Query(True),
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> Page[ServiceAccountOut]:
    return service_accounts.list_service_accounts(db, actor, params, include_disabled=include_disabled)


@router.get(
    "/service-accounts/{account_id}",
    summary="Get a service account",
    description="Service accounts of other organizations answer 404.",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def get_service_account(
    account_id: uuid.UUID,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    return ServiceAccountOut.model_validate(service_accounts.get_service_account(db, actor, account_id))


@router.patch(
    "/service-accounts/{account_id}",
    summary="Update a service account",
    description="Changes to role, scopes or project restriction apply immediately to every bound key.",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_service_account(
    account_id: uuid.UUID,
    body: ServiceAccountUpdate,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    return ServiceAccountOut.model_validate(service_accounts.update_service_account(db, actor, account_id, body))


@router.post(
    "/service-accounts/{account_id}/disable",
    summary="Disable a service account",
    description="Every API key bound to the account stops authenticating immediately.",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def disable_service_account(
    account_id: uuid.UUID,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    return ServiceAccountOut.model_validate(service_accounts.disable_service_account(db, actor, account_id))


@router.post(
    "/service-accounts/{account_id}/keys",
    summary="Issue an API key for a service account",
    description="The plaintext key is returned once and cannot be retrieved again.",
    response_model=ApiKeyIssuedOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT},
)
def issue_service_account_key(
    account_id: uuid.UUID,
    body: ServiceAccountKeyCreate,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> ApiKeyIssuedOut:
    issued = service_accounts.issue_service_account_key(db, actor, account_id, body)
    return ApiKeyIssuedOut(api_key=LabApiKeyOut.model_validate(issued.api_key), plaintext=issued.plaintext)


@router.get(
    "/service-accounts/{account_id}/keys",
    summary="List a service account's API keys",
    description="Key metadata only (prefix, role, scopes, expiry, revocation) — never the secret.",
    response_model=list[LabApiKeyOut],
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def list_service_account_keys(
    account_id: uuid.UUID,
    actor: Actor = Depends(require_actor("service_account:manage")),
    db: Session = Depends(get_db),
) -> list[LabApiKeyOut]:
    return [LabApiKeyOut.model_validate(k) for k in service_accounts.list_service_account_keys(db, actor, account_id)]


@router.post(
    "/api-keys/{key_id}/rotate",
    summary="Rotate an API key",
    description="Issues a replacement key (same name, role, scopes and service account). The previous key is "
    "revoked immediately or, with `grace_seconds`, keeps working until the grace period ends.",
    response_model=ApiKeyRotatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def rotate_api_key(
    key_id: uuid.UUID,
    body: ApiKeyRotate,
    actor: Actor = Depends(require_actor("api_keys:manage")),
    db: Session = Depends(get_db),
) -> ApiKeyRotatedOut:
    rotated = service_accounts.rotate_api_key(db, actor, key_id, grace_seconds=body.grace_seconds)
    return ApiKeyRotatedOut(
        api_key=LabApiKeyOut.model_validate(rotated.issued.api_key),
        plaintext=rotated.issued.plaintext,
        previous_key_id=str(rotated.previous.id),
        previous_key_expires_at=rotated.previous.expires_at,
        previous_key_revoked=rotated.previous.revoked_at is not None,
    )


# =====================================================================================================
# Tokens, password reset, email verification (identity layer)
# =====================================================================================================
@router.post(
    "/auth/token",
    tags=["Auth"],
    summary="Obtain an access token (password grant)",
    description="Exchange email + password for a short-lived JWT access token and a rotating refresh token. "
    "Use the access token as `Authorization: Bearer <token>`.",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, 429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def issue_token(body: TokenRequest, request: Request, db: Session = Depends(_raw_session)) -> TokenResponse:
    pair = tokens.password_grant(
        db, email=body.email, password=body.password, organization_id=body.organization_id, client=_client(request)
    )
    db.commit()
    return _token_response(pair)


@router.post(
    "/auth/token/refresh",
    tags=["Auth"],
    summary="Refresh an access token",
    description="Rotates the refresh token. Presenting an already-used refresh token revokes the whole sign-in.",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, 429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def refresh_token(body: RefreshRequest, request: Request, db: Session = Depends(_raw_session)) -> TokenResponse:
    try:
        pair = tokens.refresh(db, refresh_token=body.refresh_token, client=_client(request))
    except tokens.RefreshTokenReuse:
        db.commit()  # the family revocation and its audit record must persist
        raise
    db.commit()
    return _token_response(pair)


@router.post(
    "/auth/token/revoke",
    tags=["Auth"],
    summary="Revoke a refresh token",
    description="Revokes the sign-in the token belongs to. Always succeeds (no token oracle).",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def revoke_token(body: RefreshRequest, request: Request, db: Session = Depends(_raw_session)) -> Message:
    tokens.revoke(db, refresh_token=body.refresh_token, client=_client(request))
    db.commit()
    return Message(message="Token revoked")


@router.post(
    "/auth/password-reset/request",
    tags=["Auth"],
    summary="Request a password reset",
    description="Always returns 202, whether or not the address exists.",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def request_password_reset(body: PasswordResetRequest, db: Session = Depends(_raw_session)) -> Message:
    credentials.request_password_reset(db, email=body.email)
    db.commit()
    return Message(message="If an account exists for this address, a reset link has been sent")


@router.post(
    "/auth/password-reset/confirm",
    tags=["Auth"],
    summary="Complete a password reset",
    description="Sets the new password and signs out every session and refresh token of the account.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**INVALID, 429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def confirm_password_reset(
    body: PasswordResetConfirm, request: Request, db: Session = Depends(_raw_session)
) -> Message:
    credentials.confirm_password_reset(
        db, token=body.token, new_password=body.new_password, request_id=getattr(request.state, "request_id", None)
    )
    db.commit()
    return Message(message="Password updated. Please sign in again.")


@router.post(
    "/auth/email/verify/request",
    tags=["Auth"],
    summary="Request an email verification link",
    description="Emails a single-use, 24-hour verification link to the signed-in user.",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    responses=AUTH,
)
def request_email_verification(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(_raw_session)
) -> Message:
    if principal.auth_method in ("api_key", "service_account"):
        raise Forbidden("Email verification is only available to signed-in users")
    user = db.get(User, principal.user_id)
    if user is None:
        raise Unauthorized("Authentication required")
    sent = credentials.request_email_verification(db, user=user)
    db.commit()
    return Message(message="Verification email sent" if sent else "Email address is already verified")


@router.post(
    "/auth/email/verify/confirm",
    tags=["Auth"],
    summary="Confirm an email address",
    description="Consumes the single-use verification token and marks the address verified.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**INVALID, 429: {"description": "Rate limited"}},
    dependencies=[Depends(public_rate_limit)],
)
def confirm_email_verification(
    body: EmailVerifyConfirm, request: Request, db: Session = Depends(_raw_session)
) -> Message:
    credentials.confirm_email_verification(db, token=body.token, request_id=getattr(request.state, "request_id", None))
    db.commit()
    return Message(message="Email address verified")


# =====================================================================================================
# Enterprise SSO (OIDC)
# =====================================================================================================
@router.post(
    "/sso/providers",
    summary="Register an identity provider",
    description="Requires the `enterprise_sso` feature. The client secret is stored encrypted and never returned. "
    "`kind=saml` can be stored but SAML sign-in is not supported yet.",
    response_model=SSOProviderOut,
    status_code=status.HTTP_201_CREATED,
    responses={**AUTH, **CONFLICT, **INVALID},
)
def create_sso_provider(
    body: SSOProviderCreate, actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> SSOProviderOut:
    return sso.provider_out(sso.create_provider(db, actor, body))


@router.get(
    "/sso/providers",
    summary="List identity providers",
    description="Configured SSO connections (client secrets are never returned).",
    response_model=list[SSOProviderOut],
    status_code=status.HTTP_200_OK,
    responses=AUTH,
)
def list_sso_providers(
    actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> list[SSOProviderOut]:
    return [sso.provider_out(p) for p in sso.list_providers(db, actor)]


@router.get(
    "/sso/providers/{provider_id}",
    summary="Get an identity provider",
    description="Includes the redirect URI to register at the IdP.",
    response_model=SSOProviderOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def get_sso_provider(
    provider_id: uuid.UUID, actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> SSOProviderOut:
    return sso.provider_out(sso.get_provider(db, actor, provider_id))


@router.patch(
    "/sso/providers/{provider_id}",
    summary="Update an identity provider",
    description="Partial update; pass `client_secret` to rotate the stored secret.",
    response_model=SSOProviderOut,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND, **CONFLICT, **INVALID},
)
def update_sso_provider(
    provider_id: uuid.UUID,
    body: SSOProviderUpdate,
    actor: Actor = Depends(require_actor("org:manage")),
    db: Session = Depends(get_db),
) -> SSOProviderOut:
    return sso.provider_out(sso.update_provider(db, actor, provider_id, body))


@router.delete(
    "/sso/providers/{provider_id}",
    summary="Delete an identity provider",
    description="Removes the connection and its stored client secret. Existing sessions are not affected.",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    responses={**AUTH, **NOT_FOUND},
)
def delete_sso_provider(
    provider_id: uuid.UUID, actor: Actor = Depends(require_actor("org:manage")), db: Session = Depends(get_db)
) -> Message:
    sso.delete_provider(db, actor, provider_id)
    return Message(message="Identity provider deleted")


@router.get(
    "/auth/sso/{provider_id}/start",
    tags=["Auth"],
    summary="Start an SSO sign-in",
    description="Returns the IdP authorization URL (state + nonce + PKCE S256) and binds the attempt to this "
    "browser with a short-lived cookie. SAML providers answer 501 `sso_protocol_not_supported`.",
    response_model=SSOStartOut,
    status_code=status.HTTP_200_OK,
    responses={
        **NOT_FOUND,
        429: {"description": "Rate limited"},
        501: {"description": "Protocol not supported (SAML)"},
        502: {"description": "Identity provider error"},
        503: {"description": "Identity provider unreachable"},
    },
    dependencies=[Depends(public_rate_limit)],
)
def start_sso(provider_id: uuid.UUID, response: Response) -> SSOStartOut:
    started = sso.start_login(provider_id)
    response.set_cookie(
        sso.STATE_COOKIE,
        started.state,
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
        max_age=int(sso.STATE_TTL.total_seconds()),
        path="/",
    )
    return SSOStartOut(authorization_url=started.authorization_url, expires_at=started.expires_at)


@router.get(
    "/auth/sso/callback",
    tags=["Auth"],
    summary="Complete an SSO sign-in",
    description="OIDC redirect target: exchanges the code, validates the ID token and creates a session "
    "(same response and cookie as `/auth/login`).",
    response_model=SessionOut,
    status_code=status.HTTP_200_OK,
    responses={
        **AUTH,
        **INVALID,
        429: {"description": "Rate limited"},
        502: {"description": "Identity provider error"},
        503: {"description": "Identity provider unreachable"},
    },
    dependencies=[Depends(public_rate_limit)],
)
def sso_callback(
    request: Request,
    response: Response,
    code: str = Query(..., min_length=1, max_length=4096),
    state: str = Query(..., min_length=1, max_length=512),
) -> SessionOut:
    result = sso.complete_login(code=code, state=state, cookie_state=request.cookies.get(sso.STATE_COOKIE))
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    response.delete_cookie(sso.STATE_COOKIE, path="/")
    return _session_out(result)
