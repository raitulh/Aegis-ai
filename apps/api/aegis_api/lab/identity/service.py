"""Identity application services: the organization hierarchy (workspaces → projects), memberships, teams,
users and the RBAC catalog.

Public API used by other contexts (names are part of the lab contract):

* :func:`create_workspace`, :func:`create_project` (creator becomes project member ``research_lead``),
  :func:`add_project_member`, :func:`get_default_project`
* :func:`seed_rbac_catalog` — idempotent start-up seeding of roles / permissions / role_permissions

All functions take the caller's RLS-scoped session and an :class:`Actor`; they never commit.

Privilege-escalation guards: a role can only be granted (directly, or indirectly through team membership)
by an actor who already holds every permission of that role inside the project, and membership changes
are restricted to signed-in humans.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import (
    ORG_SUPERUSER_ROLES,
    effective_permissions,
    get_owned,
    load_project,
    visible_project_ids,
)
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.org_settings import get_org_settings
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.identity import validators
from aegis_api.lab.identity.schemas import (
    MembershipSummary,
    MeOut,
    OrgUserOut,
    PermissionOut,
    ProjectBudget,
    ProjectCreate,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
    RoleOut,
    TeamCreate,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
    UserProfileOut,
    WorkspaceCreate,
    WorkspaceMemberOut,
    WorkspaceOut,
    WorkspaceUpdate,
)
from aegis_api.lab.models import (
    PermissionRecord,
    Project,
    ProjectMember,
    RolePermission,
    RoleRecord,
    Team,
    TeamMember,
    Workspace,
    WorkspaceMember,
)
from aegis_api.models import Membership, Organization, User
from aegis_api.models.enums import MembershipStatus, Role
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.rbac import (
    ALL_LAB_PERMISSIONS,
    HUMAN_ONLY_PERMISSIONS,
    LAB_PERMISSION_CATALOG,
    OWNER,
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    ROLE_RANK,
    permissions_for_role,
)
from engines.lab.states import autonomy_rank

PROJECT_CREATOR_ROLE = Role.RESEARCH_LEAD
WORKSPACE_CREATOR_ROLE = Role.RESEARCH_LEAD
MAX_SETTINGS_JSON_BYTES = 16 * 1024


# --- helpers -------------------------------------------------------------------------------------
def _bounded_json(value: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{label} must be JSON-serializable") from exc
    if len(encoded.encode()) > MAX_SETTINGS_JSON_BYTES:
        raise ValidationFailed(f"{label} must be at most {MAX_SETTINGS_JSON_BYTES} bytes")
    return json.loads(encoded)


def _flush_or_conflict(db: Session, message: str) -> None:
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        raise Conflict(message) from exc


def _unique_slug(db: Session, column: Any, scope: Any, name: str, fallback: str) -> str:
    base = validators.slugify(name, fallback=fallback, max_length=70)
    taken: set[str] = set(db.scalars(select(column).where(scope, column.like(f"{base}%"))).all())
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _require_org_member(db: Session, actor: Actor, user_id: uuid.UUID) -> tuple[Membership, User]:
    row = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == actor.organization_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    ).first()
    if row is None:
        raise NotFound("User is not an active member of this organization")
    return row[0], row[1]


def _require_membership_admin(actor: Actor) -> None:
    """Membership changes grant permissions: signed-in humans, or the platform itself (``system`` actors used by
    onboarding/seeding). API keys, service accounts, agents and workflows are refused."""
    if actor.kind != "system":
        actor.require_human("changing project membership")


def _check_can_grant(db: Session, actor: Actor, project: Project, role: str) -> None:
    """An actor may only grant (or revoke) a project role whose permissions it already holds there."""
    if actor.kind == "system":
        return  # platform-controlled actor (never an agent or external credential)
    held = effective_permissions(db, actor, project)
    if permissions_for_role(role) - held:
        raise Forbidden(f"You cannot grant or change the '{role}' role: it includes permissions you do not hold")


# --- workspaces ----------------------------------------------------------------------------------
def create_workspace(db: Session, actor: Actor, data: WorkspaceCreate) -> Workspace:
    actor.require("workspace:create")
    scope = Workspace.organization_id == actor.organization_id
    if data.slug:
        slug = validators.validate_slug(data.slug)
        if db.scalar(select(Workspace.id).where(scope, Workspace.slug == slug)):
            raise Conflict("A workspace with this slug already exists")
    else:
        slug = _unique_slug(db, Workspace.slug, scope, data.name, "workspace")
    workspace = Workspace(
        organization_id=actor.organization_id,
        name=data.name.strip(),
        slug=slug,
        description=data.description,
        settings=_bounded_json(data.settings, "settings"),
        created_by_id=actor.user_id,
    )
    db.add(workspace)
    _flush_or_conflict(db, "A workspace with this slug already exists")
    if actor.kind == "user" and actor.user_id is not None:
        db.add(
            WorkspaceMember(
                organization_id=actor.organization_id,
                workspace_id=workspace.id,
                user_id=actor.user_id,
                role=WORKSPACE_CREATOR_ROLE,
            )
        )
    audit(db, actor, AuditAction.WORKSPACE_CREATED, "workspace", workspace.id, after={"name": workspace.name})
    db.flush()
    return workspace


def list_workspaces(
    db: Session, actor: Actor, params: PageParams, *, include_archived: bool = False, q: str | None = None
) -> Page:
    actor.require("workspace:read")
    stmt = select(Workspace).where(Workspace.organization_id == actor.organization_id)
    if not include_archived:
        stmt = stmt.where(Workspace.archived_at.is_(None))
    if q:
        stmt = stmt.where(Workspace.name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    return paginate(
        db, stmt.order_by(Workspace.created_at.asc(), Workspace.id.asc()), params, WorkspaceOut.model_validate
    )


def get_workspace(db: Session, actor: Actor, workspace_id: uuid.UUID | str) -> Workspace:
    actor.require("workspace:read")
    return get_owned(db, Workspace, workspace_id, actor, label="Workspace")


def _mutable_workspace(db: Session, actor: Actor, workspace_id: uuid.UUID | str) -> Workspace:
    actor.require("workspace:manage")
    workspace = get_owned(db, Workspace, workspace_id, actor, label="Workspace")
    if workspace.archived_at is not None:
        raise Conflict("The workspace is archived", code="archived")
    return workspace


def update_workspace(db: Session, actor: Actor, workspace_id: uuid.UUID | str, data: WorkspaceUpdate) -> Workspace:
    workspace = _mutable_workspace(db, actor, workspace_id)
    changes = data.model_dump(exclude_unset=True)
    before = {k: getattr(workspace, k) for k in changes}
    if "name" in changes and data.name is not None:
        workspace.name = data.name.strip()
    if "description" in changes:
        workspace.description = data.description
    if "settings" in changes and data.settings is not None:
        workspace.settings = _bounded_json(data.settings, "settings")
    audit(db, actor, "WORKSPACE_UPDATED", "workspace", workspace.id, before=before, after=changes)
    db.flush()
    return workspace


def archive_workspace(db: Session, actor: Actor, workspace_id: uuid.UUID | str) -> Workspace:
    """Archive a workspace and every active project in it (records are kept; nothing is deleted)."""
    workspace = _mutable_workspace(db, actor, workspace_id)
    now = utcnow()
    workspace.archived_at = now
    archived = db.execute(
        update(Project)
        .where(
            Project.organization_id == actor.organization_id,
            Project.workspace_id == workspace.id,
            Project.archived_at.is_(None),
        )
        .values(archived_at=now, updated_at=now)
        .returning(Project.id)
    ).all()
    audit(
        db,
        actor,
        "WORKSPACE_ARCHIVED",
        "workspace",
        workspace.id,
        after={"archived_projects": [str(r[0]) for r in archived]},
    )
    db.flush()
    return workspace


def _workspace_member_out(member: WorkspaceMember, user: User | None) -> WorkspaceMemberOut:
    return WorkspaceMemberOut(
        id=str(member.id),
        workspace_id=str(member.workspace_id),
        user_id=str(member.user_id),
        role=member.role,
        email=user.email if user else None,
        full_name=user.full_name if user else None,
        created_at=member.created_at,
    )


def list_workspace_members(db: Session, actor: Actor, workspace_id: uuid.UUID | str) -> list[WorkspaceMemberOut]:
    workspace = get_workspace(db, actor, workspace_id)
    rows = db.execute(
        select(WorkspaceMember, User)
        .outerjoin(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.organization_id == actor.organization_id)
        .order_by(WorkspaceMember.created_at.asc())
    ).all()
    return [_workspace_member_out(m, u) for m, u in rows]


def add_workspace_member(
    db: Session, actor: Actor, workspace_id: uuid.UUID | str, *, user_id: uuid.UUID, role: str
) -> WorkspaceMemberOut:
    actor.require_human("changing workspace membership")
    workspace = _mutable_workspace(db, actor, workspace_id)
    role = validators.validate_assignable_role(role)
    _, user = _require_org_member(db, actor, user_id)
    exists = db.scalar(
        select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user_id
        )
    )
    if exists:
        raise Conflict("The user is already a member of this workspace")
    member = WorkspaceMember(
        organization_id=actor.organization_id, workspace_id=workspace.id, user_id=user_id, role=role
    )
    db.add(member)
    _flush_or_conflict(db, "The user is already a member of this workspace")
    audit(db, actor, "WORKSPACE_MEMBER_CHANGED", "workspace", workspace.id, after={"added": str(user_id), "role": role})
    return _workspace_member_out(member, user)


def remove_workspace_member(db: Session, actor: Actor, workspace_id: uuid.UUID | str, user_id: uuid.UUID) -> None:
    actor.require_human("changing workspace membership")
    workspace = _mutable_workspace(db, actor, workspace_id)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.organization_id == actor.organization_id,
        )
    )
    if member is None:
        raise NotFound("Workspace member not found")
    db.delete(member)
    audit(
        db,
        actor,
        "WORKSPACE_MEMBER_CHANGED",
        "workspace",
        workspace.id,
        before={"removed": str(user_id), "role": member.role},
    )
    db.flush()


# --- projects ------------------------------------------------------------------------------------
def _budget_dict(budget: ProjectBudget | None) -> dict[str, Any]:
    if budget is None:
        return {}
    return {
        key: float(Decimal(value).quantize(Decimal("0.000001")))
        for key, value in budget.model_dump(exclude_none=True).items()
    }


def _check_autonomy(db: Session, actor: Actor, level: str | None) -> str | None:
    if level is None:
        return None
    org_max = get_org_settings(db, actor.organization_id).max_autonomy_level
    if autonomy_rank(str(level)) > autonomy_rank(org_max):
        raise ValidationFailed(f"max_autonomy_level must not exceed the organization maximum ({org_max})")
    return str(level)


def create_project(db: Session, actor: Actor, data: ProjectCreate) -> Project:
    """Create a project in a workspace. A human creator becomes a project member with ``research_lead``."""
    actor.require("project:create")
    workspace = get_owned(db, Workspace, data.workspace_id, actor, label="Workspace")
    if workspace.archived_at is not None:
        raise Conflict("Projects cannot be created in an archived workspace", code="archived")
    scope = Project.workspace_id == workspace.id
    if data.slug:
        slug = validators.validate_slug(data.slug)
        if db.scalar(select(Project.id).where(scope, Project.slug == slug)):
            raise Conflict("A project with this slug already exists in the workspace")
    else:
        slug = _unique_slug(db, Project.slug, scope, data.name, "project")
    project = Project(
        organization_id=actor.organization_id,
        workspace_id=workspace.id,
        name=data.name.strip(),
        slug=slug,
        description=data.description,
        visibility=data.visibility,
        max_autonomy_level=_check_autonomy(db, actor, data.max_autonomy_level),
        budget=_budget_dict(data.budget),
        settings=_bounded_json(data.settings, "settings"),
        created_by_id=actor.user_id,
    )
    db.add(project)
    _flush_or_conflict(db, "A project with this slug already exists in the workspace")
    if actor.kind == "user" and actor.user_id is not None:
        db.add(
            ProjectMember(
                organization_id=actor.organization_id,
                project_id=project.id,
                user_id=actor.user_id,
                role=PROJECT_CREATOR_ROLE,
            )
        )
    audit(
        db,
        actor,
        AuditAction.PROJECT_CREATED,
        "project",
        project.id,
        after={"name": project.name, "workspace_id": workspace.id, "visibility": project.visibility},
    )
    db.flush()
    return project


def list_projects(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    workspace_id: uuid.UUID | None = None,
    include_archived: bool = False,
    q: str | None = None,
) -> Page:
    actor.require("project:read")
    stmt = select(Project).where(Project.organization_id == actor.organization_id)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Project.id.in_(visible))
    if workspace_id is not None:
        stmt = stmt.where(Project.workspace_id == workspace_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    if q:
        stmt = stmt.where(Project.name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    return paginate(db, stmt.order_by(Project.created_at.asc(), Project.id.asc()), params, ProjectOut.model_validate)


def get_project(db: Session, actor: Actor, project_id: uuid.UUID | str) -> Project:
    return load_project(db, actor, project_id, "project:read")


def project_permissions(db: Session, actor: Actor, project: Project) -> list[str]:
    return sorted(effective_permissions(db, actor, project) & ALL_LAB_PERMISSIONS)


def _mutable_project(db: Session, actor: Actor, project_id: uuid.UUID | str) -> Project:
    project = load_project(db, actor, project_id, "project:manage")
    if project.archived_at is not None:
        raise Conflict("The project is archived", code="archived")
    return project


def update_project(db: Session, actor: Actor, project_id: uuid.UUID | str, data: ProjectUpdate) -> Project:
    project = _mutable_project(db, actor, project_id)
    changes = data.model_dump(exclude_unset=True, mode="json")
    before = {k: getattr(project, k) for k in changes}
    if "name" in changes and data.name is not None:
        project.name = data.name.strip()
    if "description" in changes:
        project.description = data.description
    if "visibility" in changes and data.visibility is not None:
        project.visibility = data.visibility
    if "max_autonomy_level" in changes:
        project.max_autonomy_level = _check_autonomy(db, actor, data.max_autonomy_level)
    if "budget" in changes:
        project.budget = _budget_dict(data.budget)
    if "settings" in changes and data.settings is not None:
        project.settings = _bounded_json(data.settings, "settings")
    audit(db, actor, "PROJECT_UPDATED", "project", project.id, before=before, after=changes)
    db.flush()
    return project


def archive_project(db: Session, actor: Actor, project_id: uuid.UUID | str) -> Project:
    project = _mutable_project(db, actor, project_id)
    project.archived_at = utcnow()
    audit(db, actor, "PROJECT_ARCHIVED", "project", project.id, after={"archived_at": project.archived_at})
    db.flush()
    return project


def get_default_project(db: Session, actor: Actor) -> Project | None:
    """The caller's default project: the oldest active project visible to it (``None`` when there is none)."""
    stmt = select(Project).where(Project.organization_id == actor.organization_id, Project.archived_at.is_(None))
    visible = visible_project_ids(db, actor)
    if visible is not None:
        if not visible:
            return None
        stmt = stmt.where(Project.id.in_(visible))
    return db.scalar(stmt.order_by(Project.created_at.asc(), Project.id.asc()).limit(1))


