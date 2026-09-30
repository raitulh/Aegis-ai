"""Variation operators for strategy parameter vectors.

Mutation operators (one parameter at a time):

==========================  ===================  ==================================================
operator                    parameter type       effect
==========================  ===================  ==================================================
``gaussian_perturb``        float                ``x + N(0, σ)``, ``σ = mutation_scale × (max − min)``
``integer_step``            int                  ``x ± k·step`` with ``k ≥ 1`` scaled by the range
``bool_flip``               bool                 ``not x``
``choice_resample``         choice               uniformly another allowed choice
``prompt_segment_swap``     choice (prompt)      a variant differing in exactly one ``/`` segment
``ordered_list_swap``       ordered_list         swap two positions
``ordered_list_insert``     ordered_list         insert an allowed item (length ≤ max)
``ordered_list_remove``     ordered_list         remove an item (length ≥ min)
==========================  ===================  ==================================================

Crossover operators (two parents): ``uniform_crossover`` (each mutable parameter from either parent)
and ``blend_crossover`` (BLX-α for floats, uniform for the rest).

Every output is clipped/snapped to its :class:`ParameterSpec` and re-validated; immutable parameters
never change (crossover copies them from the first parent). Each call is deterministic in
``(engine seed, generation, index, step)`` and returns a :class:`MutationRecord` carrying the derived
seed that reproduces it.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from typing import Any

from engines.lab.evolution.rng import derive_seed
from engines.lab.evolution.types import (
    PROMPT_SEGMENT_SEPARATOR,
    MutationRecord,
    ParameterSchema,
    ParameterSpec,
    ParamType,
    canonical_json,
    strict_member,
)

MUTATION_OPERATORS: tuple[str, ...] = (
    "gaussian_perturb",
    "integer_step",
    "bool_flip",
    "choice_resample",
    "prompt_segment_swap",
    "ordered_list_swap",
    "ordered_list_insert",
    "ordered_list_remove",
)
CROSSOVER_OPERATORS: tuple[str, ...] = ("uniform_crossover", "blend_crossover")
BLX_ALPHA = 0.5
_MAX_OPERATOR_TRIES = 8


class MutationError(ValueError):
    """Raised when no valid mutation exists (e.g. every parameter is immutable or fixed)."""


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str | int | float | bool) and isinstance(b, str | int | float | bool):
        return type(a) is type(b) and a == b
    return canonical_json(a) == canonical_json(b)


# ---------------------------------------------------------------------------------------------
# Single-parameter operators: (spec, value, rng) -> (new_value, detail)
# ---------------------------------------------------------------------------------------------
def gaussian_perturb(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    span = spec.value_range
    sigma = spec.scale * span if span else spec.scale * max(abs(float(value)), 1.0)
    return spec.coerce(float(value) + rng.gauss(0.0, sigma)), {"sigma": round(sigma, 12)}


def integer_step(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    step = int(spec.step) if spec.step is not None else 1
    span = spec.value_range
    sigma_steps = max(1.0, spec.scale * span / step) if span else 1.0
    k = max(1, round(abs(rng.gauss(0.0, sigma_steps))))
    direction = 1 if rng.random() < 0.5 else -1
    candidate = spec.coerce(int(value) + direction * k * step)
    if candidate == value:  # pinned at a bound: go the other way
        direction = -direction
        candidate = spec.coerce(int(value) + direction * k * step)
    return candidate, {"steps": direction * k}


def bool_flip(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    return (not bool(value)), {}


def choice_resample(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    assert spec.choices is not None
    others = [c for c in spec.choices if not (type(c) is type(value) and c == value)]
    if not others:
        return value, {}
    return rng.choice(others), {}


def _segments(variant: Any) -> list[str] | None:
    if not isinstance(variant, str):
        return None
    return variant.split(PROMPT_SEGMENT_SEPARATOR)


def prompt_segment_swap(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    """Move to a prompt variant that differs from ``value`` in exactly one segment.

    Variants are ``/``-separated segment lists (``"persona:skeptic/format:bullets"``). When no
    single-segment neighbour exists among the allowed variants (flat names), falls back to a uniform
    resample of another variant and records ``fallback`` (single-segment names are flat).
    """
    assert spec.choices is not None
    current = _segments(value)
    neighbours: list[tuple[Any, int]] = []
    if current is not None and len(current) >= 2:
        for option in spec.choices:
            segs = _segments(option)
            if segs is None or len(segs) != len(current) or option == value:
                continue
            diffs = [i for i, (a, b) in enumerate(zip(segs, current, strict=True)) if a != b]
            if len(diffs) == 1:
                neighbours.append((option, diffs[0]))
    if neighbours:
        chosen, position = rng.choice(neighbours)
        return chosen, {"segment": position, "from": current[position] if current else None}
    resampled, _ = choice_resample(spec, value, rng)
    return resampled, {"fallback": "choice_resample"}


def ordered_list_swap(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    items = list(value)
    if len(items) < 2:
        return items, {}
    i, j = sorted(rng.sample(range(len(items)), 2))
    items[i], items[j] = items[j], items[i]
    return items, {"positions": [i, j]}


def ordered_list_insert(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    assert spec.choices is not None
    items = list(value)
    _, hi = spec.length_bounds
    if len(items) >= hi:
        return items, {}
    pool = [c for c in spec.choices if not (spec.unique_items and strict_member(c, items))]
    if not pool:
        return items, {}
    item = rng.choice(pool)
    position = rng.randint(0, len(items))
    items.insert(position, item)
    return items, {"position": position, "item": item}


def ordered_list_remove(spec: ParameterSpec, value: Any, rng: random.Random) -> tuple[Any, dict[str, Any]]:
    items = list(value)
    lo, _ = spec.length_bounds
    if len(items) <= lo or not items:
        return items, {}
    position = rng.randrange(len(items))
    removed = items.pop(position)
    return items, {"position": position, "item": removed}


OperatorFn = Callable[[ParameterSpec, Any, random.Random], tuple[Any, dict[str, Any]]]
OPERATORS: dict[str, OperatorFn] = {
    "gaussian_perturb": gaussian_perturb,
    "integer_step": integer_step,
    "bool_flip": bool_flip,
    "choice_resample": choice_resample,
    "prompt_segment_swap": prompt_segment_swap,
    "ordered_list_swap": ordered_list_swap,
    "ordered_list_insert": ordered_list_insert,
    "ordered_list_remove": ordered_list_remove,
}


def applicable_operators(spec: ParameterSpec, value: Any) -> list[str]:
    """Operators that can change ``value`` under ``spec`` (empty for immutable/fixed parameters)."""
    if not spec.mutable:
        return []
    if spec.type is ParamType.FLOAT:
        return [] if spec.value_range == 0 else ["gaussian_perturb"]
    if spec.type is ParamType.INT:
        return [] if spec.value_range == 0 else ["integer_step"]
    if spec.type is ParamType.BOOL:
        return ["bool_flip"]
    if spec.type is ParamType.CHOICE:
        if spec.choices is None or len(spec.choices) < 2:
            return []
        return ["prompt_segment_swap"] if spec.prompt_variant else ["choice_resample"]
    ops: list[str] = []
    items = list(value) if isinstance(value, list | tuple) else []
    lo, hi = spec.length_bounds
    if len(items) >= 2 and len({canonical_json(x) for x in items}) >= 2:
        ops.append("ordered_list_swap")
    if (
        len(items) < hi
        and spec.choices is not None
        and (not spec.unique_items or any(not strict_member(c, items) for c in spec.choices))
    ):
        ops.append("ordered_list_insert")
    if len(items) > lo:
        ops.append("ordered_list_remove")
    return ops


class MutationEngine:
    """Seeded mutation and crossover over a :class:`ParameterSchema`."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def _rng(self, *parts: object) -> tuple[random.Random, int]:
        seed = derive_seed(self.seed, *parts)
        return random.Random(seed), seed

    # -- mutation --------------------------------------------------------------------------------
    def mutate(
        self,
        params: Mapping[str, Any],
        schema: ParameterSchema | Mapping[str, Any],
        *,
        generation: int = 0,
        index: int = 0,
        step: int = 0,
        path: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any], MutationRecord]:
        """Apply one operator to one mutable parameter (``path`` or a seeded random choice).

        Tries parameters/operators in a seeded order until the value actually changes; raises
        :class:`MutationError` when nothing can change. The result always validates against the spec.
        """
        parsed = ParameterSchema.from_dict(schema)
        rng, seed = self._rng("mutate", generation, index, step)
        current = parsed.with_defaults(params)
        names = [path] if path is not None else list(parsed.mutable_names)
        for name in names:
            if name not in parsed.parameters:
                raise MutationError(f"unknown parameter {name!r}")
            if not parsed.parameters[name].mutable:
                raise MutationError(f"parameter {name!r} is immutable")
        rng.shuffle(names)
        for name in names:
            spec = parsed.parameters[name]
            if name not in current:
                continue
            ops = applicable_operators(spec, current[name])
            if operator is not None:
                ops = [op for op in ops if op == operator]
            for _ in range(_MAX_OPERATOR_TRIES):
                if not ops:
                    break
                op = rng.choice(ops)
                new_value, detail = OPERATORS[op](spec, current[name], rng)
                new_value = spec.coerce(new_value)
                if _same(new_value, current[name]) or spec.check_value(new_value):
                    continue
                child = dict(current)
                child[name] = new_value
                record = MutationRecord(
                    operator=op,
                    changed_paths=(name,),
                    before={name: current[name]},
                    after={name: new_value},
                    seed=seed,
                    detail=detail,
                )
                return child, record
        raise MutationError("no mutable parameter can change under the schema")

    # -- crossover ---------------------------------------------------------------------------------
    def crossover(
        self,
        first: Mapping[str, Any],
        second: Mapping[str, Any],
        schema: ParameterSchema | Mapping[str, Any],
        *,
        operator: str = "uniform_crossover",
        generation: int = 0,
        index: int = 0,
        parents: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], MutationRecord]:
        """Recombine two parents. Immutable parameters always come from ``first``."""
        if operator not in CROSSOVER_OPERATORS:
            raise ValueError(f"unknown crossover operator {operator!r}")
        parsed = ParameterSchema.from_dict(schema)
        rng, seed = self._rng("crossover", operator, generation, index)
        a = parsed.with_defaults(first)
        b = parsed.with_defaults(second)
        child = dict(a)
        for name in sorted(parsed.parameters):
            spec = parsed.parameters[name]
            if not spec.mutable or name not in a or name not in b:
                continue
            if (
                operator == "blend_crossover"
                and spec.type is ParamType.FLOAT
                and spec.min is not None
                and spec.max is not None
            ):
                lo, hi = sorted((float(a[name]), float(b[name])))
                d = hi - lo
                value = spec.coerce(rng.uniform(lo - BLX_ALPHA * d, hi + BLX_ALPHA * d))
            else:
                value = a[name] if rng.random() < 0.5 else b[name]
            if not spec.check_value(value):
                child[name] = value
        changed = tuple(name for name in sorted(child) if not _same(child[name], a.get(name)))
        record = MutationRecord(
            operator=operator,
            changed_paths=changed,
            before={name: a.get(name) for name in changed},
            after={name: child[name] for name in changed},
            seed=seed,
            parents=parents,
        )
        return child, record

    # -- sampling -----------------------------------------------------------------------------------
    def sample(self, schema: ParameterSchema | Mapping[str, Any], *, index: int = 0) -> dict[str, Any]:
        """A uniformly random valid parameter vector (requires numeric bounds). Immutable parameters
        take their default."""
        parsed = ParameterSchema.from_dict(schema)
        rng, _ = self._rng("sample", index)
        params: dict[str, Any] = {}
        for name in sorted(parsed.parameters):
            spec = parsed.parameters[name]
            if not spec.mutable and spec.default is not None:
                params[name] = spec.default
                continue
            if spec.type in (ParamType.INT, ParamType.FLOAT):
                if spec.min is None or spec.max is None:
                    raise MutationError(f"cannot sample unbounded parameter {name!r}")
                if spec.type is ParamType.INT:
                    params[name] = spec.coerce(rng.randint(int(spec.min), int(spec.max)))
                else:
                    params[name] = spec.coerce(rng.uniform(float(spec.min), float(spec.max)))
            elif spec.type is ParamType.BOOL:
                params[name] = rng.random() < 0.5
            elif spec.type is ParamType.CHOICE:
                assert spec.choices is not None
                params[name] = rng.choice(list(spec.choices))
            else:
                assert spec.choices is not None
                lo, hi = spec.length_bounds
                length = rng.randint(lo, hi)
                if spec.unique_items:
                    params[name] = rng.sample(list(spec.choices), length)
                else:
                    params[name] = [rng.choice(list(spec.choices)) for _ in range(length)]
        return params
