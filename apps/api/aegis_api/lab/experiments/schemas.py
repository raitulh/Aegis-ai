"""API contract of the experiments context (design, versions, runs, metrics, comparisons, reproducibility)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from aegis_api.schemas.common import ORMModel
from engines.lab.experiment_spec import ExperimentKind, ExperimentSpec

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
RunRole = Literal["candidate", "baseline", "ablation", "sensitivity", "reproduction", "exploratory"]
RUN_ROLES: tuple[str, ...] = ("candidate", "baseline", "ablation", "sensitivity", "reproduction", "exploratory")


class ExperimentCreate(BaseModel):
    """A new experiment and its first immutable version. The spec is validated by the design validator;
    a failing design is stored as ``DRAFT`` with its validation report (never executed)."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    hypothesis_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    kind: ExperimentKind | None = Field(default=None, description="Must equal spec.kind when given")
    spec: ExperimentSpec
    baseline_experiment_id: uuid.UUID | None = None
    parent_experiment_id: uuid.UUID | None = None
    max_retries: int = Field(default=0, ge=0, le=10)


class ExperimentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: ExperimentSpec
    change_summary: str | None = Field(default=None, max_length=2000)


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_experiment_id: uuid.UUID
    candidate_experiment_id: uuid.UUID
    metric: str | None = Field(default=None, max_length=120)


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------------------------
class ExperimentOut(ORMModel):
    id: str
    workspace_id: str
    project_id: str
    mission_id: str | None = None
    hypothesis_id: str | None = None
    title: str
    kind: str
    status: str
    status_reason: str | None = None
    current_version_id: str | None = None
    baseline_experiment_id: str | None = None
    parent_experiment_id: str | None = None
    strategy_version_id: str | None = None
    retry_count: int
    max_retries: int
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ExperimentVersionOut(ORMModel):
    id: str
    experiment_id: str
    version: int
    spec: dict[str, Any]
    spec_hash: str
    objective: str
    hypothesis_id: str | None = None
    baseline: dict[str, Any]
    dataset_version_ids: list[str]
    metrics: list[Any]
    success_criteria: list[Any]
    statistical_plan: dict[str, Any]
    seeds: list[Any]
    environment_id: str | None = None
    code_snapshot_id: str | None = None
    command: list[str]
    resource_request: dict[str, Any]
    timeout_seconds: int
    expected_cost_usd: Money
    reproducibility: dict[str, Any]
    network_policy: dict[str, Any]
    validation_report: dict[str, Any]
    validation_passed: bool
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    created_at: datetime


class MetricSummaryOut(BaseModel):
    n: int
    mean: float | None = None
    sd: float | None = None
    min: float | None = None
    max: float | None = None


class RunSummaryOut(BaseModel):
    version_id: str | None = None
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    succeeded: int = 0
    terminal: bool = False
    metrics: dict[str, dict[str, MetricSummaryOut]] = Field(
        default_factory=dict, description="source → metric → summary across successful runs"
    )


class ExperimentDetailOut(ExperimentOut):
    current_version: ExperimentVersionOut | None = None
    validation_report: dict[str, Any] | None = None
    run_summary: RunSummaryOut | None = None


class ValidationOut(BaseModel):
    experiment_id: str
    version_id: str
    version: int
    passed: bool
    status: str
    new_version_created: bool
    report: dict[str, Any]


class ExecuteOut(BaseModel):
    experiment_id: str
    version_id: str
    workflow_run_id: str | None = None
    status: str
    approval_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


class ExperimentMetricOut(ORMModel):
    id: str
    experiment_run_id: str
    name: str
    value: float
    step: int | None = None
    split: str | None = None
    source: str
    evaluation_run_id: str | None = None
    created_at: datetime


class ExperimentRunOut(ORMModel):
    id: str
    experiment_id: str
    experiment_version_id: str
    mission_id: str | None = None
    project_id: str
    run_number: int
    run_key: str
    role: str
    seed: int | None = None
    status: str
    status_reason: str | None = None
    compute_job_id: str | None = None
    attempt: int
    exit_code: int | None = None
    metrics: dict[str, Any]
    environment_manifest: dict[str, Any]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    cost_usd: Money
    error: str | None = None
    reproduction_of_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RunArtifactOut(BaseModel):
    artifact_id: str
    name: str
    kind: str
    version_id: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None


class RunJobOut(BaseModel):
    id: str
    status: str
    status_reason: str | None = None
    backend: str
    image: str
    image_digest: str | None = None
    exit_code: int | None = None
    approval_id: str | None = None
    estimated_cost_usd: Money
    cost_usd: Money


class ExperimentRunDetailOut(ExperimentRunOut):
    metric_rows: list[ExperimentMetricOut] = Field(default_factory=list)
    artifacts: list[RunArtifactOut] = Field(default_factory=list)
    job: RunJobOut | None = None


class ComparisonOut(ORMModel):
    id: str
    project_id: str | None = None
    mission_id: str | None = None
    baseline_experiment_id: str
    candidate_experiment_id: str
    baseline_version_id: str
    candidate_version_id: str
    comparison_type: str
    metric: str
    direction: str
    baseline_run_ids: list[str]
    candidate_run_ids: list[str]
    config_diff: dict[str, Any]
    statistics: dict[str, Any]
    verdict: str
    evaluator_version: str
    created_at: datetime
