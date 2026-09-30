"""Evaluator framework: inputs, results and the evaluator contract.

Evaluators are pure: they receive an :class:`ExperimentView` (spec + per-seed run metrics + baseline runs +
resource usage + status), an :class:`ArtifactBundle` (file *bytes* already loaded by the service, plus metadata
such as artifact version ids and declared checksums) and an :class:`EvalContext` (validated config, success
criteria, statistical plan). They never perform IO and never call models.

Every evaluator has an immutable ``key`` and ``version`` (the registry pins them), a ``kind``, a pydantic
``config_schema`` and returns an :class:`EvaluationResult` whose ``evidence`` records exactly what was checked,
including SHA-256 checksums that the evaluator computed itself over the bytes it read. ``independent`` is
``True`` only when the verdict does not rest on numbers reported by the experiment code itself.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from engines.lab.experiment_spec import ExperimentSpec, MetricSpec, StatisticalPlan, SuccessCriterion

EvaluatorKind = Literal[
    "metric",
    "regression",
    "classification",
    "benchmark",
    "statistical",
    "reproduction",
    "code_quality",
    "resource",
    "custom",
]
SUCCESS_STATUSES = frozenset({"SUCCEEDED", "VERIFICATION_PENDING", "VERIFIED", "COMPLETED"})
INDEPENDENT_SOURCES = frozenset({"platform", "evaluator"})
MAX_CSV_ROWS = 2_000_000
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class EvaluatorConfigError(ValueError):
    """The evaluator configuration does not match its ``config_schema``."""


class UnknownEvaluatorError(KeyError):
    """No evaluator with this key is registered."""


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------
class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRecord(_Model):
    """One experiment run (typically one seed). ``metrics`` are the run's final metric values."""

    run_id: str | None = None
    seed: int | None = None
    status: str = "SUCCEEDED"
    metrics: dict[str, float] = Field(default_factory=dict)
    ignored_metrics: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _numeric_metrics_only(cls, data: Any) -> Any:
        """Run metrics come from untrusted ``metrics.json``: keep numbers (NaN/inf allowed, excluded later),
        record everything else (strings, bools, nested objects) in ``ignored_metrics`` instead of failing."""
        if not isinstance(data, Mapping) or not isinstance(data.get("metrics"), Mapping):
            return data
        kept: dict[str, float] = {}
        ignored = list(data.get("ignored_metrics") or [])
        for key, value in data["metrics"].items():
            if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)):
                kept[str(key)] = float(value)
            else:
                ignored.append(str(key))
        return {**data, "metrics": kept, "ignored_metrics": sorted(set(ignored))}

    @property
    def succeeded(self) -> bool:
        return self.status in SUCCESS_STATUSES


class ResourceUsage(_Model):
    """Platform-measured usage of one compute job (all optional; unknown values stay ``None``)."""

    run_id: str | None = None
    cpu_seconds: float | None = Field(default=None, ge=0)
    wall_seconds: float | None = Field(default=None, ge=0)
    peak_memory_mb: float | None = Field(default=None, ge=0)
    gpu_seconds: float | None = Field(default=None, ge=0)
    requested_cpu: float | None = Field(default=None, gt=0)
    requested_memory_mb: float | None = Field(default=None, gt=0)
    cost_usd: float | None = Field(default=None, ge=0)
    oom_killed: bool = False
    timed_out: bool = False
    exit_code: int | None = None


class ExperimentView(_Model):
    """Read-only view of an experiment version and its runs, assembled by the service."""

    experiment_id: str | None = None
    version_id: str | None = None
    status: str | None = None
    spec: ExperimentSpec | None = None
    runs: list[RunRecord] = Field(default_factory=list)
    baseline_runs: list[RunRecord] = Field(default_factory=list)
    resource_usage: list[ResourceUsage] = Field(default_factory=list)
    metric_sources: dict[str, Literal["platform", "evaluator", "self_reported"]] = Field(default_factory=dict)

    def _runs(self, baseline: bool) -> list[RunRecord]:
        return self.baseline_runs if baseline else self.runs

    def values(self, metric: str, *, baseline: bool = False) -> list[float]:
        """Finite values of ``metric`` from succeeded runs, in run order."""
        out = []
        for run in self._runs(baseline):
            if run.succeeded and metric in run.metrics:
                value = float(run.metrics[metric])
                if math.isfinite(value):
                    out.append(value)
        return out

    def values_by_seed(self, metric: str, *, baseline: bool = False) -> dict[int, float]:
        """``{seed: value}`` for succeeded runs with a seed and a finite value (first run per seed wins)."""
        out: dict[int, float] = {}
        for run in self._runs(baseline):
            if run.succeeded and run.seed is not None and metric in run.metrics and run.seed not in out:
                value = float(run.metrics[metric])
                if math.isfinite(value):
                    out[run.seed] = value
        return out

    def non_finite_count(self, metric: str, *, baseline: bool = False) -> int:
        return sum(
            1
            for run in self._runs(baseline)
            if run.succeeded and metric in run.metrics and not math.isfinite(float(run.metrics[metric]))
        )

    def run_ids(self, *, baseline: bool = False) -> list[str]:
        return [r.run_id for r in self._runs(baseline) if r.run_id and r.succeeded]

    def metric_spec(self, metric: str) -> MetricSpec | None:
        return self.spec.metric(metric) if self.spec else None

    def direction_of(self, metric: str) -> Literal["maximize", "minimize"] | None:
        spec = self.metric_spec(metric)
        return spec.direction if spec else None

    def source_of(self, metric: str) -> str:
        """Measured source of a metric: explicit ``metric_sources`` → spec → conservative ``self_reported``."""
        if metric in self.metric_sources:
            return self.metric_sources[metric]
        spec = self.metric_spec(metric)
        return spec.source if spec else "self_reported"

    @property
    def plan(self) -> StatisticalPlan | None:
        return self.spec.statistical_plan if self.spec else None


