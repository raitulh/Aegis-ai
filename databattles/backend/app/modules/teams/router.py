from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, select

from app.core.deps import Actor, get_actor, require_user
from app.core.errors import NotFound
from app.core.pagination import Page, PageParams, make_page
from app.core.permissions import can_manage_competition
from app.core.schemas import Message, Schema, UserMini, user_mini
from app.models.competition import Competition, Team, TeamInvitation, TeamMember
from app.models.enums import InvitationStatus
from app.models.user import User
from app.modules.competitions import state
from app.modules.competitions.service import load_visible
from app.modules.leaderboards.service import team_score_history
from app.modules.teams import service

router = APIRouter(tags=["teams"])


class TeamMemberOut(Schema):
    user: UserMini
    role: str
    joined_at: datetime


class PendingInviteOut(Schema):
    id: uuid.UUID
    invitee: UserMini | None
    invitee_email: str | None
    created_at: datetime
    expires_at: datetime


class TeamOut(Schema):
    id: uuid.UUID
    name: str
    is_solo: bool
    captain_id: uuid.UUID
    created_at: datetime
    members: list[TeamMemberOut]
    pending_invitations: list[PendingInviteOut] = []
    workspace_url: str | None = None
    locked: bool
    min_size: int
    max_size: int
    submission_count: int
    score_history: list[dict] = []


class TeamListItem(Schema):
    id: uuid.UUID
    name: str
    is_solo: bool
    member_count: int
    members: list[UserMini]


class InvitationOut(Schema):
    id: uuid.UUID
    team_id: uuid.UUID
    team_name: str
    competition_slug: str
    competition_title: str
    inviter: UserMini | None
    created_at: datetime
    expires_at: datetime
    status: str


class TeamCreateIn(Schema):
    name: str = Field(min_length=2, max_length=60)


class TeamUpdateIn(Schema):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    workspace_url: str | None = Field(default=None, max_length=500)


class InviteIn(Schema):
    handle: str | None = Field(default=None, max_length=31)
    email: str | None = Field(default=None, max_length=320)


class RespondIn(Schema):
    accept: bool
    accept_rules: bool = False


class TransferIn(Schema):
    user_id: uuid.UUID


class RemoveIn(Schema):
    reason: str | None = Field(default=None, max_length=500)


class ClaimIn(Schema):
    token: str = Field(min_length=10, max_length=200)


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


def team_out(actor: Actor, comp: Competition, team: Team) -> TeamOut:
    db = actor.db
    rows = db.execute(select(TeamMember, User).join(User, User.id == TeamMember.user_id)
                      .where(TeamMember.team_id == team.id).order_by(TeamMember.joined_at)).all()
    is_member = any(u.id == actor.id for _, u in rows)
    manager = can_manage_competition(actor, comp)
    pending: list[PendingInviteOut] = []
    if team.captain_id == actor.id or manager:
        invs = db.execute(select(TeamInvitation, User).outerjoin(User, User.id == TeamInvitation.invitee_user_id)
                          .where(TeamInvitation.team_id == team.id, TeamInvitation.status == InvitationStatus.pending)).all()
        pending = [PendingInviteOut(id=i.id, invitee=user_mini(u), invitee_email=_mask(i.invitee_email), created_at=i.created_at,
                                    expires_at=i.expires_at) for i, u in invs]
    from app.models.submission import Submission

    subs = db.scalar(select(func.count()).select_from(Submission).where(Submission.team_id == team.id)) or 0
    return TeamOut(
        id=team.id, name=team.name, is_solo=team.is_solo, captain_id=team.captain_id, created_at=team.created_at,
        members=[TeamMemberOut(user=user_mini(u), role=m.role, joined_at=m.joined_at) for m, u in rows],  # type: ignore[arg-type]
        pending_invitations=pending, workspace_url=team.workspace_url if (is_member or manager) else None,
        locked=state.teams_locked(comp), min_size=comp.team_min_size, max_size=comp.team_max_size, submission_count=subs,
        score_history=team_score_history(db, team.id) if (is_member or manager) else [],
    )


def _team(actor: Actor, comp: Competition, team_id: uuid.UUID) -> Team:
    team = actor.db.get(Team, team_id)
    if team is None or team.competition_id != comp.id:
        raise NotFound("Team not found.")
    return team


