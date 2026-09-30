"""Multi-objective fitness: orientation, normalisation, hard constraints.

The :class:`FitnessEngine` turns raw objective measurements (as recorded in
``strategy_evaluations.objectives``) into :class:`~engines.lab.evolution.types.FitnessVector` s:

* every objective is *oriented* so that higher is better (minimised objectives are flipped) and
  normalised by its declared bounds — Pareto domination is invariant under these monotone maps;
* hard constraints (e.g. ``safety >= 1.0``) produce a summed, normalised *constraint violation*;
  a candidate is feasible iff it is zero. Constrained domination (see ``selection.py``) ensures an
  infeasible candidate never dominates a feasible one, however good its other objectives look;
* a missing or non-finite objective value makes the candidate infeasible (``missing:<name>`` /
  ``invalid:<name>``) — an unmeasured strategy can never out-rank a measured one;
* ``summary`` is a weighted mean of per-objective display scores in [0, 1]. It exists for dashboards
  only; selection never scalarises objectives.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from engines.lab.evolution.types import ConstraintSpec, Direction, FitnessVector, ObjectiveSpec, SchemaError

MISSING_VIOLATION = 1.0  # normalised violation charged per missing/invalid objective value

CANONICAL_OBJECTIVES: dict[str, ObjectiveSpec] = {
    "scientific_performance": ObjectiveSpec(
        name="scientific_performance",
        direction=Direction.MAXIMIZE,
        weight=3.0,
        bounds=(0.0, 1.0),
        description="Primary task metric of the research behaviour (normalised to [0, 1]).",
    ),
    "cost": ObjectiveSpec(
        name="cost",
        direction=Direction.MINIMIZE,
        weight=1.0,
        bounds=(0.0, 10.0),
        description="Spend per mission step in USD (LLM + compute + tools).",
    ),
    "latency": ObjectiveSpec(
        name="latency",
        direction=Direction.MINIMIZE,
        weight=0.5,
        bounds=(0.0, 600.0),
        description="Wall-clock seconds per mission step.",
    ),
    "compute_efficiency": ObjectiveSpec(
        name="compute_efficiency",
        direction=Direction.MAXIMIZE,
        weight=0.5,
        bounds=(0.0, 1.0),
        description="Useful work per unit of compute (normalised).",
    ),
    "robustness": ObjectiveSpec(
        name="robustness",
        direction=Direction.MAXIMIZE,
        weight=1.0,
        bounds=(0.0, 1.0),
        description="Performance retained across seeds, datasets and perturbations.",
    ),
    "reproducibility": ObjectiveSpec(
        name="reproducibility",
        direction=Direction.MAXIMIZE,
        weight=1.5,
        bounds=(0.0, 1.0),
        description="Fraction of results that reproduce within tolerance.",
    ),
    "novelty": ObjectiveSpec(
        name="novelty",
        direction=Direction.MAXIMIZE,
        weight=0.5,
        bounds=(0.0, 1.0),
        description="Behavioural/parameter-space novelty relative to the archive and population.",
    ),
    "safety": ObjectiveSpec(
        name="safety",
        direction=Direction.MAXIMIZE,
        weight=2.0,
        bounds=(0.0, 1.0),
        constraint=ConstraintSpec(op=">=", threshold=1.0),
        description="Fraction of safety/guardrail checks passed; must be 1.0 (hard constraint).",
    ),
}


def default_objectives() -> list[ObjectiveSpec]:
    """The eight canonical objectives in canonical order."""
    return list(CANONICAL_OBJECTIVES.values())


def resolve_objectives(items: Sequence[ObjectiveSpec | Mapping[str, Any] | str]) -> list[ObjectiveSpec]:
    """Build objective specs from names (canonical defaults), dicts or specs.

    A dict naming a canonical objective inherits the canonical bounds/constraint unless it overrides
    them explicitly.
    """
    resolved: list[ObjectiveSpec] = []
    for item in items:
        if isinstance(item, ObjectiveSpec):
            resolved.append(item)
        elif isinstance(item, str):
            if item not in CANONICAL_OBJECTIVES:
                raise SchemaError(f"unknown objective {item!r}; pass a full objective spec for custom objectives")
            resolved.append(CANONICAL_OBJECTIVES[item])
        elif isinstance(item, Mapping):
            data = dict(item)
            base = CANONICAL_OBJECTIVES.get(str(data.get("name", "")))
            if base is not None:
                merged = base.model_dump()
                merged.update(data)
                data = merged
            try:
                resolved.append(ObjectiveSpec.model_validate(data))
            except ValueError as exc:
                raise SchemaError(f"invalid objective {data.get('name')!r}: {exc}") from exc
        else:
            raise SchemaError(f"objective must be a name, dict or ObjectiveSpec, got {type(item).__name__}")
    names = [o.name for o in resolved]
    if len(set(names)) != len(names):
        raise SchemaError(f"duplicate objective names: {names}")
    return resolved


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        f = float(value)
        return f if math.isfinite(f) else None
    return None


class FitnessEngine:
    """Evaluates raw objective measurements against an ordered list of objectives."""

    def __init__(self, objectives: Sequence[ObjectiveSpec | Mapping[str, Any] | str]) -> None:
        specs = resolve_objectives(objectives)
        if not specs:
            raise SchemaError("at least one objective is required")
        self.objectives: tuple[ObjectiveSpec, ...] = tuple(specs)
        self.names: tuple[str, ...] = tuple(o.name for o in specs)

    @property
    def all_bounded(self) -> bool:
        return all(o.bounds is not None for o in self.objectives)

    def spec(self, name: str) -> ObjectiveSpec:
        for objective in self.objectives:
            if objective.name == name:
                return objective
        raise KeyError(name)

    def evaluate(self, raw: Mapping[str, Any] | None) -> FitnessVector:
        """Orient, normalise and constraint-check one candidate's raw objective values."""
        raw = raw or {}
        values: dict[str, float | None] = {}
        oriented: list[float] = []
        violation = 0.0
        violated: list[str] = []
        display: list[tuple[float, float]] = []
        for objective in self.objectives:
            present = objective.name in raw and raw[objective.name] is not None
            value = _as_number(raw.get(objective.name))
            if value is None:
                values[objective.name] = None
                oriented.append(0.0)
                violation += MISSING_VIOLATION
                violated.append(f"{'invalid' if present else 'missing'}:{objective.name}")
                display.append((objective.weight, 0.0))
                continue
            values[objective.name] = value
            o = objective.orient(value)
            oriented.append(o)
            v = objective.normalized_violation(value)
            if v > 0:
                violation += v
                assert objective.constraint is not None
                violated.append(
                    f"constraint:{objective.name}{objective.constraint.op}{objective.constraint.threshold:g}"
                )
            display.append((objective.weight, self._display_score(objective, o)))
        total_weight = sum(w for w, _ in display)
        if total_weight > 0:
            summary = sum(w * s for w, s in display) / total_weight
        else:
            summary = sum(s for _, s in display) / len(display)
        return FitnessVector(
            objectives=values,
            oriented=tuple(oriented),
            feasible=violation == 0.0,
            constraint_violation=violation,
            violations=tuple(violated),
            summary=round(summary, 12),
        )

    def evaluate_many(self, raws: Sequence[Mapping[str, Any] | None]) -> list[FitnessVector]:
        return [self.evaluate(r) for r in raws]

    @staticmethod
    def _display_score(objective: ObjectiveSpec, oriented: float) -> float:
        if objective.bounds is not None:
            return min(1.0, max(0.0, oriented))
        # Unbounded objectives: squash the oriented value monotonically into (0, 1) for display.
        return 0.5 + 0.5 * math.tanh(oriented)
