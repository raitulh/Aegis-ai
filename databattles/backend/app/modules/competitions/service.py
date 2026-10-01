"""Competition lifecycle: discovery, detail, drafting, publication, participation."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import render_markdown
from app.core.pagination import PageParams
from app.core.permissions import (
    assert_manage_competition,
    assert_view_competition,
    can_manage_competition,
    competition_roles,
    is_indexable_competition,
    listable_competitions_clause,
    participant_record,
)
from app.core.schemas import org_mini
from app.core.security import constant_time_eq
from app.core.slugs import unique_slug
from app.core.time import is_valid_timezone, utcnow
from app.core.validators import clean_tags, validate_external_url
from app.evaluation.metrics import METRICS
from app.evaluation.registry import EVALUATORS, normalize_evaluation_config
from app.models.competition import (
    DEFAULT_CERTIFICATE_RULES,
    Announcement,
    AwardCategory,
    Competition,
    CompetitionConfigVersion,
    CompetitionParticipant,
    CompetitionSponsor,
    CompetitionStaff,
    ScheduleItem,
    Team,
    TeamInvitation,
    TeamMember,
)
from app.models.dataset import Dataset, DatasetVersion
from app.models.enums import (
    AnnouncementStatus,
    CompetitionVisibility,
    Difficulty,
    EventFormat,
    EventType,
    LeaderboardVisibility,
    Lifecycle,
    MembershipStatus,
    ScoringMode,
    StaffRole,
    SubmissionStatus,
    TaskType,
    VersionStatus,
)
from app.models.judging import Rubric
from app.models.org import Organization, OrgMembership
from app.models.submission import EvaluationAsset, Submission
from app.models.user import RecentView, User
from app.modules.competitions import state
from app.modules.competitions.schemas import (
    AwardCategoryOut,
    CompetitionCard,
    CompetitionDetail,
    CompetitionWrite,
    DatasetRef,
    EvaluationSummary,
    FaqItem,
    PublishCheck,
    ScheduleItemOut,
    SponsorOut,
    StarterAsset,
    ViewerContext,
    ViewerTeam,
)
from app.modules.search.indexer import index_competition

MARKDOWN_FIELDS = {"description_md": "description_html", "rules_md": "rules_html", "evaluation_md": "evaluation_html",
                   "prize_md": "prize_html"}
# After finalization only presentation fields may change.
EDITABLE_AFTER_FINALIZATION = {"description_md", "summary", "faq", "prize_md", "scoring_notes_md", "cover_style",
                               "organizer_notes", "venue_notes", "tags", "change_reason"}
EVALUATION_FIELDS = {"evaluation", "scoring_mode"}
_ENUM_FIELDS: dict[str, Any] = {
    "event_type": EventType, "task_type": TaskType, "difficulty": Difficulty, "visibility": CompetitionVisibility,
    "scoring_mode": ScoringMode, "leaderboard_visibility": LeaderboardVisibility, "format": EventFormat,
}
# Fields recorded in configuration history after publication.
VERSIONED_FIELDS = {
    "title", "rules_md", "evaluation_md", "starts_at", "ends_at", "registration_opens_at", "registration_closes_at",
    "team_lock_at", "team_min_size", "team_max_size", "daily_submission_limit", "total_submission_limit",
    "max_submission_mb", "final_selection_limit", "scoring_mode", "evaluation", "leaderboard_visibility", "visibility",
    "starter_assets", "dataset_version_id", "certificate_rules",
}


def get_by_slug(db: Session, slug: str) -> Competition | None:
    return db.scalar(select(Competition).where(Competition.slug == slug))


def load_visible(actor: Actor, slug: str) -> Competition:
    return assert_view_competition(actor, get_by_slug(actor.db, slug))


def load_managed(actor: Actor, slug: str) -> Competition:
    comp = load_visible(actor, slug)
    assert_manage_competition(actor, comp)
    return comp


# ----------------------------------------------------------------------------- serialization


def build_cards(db: Session, comps: list[Competition]) -> list[CompetitionCard]:
    if not comps:
        return []
    org_ids = {c.host_org_id for c in comps if c.host_org_id}
    sponsor_rows = db.execute(
        select(CompetitionSponsor, Organization).join(Organization, Organization.id == CompetitionSponsor.org_id)
        .where(CompetitionSponsor.competition_id.in_([c.id for c in comps]))
        .order_by(CompetitionSponsor.display_order)
    ).all()
    orgs = {o.id: o for o in db.scalars(select(Organization).where(Organization.id.in_(org_ids)))} if org_ids else {}
    sponsors: dict[uuid.UUID, list[SponsorOut]] = {}
    for sp, org in sponsor_rows:
        sponsors.setdefault(sp.competition_id, []).append(SponsorOut(org=org_mini(org), tier=sp.tier, blurb=sp.blurb))  # type: ignore[arg-type]
    now = utcnow()
    return [
        CompetitionCard(
            id=c.id, slug=c.slug, title=c.title, summary=c.summary, status=state.effective_status(c, now),
            lifecycle=c.lifecycle, visibility=c.visibility, event_type=c.event_type, task_type=c.task_type,
            difficulty=c.difficulty, scoring_mode=c.scoring_mode, tags=list(c.tags or []),
            host=org_mini(orgs.get(c.host_org_id)) if c.host_org_id else None, sponsors=sponsors.get(c.id, []),
            starts_at=c.starts_at, ends_at=c.ends_at, registration_closes_at=c.registration_closes_at,
            participant_count=c.participant_count, team_count=c.team_count, has_prize=c.has_prize,
            prize_summary=c.prize_summary, team_min_size=c.team_min_size, team_max_size=c.team_max_size,
            metric=(c.evaluation or {}).get("metric") if c.scoring_mode == ScoringMode.automatic else None,
            format=c.format, cover_style=c.cover_style, registration_open=state.registration_open(c, now),
            is_demo=c.is_demo,
        )
        for c in comps
    ]


def evaluation_summary(comp: Competition) -> EvaluationSummary | None:
    if comp.scoring_mode != ScoringMode.automatic:
        return None
    cfg = comp.evaluation or {}
    metric = METRICS.get(cfg.get("metric", "accuracy"))
    spec = EVALUATORS.get(cfg.get("evaluator", "csv_prediction"))
    if metric is None or spec is None:
        return None
    return EvaluationSummary(
        evaluator=spec.key, metric=metric.key, metric_label=metric.label, direction=metric.direction,
        secondary_metrics=list(cfg.get("secondary_metrics", [])), id_column=cfg.get("id_column", "id"),
        target_column=cfg.get("target_column", "target"), strict_schema=bool(cfg.get("strict_schema", True)),
        submission_format=spec.submission_format, evaluator_version=spec.version,
    )


def dataset_ref(db: Session, comp: Competition) -> DatasetRef | None:
    if not comp.dataset_version_id:
        return None
    row = db.execute(select(DatasetVersion, Dataset).join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                     .where(DatasetVersion.id == comp.dataset_version_id)).first()
    if not row:
        return None
    v, d = row
    return DatasetRef(dataset_id=d.id, slug=d.slug, title=d.title, version=v.version, version_id=v.id, license=d.license,
                      file_count=v.file_count, total_bytes=v.total_bytes)


def viewer_context(actor: Actor, comp: Competition) -> ViewerContext:
    from app.modules.submissions.service import submissions_today
    from app.modules.teams.service import team_for_user

    roles = sorted(competition_roles(actor, comp))
    part = participant_record(actor, comp)
    team = team_for_user(actor.db, comp.id, actor.id) if actor.is_authenticated else None
    vteam = None
    if team is not None:
        count = actor.db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)) or 0
        vteam = ViewerTeam(id=team.id, name=team.name, is_solo=team.is_solo, is_captain=team.captain_id == actor.id,
                           member_count=count)
    join_blockers = join_blockers_for(actor, comp) if part is None else []
    submit_blockers: list[str] = []
    today = 0
    remaining: int | None = None
    if part is not None:
        submit_blockers = submission_blockers(actor, comp, team)
        if team is not None:
            today = submissions_today(actor.db, team.id)
            remaining = max(0, comp.daily_submission_limit - today)
    pending = 0
    if actor.is_authenticated:
        pending = actor.db.scalar(select(func.count()).select_from(TeamInvitation).where(
            TeamInvitation.competition_id == comp.id, TeamInvitation.invitee_user_id == actor.id,
            TeamInvitation.status == "pending")) or 0
    return ViewerContext(
        is_authenticated=actor.is_authenticated, is_participant=part is not None, roles=roles, team=vteam,
        accepted_rules_at=part.accepted_rules_at if part else None,
        can_join=part is None and not join_blockers, join_blockers=join_blockers,
        needs_invite_code=comp.visibility in (CompetitionVisibility.invite_only, CompetitionVisibility.private),
        can_submit=part is not None and not submit_blockers, submit_blockers=submit_blockers,
        submissions_today=today, submissions_remaining_today=remaining, pending_invitations=pending,
    )


def build_detail(actor: Actor, comp: Competition) -> CompetitionDetail:
    db = actor.db
    card = build_cards(db, [comp])[0]
    schedule = db.scalars(select(ScheduleItem).where(ScheduleItem.competition_id == comp.id).order_by(ScheduleItem.starts_at)).all()
    awards = db.scalars(select(AwardCategory).where(AwardCategory.competition_id == comp.id).order_by(AwardCategory.position)).all()
    team_names = {t.id: t.name for t in db.scalars(select(Team).where(Team.id.in_([a.winner_team_id for a in awards if a.winner_team_id])))}
    parent = db.get(Competition, comp.parent_id) if comp.parent_id else None
    ann_count = db.scalar(select(func.count()).select_from(Announcement).where(
        Announcement.competition_id == comp.id, Announcement.status == AnnouncementStatus.published)) or 0
    return CompetitionDetail(
        **card.model_dump(),
        description_html=comp.description_html, rules_html=comp.rules_html, evaluation_html=comp.evaluation_html,
        prize_html=comp.prize_html, scoring_notes_md=comp.scoring_notes_md,
        faq=[FaqItem(**f) for f in comp.faq or []], registration_opens_at=comp.registration_opens_at,
        team_lock_at=comp.team_lock_at, timezone=comp.timezone, daily_submission_limit=comp.daily_submission_limit,
        total_submission_limit=comp.total_submission_limit, max_submission_mb=comp.max_submission_mb,
        final_selection_limit=comp.final_selection_limit, leaderboard_visibility=comp.leaderboard_visibility,
        venue_name=comp.venue_name, venue_address=comp.venue_address, venue_notes=comp.venue_notes,
        starter_assets=[StarterAsset(**a) for a in comp.starter_assets or []], starter_asset_version=comp.starter_asset_version,
        organizer_contact_email=comp.organizer_contact_email, evaluation=evaluation_summary(comp),
        dataset=dataset_ref(db, comp),
        schedule=[ScheduleItemOut.model_validate(s) for s in schedule],
        awards=[AwardCategoryOut(id=a.id, name=a.name, description=a.description, position=a.position,
                                 winner_team_id=a.winner_team_id, winner_team_name=team_names.get(a.winner_team_id),
                                 awarded_at=a.awarded_at) for a in awards],
        parent=build_cards(db, [parent])[0] if parent and can_view_parent(actor, parent) else None,
        config_version=comp.config_version, published_at=comp.published_at, finalized_at=comp.finalized_at,
        frozen=comp.frozen, frozen_reason=comp.frozen_reason if comp.frozen else None,
        indexable=is_indexable_competition(comp), announcement_count=ann_count, viewer=viewer_context(actor, comp),
    )


def can_view_parent(actor: Actor, parent: Competition) -> bool:
    from app.core.permissions import can_view_competition

    return can_view_competition(actor, parent)


# ----------------------------------------------------------------------------- discovery


def list_competitions(actor: Actor, params: PageParams, *, q: str | None = None, status: str | None = None,
                      task_type: str | None = None, event_type: str | None = None, difficulty: str | None = None,
                      org: str | None = None, prize: str | None = None, participation: str | None = None,
                      tag: str | None = None, sort: str = "relevance") -> tuple[list[CompetitionCard], int]:
    db = actor.db
    now = utcnow()
    stmt = select(Competition).where(listable_competitions_clause(actor))
    published = Competition.lifecycle == Lifecycle.published
    if status == "upcoming":
        stmt = stmt.where(published, Competition.starts_at > now)
    elif status == "active":
        stmt = stmt.where(published, or_(Competition.starts_at.is_(None), Competition.starts_at <= now),
                          or_(Competition.ends_at.is_(None), Competition.ends_at > now))
    elif status == "registration_closed":
        stmt = stmt.where(published, Competition.registration_closes_at <= now,
                          or_(Competition.ends_at.is_(None), Competition.ends_at > now))
    elif status == "completed":
        stmt = stmt.where(or_(Competition.lifecycle == Lifecycle.finalized, and_(published, Competition.ends_at <= now)))
    elif status == "archived":
        stmt = stmt.where(Competition.lifecycle == Lifecycle.archived)
    else:
        stmt = stmt.where(Competition.lifecycle != Lifecycle.archived)
    if task_type:
        stmt = stmt.where(Competition.task_type == task_type)
    if event_type:
        stmt = stmt.where(Competition.event_type == event_type)
    if difficulty:
        stmt = stmt.where(Competition.difficulty == difficulty)
    if tag:
        stmt = stmt.where(Competition.tags.any(tag.lower()))
    if org:
        stmt = stmt.join(Organization, Organization.id == Competition.host_org_id).where(Organization.slug == org)
    if prize == "yes":
        stmt = stmt.where(Competition.has_prize.is_(True))
    elif prize == "no":
        stmt = stmt.where(Competition.has_prize.is_(False))
    if participation == "individual":
        stmt = stmt.where(Competition.team_max_size == 1)
    elif participation == "team":
        stmt = stmt.where(Competition.team_max_size > 1)
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Competition.title.ilike(like), Competition.summary.ilike(like)))
    if sort == "start":
        stmt = stmt.order_by(Competition.starts_at.asc().nulls_last(), Competition.id)
    elif sort == "end":
        stmt = stmt.order_by(Competition.ends_at.asc().nulls_last(), Competition.id)
    elif sort == "popular":
        stmt = stmt.order_by(Competition.participant_count.desc(), Competition.id)
    elif sort == "updated":
        stmt = stmt.order_by(Competition.updated_at.desc(), Competition.id)
    else:
        # Active first, then upcoming, then the rest; within group by end date.
        active_rank = case(
            (and_(published, or_(Competition.starts_at.is_(None), Competition.starts_at <= now),
                  or_(Competition.ends_at.is_(None), Competition.ends_at > now)), 0),
            (and_(published, Competition.starts_at > now), 1),
            else_=2,
        )
        stmt = stmt.order_by(active_rank, Competition.ends_at.asc().nulls_last(), Competition.id)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    comps = list(db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return build_cards(db, comps), total


def record_view(actor: Actor, comp: Competition) -> None:
    db = actor.db
    db.execute(update(Competition).where(Competition.id == comp.id).values(view_count=Competition.view_count + 1))
    if actor.is_authenticated:
        db.execute(insert(RecentView).values(user_id=actor.id, entity_type="competition", entity_id=comp.id, viewed_at=utcnow())
                   .on_conflict_do_update(index_elements=["user_id", "entity_type", "entity_id"], set_={"viewed_at": utcnow()}))
    db.commit()


# ----------------------------------------------------------------------------- write


def _validate_times(comp: Competition) -> None:
    errors: dict[str, str] = {}
    if comp.starts_at and comp.ends_at and comp.ends_at <= comp.starts_at:
        errors["ends_at"] = "End must be after start."
    if comp.registration_opens_at and comp.registration_closes_at and comp.registration_closes_at <= comp.registration_opens_at:
        errors["registration_closes_at"] = "Registration must close after it opens."
    if comp.registration_closes_at and comp.ends_at and comp.registration_closes_at > comp.ends_at:
        errors["registration_closes_at"] = "Registration must close before the competition ends."
    if comp.team_lock_at and comp.ends_at and comp.team_lock_at > comp.ends_at:
        errors["team_lock_at"] = "Team lock must be before the end."
    if comp.team_max_size < comp.team_min_size:
        errors["team_max_size"] = "Maximum team size must be at least the minimum."
    if errors:
        raise ValidationFailed(details={"fields": errors})


def _apply(actor: Actor, comp: Competition, data: CompetitionWrite, *, creating: bool) -> list[str]:
    db = actor.db
    payload = data.model_dump(exclude_unset=True)
    payload.pop("change_reason", None)
    changed: list[str] = []
    finalized = comp.lifecycle in (Lifecycle.finalized, Lifecycle.archived)
    for key, value in payload.items():
        if finalized and key not in EDITABLE_AFTER_FINALIZATION:
            raise Conflict("Results are final; only presentation fields can change.", code="competition_finalized",
                           details={"fields": {key: "Locked after finalization."}})
        if key in EVALUATION_FIELDS and comp.evaluation_locked_at is not None and not actor.is_admin:
            raise Conflict("Scoring rules are locked because submissions have been scored.", code="scoring_locked",
                           details={"fields": {key: "Locked after the first scored submission."}})
        if key in _ENUM_FIELDS and value is not None:
            try:
                value = _ENUM_FIELDS[key](value).value
            except ValueError:
                raise ValidationFailed(details={"fields": {key: "Invalid value."}}) from None
        if key == "evaluation":
            if value is None:
                continue  # evaluation config can be changed but never removed
            value = normalize_evaluation_config(value)
        if key == "tags":
            value = clean_tags(value)
        if key == "timezone" and value and not is_valid_timezone(value):
            raise ValidationFailed(details={"fields": {"timezone": "Unknown time zone."}})
        if key == "starter_assets" and value is not None:
            for asset in value:
                validate_external_url(asset["url"], "starter_assets")
        if key == "faq" and value is not None:
            value = [{"q": f["q"], "a": f["a"]} for f in value]
        if key == "host_org_id" and value is not None:
            org = db.get(Organization, value)
            if org is None or not actor.is_org_manager(org.id):
                raise Forbidden("You can only host competitions for organizations you manage.")
        if key == "parent_id" and value is not None:
            parent = db.get(Competition, value)
            if parent is None or not can_manage_competition(actor, parent):
                raise ValidationFailed(details={"fields": {"parent_id": "Unknown qualification round."}})
        if key == "dataset_version_id" and value is not None:
            version = db.get(DatasetVersion, value)
            if version is None or version.status != VersionStatus.published:
                raise ValidationFailed(details={"fields": {"dataset_version_id": "Choose a published dataset version."}})
        if key in MARKDOWN_FIELDS:
            setattr(comp, MARKDOWN_FIELDS[key], render_markdown(value or ""))
            value = value or ""
        if key == "starter_assets" and getattr(comp, key) != value and not creating:
            comp.starter_asset_version = (comp.starter_asset_version or 1) + 1
        if getattr(comp, key) != value:
            changed.append(key)
            setattr(comp, key, value)
    _validate_times(comp)
    return changed


def create_competition(actor: Actor, data: CompetitionWrite) -> Competition:
    db = actor.db
    if not data.title:
        raise ValidationFailed(details={"fields": {"title": "Enter a title."}})
    if data.host_org_id is None and not actor.is_admin:
        raise ValidationFailed("Choose an organization to host this competition.", details={
            "fields": {"host_org_id": "Choose an organization you manage (you can create a community organization)."}})
    comp = Competition(slug=unique_slug(db, Competition, data.title), title=data.title, created_by=actor.id,
                       certificate_rules=dict(DEFAULT_CERTIFICATE_RULES))
    db.add(comp)
    _apply(actor, comp, data, creating=True)
    if comp.scoring_mode == ScoringMode.judged:
        comp.leaderboard_visibility = LeaderboardVisibility.hidden
    db.flush()
    db.add(CompetitionStaff(competition_id=comp.id, user_id=actor.id, role=StaffRole.organizer, added_by=actor.id))
    record_audit(db, actor.id, "competition.create", target_type="competition", target_id=comp.id,
                 competition_id=comp.id, org_id=comp.host_org_id)
    db.commit()
    return comp


def snapshot_config(comp: Competition) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(VERSIONED_FIELDS):
        value = getattr(comp, key)
        out[key] = value.isoformat() if hasattr(value, "isoformat") else (str(value) if isinstance(value, uuid.UUID) else value)
    return out


def update_competition(actor: Actor, comp: Competition, data: CompetitionWrite) -> Competition:
    db = actor.db
    assert_manage_competition(actor, comp)
    if data.team_max_size is not None:
        counts = (select(TeamMember.team_id, func.count().label("n")).where(TeamMember.competition_id == comp.id)
                  .group_by(TeamMember.team_id).subquery())
        largest = db.scalar(select(func.max(counts.c.n))) or 0
        if data.team_max_size < largest:
            raise ValidationFailed(details={"fields": {"team_max_size": f"An existing team already has {largest} members."}})
    changed = _apply(actor, comp, data, creating=False)
    if not changed:
        return comp
    versioned = [c for c in changed if c in VERSIONED_FIELDS]
    if comp.lifecycle != Lifecycle.draft and versioned:
        comp.config_version += 1
        db.add(CompetitionConfigVersion(competition_id=comp.id, version=comp.config_version, snapshot=snapshot_config(comp),
                                        changed_fields=versioned, changed_by=actor.id, reason=data.change_reason))
    record_audit(db, actor.id, "competition.update", target_type="competition", target_id=comp.id,
                 competition_id=comp.id, reason=data.change_reason, meta={"fields": changed})
    index_competition(db, comp)
    db.commit()
    return comp


def publish_checks(db: Session, comp: Competition) -> list[PublishCheck]:
    now = utcnow()
    checks = [
        PublishCheck(key="title", label="Title", ok=bool(comp.title and len(comp.title) >= 3), required=True),
        PublishCheck(key="summary", label="Summary (at least 20 characters)", ok=len(comp.summary or "") >= 20, required=True),
        PublishCheck(key="description", label="Problem statement", ok=len(comp.description_md or "") >= 50, required=True),
        PublishCheck(key="rules", label="Rules", ok=len(comp.rules_md or "") >= 20, required=True),
        PublishCheck(key="schedule", label="Start and end times", ok=bool(comp.starts_at and comp.ends_at and comp.ends_at > comp.starts_at),
                     required=True),
        PublishCheck(key="future_end", label="End time is in the future", ok=bool(comp.ends_at and comp.ends_at > now), required=True),
        PublishCheck(key="host", label="Host organization", ok=comp.host_org_id is not None, required=False,
                     message="Competitions without a host organization are shown as community events."),
    ]
    if comp.scoring_mode == ScoringMode.automatic:
        asset = db.scalar(select(EvaluationAsset).where(EvaluationAsset.competition_id == comp.id, EvaluationAsset.is_active.is_(True)))
        checks.append(PublishCheck(key="metric", label="Evaluation metric", ok=(comp.evaluation or {}).get("metric") in METRICS, required=True))
        checks.append(PublishCheck(key="ground_truth", label="Hidden ground truth uploaded", ok=asset is not None, required=True,
                                   message=None if asset else "Upload the solution file used for scoring."))
        checks.append(PublishCheck(key="dataset", label="Dataset attached", ok=comp.dataset_version_id is not None, required=False,
                                   message="Participants need data to train on; attach a published dataset version."))
    if comp.scoring_mode == ScoringMode.judged:
        rubric = db.scalar(select(Rubric).where(Rubric.competition_id == comp.id))
        checks.append(PublishCheck(key="rubric", label="Judging rubric", ok=bool(rubric and rubric.criteria), required=True))
    return checks


def publish(actor: Actor, comp: Competition) -> Competition:
    db = actor.db
    assert_manage_competition(actor, comp)
    failing = [c for c in publish_checks(db, comp) if c.required and not c.ok]
    if failing:
        raise Conflict("Complete the required items before publishing.", code="publish_requirements_unmet",
                       details={"missing": [c.key for c in failing]})
    state.transition(comp, Lifecycle.published)
    comp.published_at = utcnow()
    if comp.visibility in (CompetitionVisibility.invite_only, CompetitionVisibility.private) and not comp.invite_code:
        comp.invite_code = secrets.token_urlsafe(9)
    db.add(CompetitionConfigVersion(competition_id=comp.id, version=comp.config_version, snapshot=snapshot_config(comp),
                                    changed_fields=["published"], changed_by=actor.id, reason="Initial publication"))
    record_audit(db, actor.id, "competition.publish", target_type="competition", target_id=comp.id, competition_id=comp.id,
                 org_id=comp.host_org_id)
    index_competition(db, comp)
    db.commit()
    return comp


def archive(actor: Actor, comp: Competition, reason: str | None) -> Competition:
    assert_manage_competition(actor, comp)
    state.transition(comp, Lifecycle.archived)
    comp.archived_at = utcnow()
    record_audit(actor.db, actor.id, "competition.archive", target_type="competition", target_id=comp.id,
                 competition_id=comp.id, reason=reason)
    index_competition(actor.db, comp)
    actor.db.commit()
    return comp


def set_frozen(actor: Actor, comp: Competition, frozen: bool, reason: str) -> Competition:
    """Platform safety control (moderators/admins): pauses registration and submissions."""
    if not actor.is_moderator:
        raise Forbidden()
    comp.frozen = frozen
    comp.frozen_reason = reason if frozen else None
    record_audit(actor.db, actor.id, "competition.freeze" if frozen else "competition.unfreeze", target_type="competition",
                 target_id=comp.id, competition_id=comp.id, reason=reason)
    actor.db.commit()
    return comp


def clone(actor: Actor, comp: Competition) -> Competition:
    db = actor.db
    assert_manage_competition(actor, comp)
    copy = Competition(slug=unique_slug(db, Competition, f"{comp.title} copy"), title=f"{comp.title} (copy)"[:140],
                       created_by=actor.id, cloned_from_id=comp.id)
    for field in ("summary", "description_md", "description_html", "rules_md", "rules_html", "evaluation_md",
                  "evaluation_html", "prize_md", "prize_html", "prize_summary", "has_prize", "faq", "scoring_notes_md",
                  "event_type", "task_type", "difficulty", "tags", "host_org_id", "visibility", "timezone", "team_min_size",
                  "team_max_size", "daily_submission_limit", "total_submission_limit", "max_submission_mb",
                  "final_selection_limit", "scoring_mode", "evaluation", "leaderboard_visibility",
                  "show_university_on_leaderboard", "certificate_rules", "format", "venue_name", "venue_address",
                  "venue_notes", "starter_assets", "organizer_contact_email", "dataset_version_id", "cover_style"):
        setattr(copy, field, getattr(comp, field))
    db.add(copy)
    db.flush()
    db.add(CompetitionStaff(competition_id=copy.id, user_id=actor.id, role=StaffRole.organizer, added_by=actor.id))
    rubric = db.scalar(select(Rubric).where(Rubric.competition_id == comp.id))
    if rubric:
        db.add(Rubric(competition_id=copy.id, criteria=rubric.criteria, reveal_scores_to_judges=rubric.reveal_scores_to_judges))
    record_audit(db, actor.id, "competition.clone", target_type="competition", target_id=copy.id, competition_id=copy.id,
                 meta={"source": str(comp.id)})
    db.commit()
    return copy


def rotate_invite_code(actor: Actor, comp: Competition) -> str:
    assert_manage_competition(actor, comp)
    comp.invite_code = secrets.token_urlsafe(9)
    record_audit(actor.db, actor.id, "competition.invite_code_rotated", target_type="competition", target_id=comp.id,
                 competition_id=comp.id)
    actor.db.commit()
    return comp.invite_code


# ----------------------------------------------------------------------------- participation


def _verified_member(actor: Actor, org_id: uuid.UUID | None) -> bool:
    if org_id is None or not actor.is_authenticated:
        return False
    m = actor.db.scalar(select(OrgMembership).where(OrgMembership.org_id == org_id, OrgMembership.user_id == actor.id,
                                                    OrgMembership.status == MembershipStatus.active))
    return m is not None and m.verified_at is not None


def join_blockers_for(actor: Actor, comp: Competition) -> list[str]:
    blockers: list[str] = []
    if not actor.is_authenticated:
        return ["sign_in_required"]
    assert actor.user is not None
    if actor.user.email_verified_at is None:
        blockers.append("email_not_verified")
    if not state.registration_open(comp):
        blockers.append("registration_closed")
    if competition_roles(actor, comp) - {"viewer"}:
        blockers.append("staff_cannot_participate")
    if comp.visibility == CompetitionVisibility.university and not _verified_member(actor, comp.host_org_id):
        blockers.append("verified_membership_required")
    return blockers


def submission_blockers(actor: Actor, comp: Competition, team: Team | None) -> list[str]:
    blockers: list[str] = []
    if comp.scoring_mode != ScoringMode.automatic:
        blockers.append("not_automatically_scored")
    if not state.submissions_open(comp):
        blockers.append("submissions_closed")
    if team is None:
        blockers.append("team_required")
    else:
        size = actor.db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)) or 0
        if size < comp.team_min_size:
            blockers.append("team_too_small")
    if actor.user and actor.user.email_verified_at is None:
        blockers.append("email_not_verified")
    return blockers


def join(actor: Actor, comp: Competition, accept_rules: bool, invite_code: str | None) -> CompetitionParticipant:
    db = actor.db
    if participant_record(actor, comp) is not None:
        raise Conflict("You have already joined this competition.", code="already_joined")
    if not accept_rules:
        raise ValidationFailed("You must accept the competition rules to join.", code="rules_not_accepted")
    blockers = join_blockers_for(actor, comp)
    if blockers:
        raise Forbidden("You cannot join this competition right now.", code=blockers[0], details={"blockers": blockers})
    if comp.visibility in (CompetitionVisibility.invite_only, CompetitionVisibility.private):
        from app.core.permissions import has_pending_team_invite

        valid_code = bool(invite_code and comp.invite_code and constant_time_eq(invite_code.strip(), comp.invite_code))
        if not valid_code and not has_pending_team_invite(actor, comp):
            raise Forbidden("A valid invite code is required.", code="invite_required")
    assert actor.user is not None and actor.id is not None
    part = CompetitionParticipant(competition_id=comp.id, user_id=actor.id, accepted_rules_at=utcnow(),
                                  rules_version=comp.config_version)
    db.add(part)
    comp.participant_count += 1
    if comp.team_min_size == 1:
        from app.modules.teams.service import create_solo_team

        team = create_solo_team(db, comp, actor.user)
        part.team_id = team.id
    db.commit()
    return part


def withdraw(actor: Actor, comp: Competition) -> None:
    from app.modules.teams.service import leave_team_internal, team_for_user

    db = actor.db
    part = participant_record(actor, comp)
    if part is None:
        raise NotFound("You are not participating.")
    team = team_for_user(db, comp.id, actor.id)
    if team is not None:
        has_subs = db.scalar(select(func.count()).select_from(Submission).where(Submission.team_id == team.id)) or 0
        if has_subs and team.is_solo:
            raise Conflict("You cannot withdraw after submitting; your results stay on record.", code="has_submissions")
        leave_team_internal(actor, comp, team)
    db.delete(part)
    comp.participant_count = max(0, comp.participant_count - 1)
    db.commit()


# ----------------------------------------------------------------------------- organizer dashboard


def organizer_competitions(actor: Actor) -> list[tuple[Competition, list[str]]]:
    db = actor.db
    staff_ids = select(CompetitionStaff.competition_id).where(CompetitionStaff.user_id == actor.id)
    clause = or_(Competition.id.in_(staff_ids), Competition.created_by == actor.id)
    admin_orgs = [oid for oid, m in actor.memberships().items() if m.role in ("owner", "admin")]
    if admin_orgs:
        clause = or_(clause, Competition.host_org_id.in_(admin_orgs))
    comps = list(db.scalars(select(Competition).where(clause).order_by(Competition.updated_at.desc()).limit(200)))
    return [(c, sorted(competition_roles(actor, c))) for c in comps]


def submission_health(db: Session, comp_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not comp_ids:
        return {}
    since = utcnow() - timedelta(hours=24)
    rows = db.execute(
        select(
            Submission.competition_id,
            func.count().filter(Submission.submitted_at > since),
            func.count().filter(Submission.status.in_([SubmissionStatus.queued, SubmissionStatus.validating, SubmissionStatus.scoring])),
            func.count().filter(Submission.status == SubmissionStatus.failed),
            func.max(Submission.submitted_at),
        ).where(Submission.competition_id.in_(comp_ids)).group_by(Submission.competition_id)
    ).all()
    return {r[0]: {"submissions_24h": r[1], "pending": r[2], "failed": r[3], "last": r[4]} for r in rows}


def user_is_member_of(db: Session, user: User, org_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(OrgMembership.id).where(OrgMembership.user_id == user.id, OrgMembership.org_id == org_id,
                                                         OrgMembership.status == MembershipStatus.active)))

