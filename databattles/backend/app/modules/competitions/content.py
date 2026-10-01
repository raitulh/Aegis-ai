"""Organizer-facing competition content: staff, sponsors, announcements, schedule, awards, evaluation assets."""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import BinaryIO

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import render_markdown
from app.core.permissions import assert_manage_competition, can_manage_competition, participant_record
from app.core.schemas import org_mini, user_mini
from app.core.time import utcnow
from app.core.validators import extension, safe_filename, validate_external_url
from app.evaluation.core import EvaluationError, inspect_ground_truth
from app.evaluation.registry import normalize_evaluation_config
from app.models.competition import (
    Announcement,
    AnnouncementRead,
    AwardCategory,
    Competition,
    CompetitionConfigVersion,
    CompetitionParticipant,
    CompetitionSponsor,
    CompetitionStaff,
    ScheduleItem,
    Team,
)
from app.models.enums import AnnouncementStatus, NotificationKind, OrgType, StaffRole
from app.models.org import Organization
from app.models.submission import EvaluationAsset, Submission
from app.models.user import User
from app.modules.competitions.schemas import (
    AnnouncementIn,
    AnnouncementOut,
    AwardCategoryIn,
    CompetitionManage,
    ConfigVersionOut,
    EvaluationAssetOut,
    ParticipantOut,
    ScheduleItemIn,
    SponsorIn,
    SponsorOut,
    StaffIn,
    StaffOut,
)
from app.modules.competitions.service import publish_checks, snapshot_config
from app.modules.competitions.state import effective_status
from app.modules.notifications.service import notify
from app.storage import get_storage
from app.storage.base import PRIVATE_PREFIX, new_key

# ----------------------------------------------------------------------------- manage view


def manage_view(actor: Actor, comp: Competition) -> CompetitionManage:
    db = actor.db
    assert_manage_competition(actor, comp)
    asset = db.scalar(select(EvaluationAsset).where(EvaluationAsset.competition_id == comp.id, EvaluationAsset.is_active.is_(True)))
    staff = db.execute(select(CompetitionStaff, User).join(User, User.id == CompetitionStaff.user_id)
                       .where(CompetitionStaff.competition_id == comp.id).order_by(CompetitionStaff.role, User.handle)).all()
    sponsors = db.execute(select(CompetitionSponsor, Organization).join(Organization, Organization.id == CompetitionSponsor.org_id)
                          .where(CompetitionSponsor.competition_id == comp.id).order_by(CompetitionSponsor.display_order)).all()
    versions = db.execute(select(CompetitionConfigVersion, User).outerjoin(User, User.id == CompetitionConfigVersion.changed_by)
                          .where(CompetitionConfigVersion.competition_id == comp.id)
                          .order_by(CompetitionConfigVersion.version.desc(), CompetitionConfigVersion.created_at.desc()).limit(50)).all()
    raw = {k: getattr(comp, k) for k in (
        "summary", "description_md", "rules_md", "evaluation_md", "prize_md", "prize_summary", "has_prize", "faq",
        "scoring_notes_md", "event_type", "task_type", "difficulty", "tags", "host_org_id", "parent_id", "visibility",
        "starts_at", "ends_at", "registration_opens_at", "registration_closes_at", "team_lock_at", "timezone",
        "team_min_size", "team_max_size", "daily_submission_limit", "total_submission_limit", "max_submission_mb",
        "final_selection_limit", "scoring_mode", "leaderboard_visibility", "format", "venue_name", "venue_address",
        "venue_notes", "starter_assets", "organizer_contact_email", "dataset_version_id", "cover_style")}
    return CompetitionManage(
        id=comp.id, slug=comp.slug, title=comp.title, lifecycle=comp.lifecycle, status=effective_status(comp),
        organizer_notes=comp.organizer_notes, invite_code=comp.invite_code, evaluation=comp.evaluation or {},
        evaluation_locked_at=comp.evaluation_locked_at,
        evaluation_asset=EvaluationAssetOut(id=asset.id, sha256_prefix=asset.sha256[:12], row_count=asset.row_count,
                                            public_count=asset.public_count, private_count=asset.private_count,
                                            created_at=asset.created_at) if asset else None,
        certificate_rules=comp.certificate_rules or {}, show_university_on_leaderboard=comp.show_university_on_leaderboard,
        staff=[StaffOut(user=user_mini(u), role=s.role) for s, u in staff],  # type: ignore[arg-type]
        sponsors=[SponsorOut(org=org_mini(o), tier=s.tier, blurb=s.blurb) for s, o in sponsors],  # type: ignore[arg-type]
        publish_checks=publish_checks(db, comp),
        config_versions=[ConfigVersionOut(version=v.version, changed_fields=list(v.changed_fields or []), reason=v.reason,
                                          changed_by=user_mini(u), created_at=v.created_at) for v, u in versions],
        raw=raw,
    )


