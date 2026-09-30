"""Failure intelligence, lessons, strategy registry, evolution runs and internal benchmarks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import (
    Base,
    CreatedMixin,
    IdMixin,
    OptimisticLockMixin,
    OrgMixin,
    TimestampMixin,
    lab_args,
    lab_fk,
)


class Failure(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "failures"
    __table_args__ = lab_args(
        Index("ix_lab_failures_signature", "organization_id", "signature"),
        Index("ix_lab_failures_mission_type", "mission_id", "failure_type"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("experiments", "SET NULL"), nullable=True, index=True
    )
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("experiment_runs", "SET NULL"), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    stage: Mapped[str] = mapped_column(String(24))
    failure_type: Mapped[str] = mapped_column(String(40))
    rule_id: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    root_cause: Mapped[str] = mapped_column(Text)
    evidence_lines: Mapped[list[str]] = mapped_column(default=list)
    traceback: Mapped[str | None] = mapped_column(Text)
    logs_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    signature: Mapped[str] = mapped_column(String(80))
    recurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    detected_at: Mapped[datetime]
    signal: Mapped[dict[str, Any]] = mapped_column(default=dict)
    diagnosis: Mapped[dict[str, Any]] = mapped_column(default=dict)  # model-assisted, advisory
    similar_failure_ids: Mapped[list[str]] = mapped_column(default=list)
    recovery_actions: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    recovery_status: Mapped[str] = mapped_column(
        String(16), default="proposed"
    )  # proposed|applied|succeeded|failed|none
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)


class Lesson(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "lessons"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "signature", "recovery_kind", name="uq_lab_lessons_sig_recovery")
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    signature: Mapped[str] = mapped_column(String(80))
    failure_type: Mapped[str] = mapped_column(String(40))
    lesson: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    recovery_kind: Mapped[str] = mapped_column(String(48), default="none")
    resolved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class Strategy(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "strategies"
    __table_args__ = lab_args(UniqueConstraint("organization_id", "name", name="uq_lab_strategies_org_name"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    promoted_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    version_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class StrategyVersion(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "strategy_versions"
    __table_args__ = lab_args(
        UniqueConstraint("strategy_id", "version", name="uq_lab_strategy_versions_strategy_version"),
        Index("ix_lab_strategy_versions_status", "strategy_id", "status"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(lab_fk("strategies"))
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("strategy_versions", "SET NULL"), nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    definition_sha256: Mapped[str] = mapped_column(String(64))
    parameter_hash: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    evolution_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(24), default="human")  # human | engine | agent_proposal
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    fitness: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evaluations: Mapped[int] = mapped_column(Integer, default=0)
    promoted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class StrategyEvaluation(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "strategy_evaluations"
    __table_args__ = lab_args(Index("ix_lab_strategy_evaluations_version", "strategy_version_id", "created_at"))

    strategy_version_id: Mapped[uuid.UUID] = mapped_column(lab_fk("strategy_versions"))
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions", "SET NULL"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("experiments", "SET NULL"), nullable=True)
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # experiment | benchmark | reproduction
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    primary_samples: Mapped[list[Any]] = mapped_column(default=list)
    fitness: Mapped[dict[str, Any]] = mapped_column(default=dict)
    feasible: Mapped[bool] = mapped_column(Boolean, default=True)
    reproduced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class StrategyMutation(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "strategy_mutations"

    __table_args__ = lab_args()
    parent_version_id: Mapped[uuid.UUID] = mapped_column(lab_fk("strategy_versions"), index=True)
    second_parent_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    child_version_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("strategy_versions", "SET NULL"), nullable=True, index=True
    )
    evolution_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    operator: Mapped[str] = mapped_column(String(32))
    changes: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    seed: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(24), default="engine")
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class EvolutionRun(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "evolution_runs"
    __table_args__ = lab_args(Index("ix_lab_evolution_runs_mission", "mission_id", "created_at"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(lab_fk("strategies"))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    max_generations: Mapped[int] = mapped_column(Integer, default=3)
    archive_version_ids: Mapped[list[str]] = mapped_column(default=list)
    generations: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class BenchmarkRun(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = lab_args(Index("ix_lab_benchmark_runs_suite_subject", "suite_key", "subject_ref"))

    suite_key: Mapped[str] = mapped_column(String(64))
    suite_version: Mapped[str] = mapped_column(String(16))
    subject_kind: Mapped[str] = mapped_column(String(16))
    subject_ref: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="running")
    n_cases: Mapped[int] = mapped_column(Integer, default=0)
    mean_score: Mapped[float | None] = mapped_column(Float)
    pass_rate: Mapped[float | None] = mapped_column(Float)
    comparison: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class BenchmarkResult(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "benchmark_results"
    __table_args__ = lab_args(UniqueConstraint("run_id", "case_id", name="uq_lab_benchmark_results_run_case"))

    run_id: Mapped[uuid.UUID] = mapped_column(lab_fk("benchmark_runs"), index=True)
    case_id: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
