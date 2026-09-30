"""ExperimentDesignValidator: deterministic checks that an experiment is well-formed before it may run.

Errors block execution; warnings are recorded with the experiment version and surfaced to reviewers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from engines.lab.experiments.spec import ExperimentSpec

Severity = Literal["error", "warning"]

_REQ_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?==(?P<version>[A-Za-z0-9.!+_-]+)$")
_DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")
_TARGET_LIKE = ("label", "target", "y_true", "ground_truth")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    field: str
    message: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [i.__dict__ for i in self.errors],
            "warnings": [i.__dict__ for i in self.warnings],
        }


@dataclass(frozen=True)
class ResourceLimits:
    max_cpu: float = 8.0
    max_memory_mb: int = 32_768
    max_disk_mb: int = 50_000
    max_gpu_count: int = 0
    max_timeout_seconds: int = 6 * 3600
    allowed_gpu_types: frozenset[str] = frozenset()
    require_digest_pinned_images: bool = False
    allow_network_allowlist: bool = False


@dataclass(frozen=True)
class EnvironmentInfo:
    environment_id: str
    image: str
    packages: Mapping[str, str]  # name(lowercase) → pinned version


_Add = Callable[[ValidationIssue], None]


@dataclass
class ExperimentDesignValidator:
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    environments: Mapping[str, EnvironmentInfo] = field(default_factory=dict)
    known_harnesses: frozenset[str] = frozenset()
    require_baseline: bool = True

    def validate(self, spec: ExperimentSpec) -> ValidationReport:
        report = ValidationReport()
        add = report.issues.append
        self._metrics(spec, add)
        self._baseline(spec, add)
        self._controls(spec, add)
        self._success(spec, add)
        self._resources(spec, add)
        self._dependencies(spec, add)
        self._reproducibility(spec, add)
        self._leakage(spec, add)
        return report

    # -- checks ----------------------------------------------------------------------------------
    def _metrics(self, spec: ExperimentSpec, add: _Add) -> None:
        if not spec.metrics:
            add(ValidationIssue("missing_metrics", "error", "metrics", "At least one metric must be declared"))
            return
        names = [m.name for m in spec.metrics]
        if len(names) != len(set(names)):
            add(ValidationIssue("duplicate_metrics", "error", "metrics", "Metric names must be unique"))
        primaries = [m for m in spec.metrics if m.primary]
        if not primaries:
            add(
                ValidationIssue(
                    "missing_primary_metric", "warning", "metrics", "No primary metric flagged; the first is used"
                )
            )
        elif len(primaries) > 1:
            add(ValidationIssue("multiple_primary_metrics", "error", "metrics", "Exactly one primary metric allowed"))

    def _baseline(self, spec: ExperimentSpec, add: _Add) -> None:
        if spec.baseline is None:
            if self.require_baseline:
                add(
                    ValidationIssue(
                        "missing_baseline", "error", "baseline", "A baseline is required to make comparative claims"
                    )
                )
            return
        if spec.baseline.parameters and spec.parameters and spec.baseline.parameters == spec.parameters:
            add(
                ValidationIssue(
                    "baseline_equals_candidate",
                    "warning",
                    "baseline.parameters",
                    "Baseline and candidate configurations are identical",
                )
            )

    def _controls(self, spec: ExperimentSpec, add: _Add) -> None:
        independent = {v.name for v in spec.variables if v.kind == "independent"}
        control_vars = {v.name for v in spec.variables if v.kind == "control"} | set(spec.controls)
        overlap = independent & control_vars
        if overlap:
            add(
                ValidationIssue(
                    "invalid_controls",
                    "error",
                    "controls",
                    f"Variables cannot be both independent and controlled: {', '.join(sorted(overlap))}",
                )
            )
        if independent and not control_vars:
            add(
                ValidationIssue(
                    "missing_controls",
                    "warning",
                    "controls",
                    "Independent variables are declared but nothing is held constant",
                )
            )
        for var in spec.variables:
            if var.kind == "independent" and len(var.values) == 1:
                add(
                    ValidationIssue(
                        "degenerate_variable",
                        "warning",
                        f"variables.{var.name}",
                        "An independent variable with a single value is not varied",
                    )
                )

    def _success(self, spec: ExperimentSpec, add: _Add) -> None:
        if not spec.success_criteria:
            add(
                ValidationIssue(
                    "unclear_success_criteria", "error", "success_criteria", "Success criteria must be declared"
                )
            )
            return
        for i, crit in enumerate(spec.success_criteria):
            metric = spec.metric(crit.metric)
            path = f"success_criteria[{i}]"
            if metric is None:
                add(
                    ValidationIssue(
                        "metric_mismatch", "error", path, f"Criterion references undeclared metric '{crit.metric}'"
                    )
                )
                continue
            if crit.comparator in ("improves_over_baseline_by", "not_worse_than_baseline_by") and spec.baseline is None:
                add(ValidationIssue("missing_baseline", "error", path, "Baseline-relative criteria need a baseline"))
            if crit.comparator in ("gt", "gte") and metric.direction == "minimize":
                add(
                    ValidationIssue(
                        "metric_mismatch",
                        "warning",
                        path,
                        f"'{metric.name}' is minimized but the criterion requires it to exceed a threshold",
                    )
                )
            if crit.comparator in ("lt", "lte") and metric.direction == "maximize":
                add(
                    ValidationIssue(
                        "metric_mismatch",
                        "warning",
                        path,
                        f"'{metric.name}' is maximized but the criterion requires it to stay below a threshold",
                    )
                )
            if crit.comparator == "improves_over_baseline_by" and crit.threshold < 0:
                add(ValidationIssue("unclear_success_criteria", "error", path, "Improvement threshold must be >= 0"))

    def _resources(self, spec: ExperimentSpec, add: _Add) -> None:
        r = spec.resources
        lim = self.limits
        if r.cpu > lim.max_cpu:
            add(ValidationIssue("impossible_resources", "error", "resources.cpu", f"cpu {r.cpu} > limit {lim.max_cpu}"))
        if r.memory_mb > lim.max_memory_mb:
            add(
                ValidationIssue(
                    "impossible_resources",
                    "error",
                    "resources.memory_mb",
                    f"memory {r.memory_mb}MB > limit {lim.max_memory_mb}MB",
                )
            )
        if r.disk_mb > lim.max_disk_mb:
            add(ValidationIssue("impossible_resources", "error", "resources.disk_mb", "disk request exceeds limit"))
        if r.timeout_seconds > lim.max_timeout_seconds:
            add(
                ValidationIssue(
                    "impossible_resources",
                    "error",
                    "resources.timeout_seconds",
                    f"timeout {r.timeout_seconds}s > limit {lim.max_timeout_seconds}s",
                )
            )
        if r.gpu_count > 0 and r.runtime != "gpu":
            add(
                ValidationIssue("impossible_resources", "error", "resources.runtime", "GPUs requested on a CPU runtime")
            )
        if r.runtime == "gpu" and r.gpu_count == 0:
            add(ValidationIssue("impossible_resources", "error", "resources.gpu_count", "GPU runtime without GPUs"))
        if r.gpu_count > lim.max_gpu_count:
            add(
                ValidationIssue(
                    "impossible_resources",
                    "error",
                    "resources.gpu_count",
                    f"{r.gpu_count} GPUs requested; this deployment allows {lim.max_gpu_count}",
                )
            )
        if r.gpu_count and lim.allowed_gpu_types and r.gpu_type not in lim.allowed_gpu_types:
            add(
                ValidationIssue(
                    "impossible_resources", "error", "resources.gpu_type", f"GPU type '{r.gpu_type}' unavailable"
                )
            )
        if r.network == "allowlist":
            if not r.egress_allowlist:
                add(
                    ValidationIssue(
                        "invalid_network", "error", "resources.egress_allowlist", "Allowlist mode needs hosts"
                    )
                )
            if not lim.allow_network_allowlist:
                add(
                    ValidationIssue(
                        "invalid_network",
                        "error",
                        "resources.network",
                        "This execution backend cannot enforce egress allowlists; use network=none",
                    )
                )

    def _dependencies(self, spec: ExperimentSpec, add: _Add) -> None:
        env = spec.environment
        info = self.environments.get(env.environment_id) if env.environment_id else None
        if env.environment_id and info is None:
            add(
                ValidationIssue(
                    "invalid_environment", "error", "environment.environment_id", "Unknown execution environment"
                )
            )
        if not env.environment_id and not env.image:
            add(ValidationIssue("invalid_environment", "error", "environment", "An environment or image is required"))
        image = info.image if info else env.image
        if image and self.limits.require_digest_pinned_images and not _DIGEST_RE.search(image):
            add(
                ValidationIssue(
                    "missing_reproducibility",
                    "error",
                    "environment.image",
                    "Images must be pinned by digest (image@sha256:...)",
                )
            )
        for dep in env.dependencies:
            match = _REQ_RE.match(dep.strip())
            if not match:
                add(
                    ValidationIssue(
                        "invalid_dependencies",
                        "error",
                        "environment.dependencies",
                        f"'{dep}' must be an exact pin (name==version); URLs/VCS/ranges are not allowed",
                    )
                )
                continue
            if info is not None:
                name = match.group("name").lower().replace("_", "-")
                pinned = {k.lower().replace("_", "-"): v for k, v in info.packages.items()}
                if name not in pinned:
                    add(
                        ValidationIssue(
                            "invalid_dependencies",
                            "error",
                            "environment.dependencies",
                            f"'{name}' is not installed in environment '{info.environment_id}' (network is denied at runtime)",
                        )
                    )
                elif pinned[name] != match.group("version"):
                    add(
                        ValidationIssue(
                            "invalid_dependencies",
                            "error",
                            "environment.dependencies",
                            f"'{name}=={match.group('version')}' conflicts with environment pin {pinned[name]}",
                        )
                    )

    def _reproducibility(self, spec: ExperimentSpec, add: _Add) -> None:
        plan = spec.statistical_plan
        if len(spec.seeds) != len(set(spec.seeds)):
            add(ValidationIssue("missing_reproducibility", "error", "seeds", "Seeds must be unique"))
        if len(spec.seeds) < plan.min_seeds:
            add(
                ValidationIssue(
                    "missing_reproducibility",
                    "error",
                    "seeds",
                    f"{len(spec.seeds)} seed(s) declared; the statistical plan requires at least {plan.min_seeds}",
                )
            )
        if spec.dataset is not None and not spec.dataset.dataset_version_id:
            add(
                ValidationIssue(
                    "missing_reproducibility",
                    "error",
                    "dataset.dataset_version_id",
                    "Experiments must reference an immutable dataset version",
                )
            )
        if spec.harness is None:
            add(
                ValidationIssue(
                    "missing_harness",
                    "error" if spec.reproducibility.require_non_self_reported_metrics else "warning",
                    "harness",
                    "No platform evaluation harness: metrics would be self-reported by generated code",
                )
            )
        elif self.known_harnesses and spec.harness.key not in self.known_harnesses:
            add(ValidationIssue("missing_harness", "error", "harness.key", f"Unknown harness '{spec.harness.key}'"))
        elif spec.harness.key == "self_reported" and spec.reproducibility.require_non_self_reported_metrics:
            add(
                ValidationIssue(
                    "missing_harness",
                    "error",
                    "harness.key",
                    "The 'self_reported' harness cannot satisfy require_non_self_reported_metrics",
                )
            )

    def _leakage(self, spec: ExperimentSpec, add: _Add) -> None:
        ds = spec.dataset
        if ds is None:
            return
        roles = list(ds.splits.values())
        if "test" in roles and "train" in roles:
            names_by_role: dict[str, set[str]] = {}
            for name, role in ds.splits.items():
                names_by_role.setdefault(role, set()).add(name.lower())
            if names_by_role.get("train", set()) & names_by_role.get("test", set()):
                add(ValidationIssue("data_leakage", "error", "dataset.splits", "Train and test share a split"))
        if "test" in roles and "harness_only" not in roles and spec.harness is not None:
            add(
                ValidationIssue(
                    "data_leakage",
                    "warning",
                    "dataset.splits",
                    "Test labels are visible to generated code; mark the evaluation split 'harness_only'",
                )
            )
        if ds.target_column and ds.target_column in ds.feature_columns:
            add(
                ValidationIssue(
                    "data_leakage", "error", "dataset.feature_columns", "The target column is also used as a feature"
                )
            )
        leaky = [c for c in ds.feature_columns if c.lower() in _TARGET_LIKE]
        if leaky:
            add(
                ValidationIssue(
                    "data_leakage",
                    "warning",
                    "dataset.feature_columns",
                    f"Feature columns look like labels: {', '.join(leaky)}",
                )
            )
        tuned_on_test = [
            v.name for v in spec.variables if "test" in v.description.lower() and "tun" in v.description.lower()
        ]
        if tuned_on_test:
            add(
                ValidationIssue(
                    "data_leakage",
                    "warning",
                    "variables",
                    f"Variables appear to be tuned on the test split: {', '.join(tuned_on_test)}",
                )
            )