# ----------------------------------------------------------------------------- staff & sponsors


def add_staff(actor: Actor, comp: Competition, data: StaffIn) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    user = db.scalar(select(User).where(User.handle == data.handle.lower()))
    if user is None:
        raise NotFound("No user with that handle.")
    if db.scalar(select(CompetitionParticipant.id).where(CompetitionParticipant.competition_id == comp.id,
                                                         CompetitionParticipant.user_id == user.id)):
        raise Conflict("Participants cannot also be staff of the same competition.", code="conflict_of_interest")
    exists_ = db.scalar(select(CompetitionStaff.id).where(CompetitionStaff.competition_id == comp.id,
                                                          CompetitionStaff.user_id == user.id, CompetitionStaff.role == data.role))
    if exists_:
        return
    db.add(CompetitionStaff(competition_id=comp.id, user_id=user.id, role=data.role, added_by=actor.id))
    record_audit(db, actor.id, "competition.staff_add", target_type="user", target_id=user.id, competition_id=comp.id,
                 meta={"role": data.role})
    if data.role == StaffRole.judge:
        notify(db, user.id, NotificationKind.judging, f"You were invited to judge {comp.title}",
               link="/judge", dedupe_key=f"judge_invite:{comp.id}")
    db.commit()


def remove_staff(actor: Actor, comp: Competition, user_id: uuid.UUID, role: str) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    row = db.scalar(select(CompetitionStaff).where(CompetitionStaff.competition_id == comp.id, CompetitionStaff.user_id == user_id,
                                                   CompetitionStaff.role == role))
    if row is None:
        raise NotFound()
    if role == StaffRole.organizer:
        organizers = db.scalar(select(func.count()).select_from(CompetitionStaff).where(
            CompetitionStaff.competition_id == comp.id, CompetitionStaff.role == StaffRole.organizer)) or 0
        if organizers <= 1:
            raise Conflict("A competition needs at least one organizer. Transfer ownership first.", code="last_organizer")
    db.delete(row)
    record_audit(db, actor.id, "competition.staff_remove", target_type="user", target_id=user_id, competition_id=comp.id,
                 meta={"role": role})
    db.commit()


def add_sponsor(actor: Actor, comp: Competition, data: SponsorIn) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    org = db.scalar(select(Organization).where(Organization.slug == data.org_slug))
    if org is None or org.type not in (OrgType.sponsor, OrgType.company, OrgType.community, OrgType.university):
        raise NotFound("Organization not found.")
    existing = db.scalar(select(CompetitionSponsor).where(CompetitionSponsor.competition_id == comp.id,
                                                          CompetitionSponsor.org_id == org.id))
    if existing:
        existing.tier, existing.blurb = data.tier, data.blurb
    else:
        order = db.scalar(select(func.count()).select_from(CompetitionSponsor).where(CompetitionSponsor.competition_id == comp.id)) or 0
        db.add(CompetitionSponsor(competition_id=comp.id, org_id=org.id, tier=data.tier, blurb=data.blurb, display_order=order))
    record_audit(db, actor.id, "competition.sponsor_set", target_type="organization", target_id=org.id,
                 competition_id=comp.id, meta={"tier": data.tier})
    db.commit()


