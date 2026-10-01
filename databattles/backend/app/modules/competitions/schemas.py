from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import EmailStr, Field, field_validator

from app.core.schemas import OrgMini, Schema, UserMini
from app.core.time import ensure_aware


class SponsorOut(Schema):
    org: OrgMini
    tier: str
    blurb: str | None = None


class CompetitionCard(Schema):
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    status: str
    lifecycle: str
    visibility: str
    event_type: str
    task_type: str
    difficulty: str
    scoring_mode: str
    tags: list[str]
    host: OrgMini | None
    sponsors: list[SponsorOut] = []
    starts_at: datetime | None
    ends_at: datetime | None
    registration_closes_at: datetime | None
    participant_count: int
    team_count: int
    has_prize: bool
    prize_summary: str | None
    team_min_size: int
    team_max_size: int
    metric: str | None
    format: str
    cover_style: str
    registration_open: bool
    is_demo: bool


class StarterAsset(Schema):
    label: str = Field(max_length=120)
    url: str = Field(max_length=500)
    kind: Literal["colab", "kaggle", "notebook", "github", "docs", "other"] = "other"


class FaqItem(Schema):
    q: str = Field(max_length=300)
    a: str = Field(max_length=4000)


class DatasetRef(Schema):
    dataset_id: uuid.UUID
    slug: str
    title: str
    version: int
    version_id: uuid.UUID
    license: str
    file_count: int
    total_bytes: int


class EvaluationSummary(Schema):
    evaluator: str
    metric: str
    metric_label: str
    direction: str
    secondary_metrics: list[str]
    id_column: str
    target_column: str
    strict_schema: bool
    submission_format: str
    evaluator_version: str


class ViewerTeam(Schema):
    id: uuid.UUID
    name: str
    is_solo: bool
    is_captain: bool
    member_count: int


class ViewerContext(Schema):
    is_authenticated: bool
    is_participant: bool
    roles: list[str]
    team: ViewerTeam | None
    accepted_rules_at: datetime | None
    can_join: bool
    join_blockers: list[str]
    needs_invite_code: bool
    can_submit: bool
    submit_blockers: list[str]
    submissions_today: int
    submissions_remaining_today: int | None
    pending_invitations: int


class ScheduleItemOut(Schema):
    id: uuid.UUID
    title: str
    kind: str
    starts_at: datetime
    ends_at: datetime | None
    location: str | None
    url: str | None
    description: str | None


class AwardCategoryOut(Schema):
    id: uuid.UUID
    name: str
    description: str | None
    position: int
    winner_team_id: uuid.UUID | None
    winner_team_name: str | None = None
    awarded_at: datetime | None


class CompetitionDetail(CompetitionCard):
    description_html: str
    rules_html: str
    evaluation_html: str
    prize_html: str
    scoring_notes_md: str
    faq: list[FaqItem]
    registration_opens_at: datetime | None
    team_lock_at: datetime | None
    timezone: str
    daily_submission_limit: int
    total_submission_limit: int | None
    max_submission_mb: int
    final_selection_limit: int
    leaderboard_visibility: str
    venue_name: str | None
    venue_address: str | None
    venue_notes: str | None
    starter_assets: list[StarterAsset]
    starter_asset_version: int
    organizer_contact_email: str | None
    evaluation: EvaluationSummary | None
    dataset: DatasetRef | None
    schedule: list[ScheduleItemOut]
    awards: list[AwardCategoryOut]
    parent: CompetitionCard | None = None
    config_version: int
    published_at: datetime | None
    finalized_at: datetime | None
    frozen: bool
    frozen_reason: str | None
    indexable: bool
    announcement_count: int
    viewer: ViewerContext


