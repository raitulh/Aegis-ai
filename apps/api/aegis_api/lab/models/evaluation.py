"""Versioned evaluators and evaluation runs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, project_scope_fk
from engines.lab.states import RunState


class LabEvaluator(IdMixin, CreatedMixin, Base):
    """Immutable evaluator definition (``key`` + ``version``). NULL organization = built-in."""

    __tablename__ = "lab_evaluators"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key",
            "version",
            name="uq_lab_evaluators_key_version",
            postgresql_nulls_not_distinct=True,
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(24))
    description: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")


class EvaluationRun(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_evaluation_runs_experiment", "experiment_id"),
        Index("ix_evaluation_runs_run", "experiment_run_id"),
        Index("ix_evaluation_runs_mission", "mission_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), nullable=True
    )
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="CASCADE"), nullable=True
    )
    comparison_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evaluator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lab_evaluators.id", ondelete="RESTRICT"))
    evaluator_key: Mapped[str] = mapped_column(String(80))
    evaluator_version: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # True when the evaluator is independent of the agent/strategy that produced the result.
    independent: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
