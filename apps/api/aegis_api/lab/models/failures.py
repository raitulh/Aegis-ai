"""Failure intelligence: classified failures and the lessons extracted from them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, project_scope_fk
from engines.lab.states import FailureStatus


class Failure(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    __tablename__ = "failures"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_failures_org_signature", "organization_id", "signature"),
        Index("ix_failures_org_type", "organization_id", "failure_type"),
        Index("ix_failures_mission", "mission_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="SET NULL"), nullable=True
    )
    compute_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    failure_type: Mapped[str] = mapped_column(String(32))
    signature: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    detected_at: Mapped[datetime]
    detected_by: Mapped[str] = mapped_column(String(16), default="platform")  # platform|agent|human
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    traceback: Mapped[str | None] = mapped_column(Text)  # redacted + truncated
    logs_excerpt: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[dict[str, Any]] = mapped_column(default=dict)  # matched rules + signals
    root_cause: Mapped[str | None] = mapped_column(Text)
    root_cause_source: Mapped[str | None] = mapped_column(String(16))  # rule|model|human
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    recovery_action: Mapped[dict[str, Any]] = mapped_column(default=dict)
    recovery_status: Mapped[str] = mapped_column(String(16), default="none")  # none|proposed|testing|succeeded|failed
    recovery_experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    recurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    similar_failure_ids: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default=FailureStatus.OPEN)


class Lesson(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    """A reusable lesson learned from one or more failures (also mirrored into FAILURE memory)."""

    __tablename__ = "lessons"
    __table_args__ = (project_scope_fk(), Index("ix_lessons_org_signature", "organization_id", "signature"))

    failure_type: Mapped[str] = mapped_column(String(32))
    signature: Mapped[str | None] = mapped_column(String(64))
    statement: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(16), default="PROPOSED")  # PROPOSED|ACTIVE|RETIRED
    source: Mapped[str] = mapped_column(String(16), default="rule")  # rule|agent|human
    times_applied: Mapped[int] = mapped_column(Integer, default=0)
    times_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