class CompetitionWrite(Schema):
    """Fields an organizer may set. All optional for PATCH; create requires title."""

    title: str | None = Field(default=None, min_length=3, max_length=140)
    summary: str | None = Field(default=None, max_length=280)
    description_md: str | None = Field(default=None, max_length=100_000)
    rules_md: str | None = Field(default=None, max_length=100_000)
    evaluation_md: str | None = Field(default=None, max_length=50_000)
    prize_md: str | None = Field(default=None, max_length=20_000)
    prize_summary: str | None = Field(default=None, max_length=120)
    has_prize: bool | None = None
    faq: list[FaqItem] | None = None
    scoring_notes_md: str | None = Field(default=None, max_length=20_000)
    event_type: str | None = None
    task_type: str | None = None
    difficulty: str | None = None
    tags: list[str] | None = None
    host_org_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    visibility: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    team_lock_at: datetime | None = None
    timezone: str | None = None
    team_min_size: int | None = Field(default=None, ge=1, le=20)
    team_max_size: int | None = Field(default=None, ge=1, le=20)
    daily_submission_limit: int | None = Field(default=None, ge=1, le=100)
    total_submission_limit: int | None = Field(default=None, ge=1, le=10_000)
    max_submission_mb: int | None = Field(default=None, ge=1, le=200)
    final_selection_limit: int | None = Field(default=None, ge=1, le=5)
    scoring_mode: str | None = None
    evaluation: dict[str, Any] | None = None
    leaderboard_visibility: str | None = None
    show_university_on_leaderboard: bool | None = None
    certificate_rules: dict[str, Any] | None = None
    format: str | None = None
    venue_name: str | None = Field(default=None, max_length=160)
    venue_address: str | None = Field(default=None, max_length=300)
    venue_notes: str | None = Field(default=None, max_length=5000)
    starter_assets: list[StarterAsset] | None = None
    organizer_contact_email: EmailStr | None = None
    organizer_notes: str | None = Field(default=None, max_length=20_000)
    dataset_version_id: uuid.UUID | None = None
    cover_style: str | None = Field(default=None, max_length=32)
    change_reason: str | None = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at", "registration_opens_at", "registration_closes_at", "team_lock_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return ensure_aware(v)


class PublishCheck(Schema):
    key: str
    label: str
    ok: bool
    required: bool
    message: str | None = None


class EvaluationAssetOut(Schema):
    id: uuid.UUID
    sha256_prefix: str
    row_count: int
    public_count: int
    private_count: int
    created_at: datetime


class StaffOut(Schema):
    user: UserMini
    role: str


class ConfigVersionOut(Schema):
    version: int
    changed_fields: list[str]
    reason: str | None
    changed_by: UserMini | None
    created_at: datetime


class CompetitionManage(Schema):
    """Organizer-only view — includes private configuration."""

    id: uuid.UUID
    slug: str
    title: str
    lifecycle: str
    status: str
    organizer_notes: str | None
    invite_code: str | None
    evaluation: dict[str, Any]
    evaluation_locked_at: datetime | None
    evaluation_asset: EvaluationAssetOut | None
    certificate_rules: dict[str, Any]
    show_university_on_leaderboard: bool
    staff: list[StaffOut]
    sponsors: list[SponsorOut]
    publish_checks: list[PublishCheck]
    config_versions: list[ConfigVersionOut]
    raw: dict[str, Any]


class JoinIn(Schema):
    accept_rules: bool
    invite_code: str | None = Field(default=None, max_length=32)


class StaffIn(Schema):
    handle: str = Field(max_length=30)
    role: Literal["organizer", "judge"]


class SponsorIn(Schema):
    org_slug: str = Field(max_length=80)
    tier: Literal["title", "platinum", "gold", "silver", "partner", "community"] = "partner"
    blurb: str | None = Field(default=None, max_length=300)


class AnnouncementIn(Schema):
    title: str = Field(min_length=3, max_length=160)
    body_md: str = Field(min_length=1, max_length=20_000)
    pinned: bool = False
    notify: bool = True
    as_sponsor_org: str | None = None


class AnnouncementOut(Schema):
    id: uuid.UUID
    title: str
    body_html: str
    pinned: bool
    status: str
    author: UserMini | None
    sponsor: OrgMini | None
    created_at: datetime
    is_read: bool = False


class ScheduleItemIn(Schema):
    title: str = Field(min_length=2, max_length=160)
    kind: Literal["session", "workshop", "deadline", "presentation", "ceremony", "checkin"] = "session"
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return ensure_aware(v)


class AwardCategoryIn(Schema):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    position: int = 0


class AwardWinnerIn(Schema):
    team_id: uuid.UUID | None


class ReasonIn(Schema):
    reason: str = Field(min_length=3, max_length=1000)


class ParticipantOut(Schema):
    user: UserMini
    team_id: uuid.UUID | None
    team_name: str | None
    joined_at: datetime
    status: str
    submission_count: int


class OrganizerCompetitionRow(Schema):
    card: CompetitionCard
    roles: list[str]
    submissions_24h: int
    pending_submissions: int
    failed_submissions: int
    last_submission_at: datetime | None
