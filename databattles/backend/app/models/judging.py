from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk
from app.models.enums import JudgeScoreStatus


class Rubric(UUIDPk, Base):
    __tablename__ = "rubrics"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), unique=True)
    # [{key, label, description, min, max, weight, allow_decimal}]
    criteria: Mapped[list[Any]] = mapped_column(default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    reveal_scores_to_judges: Mapped[bool] = mapped_column(default=False)
    blind_judging: Mapped[bool] = mapped_column(default=False)  # future: hide team identities from judges
    locked_at: Mapped[datetime | None]  # set when first score is submitted
    finalized_at: Mapped[datetime | None]
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class JudgeAssignment(UUIDPk, Base):
    """team_id NULL means the judge is assigned to every team in the event."""

    __tablename__ = "judge_assignments"
    __table_args__ = (UniqueConstraint("competition_id", "judge_id", "team_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    panel: Mapped[str | None] = mapped_column(String(60))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JudgeConflict(UUIDPk, Base):
    __tablename__ = "judge_conflicts"
    __table_args__ = (UniqueConstraint("judge_id", "team_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(300))
    declared_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JudgeScore(UUIDPk, Base):
    __tablename__ = "judge_scores"
    __table_args__ = (UniqueConstraint("competition_id", "judge_id", "team_id"),)

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    rubric_version: Mapped[int] = mapped_column(Integer)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    feedback: Mapped[str | None] = mapped_column(Text)
    weighted_total: Mapped[float | None] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(16), default=JudgeScoreStatus.draft)
    submitted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PresentationSlot(UUIDPk, Base):
    __tablename__ = "presentation_slots"

    competition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitions.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    location: Mapped[str | None] = mapped_column(String(200))
    meeting_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(500))