def remove_sponsor(actor: Actor, comp: Competition, org_id: uuid.UUID) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    row = db.scalar(select(CompetitionSponsor).where(CompetitionSponsor.competition_id == comp.id, CompetitionSponsor.org_id == org_id))
    if row is None:
        raise NotFound()
    db.delete(row)
    record_audit(db, actor.id, "competition.sponsor_remove", target_type="organization", target_id=org_id, competition_id=comp.id)
    db.commit()


# ----------------------------------------------------------------------------- announcements


def _participant_ids(db: Session, comp_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(CompetitionParticipant.user_id).where(CompetitionParticipant.competition_id == comp_id,
                                                                        CompetitionParticipant.status == "active")))


def list_announcements(actor: Actor, comp: Competition) -> list[AnnouncementOut]:
    db = actor.db
    manager = can_manage_competition(actor, comp)
    stmt = select(Announcement).where(Announcement.competition_id == comp.id)
    if not manager:
        stmt = stmt.where(Announcement.status == AnnouncementStatus.published)
    rows = db.scalars(stmt.order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).limit(100)).all()
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r.author_id for r in rows if r.author_id])))}
    orgs = {o.id: o for o in db.scalars(select(Organization).where(Organization.id.in_([r.sponsor_org_id for r in rows if r.sponsor_org_id])))}
    read: set[uuid.UUID] = set()
    if actor.is_authenticated and rows:
        read = set(db.scalars(select(AnnouncementRead.announcement_id).where(
            AnnouncementRead.user_id == actor.id, AnnouncementRead.announcement_id.in_([r.id for r in rows]))))
    return [AnnouncementOut(id=r.id, title=r.title, body_html=r.body_html, pinned=r.pinned, status=r.status,
                            author=user_mini(authors.get(r.author_id)), sponsor=org_mini(orgs.get(r.sponsor_org_id)),
                            created_at=r.created_at, is_read=r.id in read) for r in rows]


def create_announcement(actor: Actor, comp: Competition, data: AnnouncementIn) -> Announcement:
    db = actor.db
    sponsor_org: Organization | None = None
    if data.as_sponsor_org:
        sponsor_org = db.scalar(select(Organization).where(Organization.slug == data.as_sponsor_org))
        linked = sponsor_org and db.scalar(select(CompetitionSponsor.id).where(
            CompetitionSponsor.competition_id == comp.id, CompetitionSponsor.org_id == sponsor_org.id))
        if not linked or not actor.is_org_manager(sponsor_org.id):  # type: ignore[union-attr]
            raise Forbidden("Only managers of a sponsor attached to this competition can post as the sponsor.")
        status = AnnouncementStatus.pending_review  # editorial control stays with organizers/moderators
    else:
        assert_manage_competition(actor, comp)
        status = AnnouncementStatus.published
    ann = Announcement(competition_id=comp.id, author_id=actor.id, sponsor_org_id=sponsor_org.id if sponsor_org else None,
                       title=data.title, body_md=data.body_md, body_html=render_markdown(data.body_md),
                       pinned=data.pinned and status == AnnouncementStatus.published, status=status)
    db.add(ann)
    db.flush()
    record_audit(db, actor.id, "competition.announcement_create", target_type="announcement", target_id=ann.id,
                 competition_id=comp.id, meta={"status": status})
    if status == AnnouncementStatus.published and data.notify:
        _notify_announcement(db, comp, ann)
    db.commit()
    return ann


def _notify_announcement(db: Session, comp: Competition, ann: Announcement) -> None:
    for uid in _participant_ids(db, comp.id):
        notify(db, uid, NotificationKind.announcement, f"{comp.title}: {ann.title}", link=f"/competitions/{comp.slug}#announcements",
               dedupe_key=f"announcement:{ann.id}", group_key=f"announcements:{comp.id}", email_template="announcement",
               email_context={"competition": comp.title, "title": ann.title})


