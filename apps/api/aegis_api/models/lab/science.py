"""Hypotheses, environments, datasets, experiments (immutable versions), runs, execution jobs, artifacts,
evaluator registry, evaluation runs and explicit baseline/candidate comparisons."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class Hypothesis(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "hypotheses"
    __table_args__ = lab_args(Index("ix_lab_hypotheses_mission_status", "mission_id", "status"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    expected_outcome: Mapped[str | None] = mapped_column(Text)
    measurable_prediction: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assumptions: Mapped[list[str]] = mapped_column(default=list)
    novelty_notes: Mapped[str | None] = mapped_column(Text)
    feasibility: Mapped[float | None] = mapped_column(Float)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="generated")
    parent_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("hypotheses", "SET NULL"), nullable=True)
    generated_by_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    critique: Mapped[dict[str, Any]] = mapped_column(default=dict)
    critique_score: Mapped[float | None] = mapped_column(Float)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    outcome_summary: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class HypothesisEvidence(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "hypothesis_evidence"
    __table_args__ = lab_args(
        UniqueConstraint(
            "hypothesis_id", "source_type", "source_id", "relation", name="uq_lab_hypothesis_evidence_quad"
        )
    )

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(lab_fk("hypotheses"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # research_source | evidence | memory | claim | run
    source_id: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(16))  # supports | contradicts
    note: Mapped[str | None] = mapped_column(Text)


class ExecutionEnvironment(IdMixin, TimestampMixin, Base):
    """Registered execution environment (container image + pinned packages). Global when organization_id is NULL."""

    __tablename__ = "environments"
    __table_args__ = lab_args(UniqueConstraint("organization_id", "name", name="uq_lab_environments_org_name"))

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str] = mapped_column(String(500))
    image_digest: Mapped[str | None] = mapped_column(String(100))
    packages: Mapped[dict[str, Any]] = mapped_column(default=dict)
    lockfile_sha256: Mapped[str | None] = mapped_column(String(64))
    runtime: Mapped[str] = mapped_column(String(8), default="cpu")
    status: Mapped[str] = mapped_column(String(16), default="active")


class Dataset(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = lab_args(UniqueConstraint("project_id", "name", name="uq_lab_datasets_project_name"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(1000))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class DatasetVersion(IdMixin, TimestampMixin, OrgMixin, Base):
    """Immutable dataset version: content fields are protected by a database trigger."""

    __tablename__ = "dataset_versions"
    __table_args__ = lab_args(
        UniqueConstraint("dataset_id", "version", name="uq_lab_dataset_versions_ds_version"),
        UniqueConstraint("dataset_id", "checksum", name="uq_lab_dataset_versions_ds_checksum"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(lab_fk("datasets"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("dataset_versions", "SET NULL"), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    schema_info: Mapped[dict[str, Any]] = mapped_column("schema", default=dict)
    ds_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    license: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(1000))
    transformations: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    # [{"name", "split", "role": train|validation|test|harness_only, "sha256", "size_bytes", "storage_key"}]
    files: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    storage_prefix: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="ready")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Experiment(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = lab_args(
        Index("ix_lab_experiments_mission_status", "mission_id", "status"),
        Index("ix_lab_experiments_org_status", "organization_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("hypotheses", "SET NULL"), nullable=True, index=True)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    version_count: Mapped[int] = mapped_column(Integer, default=0)
    designed_by_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("agent_runs", "SET NULL"), nullable=True)
    outcome: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ExperimentVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable experiment configuration (append-only; enforced by trigger)."""

    __tablename__ = "experiment_versions"
    __table_args__ = lab_args(
        UniqueConstraint("experiment_id", "version", name="uq_lab_experiment_versions_exp_version")
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiments"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)
    spec_sha256: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    code_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    code_sha256: Mapped[str | None] = mapped_column(String(64))
    generated_by_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ExperimentRun(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "experiment_runs"
    __table_args__ = lab_args(
        Index("ix_lab_experiment_runs_exp_kind", "experiment_id", "run_kind"),
        Index("ix_lab_experiment_runs_org_status", "organization_id", "status"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_lab_experiment_runs_idem"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiments"))
    experiment_version_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiment_versions"))
    run_kind: Mapped[str] = mapped_column(String(16))
    variant: Mapped[str | None] = mapped_column(String(120))
    seed: Mapped[int] = mapped_column(Integer)
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    execution_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    harness_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reproduction_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("experiment_runs", "SET NULL"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    self_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    resources: Mapped[dict[str, Any]] = mapped_column(default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ExperimentMetric(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "experiment_metrics"
    __table_args__ = lab_args(
        UniqueConstraint("run_id", "name", "source", name="uq_lab_experiment_metrics_run_name_source"),
        Index("ix_lab_experiment_metrics_exp_name", "experiment_id", "name"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiments"))
    run_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiment_runs"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))  # harness | backend | self_reported
    unit: Mapped[str | None] = mapped_column(String(32))


class ExecutionJob(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    """A unit of sandboxed execution (candidate code, harness, quick python tool)."""

    __tablename__ = "compute_jobs"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "idempotency_key", name="uq_lab_compute_jobs_idem"),
        Index("ix_lab_compute_jobs_org_status", "organization_id", "status"),
        Index("ix_lab_compute_jobs_status_heartbeat", "status", "heartbeat_at"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("experiments"), nullable=True)
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("experiment_runs"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # candidate | harness | quick | tool
    backend: Mapped[str] = mapped_column(String(24))
    image: Mapped[str] = mapped_column(String(500))
    image_digest: Mapped[str | None] = mapped_column(String(100))
    command: Mapped[list[str]] = mapped_column(default=list)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resource_request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    network_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    filesystem_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secrets_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    backend_ref: Mapped[str | None] = mapped_column(String(255))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    measured: Mapped[dict[str, Any]] = mapped_column(default=dict)
    artifact_ids: Mapped[list[str]] = mapped_column(default=list)
    logs_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Artifact(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = lab_args(Index("ix_lab_artifacts_project_kind", "project_id", "kind"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("experiment_runs", "SET NULL"), nullable=True, index=True
    )
    execution_job_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("compute_jobs", "SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    retention_class: Mapped[str] = mapped_column(String(16), default="standard")  # standard | evidence | ephemeral
    created_by: Mapped[str | None] = mapped_column(String(160))


class ArtifactVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable blob reference in object storage (content never stored in PostgreSQL)."""

    __tablename__ = "artifact_versions"
    __table_args__ = lab_args(UniqueConstraint("artifact_id", "version", name="uq_lab_artifact_versions_art_version"))

    artifact_id: Mapped[uuid.UUID] = mapped_column(lab_fk("artifacts"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(120))
    scan_status: Mapped[str] = mapped_column(String(24), default="not_scanned")
    av_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    purged_at: Mapped[datetime | None] = mapped_column(nullable=True)


class LabEvaluator(IdMixin, CreatedMixin, Base):
    """Global registry of evaluator versions (fingerprinted; immutable)."""

    __tablename__ = "evaluators"
    __table_args__ = lab_args(UniqueConstraint("key", "version", name="uq_lab_evaluators_key_version"))

    key: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    default_config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class EvaluationRun(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = lab_args(Index("ix_lab_evaluation_runs_exp", "experiment_id", "evaluator_key"))

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiments"))
    experiment_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    run_ids: Mapped[list[str]] = mapped_column(default=list)
    evaluator_key: Mapped[str] = mapped_column(String(64))
    evaluator_version: Mapped[str] = mapped_column(String(24))
    evaluator_fingerprint: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(16))  # pass | fail | inconclusive
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    warnings: Mapped[list[str]] = mapped_column(default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)
    independent: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated_by: Mapped[str] = mapped_column(String(160), default="platform")


class RunComparison(IdMixin, CreatedMixin, OrgMixin, Base):
    """Explicit baseline-vs-candidate comparison, including the configuration diff between them."""

    __tablename__ = "run_comparisons"
    __table_args__ = lab_args(Index("ix_lab_run_comparisons_exp_metric", "experiment_id", "metric"))

    experiment_id: Mapped[uuid.UUID] = mapped_column(lab_fk("experiments"))
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("evaluation_runs", "SET NULL"), nullable=True)
    metric: Mapped[str] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(8))
    baseline_run_ids: Mapped[list[str]] = mapped_column(default=list)
    candidate_run_ids: Mapped[list[str]] = mapped_column(default=list)
    baseline_mean: Mapped[float | None] = mapped_column(Float)
    candidate_mean: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    relative_change: Mapped[float | None] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    test: Mapped[str | None] = mapped_column(String(24))
    p_value: Mapped[float | None] = mapped_column(Float)
    p_adjusted: Mapped[float | None] = mapped_column(Float)
    effect_size: Mapped[float | None] = mapped_column(Float)
    config_diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
