"""Experiment design validator: turns an :class:`ExperimentSpec` into an actionable issue list.

Every issue has a stable ``code`` (below), a ``severity`` (``error`` blocks execution, ``warning`` does not), the
offending ``field`` as a dotted path, a ``message`` and a ``suggestion``. ``passed`` is ``True`` iff there are no
errors. The validator is deterministic and never raises for a structurally valid spec; a raw mapping that fails
the schema yields ``INVALID_SPEC`` issues instead of an exception.

Codes
-----
``INVALID_SPEC``              the mapping does not match the ExperimentSpec schema
``MISSING_BASELINE``          candidate/ablation/sensitivity (or reproduction) without a usable baseline
``MISSING_METRICS``           no metrics declared
``NO_PRIMARY_METRIC``         metrics but none flagged primary
``MULTIPLE_PRIMARY_METRICS``  more than one primary metric
``SELF_REPORTED_PRIMARY_METRIC`` (warning) the primary metric is only self-reported by the experiment code
``INVALID_CONTROL``           control also manipulated as an independent variable, control without value,
                              duplicate controls
``INVALID_VARIABLE``          duplicate variable names; independent variables without levels (warning)
``INCOMPLETE_DESIGN``         ablation/sensitivity experiment without ablations/sensitivity entries
``UNCLEAR_SUCCESS_CRITERIA``  no criteria; criterion on an unmeasured metric; baseline-relative criterion
                              without a baseline or reference value; delta comparator marked absolute;
                              contradictory (unsatisfiable) criteria; primary metric without criterion (warning)
``METRIC_MISMATCH``           criterion metric not in ``metrics``; comparator contradicts the metric direction;
                              hypothesis metric not measured (not primary → warning); duplicate metric names
``IMPOSSIBLE_RESOURCES``      request exceeds limits; GPU requested while disabled; GPU count without type;
                              timeout above the maximum
``IMAGE_NOT_ALLOWED``         image outside ``limits.allowed_images``
``INVALID_DEPENDENCY``        unpinned (``pkg``, ``pkg>=x``, wildcards), URL/VCS, pip options (``-e``,
                              ``--index-url``…), local paths/archives, malformed names or versions
``MISSING_REPRODUCIBILITY``   no seeds; fewer seeds than ``n_seeds``; duplicate seeds; image not digest-pinned
                              (warning; error when ``strict`` or ``require_digest_pin``); no entrypoint; no
                              code source; entrypoint missing from inline files; abbreviated git commit (warning)
``DATA_LEAKAGE``              same dataset version + split used for training and testing/evaluation;
                              evaluator-only split mounted into the experiment; target-like independent
                              variables/features; hyperparameter search with a test split (error without a
                              validation split, warning with one); shuffled temporal split; validation split
                              reused as test (warning)
``UNKNOWN_DATASET``           dataset version or split not in the validation context
``UNKNOWN_EVALUATOR``         evaluator-sourced metric without a known evaluator key
``UNSAFE_MOUNT_PATH``         absolute, ``..``, backslash, ``~``, duplicate mount paths; unsafe inline file
                              paths or entrypoint
``NETWORK_REQUESTED``         (warning) network access requested — needs approval
``STATISTICAL_PLAN_WEAK``     (warning) alpha > 0.1, n_seeds < 3, no correction with several comparisons,
                              CI level < 0.9, no minimum effect size, test unable to reach alpha with n_seeds
"""

from __future__ import annotations

import math
import posixpath
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engines.lab.evaluators.registry import BUILTIN_EVALUATOR_KEYS
from engines.lab.experiment_spec import (
    ABSOLUTE_COMPARATORS,
    BASELINE_ONLY_COMPARATORS,
    DELTA_COMPARATORS,
    ExperimentSpec,
    criterion_contradicts_direction,
)

DESIGN_VALIDATOR_VERSION = "design-validator-1.0.0"

ISSUE_CODES: tuple[str, ...] = (
    "INVALID_SPEC",
    "MISSING_BASELINE",
    "MISSING_METRICS",
    "NO_PRIMARY_METRIC",
    "MULTIPLE_PRIMARY_METRICS",
    "SELF_REPORTED_PRIMARY_METRIC",
    "INVALID_CONTROL",
    "INVALID_VARIABLE",
    "INCOMPLETE_DESIGN",
    "UNCLEAR_SUCCESS_CRITERIA",
    "METRIC_MISMATCH",
    "IMPOSSIBLE_RESOURCES",
    "IMAGE_NOT_ALLOWED",
    "INVALID_DEPENDENCY",
    "MISSING_REPRODUCIBILITY",
    "DATA_LEAKAGE",
    "UNKNOWN_DATASET",
    "UNKNOWN_EVALUATOR",
    "UNSAFE_MOUNT_PATH",
    "NETWORK_REQUESTED",
    "STATISTICAL_PLAN_WEAK",
)

