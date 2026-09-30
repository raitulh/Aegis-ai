"""PromotionGate and RollbackManager.

A candidate strategy becomes eligible for promotion only when *evidence* shows it is better than the
incumbent: enough independent evaluations, Pareto non-inferiority, a statistically supported improvement on
the primary objective, no safety regression and a successful reproduction. Eligibility is a necessary
condition; whether promotion also needs a human is decided by the policy engine (autonomy level + org policy).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from engines.lab.evaluation.statistics import hedges_g, run_test
from engines.lab.evolution.fitness import FitnessVector
from engines.lab.evolution.pareto import dominates


@dataclass(frozen=True)
class PromotionCriteria:
    min_evaluations: int = 3
    alpha: float = 0.05
    test: str = "welch_t"
    require_non_dominated: bool = True
    require_reproduction: bool = True
    min_primary_improvement: float = 0.0
    safety_objective: str = "safety"


@dataclass
class CandidateStats:
    individual_id: str
    fitness: FitnessVector
    primary_samples: list[float]
    evaluations: int
    reproduced: bool | None = None


@dataclass
class PromotionDecision:
    eligible: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    p_value: float | None = None
    effect_size: float | None = None
    primary_improvement: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "checks": self.checks,
            "reasons": self.reasons,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "primary_improvement": self.primary_improvement,
        }


class PromotionGate:
    def __init__(self, keys: Sequence[str], criteria: PromotionCriteria | None = None) -> None:
        self.keys = list(keys)
        self.criteria = criteria or PromotionCriteria()

    def evaluate(
        self, candidate: CandidateStats, incumbent: CandidateStats | None, *, primary_direction: str = "maximize"
    ) -> PromotionDecision:
        c = self.criteria
        decision = PromotionDecision(eligible=False)
        checks = decision.checks
        checks["feasible"] = candidate.fitness.feasible
        if not checks["feasible"]:
            decision.reasons.append("candidate violates hard constraints: " + "; ".join(candidate.fitness.violated))
        checks["min_evaluations"] = candidate.evaluations >= c.min_evaluations
        if not checks["min_evaluations"]:
            decision.reasons.append(f"only {candidate.evaluations} evaluation(s); {c.min_evaluations} required")
        if c.require_reproduction:
            checks["reproduced"] = candidate.reproduced is True
            if not checks["reproduced"]:
                decision.reasons.append("no successful independent reproduction")
        if incumbent is None:
            checks["beats_incumbent"] = True
            decision.eligible = all(checks.values())
            if decision.eligible:
                decision.reasons.append("no incumbent; first strategy meeting the evidence bar")
            return decision

        if c.require_non_dominated:
            checks["not_dominated"] = not dominates(incumbent.fitness, candidate.fitness, self.keys)
            if not checks["not_dominated"]:
                decision.reasons.append("incumbent Pareto-dominates the candidate")
        inc_safety = incumbent.fitness.values.get(c.safety_objective)
        cand_safety = candidate.fitness.values.get(c.safety_objective)
        if (
            inc_safety is not None
            and cand_safety is not None
            and not (math.isinf(inc_safety) or math.isinf(cand_safety))
        ):
            checks["no_safety_regression"] = cand_safety >= inc_safety
            if not checks["no_safety_regression"]:
                decision.reasons.append("safety objective regressed versus the incumbent")
        if len(candidate.primary_samples) >= 2 and len(incumbent.primary_samples) >= 2:
            res = run_test(c.test, candidate.primary_samples, incumbent.primary_samples)
            g = hedges_g(candidate.primary_samples, incumbent.primary_samples)
            oriented = g if primary_direction == "maximize" else -g
            cand_mean = sum(candidate.primary_samples) / len(candidate.primary_samples)
            inc_mean = sum(incumbent.primary_samples) / len(incumbent.primary_samples)
            improvement = (cand_mean - inc_mean) if primary_direction == "maximize" else (inc_mean - cand_mean)
            decision.p_value, decision.effect_size, decision.primary_improvement = res.p_value, oriented, improvement
            checks["statistically_better"] = res.p_value < c.alpha and oriented > 0
            checks["meets_min_improvement"] = improvement >= c.min_primary_improvement and improvement > 0
            if not checks["statistically_better"]:
                decision.reasons.append(f"primary improvement not significant (p={res.p_value:.4f}, g={oriented:.3f})")
            if not checks["meets_min_improvement"]:
                decision.reasons.append(
                    f"primary improvement {improvement:.4g} below required {c.min_primary_improvement}"
                )
        else:
            checks["statistically_better"] = False
            decision.reasons.append("not enough primary-metric samples for a statistical comparison")
        decision.eligible = all(checks.values())
        return decision


@dataclass(frozen=True)
class VersionRecord:
    version_id: str
    status: str
    promoted_at: str | None = None


class RollbackManager:
    """Choose the version to restore when a promoted strategy is rolled back."""

    @staticmethod
    def rollback_target(history: Sequence[VersionRecord], current_version_id: str) -> str | None:
        promoted = [h for h in history if h.promoted_at and h.version_id != current_version_id]
        promoted.sort(key=lambda h: h.promoted_at or "")
        for record in reversed(promoted):
            if record.status not in ("rolled_back", "retired"):
                return record.version_id
        return promoted[-1].version_id if promoted else None