# --- project members -----------------------------------------------------------------------------
def _project_member_out(member: ProjectMember, user: User | None, team: Team | None) -> ProjectMemberOut:
    return ProjectMemberOut(
        id=str(member.id),
        project_id=str(member.project_id),
        user_id=str(member.user_id) if member.user_id else None,
        team_id=str(member.team_id) if member.team_id else None,
        role=member.role,
        email=user.email if user else None,
        full_name=user.full_name if user else None,
        team_name=team.name if team else None,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def list_project_members(db: Session, actor: Actor, project_id: uuid.UUID | str) -> list[ProjectMemberOut]:
    project = load_project(db, actor, project_id, "project:read")
    rows = db.execute(
        select(ProjectMember, User, Team)
        .outerjoin(User, User.id == ProjectMember.user_id)
        .outerjoin(Team, Team.id == ProjectMember.team_id)
        .where(ProjectMember.project_id == project.id, ProjectMember.organization_id == actor.organization_id)
        .order_by(ProjectMember.created_at.asc(), ProjectMember.id.asc())
    ).all()
    return [_project_member_out(m, u, t) for m, u, t in rows]


def add_project_member(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID | str,
    *,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    role: str,
) -> ProjectMember:
    """Grant a user or a team a project role (exactly one subject; never ``owner``)."""
    _require_membership_admin(actor)
    if (user_id is None) == (team_id is None):
        raise ValidationFailed("Provide exactly one of user_id or team_id")
    project = _mutable_project(db, actor, project_id)
    role = validators.validate_assignable_role(role)
    _check_can_grant(db, actor, project, role)
    subject: dict[str, Any]
    if user_id is not None:
        _require_org_member(db, actor, user_id)
        duplicate = select(ProjectMember.id).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
        )
        subject = {"user_id": str(user_id)}
    else:
        team = get_owned(db, Team, team_id, actor, label="Team")
        duplicate = select(ProjectMember.id).where(
            ProjectMember.project_id == project.id, ProjectMember.team_id == team.id
        )
        subject = {"team_id": str(team.id)}
    if db.scalar(duplicate):
        raise Conflict("This subject is already a member of the project")
    member = ProjectMember(
        organization_id=actor.organization_id, project_id=project.id, user_id=user_id, team_id=team_id, role=role
    )
    db.add(member)
    _flush_or_conflict(db, "This subject is already a member of the project")
    audit(
        db,
        actor,
        AuditAction.PROJECT_MEMBER_CHANGED,
        "project",
        project.id,
        after={"operation": "added", "member_id": member.id, "role": role, **subject},
    )
    return member


