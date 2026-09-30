"""The canonical structured experiment specification.

:class:`ExperimentSpec` is the single contract shared by the experiments service, the ExperimentDesignerAgent
(its JSON schema is the agent's response schema), the design validator, the evaluators and the report builder.

Design notes
------------
* The schema is *structural*: types, ranges, identifier formats and size limits are enforced here
  (``extra="forbid"`` everywhere, no NaN/inf). *Scientific* validity — baselines, leakage, reproducibility,
  unsafe mount paths, resource limits — is decided by :mod:`engines.lab.design_validator`, which returns an
  actionable issue list instead of a bare exception. Never execute a spec that has not passed validation.
* :func:`spec_hash` is the SHA-256 of the canonical JSON (sorted keys, compact separators, defaults
  materialised), so semantically identical specs hash identically regardless of key order or omitted defaults.
* :func:`check_criterion` defines the exact semantics of every success-criterion comparator; evaluators and
  verification use it so the meaning of "success" cannot drift between components.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXPERIMENT_SPEC_VERSION = "experiment-spec-1.0.0"

MAX_INLINE_FILES = 64
MAX_INLINE_FILE_BYTES = 256 * 1024
MAX_INLINE_TOTAL_BYTES = 1024 * 1024
MAX_PARAMETERS_BYTES = 64 * 1024
MAX_SEEDS = 1000
MAX_SEED_VALUE = 2**32 - 1

ExperimentKind = Literal["baseline", "candidate", "ablation", "sensitivity", "reproduction", "exploratory"]
EXPERIMENT_KINDS: tuple[str, ...] = ("baseline", "candidate", "ablation", "sensitivity", "reproduction", "exploratory")
Direction = Literal["maximize", "minimize"]
MetricSource = Literal["platform", "evaluator", "self_reported"]
Comparator = Literal[
    "gt", "gte", "lt", "lte", "delta_gt", "delta_gte", "delta_lt", "delta_lte", "relative_improvement_gte"
]
ABSOLUTE_COMPARATORS = frozenset({"gt", "gte", "lt", "lte"})
DELTA_COMPARATORS = frozenset({"delta_gt", "delta_gte", "delta_lt", "delta_lte"})
BASELINE_ONLY_COMPARATORS = DELTA_COMPARATORS | {"relative_improvement_gte"}
StatTest = Literal["welch_t", "mann_whitney", "paired_t", "bootstrap"]
Correction = Literal["holm", "bh", "none"]
DatasetRole = Literal["train", "validation", "test", "eval"]

_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_REF_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_NAME = r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$"
_METRIC = r"^[A-Za-z_][A-Za-z0-9_.:/@-]{0,119}$"
_SPLIT = r"^[A-Za-z0-9_-]{1,64}$"
_HOST = r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*(?::\d{1,5})?$"
_IMAGE = r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,499}$"
_DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _json_size(value: Any, what: str) -> int:
    try:
        encoded = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"), default=_reject)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be JSON-serialisable without NaN/infinity: {exc}") from exc
    return len(encoded.encode("utf-8"))


def _reject(value: Any) -> Any:
    raise TypeError(f"value of type {type(value).__name__} is not JSON-serialisable")


class BaselineSpec(_Strict):
    """What the experiment is compared against.

    * ``experiment`` — another experiment (``experiment_id``) whose runs form the baseline sample;
    * ``reference_values`` — fixed published/previous values (``reference_metrics``) with a ``justification``;
    * ``none`` — no baseline (only valid for baseline/exploratory experiments).
    """

    kind: Literal["experiment", "reference_values", "none"] = "none"
    experiment_id: str | None = Field(default=None, pattern=_REF_ID)
    reference_metrics: dict[str, float] = Field(default_factory=dict)
    justification: str | None = Field(default=None, max_length=4000)


class Variable(_Strict):
    name: str = Field(pattern=_NAME)
    role: Literal["independent", "dependent", "control"]
    values: list[Any] | None = Field(default=None, max_length=1000)
    value: Any = None
    description: str | None = Field(default=None, max_length=2000)


class Control(_Strict):
    name: str = Field(pattern=_NAME)
    value: Any = None
    rationale: str | None = Field(default=None, max_length=2000)


class DatasetUse(_Strict):
    """A dataset version mounted into the experiment. ``mount_path`` is relative to ``/workspace/input``."""

    dataset_version_id: str = Field(pattern=_ID)
    split: str | None = Field(default=None, pattern=_SPLIT)
    role: DatasetRole
    mount_path: str | None = Field(default=None, max_length=255)

    @property
    def effective_mount_path(self) -> str:
        """The explicit ``mount_path`` or the default ``datasets/<version>/<split|all>``."""
        return self.mount_path or f"datasets/{self.dataset_version_id}/{self.split or 'all'}"


class MetricSpec(_Strict):
    """A measured quantity. ``source`` says who produces it (``self_reported`` = written by the experiment code)."""

    name: str = Field(pattern=_METRIC)
    direction: Direction
    primary: bool = False
    source: MetricSource = "self_reported"
    evaluator_key: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, max_length=48)


class SuccessCriterion(_Strict):
    """A pre-registered success condition on one metric (see :func:`check_criterion` for exact semantics).

    ``relative_to`` defaults to ``baseline`` for ``delta_*``/``relative_improvement_gte`` comparators and to
    ``absolute`` otherwise.
    """

    metric: str = Field(pattern=_METRIC)
    comparator: Comparator
    threshold: float
    relative_to: Literal["baseline", "absolute"] = "absolute"

    @model_validator(mode="before")
    @classmethod
    def _default_relative_to(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and data.get("relative_to") is None:
            data = dict(data)
            data["relative_to"] = "baseline" if data.get("comparator") in BASELINE_ONLY_COMPARATORS else "absolute"
        return data


class StatisticalPlan(_Strict):
    """Pre-registered analysis plan used by :mod:`engines.lab.comparison` and the statistical evaluator."""

    test: StatTest = "welch_t"
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    min_effect_size: float | None = Field(default=None, ge=0.0)
    correction: Correction = "holm"
    n_seeds: int = Field(default=3, ge=1, le=MAX_SEEDS)
    ci_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    bootstrap_resamples: int = Field(default=2000, ge=100, le=100_000)
    bootstrap_seed: int = Field(default=0, ge=0, le=MAX_SEED_VALUE)


class Ablation(_Strict):
    name: str = Field(pattern=_NAME)
    changes: dict[str, Any] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=2000)


class Sensitivity(_Strict):
    parameter: str = Field(pattern=_NAME)
    values: list[Any] = Field(min_length=1, max_length=1000)


class EnvironmentSpec(_Strict):
    image: str = Field(pattern=_IMAGE)
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    dependencies: list[str] = Field(default_factory=list, max_length=300)
    python_version: str | None = Field(default=None, pattern=r"^3\.\d{1,2}(\.\d{1,3})?$")

    @field_validator("dependencies")
    @classmethod
    def _dependency_length(cls, value: list[str]) -> list[str]:
        for dep in value:
            if not dep or len(dep) > 300:
                raise ValueError("each dependency must be a non-empty string of at most 300 characters")
        return value

    @property
    def is_digest_pinned(self) -> bool:
        return self.image_digest is not None or bool(_DIGEST_RE.search(self.image))


class GitRef(_Strict):
    repo: str = Field(min_length=1, max_length=1000)
    commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")


class CodeSpec(_Strict):
    """The code to run: inline ``files`` (path → source, size-limited), a stored snapshot, or a git commit."""

    entrypoint: str | None = Field(default=None, max_length=300)
    files: dict[str, str] = Field(default_factory=dict)
    code_snapshot_id: str | None = Field(default=None, pattern=_ID)
    git: GitRef | None = None

    @field_validator("files")
    @classmethod
    def _file_limits(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_INLINE_FILES:
            raise ValueError(f"at most {MAX_INLINE_FILES} inline files are allowed")
        total = 0
        for path, content in value.items():
            if not path or len(path) > 255:
                raise ValueError("inline file paths must be 1-255 characters")
            size = len(content.encode("utf-8"))
            if size > MAX_INLINE_FILE_BYTES:
                raise ValueError(f"inline file {path!r} exceeds {MAX_INLINE_FILE_BYTES} bytes")
            total += size
        if total > MAX_INLINE_TOTAL_BYTES:
            raise ValueError(f"inline files exceed {MAX_INLINE_TOTAL_BYTES} bytes in total")
        return value

    @property
    def has_source(self) -> bool:
        return bool(self.files) or self.code_snapshot_id is not None or self.git is not None


class ResourcesSpec(_Strict):
    cpu: float = Field(default=1.0, gt=0.0, le=1024.0)
    memory_mb: int = Field(default=1024, ge=16)
    disk_mb: int = Field(default=1024, ge=1)
    gpu_type: str | None = Field(default=None, max_length=64)
    gpu_count: int = Field(default=0, ge=0, le=64)


class ReproSpec(_Strict):
    seed_env_var: str = Field(default="AEGIS_SEED", pattern=r"^[A-Z_][A-Z0-9_]{0,63}$")
    deterministic_flags: list[str] = Field(default_factory=list, max_length=50)
    require_digest_pin: bool = False


class NetworkSpec(_Strict):
    mode: Literal["none", "allowlist"] = "none"
    hosts: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("hosts")
    @classmethod
    def _hostnames(cls, value: list[str]) -> list[str]:
        pattern = re.compile(_HOST)
        for host in value:
            if not pattern.match(host):
                raise ValueError(f"invalid host {host!r}: use a bare hostname (optionally :port), no scheme or path")
        return value


class ExperimentSpec(_Strict):
    """Canonical experiment specification (see module docstring)."""

    objective: str = Field(min_length=1, max_length=4000)
    hypothesis_id: str | None = Field(default=None, pattern=_REF_ID)
    kind: ExperimentKind = "candidate"
    baseline: BaselineSpec = Field(default_factory=BaselineSpec)
    method: str = Field(min_length=1, max_length=20_000)
    variables: list[Variable] = Field(default_factory=list, max_length=200)
    controls: list[Control] = Field(default_factory=list, max_length=200)
    datasets: list[DatasetUse] = Field(default_factory=list, max_length=50)
    metrics: list[MetricSpec] = Field(default_factory=list, max_length=100)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list, max_length=100)
    statistical_plan: StatisticalPlan = Field(default_factory=StatisticalPlan)
    seeds: list[int] = Field(default_factory=list, max_length=MAX_SEEDS)
    ablations: list[Ablation] = Field(default_factory=list, max_length=100)
    sensitivity: list[Sensitivity] = Field(default_factory=list, max_length=100)
    environment: EnvironmentSpec
    code: CodeSpec = Field(default_factory=CodeSpec)
    command: list[str] = Field(default_factory=list, max_length=64)
    resources: ResourcesSpec = Field(default_factory=ResourcesSpec)
    timeout_seconds: int = Field(default=900, ge=1, le=7 * 24 * 3600)
    expected_cost_usd: float | None = Field(default=None, ge=0.0)
    reproducibility: ReproSpec = Field(default_factory=ReproSpec)
    network: NetworkSpec = Field(default_factory=NetworkSpec)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("seeds")
    @classmethod
    def _seed_range(cls, value: list[int]) -> list[int]:
        for seed in value:
            if seed < 0 or seed > MAX_SEED_VALUE:
                raise ValueError(f"seeds must be in [0, {MAX_SEED_VALUE}]")
        return value

    @field_validator("command")
    @classmethod
    def _command_args(cls, value: list[str]) -> list[str]:
        for arg in value:
            if len(arg) > 4096 or "\x00" in arg:
                raise ValueError("command arguments must be at most 4096 characters and contain no NUL bytes")
        return value

    @field_validator("parameters")
    @classmethod
    def _parameters_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _json_size(value, "parameters") > MAX_PARAMETERS_BYTES:
            raise ValueError(f"parameters exceed {MAX_PARAMETERS_BYTES} bytes of JSON")
        return value

    @property
    def primary_metric(self) -> MetricSpec | None:
        """The first metric flagged ``primary`` (the validator requires exactly one)."""
        return next((m for m in self.metrics if m.primary), None)

    @property
    def metric_names(self) -> list[str]:
        return [m.name for m in self.metrics]

    def metric(self, name: str) -> MetricSpec | None:
        return next((m for m in self.metrics if m.name == name), None)


# ---------------------------------------------------------------------------------------------
# Hashing, parsing, diffing
# ---------------------------------------------------------------------------------------------
def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8, NaN/infinity rejected."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def parse_spec(data: ExperimentSpec | Mapping[str, Any]) -> ExperimentSpec:
    """Return ``data`` as an :class:`ExperimentSpec` (raises ``pydantic.ValidationError`` when invalid)."""
    if isinstance(data, ExperimentSpec):
        return data
    return ExperimentSpec.model_validate(dict(data))


def spec_hash(spec: ExperimentSpec | Mapping[str, Any]) -> str:
    """SHA-256 (hex) of the canonical JSON of the normalised spec (defaults materialised)."""
    return hashlib.sha256(canonical_json(parse_spec(spec)).encode("utf-8")).hexdigest()


def spec_json_schema() -> dict[str, Any]:
    """JSON schema of :class:`ExperimentSpec` (used as the ExperimentDesignerAgent response schema)."""
    return ExperimentSpec.model_json_schema()


_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _join(prefix: str, key: str | int) -> str:
    if isinstance(key, int):
        segment = str(key)
    elif _SIMPLE_KEY.match(key):
        segment = key
    else:
        segment = f"[{json.dumps(key, ensure_ascii=False)}]"
        return f"{prefix}{segment}" if prefix else segment
    return f"{prefix}.{segment}" if prefix else segment


def _walk_diff(a: Any, b: Any, path: str, out: dict[str, dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            child = _join(path, key)
            if key not in b:
                out["removed"][child] = a[key]
            elif key not in a:
                out["added"][child] = b[key]
            else:
                _walk_diff(a[key], b[key], child, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        for index in range(max(len(a), len(b))):
            child = _join(path, index)
            if index >= len(b):
                out["removed"][child] = a[index]
            elif index >= len(a):
                out["added"][child] = b[index]
            else:
                _walk_diff(a[index], b[index], child, out)
        return
    if a != b or type(a) is not type(b):
        out["changed"][path] = {"before": a, "after": b}


def diff_specs(a: ExperimentSpec | Mapping[str, Any], b: ExperimentSpec | Mapping[str, Any]) -> dict[str, Any]:
    """Structural diff of two specs → ``{"added": {path: value}, "removed": {...}, "changed": {path: {before,
    after}}}``.

    Paths are dotted (``statistical_plan.alpha``, ``metrics.0.name``); keys that are not simple identifiers are
    bracket-quoted (``code.files["src/train.py"]``). Both specs are normalised first so omitted defaults never
    show up as differences.
    """
    left = parse_spec(a).model_dump(mode="json")
    right = parse_spec(b).model_dump(mode="json")
    out: dict[str, dict[str, Any]] = {"added": {}, "removed": {}, "changed": {}}
    _walk_diff(left, right, "", out)
    return out


# ---------------------------------------------------------------------------------------------
# Success-criterion semantics
# ---------------------------------------------------------------------------------------------
class CriterionCheck(BaseModel):
    """Outcome of one success criterion. ``satisfied`` is ``None`` when it cannot be evaluated."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    comparator: str
    threshold: float
    relative_to: str
    value: float | None
    baseline_value: float | None
    compared_quantity: float | None
    satisfied: bool | None
    reason: str