@router.get("/competitions/{slug}/teams", response_model=Page[TeamListItem])
def list_teams(slug: str, params: PageParams = Depends(), q: str | None = Query(None, max_length=60),
               actor: Actor = Depends(get_actor)) -> dict:
    comp = load_visible(actor, slug)
    db = actor.db
    stmt = select(Team).where(Team.competition_id == comp.id)
    if q:
        stmt = stmt.where(Team.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    teams = db.scalars(stmt.order_by(Team.created_at).limit(params.page_size).offset(params.offset)).all()
    members = db.execute(select(TeamMember.team_id, User).join(User, User.id == TeamMember.user_id)
                         .where(TeamMember.team_id.in_([t.id for t in teams]))).all()
    by_team: dict[uuid.UUID, list[UserMini]] = {}
    for tid, u in members:
        by_team.setdefault(tid, []).append(user_mini(u))  # type: ignore[arg-type]
    items = [TeamListItem(id=t.id, name=t.name, is_solo=t.is_solo, member_count=len(by_team.get(t.id, [])),
                          members=by_team.get(t.id, [])) for t in teams]
    return make_page(items, total, params)


@router.get("/competitions/{slug}/team", response_model=TeamOut | None)
def my_team(slug: str, actor: Actor = Depends(require_user)) -> TeamOut | None:
    comp = load_visible(actor, slug)
    team = service.team_for_user(actor.db, comp.id, actor.id)
    return team_out(actor, comp, team) if team else None


@router.get("/competitions/{slug}/teams/{team_id}", response_model=TeamOut)
def get_team(slug: str, team_id: uuid.UUID, actor: Actor = Depends(get_actor)) -> TeamOut:
    comp = load_visible(actor, slug)
    return team_out(actor, comp, _team(actor, comp, team_id))


@router.post("/competitions/{slug}/teams", response_model=TeamOut, status_code=201)
def create_team(slug: str, data: TeamCreateIn, actor: Actor = Depends(require_user)) -> TeamOut:
    comp = load_visible(actor, slug)
    return team_out(actor, comp, service.create_team(actor, comp, data.name))


@router.patch("/competitions/{slug}/teams/{team_id}", response_model=TeamOut)
def update_team(slug: str, team_id: uuid.UUID, data: TeamUpdateIn, actor: Actor = Depends(require_user)) -> TeamOut:
    comp = load_visible(actor, slug)
    team = service.update_team(actor, comp, _team(actor, comp, team_id), data.name, data.workspace_url)
    return team_out(actor, comp, team)


@router.delete("/competitions/{slug}/teams/{team_id}", response_model=Message)
def delete_team(slug: str, team_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    comp = load_visible(actor, slug)
    service.delete_team(actor, comp, _team(actor, comp, team_id))
    return Message(message="Team deleted.")


@router.post("/competitions/{slug}/teams/{team_id}/invitations", response_model=Message, status_code=201)
def invite(slug: str, team_id: uuid.UUID, data: InviteIn, actor: Actor = Depends(require_user)) -> Message:
    comp = load_visible(actor, slug)
    service.invite(actor, comp, _team(actor, comp, team_id), handle=data.handle, email=data.email)
    return Message(message="Invitation sent.")


@router.delete("/competitions/{slug}/teams/{team_id}/invitations/{invitation_id}", response_model=Message)
def revoke_invitation(slug: str, team_id: uuid.UUID, invitation_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    comp = load_visible(actor, slug)
    service.revoke_invitation(actor, _team(actor, comp, team_id), invitation_id)
    return Message(message="Invitation revoked.")


@router.post("/competitions/{slug}/teams/{team_id}/transfer", response_model=TeamOut)
def transfer(slug: str, team_id: uuid.UUID, data: TransferIn, actor: Actor = Depends(require_user)) -> TeamOut:
    comp = load_visible(actor, slug)
    return team_out(actor, comp, service.transfer_captain(actor, comp, _team(actor, comp, team_id), data.user_id))


@router.post("/competitions/{slug}/teams/{team_id}/leave", response_model=Message)
def leave(slug: str, team_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    comp = load_visible(actor, slug)
    service.leave(actor, comp, _team(actor, comp, team_id))
    return Message(message="You left the team.")


@router.post("/competitions/{slug}/teams/{team_id}/members/{user_id}/remove", response_model=Message)
def remove_member(slug: str, team_id: uuid.UUID, user_id: uuid.UUID, data: RemoveIn, actor: Actor = Depends(require_user)) -> Message:
    comp = load_visible(actor, slug)
    service.remove_member(actor, comp, _team(actor, comp, team_id), user_id, data.reason)
    return Message(message="Member removed.")


@router.get("/me/team-invitations", response_model=list[InvitationOut], tags=["me"])
def my_invitations(actor: Actor = Depends(require_user)) -> list[InvitationOut]:
    db = actor.db
    rows = db.execute(select(TeamInvitation, Team, Competition, User)
                      .join(Team, Team.id == TeamInvitation.team_id)
                      .join(Competition, Competition.id == TeamInvitation.competition_id)
                      .outerjoin(User, User.id == TeamInvitation.inviter_id)
                      .where(TeamInvitation.invitee_user_id == actor.id, TeamInvitation.status == InvitationStatus.pending)
                      .order_by(TeamInvitation.created_at.desc())).all()
    return [InvitationOut(id=i.id, team_id=t.id, team_name=t.name, competition_slug=c.slug, competition_title=c.title,
                          inviter=user_mini(u), created_at=i.created_at, expires_at=i.expires_at, status=i.status)
            for i, t, c, u in rows]


@router.post("/me/team-invitations/claim", response_model=InvitationOut, tags=["me"])
def claim(data: ClaimIn, actor: Actor = Depends(require_user)) -> InvitationOut:
    inv = service.claim_email_invitation(actor, data.token)
    db = actor.db
    team, comp = db.get(Team, inv.team_id), db.get(Competition, inv.competition_id)
    assert team and comp
    return InvitationOut(id=inv.id, team_id=team.id, team_name=team.name, competition_slug=comp.slug, competition_title=comp.title,
                         inviter=user_mini(db.get(User, inv.inviter_id)) if inv.inviter_id else None,
                         created_at=inv.created_at, expires_at=inv.expires_at, status=inv.status)


@router.post("/me/team-invitations/{invitation_id}/respond", response_model=Message, tags=["me"])
def respond(invitation_id: uuid.UUID, data: RespondIn, actor: Actor = Depends(require_user)) -> Message:
    inv = service.respond(actor, invitation_id, data.accept, data.accept_rules)
    return Message(message="You joined the team." if inv.status == "accepted" else "Invitation declined.")