def _load_project_member(db: Session, actor: Actor, project: Project, member_id: uuid.UUID | str) -> ProjectMember:
    member = get_owned(db, ProjectMember, member_id, actor, label="Project member")
    if member.project_id != project.id:
        raise NotFound("Project member not found")
    return member


def update_project_member(
    db: Session, actor: Actor, project_id: uuid.UUID | str, member_id: uuid.UUID | str, *, role: str
) -> ProjectMember:
    _require_membership_admin(actor)
    project = _mutable_project(db, actor, project_id)
    member = _load_project_member(db, actor, project, member_id)
    role = validators.validate_assignable_role(role)
    _check_can_grant(db, actor, project, member.role)  # may the actor manage this member at all?
    _check_can_grant(db, actor, project, role)
    previous = member.role
    member.role = role
    audit(
        db,
        actor,
        AuditAction.PROJECT_MEMBER_CHANGED,
        "project",
        project.id,
        before={"member_id": member.id, "role": previous},
        after={"operation": "role_changed", "member_id": member.id, "role": role},
    )
    db.flush()
    return member


def remove_project_member(db: Session, actor: Actor, project_id: uuid.UUID | str, member_id: uuid.UUID | str) -> None:
    _require_membership_admin(actor)
    project = _mutable_project(db, actor, project_id)
    member = _load_project_member(db, actor, project, member_id)
    _check_can_grant(db, actor, project, member.role)
    snapshot = {
        "member_id": member.id,
        "role": member.role,
        "user_id": member.user_id,
        "team_id": member.team_id,
    }
    db.delete(member)
    audit(
        db,
        actor,
        AuditAction.PROJECT_MEMBER_CHANGED,
        "project",
        project.id,
        before=snapshot,
        after={"operation": "removed"},
    )
    db.flush()


