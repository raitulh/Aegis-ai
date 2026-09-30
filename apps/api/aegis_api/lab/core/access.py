"""Authorization below the organization level: workspaces and projects.

Rules (defense in depth — RLS already confines every query to the actor's organization):

1. The actor must hold the permission organization-wide (org role, narrowed by key scopes), **or** the
   project grants it through ``project_members`` (directly or via a team).
2. ``restricted`` projects are only visible to their members (and org owners/admins).
3. Service accounts / scoped keys may be limited to an explicit project list.
4. Every id supplied by a client is loaded through the tenant session and re-checked against the
   actor's organization — client-provided tenant identifiers are never trusted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import Forbidden, NotFound
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.models import Project, ProjectMember, TeamMember, Workspace
from aegis_api.models.enums import Role
from aegis_api.security.rbac import HUMAN_ONLY_PERMISSIONS, permissions_for_role

ORG_SUPERUSER_ROLES = frozenset({Role.OWNER, Role.ADMIN})


def get_owned[T](
    db: Session, model: type[T], object_id: uuid.UUID | str | None, actor: Actor, *, label: str | None = None
) -> T:
    """Load a tenant-owned row by id and verify it belongs to the actor's organization.

    Raises ``NotFound`` (never ``Forbidden``) for foreign rows so ids of other tenants are not disclosed.
    """
    name = label or getattr(model, "__name__", "Resource")
    if object_id is None:
        raise NotFound(f"{name} not found")
    try:
        key = object_id if isinstance(object_id, uuid.UUID) else uuid.UUID(str(object_id))
    except ValueError as exc:
        raise NotFound(f"{name} not found") from exc
    row = db.get(model, key)
    if row is None or getattr(row, "organization_id", None) != actor.organization_id:
        raise NotFound(f"{name} not found")
    return row


def project_role_permissions(db: Session, actor: Actor, project_id: uuid.UUID) -> frozenset[str]:
    """Permissions granted to the actor inside one project via project/team membership."""
    if actor.user_id is None or actor.kind not in ("user",):
        return frozenset()
    team_ids = select(TeamMember.team_id).where(TeamMember.user_id == actor.user_id)
    roles = db.scalars(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            or_(ProjectMember.user_id == actor.user_id, ProjectMember.team_id.in_(team_ids)),
        )
    ).all()
    granted: set[str] = set()
    for role in roles:
        granted |= permissions_for_role(role)
    return frozenset(granted)


def is_project_member(db: Session, actor: Actor, project_id: uuid.UUID) -> bool:
    if actor.user_id is None:
        return False
    team_ids = select(TeamMember.team_id).where(TeamMember.user_id == actor.user_id)
    return (
        db.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                or_(ProjectMember.user_id == actor.user_id, ProjectMember.team_id.in_(team_ids)),
            )
        )
        is not None
    )


def effective_permissions(db: Session, actor: Actor, project: Project) -> frozenset[str]:
    """Organization permissions ∪ project-role permissions, respecting actor kind limits."""
    perms = set(actor.permissions)
    if actor.kind == "user":
        perms |= project_role_permissions(db, actor, project.id)
    elif actor.kind in ("agent", "workflow"):
        # Derived actors never gain project-role permissions beyond what they were started with.
        pass
    if actor.kind in ("api_key", "service_account", "agent"):
        perms -= HUMAN_ONLY_PERMISSIONS
    return frozenset(perms)


def can_see_project(db: Session, actor: Actor, project: Project) -> bool:
    if project.organization_id != actor.organization_id:
        return False
    if not actor.can_access_project(project.id):
        return False
    if project.visibility == "restricted":
        if actor.kind in ("system", "workflow"):
            return True
        if actor.role in ORG_SUPERUSER_ROLES:
            return True
        return is_project_member(db, actor, project.id)
    return True


def load_project(db: Session, actor: Actor, project_id: uuid.UUID | str, *permissions: str) -> Project:
    """Load a project the actor may see and verify the actor holds ``permissions`` inside it."""
    project = get_owned(db, Project, project_id, actor, label="Project")
    if not can_see_project(db, actor, project):
        raise NotFound("Project not found")
    if permissions:
        granted = effective_permissions(db, actor, project)
        missing = [p for p in permissions if p not in granted]
        if missing:
            raise Forbidden(f"Missing required permission(s): {', '.join(missing)}")
    return project


def require_in_project(db: Session, actor: Actor, project_id: uuid.UUID, *permissions: str) -> Project:
    """Alias used by services that already hold a child row: re-validates project access."""
    return load_project(db, actor, project_id, *permissions)


def load_workspace(db: Session, actor: Actor, workspace_id: uuid.UUID | str) -> Workspace:
    return get_owned(db, Workspace, workspace_id, actor, label="Workspace")


def visible_project_ids(db: Session, actor: Actor) -> list[uuid.UUID] | None:
    """Project ids visible to the actor, or ``None`` when every org project is visible (fast path).

    Used to filter list endpoints: ``stmt.where(Model.project_id.in_(ids))`` when not None.
    """
    restricted = db.scalars(
        select(Project.id).where(Project.organization_id == actor.organization_id, Project.visibility == "restricted")
    ).all()
    allowed = {uuid.UUID(p) for p in actor.project_ids} if actor.project_ids else None
    if not restricted and allowed is None:
        return None
    if actor.role in ORG_SUPERUSER_ROLES or actor.kind in ("system", "workflow"):
        hidden: set[uuid.UUID] = set()
    else:
        hidden = {pid for pid in restricted if not is_project_member(db, actor, pid)}
    all_ids = set(db.scalars(select(Project.id).where(Project.organization_id == actor.organization_id)).all())
    visible = all_ids - hidden
    if allowed is not None:
        visible &= allowed
    return sorted(visible)
