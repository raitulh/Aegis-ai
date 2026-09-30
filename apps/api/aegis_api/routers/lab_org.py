"""Organization, users, workspaces, projects and teams."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, get_organization, require
from aegis_api.models import Membership, Organization, User
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.lab import (
    MemberIn,
    OrganizationOut,
    OrganizationUpdate,
    ProjectIn,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
    TeamIn,
    TeamMembersIn,
    TeamOut,
    WorkspaceIn,
    WorkspaceOut,
    WorkspaceUpdate,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import workspaces
from aegis_api.services.lab.access import get_project
from aegis_api.services.lab.common import parse_uuid

router = APIRouter(prefix="/api/v1", tags=["Organization"])


@router.get("/organizations/current", response_model=OrganizationOut)
def current_organization(org: Organization = Depends(get_organization)) -> OrganizationOut:
    return OrganizationOut.model_validate(org)


@router.patch("/organizations/current", response_model=OrganizationOut)
def update_organization(
    body: OrganizationUpdate,
    principal: Principal = Depends(require("org:manage")),
    db: Session = Depends(get_db),
) -> OrganizationOut:
    principal.require_human("organization update")
    org = db.get(Organization, principal.organization_id)
    assert org is not None
    before = org.name
    org.name = body.name
    audit_log.record(
        db,
        organization_id=org.id,
        action="organization.updated",
        resource_type="organization",
        resource_id=org.id,
        principal=principal,
        before={"name": before},
        after={"name": org.name},
    )
    return OrganizationOut.model_validate(org)


@router.get("/users/me")
def me(principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.get(User, principal.user_id) if principal.actor_type == "user" else None
    return {
        "user_id": str(principal.user_id),
        "email": user.email if user else principal.email,
        "display_name": getattr(user, "full_name", None) if user else principal.display_name,
        "organization_id": str(principal.organization_id),
        "role": principal.role,
        "actor_type": principal.actor_type,
        "permissions": sorted(principal.permissions),
        "project_ids": sorted(principal.project_ids),
    }


@router.get("/users")
def list_users(
    principal: Principal = Depends(require("team:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == principal.organization_id, Membership.status == "active")
        .order_by(User.email)
    ).all()
    return [
        {"user_id": str(u.id), "email": u.email, "display_name": getattr(u, "full_name", None), "role": m.role}
        for m, u in rows
    ]


# --- workspaces ----------------------------------------------------------------------------------------------


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    principal: Principal = Depends(require("workspace:read")), db: Session = Depends(get_db)
) -> list[WorkspaceOut]:
    workspaces.ensure_default_workspace(
        db, principal.organization_id, principal.user_id if principal.is_human else None
    )
    return [WorkspaceOut.model_validate(w) for w in workspaces.list_workspaces(db, principal)]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    body: WorkspaceIn, principal: Principal = Depends(require("workspace:manage")), db: Session = Depends(get_db)
) -> WorkspaceOut:
    return WorkspaceOut.model_validate(
        workspaces.create_workspace(db, principal, name=body.name, description=body.description, settings=body.settings)
    )


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdate,
    principal: Principal = Depends(require("workspace:manage")),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    ws = workspaces.get_workspace(db, principal, workspace_id)
    return WorkspaceOut.model_validate(
        workspaces.update_workspace(db, principal, ws, body.model_dump(exclude_unset=True))
    )


@router.put("/workspaces/{workspace_id}/members", response_model=Message)
def set_workspace_member(
    workspace_id: uuid.UUID,
    body: MemberIn,
    principal: Principal = Depends(require("workspace:manage")),
    db: Session = Depends(get_db),
) -> Message:
    ws = workspaces.get_workspace(db, principal, workspace_id)
    workspaces.add_workspace_member(db, principal, ws, parse_uuid(body.user_id, "User"), body.role)
    return Message(message="Member updated")


# --- projects ------------------------------------------------------------------------------------------------


@router.get("/projects", response_model=Page[ProjectOut])
def list_projects(
    params: PageParams = Depends(),
    workspace_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("project:read")),
    db: Session = Depends(get_db),
) -> Page[ProjectOut]:
    return paginate(
        db, workspaces.list_projects(db, principal, workspace_id=workspace_id), params, ProjectOut.model_validate
    )


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectIn, principal: Principal = Depends(require("project:create")), db: Session = Depends(get_db)
) -> ProjectOut:
    project = workspaces.create_project(
        db,
        principal,
        workspace_id=parse_uuid(body.workspace_id, "Workspace") if body.workspace_id else None,
        name=body.name,
        description=body.description,
        domain=body.domain,
        visibility=body.visibility,
        budget=body.budget,
        verification_criteria=body.verification_criteria,
        settings=body.settings,
    )
    return ProjectOut.model_validate(project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project_endpoint(
    project_id: uuid.UUID, principal: Principal = Depends(require("project:read")), db: Session = Depends(get_db)
) -> ProjectOut:
    return ProjectOut.model_validate(get_project(db, principal, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    principal: Principal = Depends(require("project:manage")),
    db: Session = Depends(get_db),
) -> ProjectOut:
    return ProjectOut.model_validate(
        workspaces.update_project(db, principal, project_id, body.model_dump(exclude_unset=True))
    )


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberOut])
def project_members(
    project_id: uuid.UUID, principal: Principal = Depends(require("project:read")), db: Session = Depends(get_db)
) -> list[ProjectMemberOut]:
    project = get_project(db, principal, project_id)
    return [ProjectMemberOut.model_validate(m) for m in workspaces.project_members(db, project)]


@router.put("/projects/{project_id}/members", response_model=Message)
def set_project_member(
    project_id: uuid.UUID,
    body: MemberIn,
    principal: Principal = Depends(require("project:manage")),
    db: Session = Depends(get_db),
) -> Message:
    project = get_project(db, principal, project_id)
    workspaces.set_project_member(db, principal, project, parse_uuid(body.user_id, "User"), body.role)
    return Message(message="Project member updated")


@router.delete("/projects/{project_id}/members/{user_id}", response_model=Message)
def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require("project:manage")),
    db: Session = Depends(get_db),
) -> Message:
    project = get_project(db, principal, project_id)
    workspaces.remove_project_member(db, principal, project, user_id)
    return Message(message="Project member removed")


# --- teams ---------------------------------------------------------------------------------------------------


@router.get("/teams", response_model=list[TeamOut])
def list_teams(principal: Principal = Depends(require("team:read")), db: Session = Depends(get_db)) -> list[TeamOut]:
    return [TeamOut.model_validate(t) for t in workspaces.list_teams(db, principal)]


@router.post("/teams", response_model=TeamOut, status_code=201)
def create_team(
    body: TeamIn, principal: Principal = Depends(require("team:manage")), db: Session = Depends(get_db)
) -> TeamOut:
    return TeamOut.model_validate(workspaces.create_team(db, principal, name=body.name, description=body.description))


@router.put("/teams/{team_id}/members", response_model=Message)
def set_team_members(
    team_id: uuid.UUID,
    body: TeamMembersIn,
    principal: Principal = Depends(require("team:manage")),
    db: Session = Depends(get_db),
) -> Message:
    workspaces.set_team_members(db, principal, team_id, [parse_uuid(u, "User") for u in body.user_ids])
    return Message(message="Team members updated")
