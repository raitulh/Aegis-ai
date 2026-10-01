from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import (
    AnnouncementStatus,
    CompetitionVisibility,
    Difficulty,
    EventFormat,
    EventType,
    LeaderboardVisibility,
    Lifecycle,
    ScoringMode,
)

DEFAULT_EVALUATION: dict[str, Any] = {
    "evaluator": "csv_prediction",
    "metric": "accuracy",
    "secondary_metrics": [],
    "id_column": "id",
    "target_column": "target",
    "strict_schema": True,
    "positive_label": "1",
}

DEFAULT_CERTIFICATE_RULES: dict[str, Any] = {
    "participation": True,          # at least one valid (scored) submission / event submission
    "award_top_n": 10,              # rank <= N receives an award certificate
}


class Competition(UUIDPk, Timestamps, Base):
    """Competitions and events share one model; `event_type` + `scoring_mode` pick behavior."""

    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint("team_min_size >= 1 AND team_max_size >= team_min_size", name="team_size_range"),
        CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="time_range"),
        Index("ix_competitions_discovery", "lifecycle", "visibility", "starts_at"),
        Index("ix_competitions_tags", "tags", postgresql_using="gin"),
    )

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(280), default="")
    description_md: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    rules_md: Mapped[str] = mapped_column(Text, default="")
    rules_html: Mapped[str] = mapped_column(Text, default="")
    evaluation_md: Mapped[str] = mapped_column(Text, default="")
    evaluation_html: Mapped[str] = mapped_column(Text, default="")
    prize_md: Mapped[str] = mapped_column(Text, default="")
    prize_html: Mapped[str] = mapped_column(Text, default="")
    prize_summary: Mapped[str | None] = mapped_column(String(120))
    has_prize: Mapped[bool] = mapped_column(default=False)
    faq: Mapped[list[Any]] = mapped_column(default=list)
    scoring_notes_md: Mapped[str] = mapped_column(Text, default="")

    event_type: Mapped[str] = mapped_column(String(24), default=EventType.ml_competition)
    task_type: Mapped[str] = mapped_column(String(24), default="classification")
    difficulty: Mapped[str] = mapped_column(String(16), default=Difficulty.intermediate)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")

    host_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitions.id", ondelete="SET NULL"))
    cloned_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitions.id", ondelete="SET NULL"))

    visibility: Mapped[str] = mapped_column(String(16), default=CompetitionVisibility.public)
    invite_code: Mapped[str | None] = mapped_column(String(32))
    lifecycle: Mapped[str] = mapped_column(String(16), default=Lifecycle.draft)
    frozen: Mapped[bool] = mapped_column(default=False)
    frozen_reason: Mapped[str | None] = mapped_column(String(500))

    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None] = mapped_column(index=True)
    registration_opens_at: Mapped[datetime | None]
    registration_closes_at: Mapped[datetime | None]
    team_lock_at: Mapped[datetime | None]
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    team_min_size: Mapped[int] = mapped_column(Integer, default=1)
    team_max_size: Mapped[int] = mapped_column(Integer, default=4)
    daily_submission_limit: Mapped[int] = mapped_column(Integer, default=5)
    total_submission_limit: Mapped[int | None] = mapped_column(Integer)
    max_submission_mb: Mapped[int] = mapped_column(Integer, default=20)
    final_selection_limit: Mapped[int] = mapped_column(Integer, default=2)

    scoring_mode: Mapped[str] = mapped_column(String(16), default=ScoringMode.automatic)
    evaluation: Mapped[dict[str, Any]] = mapped_column(default=lambda: dict(DEFAULT_EVALUATION))
    evaluation_locked_at: Mapped[datetime | None]
    leaderboard_visibility: Mapped[str] = mapped_column(String(16), default=LeaderboardVisibility.visible)
    show_university_on_leaderboard: Mapped[bool] = mapped_column(default=True)
    leaderboard_rules_version: Mapped[int] = mapped_column(Integer, default=1)
    certificate_rules: Mapped[dict[str, Any]] = mapped_column(default=lambda: dict(DEFAULT_CERTIFICATE_RULES))

    format: Mapped[str] = mapped_column(String(16), default=EventFormat.online)
    venue_name: Mapped[str | None] = mapped_column(String(160))
    venue_address: Mapped[str | None] = mapped_column(String(300))
    venue_notes: Mapped[str | None] = mapped_column(Text)

    starter_assets: Mapped[list[Any]] = mapped_column(default=list)  # [{label, url, kind}]
    starter_asset_version: Mapped[int] = mapped_column(Integer, default=1)
    organizer_contact_email: Mapped[str | None] = mapped_column(String(320))
    organizer_notes: Mapped[str | None] = mapped_column(Text)  # private to staff

    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_versions.id", ondelete="SET NULL"))
    cover_style: Mapped[str] = mapped_column(String(32), default="aurora")

    config_version: Mapped[int] = mapped_column(Integer, default=1)
    published_at: Mapped[datetime | None]
    finalized_at: Mapped[datetime | None]
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archived_at: Mapped[datetime | None]

    participant_count: Mapped[int] = mapped_column(Integer, default=0)
    team_count: Mapped[int] = mapped_column(Integer, default=0)
    submission_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(default=False)


