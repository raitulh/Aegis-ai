"""Structured experiment specification (the contract between design, execution, evaluation and verification).

An experiment version stores exactly one ``ExperimentSpec``. Specs are immutable once a version is created;
changes create a new version. The spec is domain-neutral: AI/ML, simulation, optimization or wet-lab
analysis pipelines all express themselves through metrics, variables, controls, datasets and a harness.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["maximize", "minimize"]
Comparator = Literal["gt", "gte", "lt", "lte", "improves_over_baseline_by", "not_worse_than_baseline_by"]
StatTest = Literal["welch_t", "mann_whitney", "bootstrap", "permutation"]
Correction = Literal["holm", "bonferroni", "none"]
NetworkMode = Literal["none", "allowlist"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaselineSpec(_Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    reference_run_id: str | None = None


class VariableSpec(_Strict):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["independent", "dependent", "control"]
    values: list[Any] = Field(default_factory=list)
    description: str = ""


class DatasetRef(_Strict):
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    checksum: str | None = None
    # split name → role; roles: "train" | "validation" | "test" | "harness_only"
    splits: dict[str, Literal["train", "validation", "test", "harness_only"]] = Field(default_factory=dict)
    target_column: str | None = None
    feature_columns: list[str] = Field(default_factory=list)


class MetricSpec(_Strict):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    direction: Direction
    unit: str | None = None
    primary: bool = False


class SuccessCriterion(_Strict):
    metric: str
    comparator: Comparator
    threshold: float
    relative: bool = False


class StatisticalPlan(_Strict):
    test: StatTest = "welch_t"
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    min_seeds: int = Field(default=3, ge=1, le=1000)
    correction: Correction = "holm"
    min_effect_size: float | None = Field(default=None, description="Minimum |Cohen's d| to count as meaningful")


class AblationSpec(_Strict):
    name: str
    parameter_changes: dict[str, Any]


class SensitivitySpec(_Strict):
    parameter: str
    values: list[Any] = Field(min_length=2)


class EnvironmentSpec(_Strict):
    environment_id: str | None = None
    image: str | None = Field(default=None, description="Container image; must be digest-pinned in production")
    dependencies: list[str] = Field(default_factory=list, description="Pinned requirements (name==version)")


class ResourceRequest(_Strict):
    runtime: Literal["cpu", "gpu"] = "cpu"
    cpu: float = Field(default=1.0, gt=0)
    memory_mb: int = Field(default=1024, gt=0)
    disk_mb: int = Field(default=1024, gt=0)
    gpu_type: str | None = None
    gpu_count: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=600, gt=0)
    network: NetworkMode = "none"
    egress_allowlist: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list, description="Named secrets requested (require approval)")


class ReproducibilityRequirements(_Strict):
    min_reproductions: int = Field(default=1, ge=0, le=10)
    relative_tolerance: float = Field(default=0.05, ge=0, le=1)
    require_non_self_reported_metrics: bool = True
    deterministic_seeds: bool = True


class HarnessSpec(_Strict):
    key: str = Field(description="Registered, versioned evaluation harness (platform-owned, not agent-written)")
    version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CodeSpec(_Strict):
    entrypoint: list[str] = Field(default_factory=lambda: ["python", "main.py"], min_length=1)
    code_artifact_id: str | None = None
    code_sha256: str | None = None


class ExperimentSpec(_Strict):
    objective: str = Field(min_length=3, max_length=4000)
    hypothesis_id: str | None = None
    hypothesis_statement: str | None = None
    domain: str = "general"
    baseline: BaselineSpec | None = None
    method: str = Field(default="", max_length=8000)
    variables: list[VariableSpec] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    dataset: DatasetRef | None = None
    metrics: list[MetricSpec] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    statistical_plan: StatisticalPlan = Field(default_factory=StatisticalPlan)
    seeds: list[int] = Field(default_factory=list)
    ablations: list[AblationSpec] = Field(default_factory=list)
    sensitivity: list[SensitivitySpec] = Field(default_factory=list)
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    resources: ResourceRequest = Field(default_factory=ResourceRequest)
    expected_cost_usd: float | None = Field(default=None, ge=0)
    reproducibility: ReproducibilityRequirements = Field(default_factory=ReproducibilityRequirements)
    harness: HarnessSpec | None = None
    code: CodeSpec = Field(default_factory=CodeSpec)
    parameters: dict[str, Any] = Field(default_factory=dict, description="Candidate configuration under test")

    @property
    def primary_metric(self) -> MetricSpec | None:
        primaries = [m for m in self.metrics if m.primary]
        if primaries:
            return primaries[0]
        return self.metrics[0] if self.metrics else None

    def metric(self, name: str) -> MetricSpec | None:
        return next((m for m in self.metrics if m.name == name), None)

    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
