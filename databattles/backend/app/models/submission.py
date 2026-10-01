from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk
from app.models.enums import SubmissionStatus


class EvaluationAsset(UUIDPk, Base):
    """Hidden ground truth. Stored under the private storage prefix and never served to clients."""

    __tablename__ = "evaluation_assets"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    public_count: Mapped[int] = mapped_column(Integer)
    private_count: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Submission(UUIDPk, Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_leaderboard", "competition_id", "status", "team_id"),
        Index("ix_submissions_team_time", "team_id", "submitted_at"),
        Index("uq_submissions_idempotency", "team_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=SubmissionStatus.queued)
    description: Mapped[str | None] = mapped_column(String(500))
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    public_score: Mapped[float | None] = mapped_column(Float)
    private_score: Mapped[float | None] = mapped_column(Float)  # never exposed before finalization
    secondary_scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evaluator_version: Mapped[str | None] = mapped_column(String(40))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    config_version: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(500))
    error_details: Mapped[list[Any]] = mapped_column(default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    is_final_selected: Mapped[bool] = mapped_column(default=False)
    invalidated_at: Mapped[datetime | None]
    invalidated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    invalidation_reason: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(default=False)


class LeaderboardSnapshot(UUIDPk, Base):
    """Frozen results. Corrections create a new version; history is never rewritten."""

    __tablename__ = "leaderboard_snapshots"
    __table_args__ = (UniqueConstraint("competition_id", "version"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="final")  # final|correction
    source: Mapped[str] = mapped_column(String(16), default="automatic")  # automatic|judging
    rows: Mapped[list[Any]] = mapped_column(default=list)
    rules_version: Mapped[int] = mapped_column(Integer)
    evaluator_version: Mapped[str | None] = mapped_column(String(40))
    metric: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(8))
    is_current: Mapped[bool] = mapped_column(default=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class EventSubmission(UUIDPk, Base):
    """Project submissions for judged events (hackathons, demo days)."""

    __tablename__ = "event_submissions"
    __table_args__ = (UniqueConstraint("competition_id", "team_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(300), default="")
    description_md: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    repo_url: Mapped[str | None] = mapped_column(String(500))
    demo_url: Mapped[str | None] = mapped_column(String(500))
    video_url: Mapped[str | None] = mapped_column(String(500))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CompetitionResult(UUIDPk, Base):
    """Per-user final placement derived from the current leaderboard snapshot (powers profiles and badges)."""

    __tablename__ = "competition_results"
    __table_args__ = (UniqueConstraint("competition_id", "user_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    rank: Mapped[int | None] = mapped_column(Integer)
    total_ranked: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(40))  # factual: Winner | Top 3 | Top 10 | Participant | award name
    score: Mapped[float | None] = mapped_column(Float)
    snapshot_version: Mapped[int] = mapped_column(Integer)
    hidden_on_profile: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