TARGET_LIKE_NAMES = frozenset(
    {
        "label",
        "labels",
        "target",
        "targets",
        "y",
        "y_true",
        "ytrue",
        "true_label",
        "gold",
        "gold_label",
        "ground_truth",
        "groundtruth",
    }
)
HYPERPARAMETER_NAMES = frozenset(
    {
        "lr",
        "learning_rate",
        "batch_size",
        "epochs",
        "num_epochs",
        "dropout",
        "weight_decay",
        "momentum",
        "optimizer",
        "hidden_size",
        "hidden_dim",
        "num_layers",
        "n_layers",
        "depth",
        "max_depth",
        "n_estimators",
        "num_leaves",
        "regularization",
        "l2",
        "l1",
        "c",
        "gamma",
        "warmup_steps",
        "temperature",
        "k",
        "n_neighbors",
    }
)
_SEARCH_WORDS = ("hparam", "hyperparam", "search", "tune", "tuning", "sweep", "grid")
_TEMPORAL_SPLITS = frozenset({"temporal", "time", "time_series", "timeseries", "chronological"})
_PEP508_NAME = re.compile(r"^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])(\[[A-Za-z0-9._,\s-]+\])?$")
_PEP440_VERSION = re.compile(
    r"^(\d+!)?\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?(\+[a-z0-9]+(\.[a-z0-9]+)*)?$", re.IGNORECASE
)
_HASH_OPTION = re.compile(r"^--hash=sha256:[a-f0-9]{64}$")
_VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")
_ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".tgz", ".zip", ".tar.bz2", ".egg")


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning"]
    field: str
    message: str
    suggestion: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[Issue]
    summary: str
    validator_version: str = DESIGN_VALIDATOR_VERSION

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def has(self, code: str, severity: str | None = None) -> bool:
        return any(i.code == code and (severity is None or i.severity == severity) for i in self.issues)


class ValidationLimits(BaseModel):
    """Platform limits (the service builds this from settings / org quotas)."""

    model_config = ConfigDict(extra="forbid")

    max_cpu: float = Field(default=4.0, gt=0)
    max_memory_mb: int = Field(default=8192, gt=0)
    max_disk_mb: int = Field(default=10240, gt=0)
    max_timeout_seconds: int = Field(default=3600, gt=0)
    gpu_enabled: bool = False
    allowed_images: list[str] = Field(default_factory=list)
    strict: bool = False


class SplitInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    visibility: Literal["experiment", "evaluator_only"] = "experiment"


class DatasetVersionInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    splits: dict[str, SplitInfo] = Field(default_factory=dict)


class ValidationContext(BaseModel):
    """What the validator may know beyond the spec. ``known_evaluators`` defaults to the built-in keys."""

    model_config = ConfigDict(extra="forbid")

    dataset_versions: dict[str, DatasetVersionInfo] = Field(default_factory=dict)
    hypothesis_metric: str | None = None
    known_evaluators: set[str] = Field(default_factory=set)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def unsafe_relative_path(path: str) -> str | None:
    """Return why ``path`` is not a safe relative path (``None`` when it is safe)."""
    if not path or not path.strip():
        return "empty path"
    if "\x00" in path:
        return "contains a NUL byte"
    if "\\" in path:
        return "contains a backslash"
    if path.startswith(("/", "~")):
        return "is absolute or home-relative"
    if re.match(r"^[A-Za-z]:", path):
        return "has a drive letter"
    parts = path.split("/")
    if ".." in parts:
        return "contains '..'"
    normalised = posixpath.normpath(path)
    if normalised.startswith("..") or normalised == ".":
        return "escapes or equals the base directory"
    return None


def check_dependency(dep: str) -> str | None:
    """Return why a dependency string is not an exactly pinned PyPI requirement (``None`` when valid).

    Accepted: ``name[extras]==version`` or ``===version``, optionally followed by ``--hash=sha256:<hex>``
    options and/or a ``; marker``.
    """
    text = dep.strip()
    if not text:
        return "empty dependency"
    if text.startswith("-"):
        return "pip options (-e, -r, --index-url, --extra-index-url, --find-links …) are not allowed"
    lowered = text.lower()
    if "://" in text or lowered.startswith(_VCS_PREFIXES) or " @ " in text or "@" in text.split(";")[0]:
        return "URL, VCS or direct references are not allowed"
    if text.startswith((".", "/", "~", "file:")) or "\\" in text or lowered.endswith(_ARCHIVE_SUFFIXES):
        return "local paths and archives are not allowed"
    requirement, _, marker = text.partition(";")
    if marker and ("://" in marker or "@" in marker):
        return "invalid environment marker"
    # pip accepts "numpy == 2.1.3" and "pkg[a, b]==1": normalise that whitespace before tokenising.
    requirement = re.sub(r"\s*(===|==|>=|<=|!=|~=|>|<)\s*", r"\1", requirement)
    requirement = re.sub(r"\[([^\]]*)\]", lambda m: "[" + re.sub(r"\s+", "", m.group(1)) + "]", requirement)
    tokens = requirement.split()
    if not tokens:
        return "empty requirement"
    spec, options = tokens[0], tokens[1:]
    for option in options:
        if not option.startswith("--"):
            return f"malformed requirement (unexpected text {option!r}); package names contain no spaces"
        if not _HASH_OPTION.match(option):
            return f"unsupported requirement option {option!r} (only --hash=sha256:<hex>)"
    match = re.match(r"^([^<>=!~\s]+)\s*(?:(===|==|>=|<=|!=|~=|>|<)\s*([^,]*))?(,.*)?$", spec)
    if match is None:
        return "malformed requirement"
    name, operator, version, extra = match.group(1), match.group(2), match.group(3) or "", match.group(4)
    if not _PEP508_NAME.match(name):
        return f"malformed package name {name!r}"
    if operator is None:
        return "unpinned: use name==version"
    if operator not in ("==", "===") or extra:
        return f"not an exact pin ({operator}{version}{extra or ''}): use name==version"
    if "*" in version:
        return "wildcard versions are not exact pins"
    if not version or (operator == "==" and not _PEP440_VERSION.match(version)):
        return f"malformed version {version!r}"
    return None