def project_member_out(db: Session, member: ProjectMember) -> ProjectMemberOut:
    user = db.get(User, member.user_id) if member.user_id else None
    team = db.get(Team, member.team_id) if member.team_id else None
    return _project_member_out(member, user, team)


# --- teams ---------------------------------------------------------------------------------------
def _team_out(db: Session, team: Team) -> TeamOut:
    count = db.scalar(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)) or 0
    return TeamOut(
        id=str(team.id),
        name=team.name,
        description=team.description,
        created_by_id=str(team.created_by_id) if team.created_by_id else None,
        member_count=int(count),
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def team_out(db: Session, team: Team) -> TeamOut:
    return _team_out(db, team)


def _manage_teams(actor: Actor) -> None:
    actor.require("team:manage")
    actor.require_human("managing teams")


def create_team(db: Session, actor: Actor, data: TeamCreate) -> Team:
    _manage_teams(actor)
    name = data.name.strip()
    if db.scalar(select(Team.id).where(Team.organization_id == actor.organization_id, Team.name == name)):
        raise Conflict("A team with this name already exists")
    team = Team(
        organization_id=actor.organization_id, name=name, description=data.description, created_by_id=actor.user_id
    )
    db.add(team)
    _flush_or_conflict(db, "A team with this name already exists")
    audit(db, actor, "TEAM_CHANGED", "team", team.id, after={"operation": "created", "name": name})
    return team


def list_teams(db: Session, actor: Actor, params: PageParams, *, q: str | None = None) -> Page:
    actor.require("team:read")
    stmt = select(Team).where(Team.organization_id == actor.organization_id)
    if q:
        stmt = stmt.where(Team.name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    return paginate(db, stmt.order_by(Team.name.asc(), Team.id.asc()), params, lambda t: _team_out(db, t))


def get_team(db: Session, actor: Actor, team_id: uuid.UUID | str) -> Team:
    actor.require("team:read")
    return get_owned(db, Team, team_id, actor, label="Team")


def update_team(db: Session, actor: Actor, team_id: uuid.UUID | str, data: TeamUpdate) -> Team:
    _manage_teams(actor)
    team = get_owned(db, Team, team_id, actor, label="Team")
    changes = data.model_dump(exclude_unset=True)
    before = {k: getattr(team, k) for k in changes}
    if "name" in changes and data.name is not None:
        team.name = data.name.strip()
    if "description" in changes:
        team.description = data.description
    _flush_or_conflict(db, "A team with this name already exists")
    audit(db, actor, "TEAM_CHANGED", "team", team.id, before=before, after={"operation": "updated", **changes})
    return team


def delete_team(db: Session, actor: Actor, team_id: uuid.UUID | str) -> None:
    """Delete a team; its project grants disappear with it (``project_members`` cascade)."""
    _manage_teams(actor)
    team = get_owned(db, Team, team_id, actor, label="Team")
    grants = db.execute(
        select(ProjectMember.project_id, ProjectMember.role).where(ProjectMember.team_id == team.id)
    ).all()
    snapshot = {"name": team.name, "project_grants": [{"project_id": p, "role": r} for p, r in grants]}
    db.delete(team)
    audit(db, actor, "TEAM_CHANGED", "team", team.id, before=snapshot, after={"operation": "deleted"})
    db.flush()


def list_team_members(db: Session, actor: Actor, team_id: uuid.UUID | str) -> list[TeamMemberOut]:
    team = get_team(db, actor, team_id)
    rows = db.execute(
        select(TeamMember, User)
        .outerjoin(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team.id, TeamMember.organization_id == actor.organization_id)
        .order_by(TeamMember.created_at.asc())
    ).all()
    return [_team_member_out(m, u) for m, u in rows]


def _team_member_out(member: TeamMember, user: User | None) -> TeamMemberOut:
    return TeamMemberOut(
        id=str(member.id),
        team_id=str(member.team_id),
        user_id=str(member.user_id),
        email=user.email if user else None,
        full_name=user.full_name if user else None,
        created_at=member.created_at,
    )


def _check_team_grants(db: Session, actor: Actor, team: Team) -> None:
    """Adding someone to a team grants them the team's project roles: require the actor could grant them."""
    if actor.role in ORG_SUPERUSER_ROLES:
        return
    grants = db.execute(
        select(ProjectMember.role, Project)
        .join(Project, Project.id == ProjectMember.project_id)
        .where(ProjectMember.team_id == team.id)
    ).all()
    for role, project in grants:
        if permissions_for_role(role) - effective_permissions(db, actor, project):
            raise Forbidden("This team grants project roles that exceed your permissions; ask an administrator")


def add_team_member(db: Session, actor: Actor, team_id: uuid.UUID | str, *, user_id: uuid.UUID) -> TeamMemberOut:
    _manage_teams(actor)
    team = get_owned(db, Team, team_id, actor, label="Team")
    _, user = _require_org_member(db, actor, user_id)
    if db.scalar(select(TeamMember.id).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)):
        raise Conflict("The user is already a member of this team")
    _check_team_grants(db, actor, team)
    member = TeamMember(organization_id=actor.organization_id, team_id=team.id, user_id=user_id)
    db.add(member)
    _flush_or_conflict(db, "The user is already a member of this team")
    audit(db, actor, "TEAM_CHANGED", "team", team.id, after={"operation": "member_added", "user_id": user_id})
    return _team_member_out(member, user)


def remove_team_member(db: Session, actor: Actor, team_id: uuid.UUID | str, user_id: uuid.UUID) -> None:
    _manage_teams(actor)
    team = get_owned(db, Team, team_id, actor, label="Team")
    member = db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.user_id == user_id,
            TeamMember.organization_id == actor.organization_id,
        )
    )
    if member is None:
        raise NotFound("Team member not found")
    db.delete(member)
    audit(
        db, actor, "TEAM_CHANGED", "team", team.id, before={"user_id": user_id}, after={"operation": "member_removed"}
    )
    db.flush()


