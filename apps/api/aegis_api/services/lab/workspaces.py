"""Workspaces, projects and teams (tenant hierarchy: organization → workspace → project).

Members added to workspaces/projects/teams must already be active members of the organization; project member
roles can never exceed the member's organization role (no escalation through project membership).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.models import (
    Membership,
    Project,
    ProjectMember,
    Team,
    TeamMember,
    Workspace,
    WorkspaceMember,
)
from aegis_api.security.context import Principal
from aegis_api.security.rbac import ROLE_RANK
from aegis_api.services import audit_log
from aegis_api.services.lab.access import accessible_project_ids, get_project
from engines.lab.verification.criteria import DOMAIN_PRESETS, criteria_for

_SLUG = re.compile(r"[^a-z0-9]+")
BUDGET_KEYS = frozenset(
    {
        "max_total_cost",
        "max_llm_cost",
        "max_compute_cost",
        "max_experiment_count",
        "max_llm_tokens",
        "max_compute_seconds",
    }
)


def slugify(value: str) -> str:
    slug = _SLUG.sub("-", value.lower()).strip("-")[:60]
    return slug or "item"


def validate_budget(budget: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (budget or {}).items():
        if key not in BUDGET_KEYS:
            raise ValidationFailed(f"unknown budget key '{key}' (allowed: {', '.join(sorted(BUDGET_KEYS))})")
        if value is None:
            continue
        number = float(value)
        if number < 0:
            raise ValidationFailed(f"budget '{key}' must be ≥ 0")
        out[key] = number
    return out


def _org_member_role(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> str:
    member = db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if member is None:
        raise ValidationFailed("User is not an active member of this organization")
    return member.role


# --- workspaces ----------------------------------------------------------------------------------------------


def ensure_default_workspace(db: Session, organization_id: uuid.UUID, created_by: uuid.UUID | None) -> Workspace:
    ws = db.scalar(
        select(Workspace).where(Workspace.organization_id == organization_id, Workspace.is_default.is_(True))
    )
    if ws is None:
        ws = Workspace(
            organization_id=organization_id,
            name="Default workspace",
            slug="default",
            is_default=True,
            settings={},
            created_by_id=created_by,
        )
        db.add(ws)
        db.flush()
    return ws


def list_workspaces(db: Session, principal: Principal) -> list[Workspace]:
    return list(
        db.scalars(
            select(Workspace)
            .where(Workspace.organization_id == principal.organization_id, Workspace.archived_at.is_(None))
            .order_by(Workspace.created_at)
        ).all()
    )


def get_workspace(db: Session, principal: Principal, workspace_id: uuid.UUID | str) -> Workspace:
    try:
        wid = uuid.UUID(str(workspace_id))
    except ValueError as exc:
        raise NotFound("Workspace not found") from exc
    ws = db.get(Workspace, wid)
    if ws is None or ws.organization_id != principal.organization_id or ws.archived_at is not None:
        raise NotFound("Workspace not found")
    return ws


def create_workspace(
    db: Session, principal: Principal, *, name: str, description: str | None, settings: dict[str, Any] | None
) -> Workspace:
    principal.require("workspace:manage")
    ws = Workspace(
        organization_id=principal.organization_id,
        name=name[:200],
        slug=_unique_slug(db, Workspace, slugify(name), Workspace.organization_id == principal.organization_id),
        description=description,
        is_default=False,
        settings=settings or {},
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(ws)
    db.flush()
    if principal.is_human:
        db.add(
            WorkspaceMember(
                organization_id=principal.organization_id, workspace_id=ws.id, user_id=principal.user_id, role="owner"
            )
        )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="workspace.created",
        resource_type="workspace",
        resource_id=ws.id,
        principal=principal,
        after={"name": ws.name},
    )
    return ws


def update_workspace(db: Session, principal: Principal, ws: Workspace, changes: dict[str, Any]) -> Workspace:
    principal.require("workspace:manage")
    for key in ("name", "description", "settings"):
        if key in changes and changes[key] is not None:
            setattr(ws, key, changes[key])
    if changes.get("archived"):
        if ws.is_default:
            raise ValidationFailed("The default workspace cannot be archived")
        ws.archived_at = utcnow()
    audit_log.record(
        db,
        organization_id=ws.organization_id,
        action="workspace.updated",
        resource_type="workspace",
        resource_id=ws.id,
        principal=principal,
        after={k: v for k, v in changes.items() if k != "settings"},
    )
    return ws


def add_workspace_member(db: Session, principal: Principal, ws: Workspace, user_id: uuid.UUID, role: str) -> None:
    principal.require("workspace:manage")
    if role not in ("owner", "member", "viewer"):
        raise ValidationFailed("workspace role must be owner, member or viewer")
    _org_member_role(db, ws.organization_id, user_id)
    existing = db.scalar(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user_id)
    )
    if existing:
        existing.role = role
    else:
        db.add(WorkspaceMember(organization_id=ws.organization_id, workspace_id=ws.id, user_id=user_id, role=role))
    audit_log.record(
        db,
        organization_id=ws.organization_id,
        action="workspace.member_set",
        resource_type="workspace",
        resource_id=ws.id,
        principal=principal,
        after={"user_id": str(user_id), "role": role},
    )


# --- projects ------------------------------------------------------------------------------------------------


def list_projects(db: Session, principal: Principal, *, workspace_id: uuid.UUID | None = None) -> Select[Project]:
    stmt = select(Project).where(Project.organization_id == principal.organization_id, Project.archived_at.is_(None))
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Project.id.in_(visible))
    if workspace_id:
        stmt = stmt.where(Project.workspace_id == workspace_id)
    return stmt.order_by(Project.created_at.desc())


def create_project(
    db: Session,
    principal: Principal,
    *,
    workspace_id: uuid.UUID | None,
    name: str,
    description: str | None,
    domain: str,
    visibility: str,
    budget: dict[str, Any] | None,
    verification_criteria: dict[str, Any] | None,
    settings: dict[str, Any] | None,
) -> Project:
    principal.require("project:create")
    if visibility not in ("organization", "private"):
        raise ValidationFailed("visibility must be 'organization' or 'private'")
    ws = (
        get_workspace(db, principal, workspace_id)
        if workspace_id
        else ensure_default_workspace(db, principal.organization_id, principal.user_id if principal.is_human else None)
    )
    try:
        criteria = criteria_for(domain, verification_criteria).model_dump()
    except Exception as exc:
        raise ValidationFailed(f"invalid verification criteria: {exc}") from exc
    project = Project(
        organization_id=principal.organization_id,
        workspace_id=ws.id,
        name=name[:200],
        slug=_unique_slug(db, Project, slugify(name), Project.workspace_id == ws.id),
        description=description,
        domain=domain if domain in DOMAIN_PRESETS else domain[:48],
        visibility=visibility,
        budget=validate_budget(budget),
        verification_criteria=criteria,
        settings=settings or {},
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(project)
    db.flush()
    if principal.is_human:
        db.add(
            ProjectMember(
                organization_id=principal.organization_id, project_id=project.id, user_id=principal.user_id, role="lead"
            )
        )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        principal=principal,
        after={"name": project.name, "visibility": visibility, "budget": project.budget},
    )
    return project


def update_project(db: Session, principal: Principal, project_id: uuid.UUID | str, changes: dict[str, Any]) -> Project:
    principal.require("project:manage")
    project = get_project(db, principal, project_id)
    before = {"budget": project.budget, "visibility": project.visibility, "name": project.name}
    if changes.get("name"):
        project.name = str(changes["name"])[:200]
    if "description" in changes:
        project.description = changes["description"]
    if changes.get("visibility"):
        if changes["visibility"] not in ("organization", "private"):
            raise ValidationFailed("visibility must be 'organization' or 'private'")
        project.visibility = changes["visibility"]
    if "budget" in changes and changes["budget"] is not None:
        project.budget = validate_budget(changes["budget"])
    if "verification_criteria" in changes and changes["verification_criteria"] is not None:
        try:
            project.verification_criteria = criteria_for(project.domain, changes["verification_criteria"]).model_dump()
        except Exception as exc:
            raise ValidationFailed(f"invalid verification criteria: {exc}") from exc
    if "settings" in changes and changes["settings"] is not None:
        project.settings = changes["settings"]
    if changes.get("archived"):
        project.archived_at = utcnow()
    audit_log.record(
        db,
        organization_id=project.organization_id,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        principal=principal,
        before=before,
        after={"budget": project.budget, "visibility": project.visibility, "name": project.name},
    )
    return project


PROJECT_ROLES = {"lead": "research_lead", "member": "researcher", "viewer": "viewer"}


def set_project_member(db: Session, principal: Principal, project: Project, user_id: uuid.UUID, role: str) -> None:
    principal.require("project:manage")
    if role not in PROJECT_ROLES:
        raise ValidationFailed(f"project role must be one of {', '.join(PROJECT_ROLES)}")
    org_role = _org_member_role(db, project.organization_id, user_id)
    if ROLE_RANK.get(PROJECT_ROLES[role], 0) > ROLE_RANK.get(org_role, 0):
        raise Forbidden("A project role cannot exceed the member's organization role")
    existing = db.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user_id)
    )
    if existing:
        existing.role = role
    else:
        db.add(
            ProjectMember(organization_id=project.organization_id, project_id=project.id, user_id=user_id, role=role)
        )
    audit_log.record(
        db,
        organization_id=project.organization_id,
        action="project.member_set",
        resource_type="project",
        resource_id=project.id,
        principal=principal,
        after={"user_id": str(user_id), "role": role},
    )


def remove_project_member(db: Session, principal: Principal, project: Project, user_id: uuid.UUID) -> None:
    principal.require("project:manage")
    member = db.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user_id)
    )
    if member is None:
        raise NotFound("Project member not found")
    db.delete(member)
    audit_log.record(
        db,
        organization_id=project.organization_id,
        action="project.member_removed",
        resource_type="project",
        resource_id=project.id,
        principal=principal,
        after={"user_id": str(user_id)},
    )


def project_members(db: Session, project: Project) -> list[ProjectMember]:
    return list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id)).all())


# --- teams ---------------------------------------------------------------------------------------------------


def list_teams(db: Session, principal: Principal) -> list[Team]:
    return list(
        db.scalars(select(Team).where(Team.organization_id == principal.organization_id).order_by(Team.name)).all()
    )


def create_team(db: Session, principal: Principal, *, name: str, description: str | None) -> Team:
    principal.require("team:manage")
    team = Team(organization_id=principal.organization_id, name=name[:120], description=description)
    db.add(team)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("A team with this name already exists") from exc
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="team.created",
        resource_type="team",
        resource_id=team.id,
        principal=principal,
    )
    return team


def set_team_members(db: Session, principal: Principal, team_id: uuid.UUID | str, user_ids: list[uuid.UUID]) -> Team:
    principal.require("team:manage")
    try:
        team = db.get(Team, uuid.UUID(str(team_id)))
    except ValueError as exc:
        raise NotFound("Team not found") from exc
    if team is None or team.organization_id != principal.organization_id:
        raise NotFound("Team not found")
    for uid in user_ids:
        _org_member_role(db, principal.organization_id, uid)
    current = {m.user_id: m for m in db.scalars(select(TeamMember).where(TeamMember.team_id == team.id)).all()}
    for uid, member in current.items():
        if uid not in user_ids:
            db.delete(member)
    for uid in user_ids:
        if uid not in current:
            db.add(TeamMember(organization_id=team.organization_id, team_id=team.id, user_id=uid))
    audit_log.record(
        db,
        organization_id=team.organization_id,
        action="team.members_set",
        resource_type="team",
        resource_id=team.id,
        principal=principal,
        after={"user_ids": [str(u) for u in user_ids]},
    )
    return team


def team_members(db: Session, team: Team) -> list[TeamMember]:
    return list(db.scalars(select(TeamMember).where(TeamMember.team_id == team.id)).all())


def _unique_slug(db: Session, model: Any, base: str, scope: Any) -> str:
    slug = base
    n = 1
    while db.scalar(select(model.id).where(scope, model.slug == slug)) is not None:
        n += 1
        slug = f"{base}-{n}"[:64]
    return slug
