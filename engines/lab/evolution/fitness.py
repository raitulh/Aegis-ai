"""Multi-objective fitness. Objectives are oriented so that larger is always better internally.

Constraints (e.g. ``safety_violations <= 0``) make a solution *infeasible* rather than merely worse, so a
strategy that trades safety for performance can never dominate a safe one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    metric: str
    direction: Literal["maximize", "minimize"]
    weight: float = Field(default=1.0, ge=0)
    required: bool = False
    constraint_max: float | None = None
    constraint_min: float | None = None
    description: str = ""


DEFAULT_OBJECTIVES: tuple[Objective, ...] = (
    Objective(
        name="performance",
        metric="primary",
        direction="maximize",
        weight=3.0,
        required=True,
        description="Primary scientific metric (oriented to maximize)",
    ),
    Objective(name="cost", metric="cost_usd", direction="minimize", weight=1.0, description="Monetary cost"),
    Objective(name="latency", metric="latency_ms", direction="minimize", weight=0.5, description="Inference latency"),
    Objective(
        name="compute", metric="compute_seconds", direction="minimize", weight=1.0, description="Compute efficiency"
    ),
    Objective(
        name="robustness", metric="primary_std", direction="minimize", weight=1.0, description="Variance across seeds"
    ),
    Objective(
        name="reproducibility",
        metric="reproducibility",
        direction="maximize",
        weight=1.0,
        description="Fraction of successful reproductions",
    ),
    Objective(
        name="novelty", metric="novelty", direction="maximize", weight=0.5, description="Distance to the archive"
    ),
    Objective(
        name="safety",
        metric="safety_violations",
        direction="minimize",
        weight=2.0,
        constraint_max=0.0,
        description="Safety/policy violations (hard constraint)",
    ),
)


@dataclass
class FitnessVector:
    values: dict[str, float]  # oriented (higher = better); missing optional objectives are -inf
    raw: dict[str, float]
    feasible: bool = True
    violations: float = 0.0
    violated: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "values": {k: (None if math.isinf(v) else v) for k, v in self.values.items()},
            "raw": self.raw,
            "feasible": self.feasible,
            "violations": self.violations,
            "violated": self.violated,
            "missing": self.missing,
        }


class FitnessEngine:
    def __init__(self, objectives: Sequence[Objective] | None = None) -> None:
        self.objectives = list(objectives or DEFAULT_OBJECTIVES)
        names = [o.name for o in self.objectives]
        if len(names) != len(set(names)):
            raise ValueError("objective names must be unique")

    @property
    def keys(self) -> list[str]:
        return [o.name for o in self.objectives]

    def compute(self, metrics: Mapping[str, float | None]) -> FitnessVector:
        values: dict[str, float] = {}
        raw: dict[str, float] = {}
        violated: list[str] = []
        missing: list[str] = []
        violations = 0.0
        feasible = True
        for obj in self.objectives:
            value = metrics.get(obj.metric)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                missing.append(obj.name)
                values[obj.name] = -math.inf
                if obj.required:
                    feasible = False
                    violated.append(f"{obj.name}: missing required metric '{obj.metric}'")
                    violations += 1.0
                continue
            v = float(value)
            raw[obj.name] = v
            values[obj.name] = v if obj.direction == "maximize" else -v
            if obj.constraint_max is not None and v > obj.constraint_max:
                feasible = False
                violations += v - obj.constraint_max
                violated.append(f"{obj.name}: {v} > {obj.constraint_max}")
            if obj.constraint_min is not None and v < obj.constraint_min:
                feasible = False
                violations += obj.constraint_min - v
                violated.append(f"{obj.name}: {v} < {obj.constraint_min}")
        return FitnessVector(
            values=values, raw=raw, feasible=feasible, violations=violations, violated=violated, missing=missing
        )

    def scalarize(self, vectors: Sequence[FitnessVector]) -> list[float]:
        """Weighted sum of min-max normalized objectives across a population (reporting/tie-breaks only)."""
        if not vectors:
            return []
        bounds: dict[str, tuple[float, float]] = {}
        for obj in self.objectives:
            finite = [v.values[obj.name] for v in vectors if not math.isinf(v.values[obj.name])]
            bounds[obj.name] = (min(finite), max(finite)) if finite else (0.0, 0.0)
        scores: list[float] = []
        for vec in vectors:
            total = 0.0
            for obj in self.objectives:
                lo, hi = bounds[obj.name]
                value = vec.values[obj.name]
                norm = 0.0 if math.isinf(value) else ((value - lo) / (hi - lo) if hi > lo else 1.0)
                total += obj.weight * norm
            scores.append(total - (1e3 * vec.violations if not vec.feasible else 0.0))
        return scores
