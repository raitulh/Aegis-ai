"""Tenant- and project-scoped access helpers used by every lab service.

Defense in depth: every lookup filters by the principal's organization (application layer) *and* runs on the
RLS-scoped session (database layer). Project visibility and service-account project restrictions are enforced
here. Not-found and not-permitted both surface as 404 to avoid leaking existence across tenants/projects.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound
from aegis_api.models import Project, ProjectMember
from aegis_api.security.context import Principal


def can_access_project(db: Session, principal: Principal, project: Project) -> bool:
    if project.organization_id != principal.organization_id:
        return False
    if not principal.may_access_project(project.id):
        return False
    if project.visibility != "private" or principal.has("project:access_all"):
        return True
    if principal.actor_type != "user":
        return False
    return (
        db.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == principal.user_id
            )
        )
        is not None
    )


def get_project(db: Session, principal: Principal, project_id: uuid.UUID | str) -> Project:
    try:
        pid = uuid.UUID(str(project_id))
    except ValueError as exc:
        raise NotFound("Project not found") from exc
    project = db.get(Project, pid)
    if project is None or project.archived_at is not None or not can_access_project(db, principal, project):
        raise NotFound("Project not found")
    return project


def get_scoped[T](
    db: Session, principal: Principal, model: type[T], obj_id: uuid.UUID | str, *, label: str | None = None
) -> T:
    """Fetch an org-owned row; if it has ``project_id``, also enforce project access."""
    name = label or getattr(model, "__name__", "Resource")
    try:
        oid = uuid.UUID(str(obj_id))
    except ValueError as exc:
        raise NotFound(f"{name} not found") from exc
    row: Any = db.get(model, oid)
    if row is None or getattr(row, "organization_id", None) != principal.organization_id:
        raise NotFound(f"{name} not found")
    project_id = getattr(row, "project_id", None)
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is None or not can_access_project(db, principal, project):
            raise NotFound(f"{name} not found")
    return row


def accessible_project_ids(db: Session, principal: Principal) -> list[uuid.UUID] | None:
    """Project ids the principal may see, or None when unrestricted (all org projects)."""
    stmt = select(Project.id, Project.visibility).where(
        Project.organization_id == principal.organization_id, Project.archived_at.is_(None)
    )
    rows = db.execute(stmt).all()
    private = [r.id for r in rows if r.visibility == "private"]
    restricted = bool(principal.project_ids)
    if not private and not restricted:
        return None
    member_of: set[uuid.UUID] = set()
    if private and principal.actor_type == "user" and not principal.has("project:access_all"):
        member_of = set(
            db.scalars(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == principal.user_id, ProjectMember.project_id.in_(private)
                )
            ).all()
        )
    out: list[uuid.UUID] = []
    for r in rows:
        if restricted and str(r.id) not in principal.project_ids:
            continue
        if r.visibility == "private" and not principal.has("project:access_all") and r.id not in member_of:
            continue
        out.append(r.id)
    return out