COMPARISON_RELATIVE_TOLERANCE = 1e-12


def _compare(op: str, lhs: float, rhs: float) -> bool:
    """Compare with a tiny relative tolerance so binary rounding noise cannot decide a boundary case
    (``(0.84 - 0.80) / 0.80`` is 0.04999… in floating point): values within ``1e-12`` (relative) are treated as
    equal, which satisfies ``gte``/``lte`` and fails ``gt``/``lt``."""
    equal = math.isclose(lhs, rhs, rel_tol=COMPARISON_RELATIVE_TOLERANCE, abs_tol=COMPARISON_RELATIVE_TOLERANCE)
    if op == "gt":
        return lhs > rhs and not equal
    if op == "gte":
        return lhs >= rhs or equal
    if op == "lt":
        return lhs < rhs and not equal
    return lhs <= rhs or equal


def relative_improvement(value: float, baseline_value: float, direction: str) -> float | None:
    """Direction-aware relative improvement ``(value - baseline) / |baseline|`` (sign flipped for minimize)."""
    if baseline_value == 0.0:
        return None
    delta = value - baseline_value if direction == "maximize" else baseline_value - value
    return delta / abs(baseline_value)


def check_criterion(
    criterion: SuccessCriterion,
    value: float | None,
    baseline_value: float | None = None,
    direction: str = "maximize",
) -> CriterionCheck:
    """Evaluate one criterion. Semantics (``v`` = observed value, ``b`` = baseline value, ``θ`` = threshold):

    * ``gt|gte|lt|lte`` with ``relative_to="absolute"``: ``v op θ``;
    * ``gt|gte|lt|lte`` with ``relative_to="baseline"``: ``v op b + θ``;
    * ``delta_gt|delta_gte|delta_lt|delta_lte``: ``(v - b) op θ`` (raw difference, not direction-adjusted);
    * ``relative_improvement_gte``: direction-aware ``(v - b)/|b|`` (maximize) or ``(b - v)/|b|`` (minimize)
      ``>= θ``; undefined when ``b == 0``.

    Missing or non-finite values yield ``satisfied=None`` — never a silent pass or fail.
    """
    base: dict[str, Any] = {
        "metric": criterion.metric,
        "comparator": criterion.comparator,
        "threshold": criterion.threshold,
        "relative_to": criterion.relative_to,
        "value": value,
        "baseline_value": baseline_value,
    }
    if value is None or not math.isfinite(value):
        return CriterionCheck(**{**base, "value": None}, compared_quantity=None, satisfied=None, reason="no value")
    needs_baseline = criterion.comparator in BASELINE_ONLY_COMPARATORS or criterion.relative_to == "baseline"
    if needs_baseline and (baseline_value is None or not math.isfinite(baseline_value)):
        return CriterionCheck(
            **{**base, "baseline_value": None}, compared_quantity=None, satisfied=None, reason="no baseline value"
        )
    comparator = criterion.comparator
    if comparator in ABSOLUTE_COMPARATORS:
        offset = baseline_value if criterion.relative_to == "baseline" and baseline_value is not None else 0.0
        reference = criterion.threshold + offset
        ok = _compare(comparator, value, reference)
        return CriterionCheck(
            **base, compared_quantity=value, satisfied=ok, reason=f"{value:.6g} {comparator} {reference:.6g}"
        )
    assert baseline_value is not None
    if comparator in DELTA_COMPARATORS:
        delta = value - baseline_value
        ok = _compare(comparator.removeprefix("delta_"), delta, criterion.threshold)
        return CriterionCheck(
            **base,
            compared_quantity=delta,
            satisfied=ok,
            reason=f"delta {delta:.6g} {comparator.removeprefix('delta_')} {criterion.threshold:.6g}",
        )
    rel = relative_improvement(value, baseline_value, direction)
    if rel is None:
        return CriterionCheck(
            **base, compared_quantity=None, satisfied=None, reason="relative improvement undefined (baseline is 0)"
        )
    return CriterionCheck(
        **base,
        compared_quantity=rel,
        satisfied=_compare("gte", rel, criterion.threshold),
        reason=f"relative improvement {rel:.6g} >= {criterion.threshold:.6g}",
    )


def criterion_contradicts_direction(criterion: SuccessCriterion, direction: str) -> bool:
    """True when a criterion rewards moving the metric in the *wrong* direction (e.g. ``accuracy lt 0.5``)."""
    op = criterion.comparator.removeprefix("delta_")
    if criterion.comparator == "relative_improvement_gte":
        return False
    if direction == "maximize":
        return op in ("lt", "lte")
    return op in ("gt", "gte")