# --- users ---------------------------------------------------------------------------------------
def get_me(db: Session, actor: Actor) -> MeOut:
    """The caller's identity, organization context and effective permissions."""
    user: User | None = None
    memberships: list[MembershipSummary] = []
    if actor.kind == "user" and actor.user_id is not None:
        user = db.get(User, actor.user_id)
        rows = db.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(Membership.user_id == actor.user_id)
            .order_by(Organization.name, Organization.id)
        ).all()
        memberships = [
            MembershipSummary(
                organization_id=str(org.id),
                organization_name=org.name,
                organization_slug=org.slug,
                role=m.role,
                status=m.status,
            )
            for m, org in rows
        ]
    return MeOut(
        kind=actor.kind,
        user=UserProfileOut.model_validate(user) if user is not None else None,
        organization_id=str(actor.organization_id),
        role=actor.role or "",
        auth_method=actor.auth_method or actor.kind,
        permissions=sorted(actor.permissions),
        lab_permissions=sorted(actor.permissions & ALL_LAB_PERMISSIONS),
        is_platform_admin=actor.is_platform_admin,
        service_account_id=str(actor.service_account_id) if actor.service_account_id else None,
        api_key_id=str(actor.api_key_id) if actor.api_key_id else None,
        project_ids=sorted(actor.project_ids),
        memberships=memberships,
    )