def _normalise_name(dep: str) -> str:
    head = re.split(r"[\s<>=!~;\[]", dep.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def _mwu_min_two_sided_p(n1: int, n2: int) -> float:
    """Smallest achievable exact two-sided Mann–Whitney p-value: 2 / C(n1 + n2, n1)."""
    return min(1.0, 2.0 / math.comb(n1 + n2, n1))


# ---------------------------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------------------------
class ExperimentDesignValidator:
    """Validate experiment designs against scientific-method rules and platform limits."""

    def __init__(self, limits: ValidationLimits | None = None) -> None:
        self.limits = limits or ValidationLimits()

    def validate(
        self,
        spec: ExperimentSpec | Mapping[str, Any],
        context: ValidationContext | Mapping[str, Any] | None = None,
    ) -> ValidationReport:
        ctx = (
            context if isinstance(context, ValidationContext) else ValidationContext.model_validate(dict(context or {}))
        )
        if not isinstance(spec, ExperimentSpec):
            try:
                spec = ExperimentSpec.model_validate(dict(spec))
            except ValidationError as exc:
                issues = [
                    Issue(
                        code="INVALID_SPEC",
                        severity="error",
                        field=".".join(str(p) for p in err["loc"]) or "spec",
                        message=err["msg"],
                        suggestion="fix the specification so it matches the ExperimentSpec schema",
                    )
                    for err in exc.errors()
                ]
                return self._report(issues)
        run = _Run(spec, ctx, self.limits)
        run.check_all()
        return self._report(run.issues)

    @staticmethod
    def _report(issues: list[Issue]) -> ValidationReport:
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        if not issues:
            summary = "Design passed validation with no issues."
        else:
            codes = sorted({i.code for i in issues})
            state = "failed" if errors else "passed with warnings"
            summary = f"Design {state}: {len(errors)} error(s), {len(warnings)} warning(s) ({', '.join(codes)})."
        return ValidationReport(passed=not errors, issues=issues, summary=summary)


class _Run:
    """One validation pass (keeps the issue list and shared lookups)."""

    def __init__(self, spec: ExperimentSpec, ctx: ValidationContext, limits: ValidationLimits) -> None:
        self.spec = spec
        self.ctx = ctx
        self.limits = limits
        self.issues: list[Issue] = []
        self.metric_names = {m.name for m in spec.metrics}

    def add(self, code: str, severity: Literal["error", "warning"], field: str, message: str, suggestion: str) -> None:
        self.issues.append(Issue(code=code, severity=severity, field=field, message=message, suggestion=suggestion))

    def check_all(self) -> None:
        self.baseline()
        self.metrics()
        self.variables_and_controls()
        self.design_completeness()
        self.success_criteria()
        self.resources()
        self.dependencies()
        self.reproducibility()
        self.datasets()
        self.leakage()
        self.mount_paths()
        self.network()
        self.statistical_plan()

    # -------------------------------------------------------------------------------------------
    def baseline(self) -> None:
        spec, b = self.spec, self.spec.baseline
        if spec.kind in ("candidate", "ablation", "sensitivity") and b.kind == "none":
            self.add(
                "MISSING_BASELINE",
                "error",
                "baseline",
                f"a {spec.kind} experiment needs a baseline to compare against",
                "reference a baseline experiment (baseline.kind='experiment') or justified reference values",
            )
        if spec.kind == "reproduction" and (b.kind != "experiment" or not b.experiment_id):
            self.add(
                "MISSING_BASELINE",
                "error",
                "baseline.experiment_id",
                "a reproduction must reference the original experiment",
                "set baseline.kind='experiment' and baseline.experiment_id to the experiment being reproduced",
            )
        if b.kind == "experiment" and not b.experiment_id:
            self.add(
                "MISSING_BASELINE",
                "error",
                "baseline.experiment_id",
                "baseline.kind is 'experiment' but no experiment_id is given",
                "set baseline.experiment_id",
            )
        if b.kind == "reference_values":
            if not b.reference_metrics:
                self.add(
                    "MISSING_BASELINE",
                    "error",
                    "baseline.reference_metrics",
                    "reference-value baseline without any values",
                    "provide reference_metrics for the compared metrics",
                )
            if not b.justification:
                self.add(
                    "MISSING_BASELINE",
                    "warning",
                    "baseline.justification",
                    "reference values have no justification or provenance",
                    "cite where the reference values come from (paper, prior run, benchmark)",
                )
            for name, value in b.reference_metrics.items():
                if not math.isfinite(value):
                    self.add(
                        "MISSING_BASELINE",
                        "error",
                        f"baseline.reference_metrics.{name}",
                        "non-finite reference value",
                        "use finite values",
                    )

    def metrics(self) -> None:
        spec = self.spec
        if not spec.metrics:
            self.add("MISSING_METRICS", "error", "metrics", "no metrics are declared", "declare at least one metric")
            return
        seen: set[str] = set()
        for i, metric in enumerate(spec.metrics):
            if metric.name in seen:
                self.add(
                    "METRIC_MISMATCH",
                    "error",
                    f"metrics.{i}.name",
                    f"duplicate metric {metric.name!r}",
                    "declare each metric once",
                )
            seen.add(metric.name)
            if metric.source == "evaluator":
                known = self.ctx.known_evaluators or set(BUILTIN_EVALUATOR_KEYS)
                if not metric.evaluator_key:
                    self.add(
                        "UNKNOWN_EVALUATOR",
                        "error",
                        f"metrics.{i}.evaluator_key",
                        f"metric {metric.name!r} is evaluator-sourced but names no evaluator",
                        "set evaluator_key to a registered evaluator",
                    )
                elif metric.evaluator_key not in known:
                    self.add(
                        "UNKNOWN_EVALUATOR",
                        "error",
                        f"metrics.{i}.evaluator_key",
                        f"unknown evaluator {metric.evaluator_key!r}",
                        f"use one of: {', '.join(sorted(known))}",
                    )
        primaries = [m for m in spec.metrics if m.primary]
        if not primaries:
            self.add(
                "NO_PRIMARY_METRIC",
                "error",
                "metrics",
                "no primary metric: the success decision is ambiguous",
                "flag exactly one metric as primary",
            )
        elif len(primaries) > 1:
            self.add(
                "MULTIPLE_PRIMARY_METRICS",
                "error",
                "metrics",
                f"{len(primaries)} primary metrics ({', '.join(m.name for m in primaries)})",
                "flag exactly one metric as primary; treat the others as secondary",
            )
        elif primaries[0].source == "self_reported":
            self.add(
                "SELF_REPORTED_PRIMARY_METRIC",
                "warning",
                "metrics",
                f"primary metric {primaries[0].name!r} is only self-reported by the experiment code",
                "have an independent evaluator (e.g. classification/regression) recompute it",
            )
        hyp = self.ctx.hypothesis_metric
        if hyp is not None:
            if hyp not in self.metric_names:
                self.add(
                    "METRIC_MISMATCH",
                    "error",
                    "metrics",
                    f"the hypothesis is about {hyp!r} but the experiment does not measure it",
                    f"add {hyp!r} to metrics (as the primary metric)",
                )
            elif primaries and primaries[0].name != hyp:
                self.add(
                    "METRIC_MISMATCH",
                    "warning",
                    "metrics",
                    f"the hypothesis metric {hyp!r} is not the primary metric",
                    f"make {hyp!r} the primary metric",
                )

    def variables_and_controls(self) -> None:
        spec = self.spec
        independent = {v.name for v in spec.variables if v.role == "independent"}
        seen_vars: set[str] = set()
        for i, var in enumerate(spec.variables):
            if var.name in seen_vars:
                self.add(
                    "INVALID_VARIABLE",
                    "error",
                    f"variables.{i}.name",
                    f"duplicate variable {var.name!r}",
                    "declare each variable once",
                )
            seen_vars.add(var.name)
            if var.role == "control" and var.value is None:
                self.add(
                    "INVALID_CONTROL",
                    "error",
                    f"variables.{i}.value",
                    f"control variable {var.name!r} has no fixed value",
                    "set the value the variable is held at",
                )
            if var.role == "independent" and not var.values and spec.kind not in ("baseline", "reproduction"):
                self.add(
                    "INVALID_VARIABLE",
                    "warning",
                    f"variables.{i}.values",
                    f"independent variable {var.name!r} lists no levels",
                    "list the values the variable is manipulated over",
                )
        seen_controls: set[str] = set()
        for i, control in enumerate(spec.controls):
            if control.name in seen_controls:
                self.add(
                    "INVALID_CONTROL",
                    "error",
                    f"controls.{i}.name",
                    f"duplicate control {control.name!r}",
                    "declare each control once",
                )
            seen_controls.add(control.name)
            if control.name in independent:
                self.add(
                    "INVALID_CONTROL",
                    "error",
                    f"controls.{i}.name",
                    f"{control.name!r} is both a control and an independent variable",
                    "a controlled variable must be held constant; remove it from one of the lists",
                )
            if control.value is None:
                self.add(
                    "INVALID_CONTROL",
                    "error",
                    f"controls.{i}.value",
                    f"control {control.name!r} has no value",
                    "set the value the control is held at",
                )

    def design_completeness(self) -> None:
        spec = self.spec
        if spec.kind == "ablation" and not spec.ablations:
            self.add(
                "INCOMPLETE_DESIGN",
                "error",
                "ablations",
                "ablation experiment without ablations",
                "list the components removed or changed",
            )
        if spec.kind == "sensitivity" and not spec.sensitivity:
            self.add(
                "INCOMPLETE_DESIGN",
                "error",
                "sensitivity",
                "sensitivity experiment without parameters",
                "list parameters and the values to sweep",
            )
        for i, entry in enumerate(spec.sensitivity):
            if len(entry.values) < 2:
                self.add(
                    "INCOMPLETE_DESIGN",
                    "warning",
                    f"sensitivity.{i}.values",
                    f"sensitivity of {entry.parameter!r} over a single value",
                    "sweep at least two values",
                )

    def success_criteria(self) -> None:
        spec = self.spec
        if not spec.success_criteria:
            self.add(
                "UNCLEAR_SUCCESS_CRITERIA",
                "error",
                "success_criteria",
                "no pre-registered success criteria",
                "state measurable criteria (metric, comparator, threshold) before running",
            )
            return
        has_baseline = spec.baseline.kind != "none"
        bounds: dict[str, list[tuple[str, float]]] = {}
        for i, crit in enumerate(spec.success_criteria):
            f = f"success_criteria.{i}"
            metric = spec.metric(crit.metric)
            if metric is None:
                self.add(
                    "METRIC_MISMATCH",
                    "error",
                    f"{f}.metric",
                    f"criterion metric {crit.metric!r} is not declared in metrics",
                    "declare the metric or fix the criterion",
                )
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    f"{f}.metric",
                    f"criterion on {crit.metric!r} cannot be evaluated: the metric is not measured",
                    "only use declared metrics in success criteria",
                )
            elif criterion_contradicts_direction(crit, metric.direction):
                self.add(
                    "METRIC_MISMATCH",
                    "error",
                    f"{f}.comparator",
                    f"{crit.comparator} rewards moving {crit.metric!r} against its direction ({metric.direction})",
                    "use a comparator consistent with the metric direction",
                )
            if crit.comparator in DELTA_COMPARATORS and crit.relative_to == "absolute":
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    f"{f}.relative_to",
                    f"{crit.comparator} is a difference to the baseline but relative_to is 'absolute'",
                    "set relative_to='baseline' or use gt/gte/lt/lte for absolute thresholds",
                )
            baseline_relative = crit.relative_to == "baseline" or crit.comparator in BASELINE_ONLY_COMPARATORS
            if baseline_relative and not has_baseline:
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    f,
                    "criterion is relative to a baseline but the experiment has none",
                    "add a baseline or express the criterion as an absolute threshold",
                )
            elif (
                baseline_relative
                and spec.baseline.kind == "reference_values"
                and crit.metric not in spec.baseline.reference_metrics
            ):
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    f,
                    f"no reference value for {crit.metric!r} to compare against",
                    f"add baseline.reference_metrics[{crit.metric!r}]",
                )
            if crit.comparator == "relative_improvement_gte" and spec.baseline.reference_metrics.get(crit.metric) == 0:
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    f,
                    "relative improvement over a zero reference value is undefined",
                    "use a delta comparator",
                )
            if crit.comparator in ABSOLUTE_COMPARATORS and crit.relative_to == "absolute":
                bounds.setdefault(crit.metric, []).append((crit.comparator, crit.threshold))
        for metric_name, items in bounds.items():
            lows = [(t, op == "gt") for op, t in items if op in ("gt", "gte")]
            highs = [(t, op == "lt") for op, t in items if op in ("lt", "lte")]
            if not lows or not highs:
                continue
            lo = max(t for t, _ in lows)
            hi = min(t for t, _ in highs)
            lo_strict = any(strict for t, strict in lows if t == lo)
            hi_strict = any(strict for t, strict in highs if t == hi)
            if lo > hi or (lo == hi and (lo_strict or hi_strict)):
                self.add(
                    "UNCLEAR_SUCCESS_CRITERIA",
                    "error",
                    "success_criteria",
                    f"criteria on {metric_name!r} can never be satisfied together (lower bound {lo:g}, upper bound {hi:g})",
                    "fix the contradictory thresholds",
                )
        primary = spec.primary_metric
        if primary is not None and all(c.metric != primary.name for c in spec.success_criteria):
            self.add(
                "UNCLEAR_SUCCESS_CRITERIA",
                "warning",
                "success_criteria",
                f"no success criterion on the primary metric {primary.name!r}",
                "add a criterion for the primary metric",
            )

    def resources(self) -> None:
        r, lim = self.spec.resources, self.limits
        checks = (
            ("resources.cpu", r.cpu > lim.max_cpu, f"cpu {r.cpu:g} exceeds the limit {lim.max_cpu:g}"),
            (
                "resources.memory_mb",
                r.memory_mb > lim.max_memory_mb,
                f"memory {r.memory_mb} MB exceeds the limit {lim.max_memory_mb} MB",
            ),
            (
                "resources.disk_mb",
                r.disk_mb > lim.max_disk_mb,
                f"disk {r.disk_mb} MB exceeds the limit {lim.max_disk_mb} MB",
            ),
            (
                "timeout_seconds",
                self.spec.timeout_seconds > lim.max_timeout_seconds,
                f"timeout {self.spec.timeout_seconds}s exceeds the maximum {lim.max_timeout_seconds}s",
            ),
        )
        for field, bad, message in checks:
            if bad:
                self.add(
                    "IMPOSSIBLE_RESOURCES", "error", field, message, "reduce the request to within platform limits"
                )
        if r.gpu_count > 0 and not lim.gpu_enabled:
            self.add(
                "IMPOSSIBLE_RESOURCES",
                "error",
                "resources.gpu_count",
                "GPUs requested but GPU execution is disabled",
                "run on CPU or ask an administrator to enable GPUs",
            )
        if r.gpu_count > 0 and not r.gpu_type:
            self.add(
                "IMPOSSIBLE_RESOURCES",
                "error",
                "resources.gpu_type",
                "gpu_count > 0 without gpu_type",
                "name the GPU type",
            )
        if r.gpu_type and r.gpu_count == 0:
            self.add(
                "IMPOSSIBLE_RESOURCES",
                "warning",
                "resources.gpu_type",
                "gpu_type set but gpu_count is 0",
                "set gpu_count or remove gpu_type",
            )
        if self.limits.allowed_images:
            image = self.spec.environment.image
            repo = image.split("@", 1)[0]
            repo_no_tag = repo.rsplit(":", 1)[0] if ":" in repo.rsplit("/", 1)[-1] else repo
            if not any(
                image == allowed or repo == allowed or repo_no_tag == allowed for allowed in self.limits.allowed_images
            ):
                self.add(
                    "IMAGE_NOT_ALLOWED",
                    "error",
                    "environment.image",
                    f"image {image!r} is not in the allowed image list",
                    f"use one of: {', '.join(self.limits.allowed_images)}",
                )

    def dependencies(self) -> None:
        seen: dict[str, str] = {}
        for i, dep in enumerate(self.spec.environment.dependencies):
            problem = check_dependency(dep)
            if problem:
                self.add(
                    "INVALID_DEPENDENCY",
                    "error",
                    f"environment.dependencies.{i}",
                    f"{dep!r}: {problem}",
                    "pin an exact PyPI release, e.g. numpy==2.1.3",
                )
                continue
            name = _normalise_name(dep)
            if name in seen and seen[name] != dep.strip():
                self.add(
                    "INVALID_DEPENDENCY",
                    "error",
                    f"environment.dependencies.{i}",
                    f"conflicting pins for {name!r}: {seen[name]!r} and {dep!r}",
                    "keep exactly one pin per package",
                )
            elif name in seen:
                self.add(
                    "INVALID_DEPENDENCY",
                    "warning",
                    f"environment.dependencies.{i}",
                    f"duplicate dependency {dep!r}",
                    "remove the duplicate",
                )
            seen[name] = dep.strip()

    def reproducibility(self) -> None:
        spec = self.spec
        plan = spec.statistical_plan
        if not spec.seeds:
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "seeds",
                "no seeds declared",
                f"declare at least {plan.n_seeds} seeds",
            )
        elif len(set(spec.seeds)) < plan.n_seeds:
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "seeds",
                f"{len(set(spec.seeds))} distinct seed(s) declared but the plan needs {plan.n_seeds}",
                "declare as many distinct seeds as statistical_plan.n_seeds",
            )
        if len(set(spec.seeds)) != len(spec.seeds):
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "seeds",
                "duplicate seeds",
                "use distinct seeds (duplicates are not replicates)",
            )
        if not spec.environment.is_digest_pinned:
            strict = self.limits.strict or spec.reproducibility.require_digest_pin
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error" if strict else "warning",
                "environment.image_digest",
                f"image {spec.environment.image!r} is not pinned by digest",
                "pin the image with image_digest='sha256:…' or image@sha256:…",
            )
        code = spec.code
        if not code.entrypoint and not spec.command:
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "code.entrypoint",
                "no code entrypoint or command",
                "set code.entrypoint (e.g. 'train.py') or command",
            )
        if not code.has_source:
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "code",
                "no code source (inline files, code snapshot or git commit)",
                "provide code.files, code.code_snapshot_id or code.git",
            )
        elif (
            code.entrypoint
            and code.files
            and not code.code_snapshot_id
            and not code.git
            and code.entrypoint not in code.files
        ):
            self.add(
                "MISSING_REPRODUCIBILITY",
                "error",
                "code.entrypoint",
                f"entrypoint {code.entrypoint!r} is not among the inline files",
                "add the entrypoint file or fix its path",
            )
        if code.git is not None and len(code.git.commit) < 40:
            self.add(
                "MISSING_REPRODUCIBILITY",
                "warning",
                "code.git.commit",
                "abbreviated git commit hash",
                "use the full 40-character commit SHA",
            )

    def datasets(self) -> None:
        for i, use in enumerate(self.spec.datasets):
            info = self.ctx.dataset_versions.get(use.dataset_version_id)
            if info is None:
                self.add(
                    "UNKNOWN_DATASET",
                    "error",
                    f"datasets.{i}.dataset_version_id",
                    f"dataset version {use.dataset_version_id!r} is unknown",
                    "reference an existing dataset version of this project",
                )
                continue
            if use.split is not None and info.splits and use.split not in info.splits:
                self.add(
                    "UNKNOWN_DATASET",
                    "error",
                    f"datasets.{i}.split",
                    f"split {use.split!r} does not exist in dataset version {use.dataset_version_id!r}",
                    f"use one of: {', '.join(sorted(info.splits))}",
                )

    def leakage(self) -> None:
        spec = self.spec
        uses = spec.datasets
        for i, use in enumerate(uses):
            info = self.ctx.dataset_versions.get(use.dataset_version_id)
            if info is None:
                continue
            if (
                use.split is not None
                and use.split in info.splits
                and info.splits[use.split].visibility == "evaluator_only"
            ):
                self.add(
                    "DATA_LEAKAGE",
                    "error",
                    f"datasets.{i}.split",
                    f"split {use.split!r} is evaluator-only and must not be mounted into the experiment",
                    "leave held-out labels to an independent evaluator",
                )
            if use.split is None and any(s.visibility == "evaluator_only" for s in info.splits.values()):
                self.add(
                    "DATA_LEAKAGE",
                    "error",
                    f"datasets.{i}.split",
                    "the whole dataset version is mounted but it contains evaluator-only splits",
                    "mount only the experiment-visible splits",
                )
        training = {"train"}
        testing = {"test", "eval"}
        for i, a in enumerate(uses):
            for j in range(i + 1, len(uses)):
                b = uses[j]
                if a.dataset_version_id != b.dataset_version_id:
                    continue
                overlap = a.split is None or b.split is None or a.split == b.split
                if not overlap:
                    continue
                roles = {a.role, b.role}
                if roles & training and roles & testing:
                    self.add(
                        "DATA_LEAKAGE",
                        "error",
                        f"datasets.{j}",
                        f"dataset {a.dataset_version_id!r} split {a.split or b.split or '(all)'!r} is used for both "
                        f"{a.role} and {b.role}",
                        "train and test on disjoint splits",
                    )
                elif "validation" in roles and roles & testing:
                    self.add(
                        "DATA_LEAKAGE",
                        "warning",
                        f"datasets.{j}",
                        "the same split is used for validation (model selection) and testing",
                        "keep the test split untouched until the final evaluation",
                    )
        for i, var in enumerate(spec.variables):
            if var.role == "independent" and var.name.lower() in TARGET_LIKE_NAMES:
                self.add(
                    "DATA_LEAKAGE",
                    "error",
                    f"variables.{i}.name",
                    f"independent variable {var.name!r} looks like the prediction target",
                    "never feed the target to the model as an input",
                )
        params = spec.parameters
        features = params.get("features")
        if isinstance(features, list):
            target = params.get("target") or params.get("label") or params.get("target_column")
            for feature in features:
                if isinstance(feature, str) and (
                    feature.lower() in TARGET_LIKE_NAMES or (isinstance(target, str) and feature == target)
                ):
                    self.add(
                        "DATA_LEAKAGE",
                        "error",
                        "parameters.features",
                        f"feature {feature!r} is the prediction target",
                        "remove the target from the feature list",
                    )
        split_kind = next(
            (
                str(params[k]).lower()
                for k in ("split", "split_strategy", "split_type")
                if isinstance(params.get(k), str)
            ),
            None,
        )
        if split_kind in _TEMPORAL_SPLITS and params.get("shuffle") is True:
            self.add(
                "DATA_LEAKAGE",
                "error",
                "parameters.shuffle",
                "temporal split with shuffle=true leaks future information into training",
                "split chronologically without shuffling",
            )
        if self._hyperparameter_search():
            roles = {u.role for u in uses}
            if "test" in roles:
                has_validation = "validation" in roles
                self.add(
                    "DATA_LEAKAGE",
                    "warning" if has_validation else "error",
                    "datasets",
                    "hyperparameter search with a test split"
                    + ("" if has_validation else " and no validation split: selection would happen on test data"),
                    "select hyperparameters on a validation split and evaluate the chosen configuration once on test",
                )

    def _hyperparameter_search(self) -> bool:
        spec = self.spec
        if spec.sensitivity and any(s.parameter.lower() in HYPERPARAMETER_NAMES for s in spec.sensitivity):
            return True
        for ablation in spec.ablations:
            name = ablation.name.lower()
            if any(word in name for word in _SEARCH_WORDS):
                return True
            if any(str(k).lower() in HYPERPARAMETER_NAMES for k in ablation.changes):
                return True
        return any(
            str(k).lower() in ("hyperparameter_search", "search_space", "param_grid", "sweep") for k in spec.parameters
        )

    def mount_paths(self) -> None:
        seen: dict[str, int] = {}
        for i, use in enumerate(self.spec.datasets):
            path = use.effective_mount_path
            problem = unsafe_relative_path(path)
            if problem:
                self.add(
                    "UNSAFE_MOUNT_PATH",
                    "error",
                    f"datasets.{i}.mount_path",
                    f"mount path {path!r} {problem}",
                    "use a relative path below /workspace/input without '..'",
                )
                continue
            normalised = posixpath.normpath(path)
            if normalised in seen:
                self.add(
                    "UNSAFE_MOUNT_PATH",
                    "error",
                    f"datasets.{i}.mount_path",
                    f"mount path {path!r} collides with datasets.{seen[normalised]}",
                    "give every dataset its own mount path",
                )
            seen[normalised] = i
        for path in self.spec.code.files:
            problem = unsafe_relative_path(path)
            if problem:
                self.add(
                    "UNSAFE_MOUNT_PATH",
                    "error",
                    f'code.files["{path}"]',
                    f"inline file path {path!r} {problem}",
                    "use relative paths without '..'",
                )
        entry = self.spec.code.entrypoint
        if entry:
            problem = unsafe_relative_path(entry)
            if problem:
                self.add(
                    "UNSAFE_MOUNT_PATH",
                    "error",
                    "code.entrypoint",
                    f"entrypoint {entry!r} {problem}",
                    "use a relative path",
                )

    def network(self) -> None:
        net = self.spec.network
        if net.mode == "allowlist":
            self.add(
                "NETWORK_REQUESTED",
                "warning",
                "network",
                f"network egress requested to {', '.join(net.hosts) or '(no hosts)'}; requires approval",
                "avoid network access or justify each host",
            )
        elif net.hosts:
            self.add(
                "NETWORK_REQUESTED",
                "warning",
                "network.hosts",
                "hosts are listed but network mode is 'none'",
                "remove the hosts",
            )

    def statistical_plan(self) -> None:
        plan = self.spec.statistical_plan
        if plan.alpha > 0.1:
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.alpha",
                f"alpha {plan.alpha:g} > 0.1",
                "use alpha <= 0.05",
            )
        if plan.n_seeds < 3:
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.n_seeds",
                f"n_seeds={plan.n_seeds}: too few replicates to estimate variance reliably",
                "use at least 3 (preferably 5+) seeds",
            )
        if plan.ci_level < 0.9:
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.ci_level",
                f"ci_level {plan.ci_level:g} < 0.9",
                "use 0.95",
            )
        compared = {c.metric for c in self.spec.success_criteria}
        if plan.correction == "none" and len(compared) > 1:
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.correction",
                f"{len(compared)} metrics are tested without multiple-comparison correction",
                "use correction='holm' (or 'bh')",
            )
        if plan.min_effect_size is None and self.spec.kind in ("candidate", "ablation", "sensitivity"):
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.min_effect_size",
                "no minimum effect size: negligible but significant differences would count as improvements",
                "pre-register the smallest effect size of practical interest",
            )
        if plan.test == "mann_whitney":
            n = max(plan.n_seeds, 1)
            p_min = _mwu_min_two_sided_p(n, n)
            if p_min > plan.alpha:
                self.add(
                    "STATISTICAL_PLAN_WEAK",
                    "warning",
                    "statistical_plan.test",
                    f"with {n} seeds per arm the smallest possible Mann–Whitney p-value is {p_min:.3g} > alpha {plan.alpha:g}",
                    "use more seeds or a parametric test",
                )
        if plan.test in ("welch_t", "paired_t", "bootstrap") and plan.n_seeds < 2:
            self.add(
                "STATISTICAL_PLAN_WEAK",
                "warning",
                "statistical_plan.n_seeds",
                f"{plan.test} needs at least 2 seeds per arm",
                "use more seeds",
            )


def validate_design(
    spec: ExperimentSpec | Mapping[str, Any],
    context: ValidationContext | Mapping[str, Any] | None = None,
    limits: ValidationLimits | None = None,
) -> ValidationReport:
    """Convenience wrapper around :class:`ExperimentDesignValidator`."""
    return ExperimentDesignValidator(limits).validate(spec, context)