class CompetitionStaff(UUIDPk, Base):
    __tablename__ = "competition_staff"
    __table_args__ = (UniqueConstraint("competition_id", "user_id", "role"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CompetitionSponsor(UUIDPk, Base):
    __tablename__ = "competition_sponsors"
    __table_args__ = (UniqueConstraint("competition_id", "org_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    tier: Mapped[str] = mapped_column(String(16), default="partner")
    blurb: Mapped[str | None] = mapped_column(String(300))
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class CompetitionConfigVersion(UUIDPk, Base):
    """Immutable history of configuration after publication (who changed what, when, why)."""

    __tablename__ = "competition_config_versions"
    __table_args__ = (UniqueConstraint("competition_id", "version"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    changed_fields: Mapped[list[Any]] = mapped_column(default=list)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Announcement(UUIDPk, Base):
    __tablename__ = "announcements"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sponsor_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160))
    body_md: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default=AnnouncementStatus.published)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"

    announcement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScheduleItem(UUIDPk, Base):
    __tablename__ = "schedule_items"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24), default="session")  # session|workshop|deadline|presentation|ceremony
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime | None]
    location: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)


class AwardCategory(UUIDPk, Base):
    __tablename__ = "award_categories"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer, default=0)
    winner_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    awarded_at: Mapped[datetime | None]


class CompetitionParticipant(UUIDPk, Base):
    __tablename__ = "competition_participants"
    __table_args__ = (UniqueConstraint("competition_id", "user_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    accepted_rules_at: Mapped[datetime]
    rules_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|withdrawn|disqualified
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Team(UUIDPk, Base):
    """Every entrant is a team. Individuals compete as solo teams (is_solo)."""

    __tablename__ = "teams"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    captain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    is_solo: Mapped[bool] = mapped_column(default=False)
    workspace_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TeamMember(UUIDPk, Base):
    __tablename__ = "team_members"
    __table_args__ = (
        # One team per user per competition — enforced by the database.
        UniqueConstraint("competition_id", "user_id", name="uq_team_members_one_team_per_competition"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # captain|member
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TeamInvitation(UUIDPk, Base):
    __tablename__ = "team_invitations"
    __table_args__ = (Index("ix_team_invitations_invitee", "invitee_user_id", "status"),)

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"))
    inviter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    invitee_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    invitee_email: Mapped[str | None] = mapped_column(String(320))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    responded_at: Mapped[datetime | None]


Index("uq_teams_competition_name_lower", Team.competition_id, func.lower(Team.name), unique=True)
