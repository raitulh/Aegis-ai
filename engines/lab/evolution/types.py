"""Core value types of the evolution engine.

* :class:`ParameterSpec` / :class:`ParameterSchema` — the *guardrail contract* stored in
  ``strategies.parameter_schema``: evolution may only change parameters declared here, only within the
  declared type/bounds/choices, and never parameters marked ``mutable=False``.
* :class:`ObjectiveSpec` — one optimisation objective (direction, display weight, optional hard
  constraint, normalisation bounds).
* :class:`FitnessVector` — an evaluated candidate: raw objective values, the oriented (all-maximised,
  normalised) vector used for domination, and constraint feasibility.
* :class:`Candidate` / :class:`MutationRecord` — a strategy parameter vector with lineage.

Stored form of a parameter schema::

    {"parameters": {"temperature": {"type": "float", "min": 0.0, "max": 1.5, "mutation_scale": 0.1}, ...},
     "definition_keys": ["steps", "tools", "prompt_template"]}

``ordered_list`` parameters use ``choices`` as the allowed item vocabulary and ``min``/``max`` as the
allowed list length. A ``choice`` parameter with ``prompt_variant=True`` names prompt variants; its
values may be composed of ``/``-separated segments (e.g. ``"persona:skeptic/format:bullets"``) which
the ``prompt_segment_swap`` mutation operator swaps one at a time.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PARAM_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
PROMPT_SEGMENT_SEPARATOR = "/"
DEFAULT_MUTATION_SCALE = 0.1
MAX_ORDERED_LIST_LENGTH = 64
_ALIGN_TOL = 1e-9


class SchemaError(ValueError):
    """Raised when a parameter schema (or objective/config definition) is malformed."""


class ParamType(StrEnum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    CHOICE = "choice"
    ORDERED_LIST = "ordered_list"


# ---------------------------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------------------------
def _canonicalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        normalized = float(f"{value:.12g}")
        return 0.0 if normalized == 0 else normalized
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(v) for v in value]
    return repr(value)


def canonical_json(value: Any) -> str:
    """Deterministic JSON (sorted keys, floats normalised to 12 significant digits)."""
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_hash(value: Any) -> str:
    """SHA-256 of :func:`canonical_json` — used to de-duplicate parameter vectors."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_json_scalar(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, str | int | bool)


def strict_member(value: Any, options: Sequence[Any]) -> bool:
    """Membership that does not conflate ``True`` with ``1`` or ``1`` with ``1.0``."""
    return any(type(opt) is type(value) and opt == value for opt in options)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


