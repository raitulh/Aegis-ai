"""Teams. Invariants (enforced here and, where possible, by database constraints):

* A user belongs to at most one team per competition (unique constraint).
* Team size never exceeds the competition maximum (members + pending invitations).
* Membership changes stop at the team lock time.
* Teams with submissions cannot be deleted; submissions always stay with their team.
* Only the captain manages membership; captaincy can be transferred.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import random_suffix
from app.core.permissions import competition_roles, participant_record
from app.core.security import hash_token, new_token
from app.core.time import utcnow
from app.core.validators import validate_external_url
from app.email.sender import queue_email
from app.models.competition import Competition, CompetitionParticipant, Team, TeamInvitation, TeamMember
from app.models.enums import CompetitionVisibility, InvitationStatus, NotificationKind
from app.models.submission import Submission
from app.models.user import User
from app.modules.competitions import state
from app.modules.notifications.service import notify

INVITE_TTL = timedelta(days=14)


def team_for_user(db: Session, competition_id: uuid.UUID, user_id: uuid.UUID | None) -> Team | None:
    if user_id is None:
        return None
    return db.scalar(select(Team).join(TeamMember, TeamMember.team_id == Team.id)
                     .where(TeamMember.competition_id == competition_id, TeamMember.user_id == user_id))


def member_ids(db: Session, team_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == team_id)))


def team_size(db: Session, team_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)) or 0


def has_submissions(db: Session, team_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(func.count()).select_from(Submission).where(Submission.team_id == team_id)))


def _unique_team_name(db: Session, comp_id: uuid.UUID, base: str) -> str:
    name = base[:60]
    while db.scalar(select(Team.id).where(Team.competition_id == comp_id, func.lower(Team.name) == name.lower())):
        name = f"{base[:54]}-{random_suffix(4)}"
    return name


def _validate_name(db: Session, comp_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> str:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 60:
        raise ValidationFailed(details={"fields": {"name": "Team names are 2–60 characters."}})
    stmt = select(Team.id).where(Team.competition_id == comp_id, func.lower(Team.name) == name.lower())
    if exclude:
        stmt = stmt.where(Team.id != exclude)
    if db.scalar(stmt):
        raise Conflict("That team name is taken in this competition.", code="team_name_taken",
                       details={"fields": {"name": "Name already taken."}})
    return name


def create_solo_team(db: Session, comp: Competition, user: User) -> Team:
    team = Team(competition_id=comp.id, name=_unique_team_name(db, comp.id, user.handle), captain_id=user.id, is_solo=True)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, competition_id=comp.id, user_id=user.id, role="captain"))
    comp.team_count += 1
    return team


def _assert_unlocked(comp: Competition) -> None:
    if state.teams_locked(comp):
        raise Conflict("Team membership is locked for this competition.", code="team_locked")


def _assert_captain(actor: Actor, team: Team) -> None:
    if team.captain_id != actor.id:
        raise Forbidden("Only the team captain can do that.")


def _dissolve_team(db: Session, comp: Competition, team: Team) -> None:
    db.execute(delete(TeamInvitation).where(TeamInvitation.team_id == team.id))
    db.execute(delete(TeamMember).where(TeamMember.team_id == team.id))
    db.delete(team)
    comp.team_count = max(0, comp.team_count - 1)


def _detach_from_current_team(db: Session, comp: Competition, user_id: uuid.UUID) -> None:
    """Allow switching teams only out of an empty-handed solo team."""
    current = team_for_user(db, comp.id, user_id)
    if current is None:
        return
    if not current.is_solo or team_size(db, current.id) > 1:
        raise Conflict("Leave your current team before joining another.", code="already_in_team")
    if has_submissions(db, current.id):
        raise Conflict("You already submitted as an individual, so you cannot merge into a team.", code="has_submissions")
    _dissolve_team(db, comp, current)
    db.flush()


def create_team(actor: Actor, comp: Competition, name: str) -> Team:
    db = actor.db
    part = participant_record(actor, comp)
    if part is None:
        raise Forbidden("Join the competition first.", code="not_participant")
    _assert_unlocked(comp)
    if comp.team_max_size < 2:
        raise Conflict("This competition is individual-only.", code="individual_only")
    name = _validate_name(db, comp.id, name)
    _detach_from_current_team(db, comp, actor.id)  # type: ignore[arg-type]
    team = Team(competition_id=comp.id, name=name, captain_id=actor.id, is_solo=False)
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, competition_id=comp.id, user_id=actor.id, role="captain"))
    part.team_id = team.id
    comp.team_count += 1
    record_audit(db, actor.id, "team.create", target_type="team", target_id=team.id, competition_id=comp.id)
    db.commit()
    return team


def update_team(actor: Actor, comp: Competition, team: Team, name: str | None, workspace_url: str | None) -> Team:
    db = actor.db
    _assert_captain(actor, team)
    if name is not None and name != team.name:
        _assert_unlocked(comp)
        team.name = _validate_name(db, comp.id, name, exclude=team.id)
        team.is_solo = False if team_size(db, team.id) > 1 else team.is_solo
    if workspace_url is not None:
        team.workspace_url = validate_external_url(workspace_url, "workspace_url")
    db.commit()
    return team


def _eligible(actor_db: Session, comp: Competition, user: User) -> str | None:
    if user.status != "active":
        return "This user cannot join teams."
    from app.core.deps import Actor as _Actor

    probe = _Actor(db=actor_db, user=user)
    if competition_roles(probe, comp):
        return "Organizers and judges cannot join teams."
    if comp.visibility == CompetitionVisibility.university:
        from app.modules.competitions.service import _verified_member

        if not _verified_member(probe, comp.host_org_id):
            return "Only verified members of the host organization can participate."
    current = team_for_user(actor_db, comp.id, user.id)
    if current is not None and (not current.is_solo or has_submissions(actor_db, current.id)):
        return "This user is already on a team in this competition."
    return None


def invite(actor: Actor, comp: Competition, team: Team, *, handle: str | None, email: str | None) -> TeamInvitation | None:
    db = actor.db
    _assert_captain(actor, team)
    _assert_unlocked(comp)
    if not handle and not email:
        raise ValidationFailed(details={"fields": {"handle": "Enter a handle or an email address."}})
    pending = db.scalar(select(func.count()).select_from(TeamInvitation).where(
        TeamInvitation.team_id == team.id, TeamInvitation.status == InvitationStatus.pending)) or 0
    if team_size(db, team.id) + pending >= comp.team_max_size:
        raise Conflict(f"Teams can have at most {comp.team_max_size} members (including pending invitations).", code="team_full")
    invitee: User | None = None
    if handle:
        invitee = db.scalar(select(User).where(User.handle == handle.strip().lower().lstrip("@")))
        if invitee is None:
            raise NotFound("No user with that handle.")
    elif email:
        invitee = db.scalar(select(User).where(User.email == email.strip().lower()))
    if invitee is not None:
        if invitee.id == actor.id:
            raise ValidationFailed(details={"fields": {"handle": "You are already on this team."}})
        problem = _eligible(db, comp, invitee)
        if problem:
            if handle:
                raise Conflict(problem, code="invitee_ineligible")
            invitee = None  # email path: respond identically without revealing account state
        elif db.scalar(select(TeamInvitation.id).where(TeamInvitation.team_id == team.id, TeamInvitation.invitee_user_id == invitee.id,
                                                       TeamInvitation.status == InvitationStatus.pending)):
            raise Conflict("That person already has a pending invitation.", code="already_invited")
    token = new_token(24)
    inv = TeamInvitation(team_id=team.id, competition_id=comp.id, inviter_id=actor.id,
                         invitee_user_id=invitee.id if invitee else None,
                         invitee_email=(email.strip().lower() if email else None), token_hash=hash_token(token),
                         expires_at=utcnow() + INVITE_TTL)
    db.add(inv)
    db.flush()
    assert actor.user is not None
    if invitee is not None:
        notify(db, invitee.id, NotificationKind.team_invite, f"{actor.user.display_name} invited you to {team.name}",
               body=comp.title, link=f"/competitions/{comp.slug}/team", dedupe_key=f"team_invite:{inv.id}",
               email_template="team_invite", email_context={"inviter": actor.user.display_name, "team": team.name,
                                                             "competition": comp.title})
    elif email:
        queue_email(db, email.strip().lower(), "team_invite", {
            "name": "there", "inviter": actor.user.display_name, "team": team.name, "competition": comp.title,
            "link": f"{settings.WEB_BASE_URL}/competitions/{comp.slug}/team?invite={token}"},
            dedupe_key=f"team_invite_email:{inv.id}")
    record_audit(db, actor.id, "team.invite", target_type="team", target_id=team.id, competition_id=comp.id)
    db.commit()
    return inv


def claim_email_invitation(actor: Actor, token: str) -> TeamInvitation:
    """Binds an emailed invitation to the signed-in user whose verified email matches."""
    db = actor.db
    inv = db.scalar(select(TeamInvitation).where(TeamInvitation.token_hash == hash_token(token)))
    assert actor.user is not None
    if inv is None or inv.status != InvitationStatus.pending or inv.expires_at < utcnow():
        raise NotFound("This invitation is invalid or has expired.")
    if inv.invitee_user_id not in (None, actor.id) or (inv.invitee_email and inv.invitee_email != actor.user.email):
        raise Forbidden("This invitation was sent to a different email address.")
    inv.invitee_user_id = actor.id
    db.commit()
    return inv


def respond(actor: Actor, invitation_id: uuid.UUID, accept: bool, accept_rules: bool) -> TeamInvitation:
    db = actor.db
    inv = db.scalar(select(TeamInvitation).where(TeamInvitation.id == invitation_id).with_for_update())
    if inv is None or inv.invitee_user_id != actor.id:
        raise NotFound("Invitation not found.")
    if inv.status != InvitationStatus.pending:
        raise Conflict("This invitation is no longer pending.", code="invitation_closed")
    if inv.expires_at < utcnow():
        inv.status = InvitationStatus.expired
        db.commit()
        raise Conflict("This invitation has expired.", code="invitation_expired")
    team = db.get(Team, inv.team_id)
    comp = db.get(Competition, inv.competition_id)
    assert team is not None and comp is not None and actor.user is not None
    if not accept:
        inv.status = InvitationStatus.declined
        inv.responded_at = utcnow()
        notify(db, team.captain_id, NotificationKind.team_update, f"{actor.user.display_name} declined your invitation",
               body=team.name, link=f"/competitions/{comp.slug}/team", dedupe_key=f"invite_declined:{inv.id}")
        db.commit()
        return inv
    _assert_unlocked(comp)
    if team_size(db, team.id) >= comp.team_max_size:
        raise Conflict("This team is already full.", code="team_full")
    problem = _eligible(db, comp, actor.user)
    if problem:
        raise Conflict(problem, code="invitee_ineligible")
    part = participant_record(actor, comp)
    if part is None:
        if not accept_rules:
            raise ValidationFailed("Accept the competition rules to join the team.", code="rules_not_accepted")
        if actor.user.email_verified_at is None:
            raise Forbidden("Please verify your email address first.", code="email_not_verified")
        if not state.registration_open(comp):
            raise Conflict("Registration is closed.", code="registration_closed")
        part = CompetitionParticipant(competition_id=comp.id, user_id=actor.id, accepted_rules_at=utcnow(),
                                      rules_version=comp.config_version)
        db.add(part)
        comp.participant_count += 1
    _detach_from_current_team(db, comp, actor.id)  # type: ignore[arg-type]
    db.add(TeamMember(team_id=team.id, competition_id=comp.id, user_id=actor.id, role="member"))
    team.is_solo = False
    part.team_id = team.id
    inv.status = InvitationStatus.accepted
    inv.responded_at = utcnow()
    # Close other pending invitations for this user in this competition.
    for other in db.scalars(select(TeamInvitation).where(TeamInvitation.competition_id == comp.id,
                                                         TeamInvitation.invitee_user_id == actor.id,
                                                         TeamInvitation.status == InvitationStatus.pending,
                                                         TeamInvitation.id != inv.id)):
        other.status = InvitationStatus.revoked
    record_audit(db, actor.id, "team.join", target_type="team", target_id=team.id, competition_id=comp.id)
    notify(db, team.captain_id, NotificationKind.team_update, f"{actor.user.display_name} joined {team.name}",
           link=f"/competitions/{comp.slug}/team", dedupe_key=f"invite_accepted:{inv.id}")
    db.commit()
    return inv


def revoke_invitation(actor: Actor, team: Team, invitation_id: uuid.UUID) -> None:
    db = actor.db
    _assert_captain(actor, team)
    inv = db.get(TeamInvitation, invitation_id)
    if inv is None or inv.team_id != team.id or inv.status != InvitationStatus.pending:
        raise NotFound()
    inv.status = InvitationStatus.revoked
    db.commit()


def transfer_captain(actor: Actor, comp: Competition, team: Team, user_id: uuid.UUID) -> Team:
    db = actor.db
    _assert_captain(actor, team)
    target = db.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id))
    if target is None:
        raise ValidationFailed(details={"fields": {"user_id": "That user is not on this team."}})
    current = db.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == actor.id))
    assert current is not None
    current.role, target.role = "member", "captain"
    team.captain_id = user_id
    record_audit(db, actor.id, "team.captain_transfer", target_type="team", target_id=team.id, competition_id=comp.id,
                 meta={"from": str(actor.id), "to": str(user_id)})
    notify(db, user_id, NotificationKind.team_update, f"You are now captain of {team.name}", link=f"/competitions/{comp.slug}/team",
           dedupe_key=f"captain:{team.id}:{utcnow():%Y%m%d%H%M%S}")
    db.commit()
    return team


def _remove_member(db: Session, comp: Competition, team: Team, user_id: uuid.UUID) -> None:
    db.execute(delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id))
    part = db.scalar(select(CompetitionParticipant).where(CompetitionParticipant.competition_id == comp.id,
                                                          CompetitionParticipant.user_id == user_id))
    remaining = team_size(db, team.id)
    if remaining == 0 and not has_submissions(db, team.id):
        _dissolve_team(db, comp, team)
    db.flush()
    if part is not None:
        part.team_id = None
        user = db.get(User, user_id)
        if comp.team_min_size == 1 and user is not None and not state.teams_locked(comp):
            part.team_id = create_solo_team(db, comp, user).id


def leave_team_internal(actor: Actor, comp: Competition, team: Team) -> None:
    db = actor.db
    size = team_size(db, team.id)
    if team.captain_id == actor.id and size > 1:
        raise Conflict("Transfer captaincy before leaving the team.", code="captain_must_transfer")
    if size == 1 and has_submissions(db, team.id) and not team.is_solo:
        raise Conflict("You are the last member of a team with submissions; the team must remain for the record.",
                       code="has_submissions")
    db.execute(delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == actor.id))
    if team_size(db, team.id) == 0 and not has_submissions(db, team.id):
        _dissolve_team(db, comp, team)


def leave(actor: Actor, comp: Competition, team: Team) -> None:
    db = actor.db
    _assert_unlocked(comp)
    size = team_size(db, team.id)
    if team.captain_id == actor.id and size > 1:
        raise Conflict("Transfer captaincy before leaving the team.", code="captain_must_transfer")
    if size == 1 and has_submissions(db, team.id):
        raise Conflict("You are the only member of a team with submissions, so you cannot leave it.", code="has_submissions")
    captain = team.captain_id
    _remove_member(db, comp, team, actor.id)  # type: ignore[arg-type]
    record_audit(db, actor.id, "team.leave", target_type="team", target_id=team.id, competition_id=comp.id)
    if captain != actor.id:
        assert actor.user is not None
        notify(db, captain, NotificationKind.team_update, f"{actor.user.display_name} left {team.name}",
               link=f"/competitions/{comp.slug}/team", dedupe_key=f"left:{team.id}:{actor.id}:{utcnow():%Y%m%d%H%M%S}")
    db.commit()


def remove_member(actor: Actor, comp: Competition, team: Team, user_id: uuid.UUID, reason: str | None) -> None:
    db = actor.db
    _assert_captain(actor, team)
    _assert_unlocked(comp)
    if user_id == actor.id:
        raise ValidationFailed("Use 'leave team' to remove yourself.", code="use_leave")
    if not db.scalar(select(TeamMember.id).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)):
        raise NotFound("That user is not on this team.")
    _remove_member(db, comp, team, user_id)
    record_audit(db, actor.id, "team.member_remove", target_type="team", target_id=team.id, competition_id=comp.id,
                 reason=reason, meta={"user_id": str(user_id)})
    notify(db, user_id, NotificationKind.team_update, f"You were removed from {team.name}", link=f"/competitions/{comp.slug}",
           dedupe_key=f"removed:{team.id}:{user_id}:{utcnow():%Y%m%d%H%M%S}")
    db.commit()


def delete_team(actor: Actor, comp: Competition, team: Team) -> None:
    db = actor.db
    _assert_captain(actor, team)
    _assert_unlocked(comp)
    if has_submissions(db, team.id):
        raise Conflict("Teams with submissions cannot be deleted.", code="has_submissions")
    members = member_ids(db, team.id)
    _dissolve_team(db, comp, team)
    db.flush()
    for uid in members:
        part = db.scalar(select(CompetitionParticipant).where(CompetitionParticipant.competition_id == comp.id,
                                                              CompetitionParticipant.user_id == uid))
        if part is None:
            continue
        part.team_id = None
        user = db.get(User, uid)
        if comp.team_min_size == 1 and user is not None:
            part.team_id = create_solo_team(db, comp, user).id
    record_audit(db, actor.id, "team.delete", target_type="team", target_id=team.id, competition_id=comp.id)
    db.commit()