class ArtifactBundle(_Model):
    """Files handed to an evaluator. ``metadata[name]`` may carry ``sha256``, ``artifact_version_id``,
    ``dataset_version_id``, ``split``, ``visibility``… — declared checksums are verified, not trusted."""

    files: dict[str, bytes] = Field(default_factory=dict)
    metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def sha256(self, name: str) -> str:
        return hashlib.sha256(self.files[name]).hexdigest()

    def describe(self, name: str) -> dict[str, Any]:
        """Evidence descriptor of one file: name, size, computed sha256 and declared ids."""
        meta = self.metadata.get(name, {})
        entry: dict[str, Any] = {"file": name, "size": len(self.files[name]), "sha256": self.sha256(name)}
        for key in ("artifact_version_id", "dataset_version_id", "split", "visibility"):
            if key in meta:
                entry[key] = meta[key]
        return entry

    def integrity_issues(self, names: Iterable[str] | None = None) -> list[str]:
        """Files whose declared ``sha256`` does not match their bytes (possible tampering or corruption)."""
        issues = []
        for name in names if names is not None else sorted(self.files):
            declared = self.metadata.get(name, {}).get("sha256")
            if name in self.files and declared and str(declared).lower() != self.sha256(name):
                issues.append(f"checksum mismatch for {name}: declared {declared}, computed {self.sha256(name)}")
        return issues

    def text(self, name: str) -> str:
        return self.files[name].decode("utf-8-sig")


class EvalContext(_Model):
    """Evaluation context: the evaluator's config plus the pre-registered criteria and plan."""

    config: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    plan: StatisticalPlan | None = None