def list_users(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    q: str | None = None,
    role: str | None = None,
    status: str | None = None,
) -> Page:
    if not (actor.has("team:read") or actor.has("project:read")):
        raise Forbidden("Missing required permission(s): team:read or project:read")
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == actor.organization_id)
    )
    if q:
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\")))
    if role:
        stmt = stmt.where(Membership.role == role)
    if status:
        stmt = stmt.where(Membership.status == status)
    stmt = stmt.order_by(User.email.asc(), Membership.id.asc())
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.limit(params.page_size).offset(params.offset)).all()
    items = [
        OrgUserOut(
            user_id=str(u.id),
            membership_id=str(m.id),
            email=u.email,
            full_name=u.full_name,
            role=m.role,
            status=m.status,
            email_verified=u.email_verified,
            joined_at=m.created_at,
            last_active_at=m.last_active_at or u.last_active_at,
        )
        for m, u in rows
    ]
    return Page.build(items, int(total), params)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:200]


# --- RBAC catalog --------------------------------------------------------------------------------
CORE_PERMISSIONS = frozenset(OWNER)


def _core_description(key: str) -> str:
    resource, _, verb = key.partition(":")
    return f"{verb.replace('_', ' ').capitalize()} {resource.replace('_', ' ')} (assurance platform)"