def review_announcement(actor: Actor, comp: Competition, ann_id: uuid.UUID, approve: bool) -> None:
    db = actor.db
    if not (can_manage_competition(actor, comp) or actor.is_moderator):
        raise Forbidden()
    ann = db.get(Announcement, ann_id)
    if ann is None or ann.competition_id != comp.id:
        raise NotFound()
    ann.status = AnnouncementStatus.published if approve else AnnouncementStatus.rejected
    record_audit(db, actor.id, "competition.announcement_review", target_type="announcement", target_id=ann.id,
                 competition_id=comp.id, meta={"approved": approve})
    if approve:
        _notify_announcement(db, comp, ann)
    db.commit()


def update_announcement(actor: Actor, comp: Competition, ann_id: uuid.UUID, pinned: bool | None, delete: bool) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    ann = db.get(Announcement, ann_id)
    if ann is None or ann.competition_id != comp.id:
        raise NotFound()
    if delete:
        db.delete(ann)
        record_audit(db, actor.id, "competition.announcement_delete", target_type="announcement", target_id=ann_id,
                     competition_id=comp.id)
    elif pinned is not None:
        ann.pinned = pinned
    db.commit()


def mark_announcements_read(actor: Actor, comp: Competition) -> None:
    db = actor.db
    ids = db.scalars(select(Announcement.id).where(Announcement.competition_id == comp.id,
                                                   Announcement.status == AnnouncementStatus.published)).all()
    from sqlalchemy.dialects.postgresql import insert

    for aid in ids:
        db.execute(insert(AnnouncementRead).values(announcement_id=aid, user_id=actor.id).on_conflict_do_nothing())
    db.commit()


# ----------------------------------------------------------------------------- schedule & awards


def add_schedule_item(actor: Actor, comp: Competition, data: ScheduleItemIn) -> ScheduleItem:
    assert_manage_competition(actor, comp)
    if data.ends_at and data.ends_at < data.starts_at:
        raise ValidationFailed(details={"fields": {"ends_at": "End must be after start."}})
    item = ScheduleItem(competition_id=comp.id, title=data.title, kind=data.kind, starts_at=data.starts_at, ends_at=data.ends_at,
                        location=data.location, url=validate_external_url(data.url), description=data.description)
    actor.db.add(item)
    actor.db.commit()
    return item


def delete_schedule_item(actor: Actor, comp: Competition, item_id: uuid.UUID) -> None:
    assert_manage_competition(actor, comp)
    item = actor.db.get(ScheduleItem, item_id)
    if item is None or item.competition_id != comp.id:
        raise NotFound()
    actor.db.delete(item)
    actor.db.commit()


def add_award_category(actor: Actor, comp: Competition, data: AwardCategoryIn) -> AwardCategory:
    assert_manage_competition(actor, comp)
    award = AwardCategory(competition_id=comp.id, name=data.name, description=data.description, position=data.position)
    actor.db.add(award)
    record_audit(actor.db, actor.id, "competition.award_create", target_type="award", competition_id=comp.id,
                 meta={"name": data.name})
    actor.db.commit()
    return award


def set_award_winner(actor: Actor, comp: Competition, award_id: uuid.UUID, team_id: uuid.UUID | None) -> AwardCategory:
    db = actor.db
    assert_manage_competition(actor, comp)
    award = db.get(AwardCategory, award_id)
    if award is None or award.competition_id != comp.id:
        raise NotFound()
    if team_id is not None:
        team = db.get(Team, team_id)
        if team is None or team.competition_id != comp.id:
            raise ValidationFailed(details={"fields": {"team_id": "Team is not part of this competition."}})
    award.winner_team_id = team_id
    award.awarded_at = utcnow() if team_id else None
    record_audit(db, actor.id, "competition.award_winner", target_type="award", target_id=award.id, competition_id=comp.id,
                 meta={"team_id": str(team_id) if team_id else None})
    db.commit()
    return award


def delete_award_category(actor: Actor, comp: Competition, award_id: uuid.UUID) -> None:
    assert_manage_competition(actor, comp)
    award = actor.db.get(AwardCategory, award_id)
    if award is None or award.competition_id != comp.id:
        raise NotFound()
    actor.db.delete(award)
    actor.db.commit()


