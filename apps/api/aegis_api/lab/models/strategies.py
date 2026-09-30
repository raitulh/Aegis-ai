"""Strategy registry, evolution runs and the internal benchmark framework."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, project_scope_fk, user_fk
from engines.lab.states import RunState, StrategyStatus


class Strategy(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    """A versioned research behaviour (search/hypothesis/experiment/optimization/prompt/topology/…).

    ``parameter_schema`` is the guardrail: evolution may only change parameters it declares, within the
    declared bounds. Permissions, secrets, network access and policy are *not* strategy parameters.
    """

    __tablename__ = "strategies"
    __table_args__ = (project_scope_fk(), UniqueConstraint("organization_id", "name", name="uq_strategies_org_name"))

    kind: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class StrategyVersion(IdMixin, TimestampMixin, OrgMixin, Base):
    """A strategy version. ``definition``/``parameters``/lineage are immutable (trigger); status moves
    through CANDIDATE → EXPERIMENTAL → SURVIVING → PROMOTED → RETIRED/ROLLED_BACK."""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
        Index("ix_strategy_versions_strategy_status", "strategy_id", "status"),
        Index("ix_strategy_versions_evolution_run", "evolution_run_id", "generation"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=StrategyStatus.CANDIDATE)
    evolution_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fitness: Mapped[dict[str, Any]] = mapped_column(default=dict)
    pareto_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crowding: Mapped[float | None] = mapped_column(Float, nullable=True)
    mutation_history: Mapped[list[Any]] = mapped_column(default=list)
    created_by: Mapped[str] = mapped_column(String(16), default="human")  # human|evolution|agent|seed
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    promoted_by_id: Mapped[uuid.UUID | None] = user_fk()
    retired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class StrategyMutation(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "strategy_mutations"

    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    second_parent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    child_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True
    )
    operator: Mapped[str] = mapped_column(String(48))
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(16), default="engine")  # engine|agent|human
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evolution_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)


class StrategyEvaluation(IdMixin, CreatedMixin, OrgMixin, Base):
    """Multi-objective fitness observation for a strategy version (append-only)."""

    __tablename__ = "strategy_evaluations"
    __table_args__ = (Index("ix_strategy_evaluations_version", "strategy_version_id", "created_at"),)

    strategy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategy_versions.id", ondelete="CASCADE"))
    evolution_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # performance, cost, latency, compute_efficiency, robustness, reproducibility, novelty, safety
    objectives: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raw_metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    n_samples: Mapped[int] = mapped_column(Integer, default=1)
    seeds: Mapped[list[Any]] = mapped_column(default=list)
    evaluator_version: Mapped[str] = mapped_column(String(32))
    constraints_satisfied: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence: Mapped[list[Any]] = mapped_column(default=list)


class EvolutionRun(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    __tablename__ = "evolution_runs"
    __table_args__ = (project_scope_fk(), Index("ix_evolution_runs_org_status", "organization_id", "status"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    # population_size, generations, objectives [{name, direction, weight}], constraints, mutation_rate,
    # crossover_rate, seed, benchmark_suite, archive_size, max_candidates
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    current_generation: Mapped[int] = mapped_column(Integer, default=0)
    archive: Mapped[dict[str, Any]] = mapped_column(default=dict)  # Pareto archive snapshot
    best_version_ids: Mapped[list[str]] = mapped_column(default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class BenchmarkSuite(IdMixin, CreatedMixin, Base):
    """Internal benchmark definition (LiteratureBench, HypothesisBench, …). NULL org = built-in."""

    __tablename__ = "benchmark_suites"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key",
            "version",
            name="uq_benchmark_suites_key_version",
            postgresql_nulls_not_distinct=True,
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(24))
    description: Mapped[str | None] = mapped_column(Text)
    component: Mapped[str] = mapped_column(String(48))  # what the bench measures
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class BenchmarkRun(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (Index("ix_benchmark_runs_subject", "subject_type", "subject_id"),)

    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_suites.id", ondelete="RESTRICT"), index=True)
    suite_key: Mapped[str] = mapped_column(String(64))
    suite_version: Mapped[str] = mapped_column(String(24))
    subject_type: Mapped[str] = mapped_column(String(32))  # strategy_version|agent_version|component
    subject_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=RunState.PENDING)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    case_results: Mapped[list[Any]] = mapped_column(default=list)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="SET NULL"), nullable=True
    )
    comparison: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