def permission_catalog() -> list[PermissionOut]:
    out = [
        PermissionOut(
            key=key, category=category, description=description, family="lab", human_only=key in HUMAN_ONLY_PERMISSIONS
        )
        for key, (category, description) in LAB_PERMISSION_CATALOG.items()
    ]
    out.extend(
        PermissionOut(
            key=key,
            category="assurance",
            description=_core_description(key),
            family="core",
            human_only=key in HUMAN_ONLY_PERMISSIONS,
        )
        for key in CORE_PERMISSIONS - ALL_LAB_PERMISSIONS
    )
    return sorted(out, key=lambda p: (p.family, p.category, p.key))


def role_catalog() -> list[RoleOut]:
    return [
        RoleOut(
            key=str(role),
            name=str(role).replace("_", " ").title(),
            description=ROLE_DESCRIPTIONS.get(role),
            is_system=True,
            rank=ROLE_RANK.get(role, 0),
            assignable=role in validators.ASSIGNABLE_ROLES,
            permissions=sorted(perms),
        )
        for role, perms in sorted(ROLE_PERMISSIONS.items(), key=lambda kv: (-ROLE_RANK.get(kv[0], 0), str(kv[0])))
    ]


def seed_rbac_catalog(session: Session) -> dict[str, int]:
    """Idempotently upsert the built-in role/permission catalog (owner session; called at start-up)."""
    now = utcnow()
    permissions = permission_catalog()
    perm_stmt = insert(PermissionRecord).values(
        [
            {"id": uuid.uuid4(), "key": p.key, "category": p.category, "description": p.description, "created_at": now}
            for p in permissions
        ]
    )
    session.execute(
        perm_stmt.on_conflict_do_update(
            index_elements=[PermissionRecord.key],
            set_={"category": perm_stmt.excluded.category, "description": perm_stmt.excluded.description},
        )
    )
    roles = role_catalog()
    for role in roles:
        stmt = insert(RoleRecord).values(
            id=uuid.uuid4(),
            organization_id=None,
            key=role.key,
            name=role.name,
            description=role.description,
            is_system=True,
            created_at=now,
            updated_at=now,
        )
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_roles_org_key",
                set_={"name": role.name, "description": role.description, "is_system": True, "updated_at": now},
            )
        )
    perm_ids: dict[str, uuid.UUID] = dict(
        session.execute(select(PermissionRecord.key, PermissionRecord.id)).tuples().all()
    )
    role_ids: dict[str, uuid.UUID] = dict(
        session.execute(select(RoleRecord.key, RoleRecord.id).where(RoleRecord.organization_id.is_(None)))
        .tuples()
        .all()
    )
    desired = {(role_ids[role.key], perm_ids[perm]) for role in roles for perm in role.permissions if perm in perm_ids}
    system_role_ids = [role_ids[role.key] for role in roles]
    existing = set(
        session.execute(
            select(RolePermission.role_id, RolePermission.permission_id).where(
                RolePermission.role_id.in_(system_role_ids)
            )
        )
        .tuples()
        .all()
    )
    missing = desired - existing
    if missing:
        session.execute(
            insert(RolePermission)
            .values(
                [
                    {"id": uuid.uuid4(), "organization_id": None, "role_id": r, "permission_id": p, "created_at": now}
                    for r, p in sorted(missing, key=str)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_role_permissions_role_perm")
        )
    stale = existing - desired
    for role_id, permission_id in stale:
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role_id, RolePermission.permission_id == permission_id
            )
        )
    session.flush()
    return {"permissions": len(permissions), "roles": len(roles), "added": len(missing), "removed": len(stale)}