# ---------------------------------------------------------------------------------------------
# Parameter specification (the guardrail contract)
# ---------------------------------------------------------------------------------------------
class ParameterSpec(BaseModel):
    """One evolvable parameter. See the module docstring for per-type semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: ParamType
    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    choices: tuple[Any, ...] | None = None
    mutable: bool = True
    mutation_scale: float | None = Field(default=None, gt=0, le=1)
    prompt_variant: bool = False
    unique_items: bool = True
    default: Any = None
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if not PARAM_NAME_PATTERN.match(self.name):
            raise ValueError(f"parameter name {self.name!r} must match {PARAM_NAME_PATTERN.pattern}")
        for bound in (self.min, self.max, self.step):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"{self.name}: bounds and step must be finite")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"{self.name}: min ({self.min}) > max ({self.max})")
        if self.step is not None and self.step <= 0:
            raise ValueError(f"{self.name}: step must be > 0")
        if self.prompt_variant and self.type is not ParamType.CHOICE:
            raise ValueError(f"{self.name}: prompt_variant is only valid for choice parameters")
        if self.type is ParamType.BOOL:
            if self.min is not None or self.max is not None or self.step is not None or self.choices is not None:
                raise ValueError(f"{self.name}: bool parameters take no bounds, step or choices")
        elif self.type in (ParamType.INT, ParamType.FLOAT):
            if self.choices is not None:
                raise ValueError(f"{self.name}: numeric parameters take bounds, not choices")
            if self.type is ParamType.INT:
                for bound in (self.min, self.max, self.step):
                    if bound is not None and not float(bound).is_integer():
                        raise ValueError(f"{self.name}: int bounds/step must be integers")
        elif self.type is ParamType.CHOICE:
            self._check_choices(allow_single=True)
            if self.min is not None or self.max is not None or self.step is not None:
                raise ValueError(f"{self.name}: choice parameters take no bounds or step")
        else:  # ORDERED_LIST
            self._check_choices(allow_single=True)
            if self.step is not None:
                raise ValueError(f"{self.name}: ordered_list parameters take no step")
            for bound in (self.min, self.max):
                if bound is not None and (bound < 0 or not float(bound).is_integer()):
                    raise ValueError(f"{self.name}: ordered_list length bounds must be non-negative integers")
            if self.unique_items and self.min is not None and self.choices and self.min > len(self.choices):
                raise ValueError(f"{self.name}: min length exceeds the number of unique allowed items")
        if self.default is not None:
            problems = self.check_value(self.default)
            if problems:
                raise ValueError(f"{self.name}: invalid default ({problems[0][1]})")
        return self

    def _check_choices(self, *, allow_single: bool) -> None:
        if not self.choices:
            raise ValueError(f"{self.name}: {self.type.value} parameters require non-empty choices")
        if not allow_single and len(self.choices) < 2:
            raise ValueError(f"{self.name}: at least two choices are required")
        for choice in self.choices:
            if not _is_json_scalar(choice):
                raise ValueError(f"{self.name}: choices must be JSON scalars (str/int/float/bool)")
        seen: list[Any] = []
        for choice in self.choices:
            if strict_member(choice, seen):
                raise ValueError(f"{self.name}: duplicate choice {choice!r}")
            seen.append(choice)

    # -- derived properties -------------------------------------------------------------------
    @property
    def length_bounds(self) -> tuple[int, int]:
        """Effective ``(min_len, max_len)`` for ordered lists."""
        lo = int(self.min) if self.min is not None else 0
        hi = int(self.max) if self.max is not None else MAX_ORDERED_LIST_LENGTH
        if self.unique_items and self.choices is not None:
            hi = min(hi, len(self.choices))
        return lo, min(hi, MAX_ORDERED_LIST_LENGTH)

    @property
    def value_range(self) -> float | None:
        if self.min is None or self.max is None:
            return None
        return float(self.max) - float(self.min)

    @property
    def scale(self) -> float:
        return self.mutation_scale if self.mutation_scale is not None else DEFAULT_MUTATION_SCALE

    # -- validation -----------------------------------------------------------------------------
    def _aligned(self, value: float) -> bool:
        if self.step is None:
            return True
        base = float(self.min) if self.min is not None else 0.0
        k = (float(value) - base) / float(self.step)
        return abs(k - round(k)) <= _ALIGN_TOL * max(1.0, abs(k))

    def check_value(self, value: Any) -> list[tuple[str, str]]:
        """Return ``[(code, message)]`` for every way ``value`` violates this spec (empty = valid)."""
        problems: list[tuple[str, str]] = []
        if self.type is ParamType.BOOL:
            if not isinstance(value, bool):
                problems.append(("TYPE_MISMATCH", f"{self.name} must be a bool"))
            return problems
        if self.type in (ParamType.INT, ParamType.FLOAT):
            if self.type is ParamType.INT and (not isinstance(value, int) or isinstance(value, bool)):
                return [("TYPE_MISMATCH", f"{self.name} must be an int")]
            if self.type is ParamType.FLOAT and not _is_number(value):
                return [("TYPE_MISMATCH", f"{self.name} must be a number")]
            if not math.isfinite(float(value)):
                return [("NOT_FINITE", f"{self.name} must be finite")]
            if self.min is not None and value < self.min:
                problems.append(("OUT_OF_BOUNDS", f"{self.name}={value} is below min {self.min}"))
            if self.max is not None and value > self.max:
                problems.append(("OUT_OF_BOUNDS", f"{self.name}={value} is above max {self.max}"))
            if not self._aligned(float(value)):
                problems.append(("STEP_MISALIGNED", f"{self.name}={value} is not aligned to step {self.step}"))
            return problems
        assert self.choices is not None
        if self.type is ParamType.CHOICE:
            if not strict_member(value, self.choices):
                problems.append(("INVALID_CHOICE", f"{self.name}={value!r} is not an allowed choice"))
            return problems
        # ORDERED_LIST
        if not isinstance(value, list | tuple):
            return [("TYPE_MISMATCH", f"{self.name} must be a list")]
        lo, hi = self.length_bounds
        if not lo <= len(value) <= hi:
            problems.append(("LENGTH_OUT_OF_BOUNDS", f"{self.name} length {len(value)} is outside [{lo}, {hi}]"))
        seen: list[Any] = []
        for i, item in enumerate(value):
            if not strict_member(item, self.choices):
                problems.append(("INVALID_ITEM", f"{self.name}[{i}]={item!r} is not an allowed item"))
            elif self.unique_items and strict_member(item, seen):
                problems.append(("DUPLICATE_ITEM", f"{self.name}[{i}]={item!r} is duplicated"))
            seen.append(item)
        return problems

    def coerce(self, value: Any) -> Any:
        """Clip/snap a *numeric* value into the spec (used on mutation outputs); other types unchanged."""
        if self.type is ParamType.INT:
            v = round(float(value))
            if self.step is not None:
                base = int(self.min) if self.min is not None else 0
                step = int(self.step)
                v = base + round((v - base) / step) * step
            if self.min is not None and v < self.min:
                v = int(self.min)
            if self.max is not None and v > self.max:
                v = int(self.max)
                if self.step is not None and not self._aligned(v):
                    v -= (v - (int(self.min) if self.min is not None else 0)) % int(self.step)
            return int(v)
        if self.type is ParamType.FLOAT:
            v_f = float(value)
            if self.step is not None:
                base_f = float(self.min) if self.min is not None else 0.0
                v_f = base_f + round((v_f - base_f) / float(self.step)) * float(self.step)
            if self.min is not None and v_f < self.min:
                v_f = float(self.min)
            if self.max is not None and v_f > self.max:
                v_f = float(self.max)
                if self.step is not None and not self._aligned(v_f):
                    base_f = float(self.min) if self.min is not None else 0.0
                    v_f = base_f + math.floor((v_f - base_f) / float(self.step)) * float(self.step)
            return round(v_f, 12)
        if self.type is ParamType.ORDERED_LIST and isinstance(value, tuple):
            return list(value)
        return value


class ParameterSchema(BaseModel):
    """The parsed ``strategies.parameter_schema`` document.

    ``definition_keys`` lists the allowed *top-level* keys of a strategy version's ``definition``; an
    empty list means the definition must be empty (default deny).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    definition_keys: tuple[str, ...] = ()

    @field_validator("parameters", mode="before")
    @classmethod
    def _fill_names(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        filled: dict[str, Any] = {}
        for key, spec in value.items():
            if isinstance(spec, Mapping):
                spec_dict = dict(spec)
                if "name" in spec_dict and spec_dict["name"] != key:
                    raise ValueError(f"parameter key {key!r} does not match its spec name {spec_dict['name']!r}")
                spec_dict["name"] = key
                filled[key] = spec_dict
            else:
                filled[key] = spec
        return filled

    @model_validator(mode="after")
    def _check_names(self) -> Self:
        for key, spec in self.parameters.items():
            if spec.name != key:
                raise ValueError(f"parameter key {key!r} does not match its spec name {spec.name!r}")
        return self

    @classmethod
    def from_dict(cls, data: ParameterSchema | Mapping[str, Any] | None) -> ParameterSchema:
        """Parse a stored schema document; raises :class:`SchemaError` on malformed input."""
        if isinstance(data, ParameterSchema):
            return data
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise SchemaError("parameter schema must be an object")
        try:
            return cls.model_validate(dict(data))
        except ValidationError as exc:
            raise SchemaError(f"invalid parameter schema: {exc.errors(include_url=False)}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": {
                name: spec.model_dump(mode="json", exclude_none=True, exclude={"name"})
                for name, spec in self.parameters.items()
            },
            "definition_keys": list(self.definition_keys),
        }

    @property
    def mutable_names(self) -> tuple[str, ...]:
        """Names of mutable parameters, sorted (deterministic iteration order)."""
        return tuple(sorted(name for name, spec in self.parameters.items() if spec.mutable))

    def spec(self, name: str) -> ParameterSpec:
        return self.parameters[name]

    def with_defaults(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """``params`` completed with declared defaults for missing parameters."""
        merged = dict(params)
        for name, spec in self.parameters.items():
            if name not in merged and spec.default is not None:
                merged[name] = spec.default
        return merged


# ---------------------------------------------------------------------------------------------
# Objectives and fitness
# ---------------------------------------------------------------------------------------------
class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


ConstraintOp = Literal[">=", "<=", ">", "<", "=="]


class ConstraintSpec(BaseModel):
    """A hard constraint on an objective's raw value, e.g. ``safety >= 1.0``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: ConstraintOp
    threshold: float
    tolerance: float = Field(default=1e-9, ge=0)

    @field_validator("threshold")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("constraint threshold must be finite")
        return value

    def violation(self, value: float) -> float:
        """Raw magnitude by which ``value`` violates the constraint (0 when satisfied)."""
        t, tol = self.threshold, self.tolerance
        if self.op == ">=":
            return max(0.0, (t - tol) - value)
        if self.op == "<=":
            return max(0.0, value - (t + tol))
        if self.op == ">":
            return 0.0 if value > t else max(t - value, 1e-12)
        if self.op == "<":
            return 0.0 if value < t else max(value - t, 1e-12)
        diff = abs(value - t)
        return 0.0 if diff <= max(tol, 1e-12) else diff


class ObjectiveSpec(BaseModel):
    """One optimisation objective.

    ``weight`` is for the *display* summary only — selection is Pareto-based and never scalarises.
    ``bounds`` ``(lower, upper)`` normalise the raw value so objectives are comparable (ε-boxes,
    novelty, hypervolume, display); values outside the bounds are *not* clipped for domination.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    direction: Direction = Direction.MAXIMIZE
    weight: float = Field(default=1.0, ge=0)
    constraint: ConstraintSpec | None = None
    bounds: tuple[float, float] | None = None
    description: str | None = Field(default=None, max_length=500)

    @field_validator("bounds")
    @classmethod
    def _check_bounds(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is None:
            return value
        lo, hi = value
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            raise ValueError("objective bounds must be finite with lower < upper")
        return value

    @property
    def sign(self) -> float:
        return 1.0 if self.direction is Direction.MAXIMIZE else -1.0

    def orient(self, value: float) -> float:
        """Map a raw value to the oriented scale (higher is better; 1.0 = best bound when bounded)."""
        if self.bounds is None:
            return self.sign * value
        lo, hi = self.bounds
        if self.direction is Direction.MAXIMIZE:
            return (value - lo) / (hi - lo)
        return (hi - value) / (hi - lo)

    def normalized_violation(self, value: float) -> float:
        if self.constraint is None:
            return 0.0
        raw = self.constraint.violation(value)
        if raw <= 0:
            return 0.0
        if self.bounds is not None:
            return raw / (self.bounds[1] - self.bounds[0])
        return raw


class FitnessVector(BaseModel):
    """An evaluated candidate (built by :class:`engines.lab.evolution.fitness.FitnessEngine`).

    ``oriented`` follows the objective order of the engine and is maximised on every axis.
    ``constraint_violation`` is the summed normalised violation; ``feasible`` ⇔ it is zero.
    ``summary`` is a weighted display score in [0, 1] and is never used for selection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    objectives: dict[str, float | None]
    oriented: tuple[float, ...]
    feasible: bool
    constraint_violation: float = Field(ge=0)
    violations: tuple[str, ...] = ()
    summary: float


# ---------------------------------------------------------------------------------------------
# Candidates and lineage
# ---------------------------------------------------------------------------------------------
class MutationRecord(BaseModel):
    """What one variation operator changed, and the seed that reproduces it.

    Maps onto ``strategy_mutations`` (operator, diff = {before, after}, seed, parents).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operator: str = Field(max_length=48)
    changed_paths: tuple[str, ...]
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    seed: int = Field(ge=0, le=0x7FFFFFFF)
    parents: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)

    def diff(self) -> dict[str, Any]:
        return {"before": dict(self.before), "after": dict(self.after), "paths": list(self.changed_paths)}


class Candidate(BaseModel):
    """A strategy parameter vector with lineage and (optionally) raw objective measurements.

    ``fitness`` holds *raw* objective values keyed by objective name (as stored in
    ``strategy_versions.fitness``); the engine orients/normalises them. ``None`` = not evaluated.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    params: dict[str, Any]
    parents: tuple[str, ...] = ()
    generation: int = Field(default=0, ge=0)
    fitness: dict[str, float | None] | None = None
    definition: dict[str, Any] | None = None
    mutations: tuple[MutationRecord, ...] = ()

    @property
    def param_hash(self) -> str:
        return canonical_hash(self.params)
