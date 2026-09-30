"""Experiments, immutable experiment versions, runs, metrics, comparisons, code snapshots, environments."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import (
    OptionalProjectScoped,
    ProjectScoped,
    money_column,
    project_scope_fk,
    user_fk,
)
from engines.lab.states import ExecutionStatus, ExperimentStatus


class CodeSnapshot(IdMixin, CreatedMixin, OrgMixin, ProjectScoped, Base):
    """Content-addressed code used by an experiment (inline files, git commit, or agent-generated)."""

    __tablename__ = "code_snapshots"
    __table_args__ = (project_scope_fk(), UniqueConstraint("project_id", "content_hash", name="uq_code_snapshots_hash"))

    source: Mapped[str] = mapped_column(String(16))  # inline|git|artifact|agent_generated
    git_repo: Mapped[str | None] = mapped_column(String(1000))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    entrypoint: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(24), default="python")
    # path → {"sha256": ..., "size": ...}
    files_manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(500))  # tar.gz of the files in object storage
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ExecutionEnvironment(IdMixin, CreatedMixin, OrgMixin, Base):
    """Content-addressed runtime environment: image (+digest), pinned dependencies, env-var policy."""

    __tablename__ = "execution_environments"
    __table_args__ = (UniqueConstraint("organization_id", "content_hash", name="uq_execution_environments_hash"),)

    name: Mapped[str] = mapped_column(String(160))
    image: Mapped[str] = mapped_column(String(500))
    image_digest: Mapped[str | None] = mapped_column(String(100))
    runtime: Mapped[str | None] = mapped_column(String(48))
    dependencies: Mapped[list[str]] = mapped_column(default=list)
    lockfile: Mapped[str | None] = mapped_column(Text)
    lockfile_hash: Mapped[str | None] = mapped_column(String(64))
    # Only variables named here may be injected; values never come from the host environment.
    env_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class Experiment(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """An experiment identity. Its specification lives in immutable ``experiment_versions``."""

    __tablename__ = "experiments"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_experiments_mission_status", "mission_id", "status"),
        Index("ix_experiments_org_status", "organization_id", "status"),
        Index("ix_experiments_updated_at", "updated_at"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(
        String(16), default="candidate"
    )  # baseline|candidate|ablation|sensitivity|reproduction|exploratory
    status: Mapped[str] = mapped_column(String(16), default=ExperimentStatus.DRAFT)
    status_reason: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    baseline_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    parent_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ExperimentVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable, validated experiment specification (UPDATE/DELETE blocked by trigger)."""

    __tablename__ = "experiment_versions"
    __table_args__ = (UniqueConstraint("experiment_id", "version", name="uq_experiment_versions_exp_version"),)

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)  # full structured specification
    spec_hash: Mapped[str] = mapped_column(String(64), index=True)
    objective: Mapped[str] = mapped_column(Text)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    baseline: Mapped[dict[str, Any]] = mapped_column(default=dict)
    method: Mapped[str | None] = mapped_column(Text)
    variables: Mapped[list[Any]] = mapped_column(default=list)
    controls: Mapped[list[Any]] = mapped_column(default=list)
    dataset_version_ids: Mapped[list[str]] = mapped_column(default=list)
    metrics: Mapped[list[Any]] = mapped_column(default=list)
    success_criteria: Mapped[list[Any]] = mapped_column(default=list)
    statistical_plan: Mapped[dict[str, Any]] = mapped_column(default=dict)
    seeds: Mapped[list[Any]] = mapped_column(default=list)
    ablations: Mapped[list[Any]] = mapped_column(default=list)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("execution_environments.id", ondelete="RESTRICT"), nullable=True
    )
    code_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("code_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    command: Mapped[list[str]] = mapped_column(default=list)
    # cpu, memory_mb, disk_mb, gpu_type, gpu_count, runtime
    resource_request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=900)
    expected_cost_usd: Mapped[Decimal] = money_column()
    reproducibility: Mapped[dict[str, Any]] = mapped_column(default=dict)
    network_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    validation_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ExperimentRun(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """One execution of an experiment version with one seed. ``run_key`` makes scheduling idempotent."""

    __tablename__ = "experiment_runs"
    __table_args__ = (
        project_scope_fk(),
        UniqueConstraint("experiment_version_id", "run_key", name="uq_experiment_runs_version_key"),
        Index("ix_experiment_runs_experiment_status", "experiment_id", "status"),
        Index("ix_experiment_runs_mission", "mission_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    experiment_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_versions.id", ondelete="RESTRICT"), index=True
    )
    run_number: Mapped[int] = mapped_column(Integer)
    run_key: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="candidate")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=ExecutionStatus.QUEUED)
    status_reason: Mapped[str | None] = mapped_column(Text)
    compute_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    environment_manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[Decimal] = money_column()
    error: Mapped[str | None] = mapped_column(Text)
    reproduction_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class ExperimentMetric(IdMixin, CreatedMixin, OrgMixin, Base):
    """A measured metric. ``source`` records who measured it: the platform/evaluator (trusted) or the
    experiment code itself (self-reported; never sufficient alone for verification)."""

    __tablename__ = "experiment_metrics"
    __table_args__ = (Index("ix_experiment_metrics_run_name", "experiment_run_id", "name"),)

    experiment_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    value: Mapped[float] = mapped_column(Float)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    split: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="self_reported")  # platform|evaluator|self_reported
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ExperimentComparison(IdMixin, CreatedMixin, OrgMixin, OptionalProjectScoped, Base):
    """Explicit baseline-vs-candidate comparison with the configuration diff that produced it."""

    __tablename__ = "experiment_comparisons"
    __table_args__ = (project_scope_fk(), Index("ix_experiment_comparisons_mission", "mission_id"))

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    baseline_experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    candidate_experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    baseline_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    comparison_type: Mapped[str] = mapped_column(String(16), default="baseline")  # baseline|ablation|sensitivity
    metric: Mapped[str] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(8))  # maximize|minimize
    baseline_run_ids: Mapped[list[str]] = mapped_column(default=list)
    candidate_run_ids: Mapped[list[str]] = mapped_column(default=list)
    config_diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    statistics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    verdict: Mapped[str] = mapped_column(String(32))  # improved|regressed|no_significant_difference|insufficient_data
    evaluator_version: Mapped[str] = mapped_column(String(32))
