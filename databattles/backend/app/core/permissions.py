"""Centralized authorization rules.

Every visibility and management decision goes through this module so rules
are explicit, testable and consistent between list and detail endpoints.
Services raise NotFound (not Forbidden) when revealing existence would leak
private information.

Permission matrix (summary — see docs/SECURITY.md for the full table):

| Capability                         | Rule                                                              |
|------------------------------------|-------------------------------------------------------------------|
| Manage competition                 | platform admin, competition organizer, host org owner/admin       |
| Judge competition                  | competition judge (assigned), organizers                          |
| View draft competition             | managers + judges only                                            |
| View university competition        | host org active members, participants, staff                      |
| View private competition           | participants, invitees, staff                                     |
| Manage organization                | platform admin, org owner/admin                                   |
| Manage org content (courses, etc.) | platform admin, org owner/admin/manager                           |
| Moderate content                   | platform moderator/admin                                          |
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, false, or_, select

from app.core.deps import Actor
from app.core.errors import Forbidden, NotFound
from app.models.competition import Competition, CompetitionParticipant, CompetitionStaff, TeamInvitation
from app.models.dataset import Dataset
from app.models.enums import (
    CompetitionVisibility,
    ContentStatus,
    ContentVisibility,
    Lifecycle,
    ProjectRole,
    ProjectVisibility,
    StaffRole,
)
from app.models.project import Project, ProjectMember

# ----------------------------------------------------------------------------- organizations


def can_manage_org(actor: Actor, org_id: uuid.UUID | None) -> bool:
    return actor.is_authenticated and actor.is_org_admin(org_id)


def can_manage_org_content(actor: Actor, org_id: uuid.UUID | None) -> bool:
    return actor.is_authenticated and actor.is_org_manager(org_id)


# ----------------------------------------------------------------------------- competitions


def competition_roles(actor: Actor, comp: Competition) -> set[str]:
    if not actor.is_authenticated:
        return set()
    if comp.id in actor._staff_cache:
        return actor._staff_cache[comp.id]
    roles: set[str] = set()
    if actor.is_admin or (comp.host_org_id and actor.is_org_admin(comp.host_org_id)):
        roles.add(StaffRole.organizer)
    if comp.created_by and comp.created_by == actor.id:
        roles.add(StaffRole.organizer)
    roles.update(
        actor.db.scalars(
            select(CompetitionStaff.role).where(
                CompetitionStaff.competition_id == comp.id, CompetitionStaff.user_id == actor.id
            )
        )
    )
    actor._staff_cache[comp.id] = roles
    return roles


def can_manage_competition(actor: Actor, comp: Competition) -> bool:
    return StaffRole.organizer in competition_roles(actor, comp)


def is_judge(actor: Actor, comp: Competition) -> bool:
    return StaffRole.judge in competition_roles(actor, comp)


def participant_record(actor: Actor, comp: Competition) -> CompetitionParticipant | None:
    if not actor.is_authenticated:
        return None
    return actor.db.scalar(
        select(CompetitionParticipant).where(
            CompetitionParticipant.competition_id == comp.id, CompetitionParticipant.user_id == actor.id
        )
    )


def has_pending_team_invite(actor: Actor, comp: Competition) -> bool:
    if not actor.is_authenticated:
        return False
    return bool(actor.db.scalar(
        select(exists().where(
            TeamInvitation.competition_id == comp.id,
            TeamInvitation.invitee_user_id == actor.id,
            TeamInvitation.status == "pending",
        ))
    ))


def can_view_competition(actor: Actor, comp: Competition) -> bool:
    roles = competition_roles(actor, comp)
    if roles:
        return True
    if comp.lifecycle == Lifecycle.draft:
        return False
    if comp.visibility in (CompetitionVisibility.public, CompetitionVisibility.invite_only):
        return True
    if comp.visibility == CompetitionVisibility.university:
        return actor.is_org_member(comp.host_org_id) or participant_record(actor, comp) is not None
    # private
    return participant_record(actor, comp) is not None or has_pending_team_invite(actor, comp)


def assert_view_competition(actor: Actor, comp: Competition | None) -> Competition:
    if comp is None or not can_view_competition(actor, comp):
        raise NotFound("Competition not found.")
    return comp


def assert_manage_competition(actor: Actor, comp: Competition) -> None:
    if not can_manage_competition(actor, comp):
        raise Forbidden("Only organizers of this competition can do that.")


def listable_competitions_clause(actor: Actor) -> ColumnElement[bool]:
    """SQL filter for discovery lists: never lists drafts, invite-only or private events to non-staff."""
    listed = Competition.lifecycle.in_([Lifecycle.published, Lifecycle.finalized, Lifecycle.archived])
    visible = Competition.visibility == CompetitionVisibility.public
    member_orgs = actor.member_org_ids() if actor.is_authenticated else []
    if member_orgs:
        visible = or_(visible, and_(Competition.visibility == CompetitionVisibility.university,
                                    Competition.host_org_id.in_(member_orgs)))
    clause = and_(listed, visible)
    if actor.is_authenticated:
        participating = exists().where(
            CompetitionParticipant.competition_id == Competition.id, CompetitionParticipant.user_id == actor.id
        )
        staffed = exists().where(CompetitionStaff.competition_id == Competition.id, CompetitionStaff.user_id == actor.id)
        clause = or_(clause, and_(listed, participating), staffed, Competition.created_by == actor.id)
        if actor.is_admin:
            clause = or_(clause, listed)
    return clause


def is_indexable_competition(comp: Competition) -> bool:
    return comp.lifecycle in (Lifecycle.published, Lifecycle.finalized) and comp.visibility == CompetitionVisibility.public


# ----------------------------------------------------------------------------- datasets


def can_manage_dataset(actor: Actor, ds: Dataset) -> bool:
    if not actor.is_authenticated:
        return False
    if actor.is_admin or ds.owner_user_id == actor.id:
        return True
    return bool(ds.owner_org_id and actor.is_org_manager(ds.owner_org_id))


def _dataset_used_by_joined_competition(actor: Actor, ds: Dataset) -> bool:
    if not actor.is_authenticated:
        return False
    from app.models.dataset import DatasetVersion

    return bool(actor.db.scalar(select(exists().where(
        DatasetVersion.dataset_id == ds.id,
        Competition.dataset_version_id == DatasetVersion.id,
        CompetitionParticipant.competition_id == Competition.id,
        CompetitionParticipant.user_id == actor.id,
    ))))


def can_view_dataset(actor: Actor, ds: Dataset) -> bool:
    if can_manage_dataset(actor, ds) or actor.is_moderator:
        return True
    if ds.status == ContentStatus.taken_down:
        return False
    if ds.visibility == ContentVisibility.public:
        return True
    if ds.visibility == ContentVisibility.org and ds.owner_org_id and actor.is_org_member(ds.owner_org_id):
        return True
    return _dataset_used_by_joined_competition(actor, ds)


def listable_datasets_clause(actor: Actor) -> ColumnElement[bool]:
    clause: ColumnElement[bool] = and_(Dataset.status == ContentStatus.active, Dataset.visibility == ContentVisibility.public)
    if actor.is_authenticated:
        orgs = actor.member_org_ids()
        extra = [Dataset.owner_user_id == actor.id]
        if orgs:
            extra.append(and_(Dataset.visibility == ContentVisibility.org, Dataset.owner_org_id.in_(orgs),
                              Dataset.status == ContentStatus.active))
        clause = or_(clause, *extra)
    return clause


# ----------------------------------------------------------------------------- projects


def project_role(actor: Actor, project: Project) -> str | None:
    if not actor.is_authenticated:
        return None
    if project.owner_id == actor.id:
        return ProjectRole.owner
    return actor.db.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == actor.id)
    )


def can_edit_project(actor: Actor, project: Project) -> bool:
    return actor.is_admin or project_role(actor, project) in (ProjectRole.owner, ProjectRole.maintainer)


def can_view_project(actor: Actor, project: Project) -> bool:
    if actor.is_moderator or project_role(actor, project) is not None:
        return True
    if project.taken_down:
        return False
    if project.status == "draft":
        return False
    return project.visibility in (ProjectVisibility.public, ProjectVisibility.unlisted)


def listable_projects_clause(actor: Actor) -> ColumnElement[bool]:
    clause: ColumnElement[bool] = and_(
        Project.visibility == ProjectVisibility.public, Project.taken_down.is_(False), Project.status != "draft"
    )
    if actor.is_authenticated:
        clause = or_(clause, Project.owner_id == actor.id)
    return clause


# ----------------------------------------------------------------------------- misc


def require(condition: bool, message: str | None = None, *, not_found: bool = False) -> None:
    if condition:
        return
    if not_found:
        raise NotFound(message)
    raise Forbidden(message)


def never() -> Any:
    return false()