# ---------------------------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------------------------
def json_safe(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays to Python types and non-finite floats to ``None``."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    return value


class EvaluationResult(_Model):
    """Outcome of one evaluator run. ``passed`` is ``None`` when the evaluator could not reach a verdict."""

    evaluator_key: str
    evaluator_version: str
    kind: str
    metrics: dict[str, float | dict[str, Any]] = Field(default_factory=dict)
    passed: bool | None
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    independent: bool
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _sanitise(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        data = dict(data)
        metrics: dict[str, Any] = {}
        for key, value in (data.get("metrics") or {}).items():
            clean = json_safe(value)
            if clean is not None:  # undefined scalar metrics are omitted (callers add a warning)
                metrics[str(key)] = clean
        data["metrics"] = metrics
        data["evidence"] = json_safe(list(data.get("evidence") or []))
        data["details"] = json_safe(dict(data.get("details") or {}))
        confidence = json_safe(data.get("confidence", 0.0))
        data["confidence"] = min(1.0, max(0.0, confidence if confidence is not None else 0.0))
        return data


# ---------------------------------------------------------------------------------------------
# Evaluator contract
# ---------------------------------------------------------------------------------------------
@runtime_checkable
class Evaluator(Protocol):
    """Structural type of every evaluator (built-in or plugged in by the evaluation service)."""

    @property
    def key(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def kind(self) -> str: ...

    def evaluate(
        self,
        experiment: ExperimentView,
        artifacts: ArtifactBundle | None = None,
        context: EvalContext | None = None,
    ) -> EvaluationResult: ...


class EvaluatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BaseEvaluator(ABC):
    """Base class for built-in evaluators. Subclasses set the class constants and implement ``_evaluate``."""

    key: ClassVar[str]
    version: ClassVar[str]
    kind: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    config_schema: ClassVar[type[EvaluatorConfig]] = EvaluatorConfig
    independent: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        for attr in ("key", "version", "kind", "name", "description"):
            if not isinstance(getattr(cls, attr, None), str) or not getattr(cls, attr):
                raise TypeError(f"{cls.__name__} must define a non-empty class constant {attr!r}")
        if not _SEMVER.match(cls.version):
            raise TypeError(f"{cls.__name__}.version must be semantic (x.y.z)")

    def parse_config(self, config: Mapping[str, Any] | None) -> Any:
        try:
            return self.config_schema.model_validate(dict(config or {}))
        except ValidationError as exc:
            raise EvaluatorConfigError(f"invalid config for evaluator {self.key!r}: {exc}") from exc

    def evaluate(
        self,
        experiment: ExperimentView,
        artifacts: ArtifactBundle | None = None,
        context: EvalContext | None = None,
    ) -> EvaluationResult:
        """Validate the config and evaluate. Raises :class:`EvaluatorConfigError` for an invalid config;
        problems with the *data* are reported as warnings with ``passed=None``."""
        ctx = context or EvalContext()
        cfg = self.parse_config(ctx.config)
        return self._evaluate(experiment, artifacts or ArtifactBundle(), ctx, cfg)

    @abstractmethod
    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult: ...

    def _result(
        self,
        *,
        passed: bool | None,
        confidence: float,
        metrics: Mapping[str, Any] | None = None,
        warnings: list[str] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        independent: bool | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> EvaluationResult:
        return EvaluationResult(
            evaluator_key=self.key,
            evaluator_version=self.version,
            kind=self.kind,
            metrics=dict(metrics or {}),
            passed=passed,
            confidence=confidence,
            warnings=list(warnings or []),
            evidence=list(evidence or []),
            independent=self.independent if independent is None else independent,
            details=dict(details or {}),
        )

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {
            "key": cls.key,
            "version": cls.version,
            "kind": cls.kind,
            "name": cls.name,
            "description": cls.description,
            "independent": cls.independent,
            "config_schema": cls.config_schema.model_json_schema(),
        }


# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------
def aggregate(values: list[float], how: str, direction: str | None = None) -> float | None:
    """Aggregate per-seed values: mean | median | min | max | worst (direction-aware worst seed)."""
    if not values:
        return None
    if how == "mean":
        return math.fsum(values) / len(values)
    if how == "median":
        return float(np.median(values))
    if how == "min":
        return min(values)
    if how == "max":
        return max(values)
    if how == "worst":
        return min(values) if direction != "minimize" else max(values)
    raise ValueError(f"unknown aggregate {how!r}")


def read_csv(data: bytes, *, max_rows: int = MAX_CSV_ROWS) -> tuple[list[str], list[dict[str, str]]]:
    """Parse CSV bytes (UTF-8, optional BOM) into ``(header, rows)``; raises ``ValueError`` when malformed."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if not header:
        raise ValueError("CSV has no header row")
    if len(set(header)) != len(header):
        raise ValueError("CSV header has duplicate column names")
    reader.fieldnames = header
    rows: list[dict[str, str]] = []
    for row in reader:
        if None in row:
            raise ValueError(f"CSV row {reader.line_num} has more fields than the header")
        rows.append({k: (v or "").strip() for k, v in row.items()})
        if len(rows) > max_rows:
            raise ValueError(f"CSV has more than {max_rows} rows")
    return header, rows


def align_rows(
    left: list[dict[str, str]],
    right: list[dict[str, str]],
    id_column: str | None,
    left_header: list[str],
    right_header: list[str],
) -> tuple[list[tuple[dict[str, str], dict[str, str]]], list[str], bool]:
    """Pair prediction rows with reference rows → ``(pairs, warnings, aligned_ok)``.

    With an ``id_column`` present in both files rows are joined by id (duplicate or unmatched ids produce
    warnings and ``aligned_ok=False``; pairs cover the intersection). Otherwise rows are paired by position,
    which requires equal lengths (else no pairs are produced).
    """
    warnings: list[str] = []
    if id_column and id_column in left_header and id_column in right_header:
        left_ids = [r[id_column] for r in left]
        right_ids = [r[id_column] for r in right]
        ok = True
        if len(set(left_ids)) != len(left_ids) or len(set(right_ids)) != len(right_ids):
            warnings.append(f"duplicate values in id column {id_column!r}")
            ok = False
        right_by_id = {r[id_column]: r for r in right}
        left_by_id = {r[id_column]: r for r in left}
        missing = sorted(set(right_by_id) - set(left_by_id))
        extra = sorted(set(left_by_id) - set(right_by_id))
        if missing or extra:
            warnings.append(
                f"mismatched ids: {len(missing)} reference ids without prediction, "
                f"{len(extra)} predictions without reference"
            )
            ok = False
        pairs = [(left_by_id[i], right_by_id[i]) for i in sorted(set(left_by_id) & set(right_by_id))]
        return pairs, warnings, ok
    if id_column:
        warnings.append(f"id column {id_column!r} not present in both files; aligning rows by position")
    if len(left) != len(right):
        warnings.append(f"row count mismatch: {len(left)} predictions vs {len(right)} references")
        return [], warnings, False
    return list(zip(left, right, strict=True)), warnings, True


def criteria_for(experiment: ExperimentView, context: EvalContext) -> list[SuccessCriterion]:
    """Success criteria from the context, falling back to the spec's pre-registered criteria."""
    if context.success_criteria:
        return list(context.success_criteria)
    return list(experiment.spec.success_criteria) if experiment.spec else []


def plan_for(experiment: ExperimentView, context: EvalContext) -> StatisticalPlan:
    return context.plan or experiment.plan or StatisticalPlan()


def primary_metric_name(experiment: ExperimentView) -> str | None:
    if experiment.spec and experiment.spec.primary_metric:
        return experiment.spec.primary_metric.name
    return None