# ----------------------------------------------------------------------------- hidden evaluation assets


def upload_ground_truth(actor: Actor, comp: Competition, filename: str, stream: BinaryIO) -> EvaluationAsset:
    """Store the hidden solution file under the private prefix after validating it against the metric config."""
    db = actor.db
    assert_manage_competition(actor, comp)
    if comp.evaluation_locked_at is not None and not actor.is_admin:
        raise Conflict("Ground truth is locked because submissions have been scored.", code="scoring_locked")
    name = safe_filename(filename)
    if extension(name) != ".csv":
        raise ValidationFailed("The solution file must be a CSV.", code="invalid_file_type")
    if comp.scoring_mode != "automatic":
        raise Conflict("Ground truth is only used by automatically scored competitions.", code="not_automatic")
    config = normalize_evaluation_config(comp.evaluation or {})
    storage = get_storage()
    key = new_key(f"{PRIVATE_PREFIX}/ground-truth", ".csv")
    stored = storage.save_stream(key, stream, max_bytes=settings.MAX_DATASET_FILE_MB * 1024 * 1024, content_type="text/csv")
    local = storage.local_path(key)
    tmp_path: str | None = None
    try:
        if local is None:
            fd, tmp_path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "wb") as out, storage.open(key) as src:
                out.write(src.read())
            local = tmp_path
        try:
            info = inspect_ground_truth(local, config)
        except (EvaluationError, ValueError) as exc:
            storage.delete(key)
            message = exc.message if isinstance(exc, EvaluationError) else "Target values are not valid for this metric."
            raise ValidationFailed(message, code="ground_truth_invalid") from None
    finally:
        if tmp_path:
            os.unlink(tmp_path)
    db.execute(update(EvaluationAsset).where(EvaluationAsset.competition_id == comp.id).values(is_active=False))
    asset = EvaluationAsset(competition_id=comp.id, storage_key=key, sha256=stored.sha256, row_count=info["row_count"],
                            public_count=info["public_count"], private_count=info["private_count"], uploaded_by=actor.id)
    db.add(asset)
    record_audit(db, actor.id, "competition.ground_truth_upload", target_type="competition", target_id=comp.id,
                 competition_id=comp.id, meta={"sha256": stored.sha256[:16], "rows": info["row_count"]})
    if comp.lifecycle != "draft":
        comp.config_version += 1
        db.add(CompetitionConfigVersion(competition_id=comp.id, version=comp.config_version, snapshot=snapshot_config(comp),
                                        changed_fields=["ground_truth"], changed_by=actor.id, reason="Ground truth updated"))
    db.commit()
    return asset


# ----------------------------------------------------------------------------- participants


def list_participants(actor: Actor, comp: Competition, limit: int, offset: int) -> tuple[list[ParticipantOut], int]:
    db = actor.db
    assert_manage_competition(actor, comp)
    total = db.scalar(select(func.count()).select_from(CompetitionParticipant).where(CompetitionParticipant.competition_id == comp.id)) or 0
    sub_counts = (select(Submission.user_id, func.count().label("n")).where(Submission.competition_id == comp.id)
                  .group_by(Submission.user_id).subquery())
    rows = db.execute(
        select(CompetitionParticipant, User, Team, sub_counts.c.n)
        .join(User, User.id == CompetitionParticipant.user_id)
        .outerjoin(Team, Team.id == CompetitionParticipant.team_id)
        .outerjoin(sub_counts, sub_counts.c.user_id == CompetitionParticipant.user_id)
        .where(CompetitionParticipant.competition_id == comp.id)
        .order_by(CompetitionParticipant.joined_at.desc()).limit(limit).offset(offset)
    ).all()
    return [ParticipantOut(user=user_mini(u), team_id=t.id if t else None, team_name=t.name if t else None,  # type: ignore[arg-type]
                           joined_at=p.joined_at, status=p.status, submission_count=n or 0) for p, u, t, n in rows], total


def is_participant(actor: Actor, comp: Competition) -> bool:
    return participant_record(actor, comp) is not None
