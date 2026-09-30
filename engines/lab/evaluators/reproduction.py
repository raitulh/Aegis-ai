"""ReproductionEvaluator — do independently re-run results match the original ones within tolerance?

Original values come from ``config.original`` or the aggregate of the experiment's ``baseline_runs`` (for a
reproduction experiment the baseline *is* the original); reproduced values from ``config.reproduced`` or the
aggregate of ``runs``. A metric is reproduced when

    |reproduced - original| <= max(abs_tol, rel_tol × |original|)

(per-metric tolerances override the defaults). With ``one_sided=True`` a reproduced value that is *better*
than the original (direction-aware) also counts as reproduced.

Verdict: ``reproduced`` (every compared metric within tolerance), ``not_reproduced`` (the primary metric — or
every metric — outside tolerance), ``partially_reproduced`` (otherwise), ``inconclusive`` (nothing comparable).
``passed`` is ``True`` only for ``reproduced`` (``None`` when inconclusive).
``confidence = compared metrics / requested metrics`` (1 when all requested metrics could be compared).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.evaluators.base import (
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    aggregate,
    primary_metric_name,
)

ReproductionVerdict = Literal["reproduced", "partially_reproduced", "not_reproduced", "inconclusive"]


class Tolerance(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    abs_tol: float | None = Field(default=None, ge=0)
    rel_tol: float | None = Field(default=None, ge=0)


class ReproductionConfig(EvaluatorConfig):
    original: dict[str, float] | None = None
    reproduced: dict[str, float] | None = None
    metrics: list[str] = Field(default_factory=list, max_length=100)
    primary_metric: str | None = None
    abs_tol: float = Field(default=0.0, ge=0)
    rel_tol: float = Field(default=0.05, ge=0)
    per_metric: dict[str, Tolerance] = Field(default_factory=dict)
    one_sided: bool = False
    aggregate: Literal["mean", "median"] = "mean"


def within_tolerance(
    original: float,
    reproduced: float,
    *,
    abs_tol: float,
    rel_tol: float,
    one_sided: bool = False,
    direction: str = "maximize",
) -> tuple[bool, float]:
    """Return ``(ok, allowed_difference)`` for one metric (see module docstring)."""
    allowed = max(abs_tol, rel_tol * abs(original))
    diff = reproduced - original
    if abs(diff) <= allowed:
        return True, allowed
    if one_sided and ((direction == "maximize" and diff > 0) or (direction == "minimize" and diff < 0)):
        return True, allowed
    return False, allowed


def reproduction_verdict(outcomes: dict[str, bool], primary: str | None) -> ReproductionVerdict:
    if not outcomes:
        return "inconclusive"
    if all(outcomes.values()):
        return "reproduced"
    if not any(outcomes.values()) or (primary is not None and outcomes.get(primary) is False):
        return "not_reproduced"
    return "partially_reproduced"


class ReproductionEvaluator(BaseEvaluator):
    key = "reproduction"
    version = "1.0.0"
    kind = "reproduction"
    name = "Reproduction tolerance check"
    description = "Compares original and reproduced metric values per metric with absolute/relative tolerance."
    config_schema = ReproductionConfig
    independent = True

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: ReproductionConfig = config
        warnings: list[str] = []
        primary = cfg.primary_metric or primary_metric_name(experiment)
        requested = list(cfg.metrics)
        if not requested:
            names: set[str] = set(cfg.original or {}) | set(cfg.reproduced or {})
            if experiment.spec:
                names |= set(experiment.spec.metric_names)
            for run in experiment.runs + experiment.baseline_runs:
                names |= set(run.metrics)
            requested = sorted(names)
        per_metric: dict[str, Any] = {}
        outcomes: dict[str, bool] = {}
        for name in requested:
            original = self._value(cfg.original, experiment, name, baseline=True, how=cfg.aggregate)
            reproduced = self._value(cfg.reproduced, experiment, name, baseline=False, how=cfg.aggregate)
            if original is None or reproduced is None:
                warnings.append(f"{name!r}: missing {'original' if original is None else 'reproduced'} value")
                continue
            tol = cfg.per_metric.get(name, Tolerance())
            abs_tol = cfg.abs_tol if tol.abs_tol is None else tol.abs_tol
            rel_tol = cfg.rel_tol if tol.rel_tol is None else tol.rel_tol
            direction = experiment.direction_of(name) or "maximize"
            ok, allowed = within_tolerance(
                original, reproduced, abs_tol=abs_tol, rel_tol=rel_tol, one_sided=cfg.one_sided, direction=direction
            )
            outcomes[name] = ok
            diff = reproduced - original
            per_metric[name] = {
                "original": original,
                "reproduced": reproduced,
                "abs_diff": abs(diff),
                "rel_diff": abs(diff) / abs(original) if original != 0 else None,
                "allowed_diff": allowed,
                "abs_tol": abs_tol,
                "rel_tol": rel_tol,
                "within_tolerance": ok,
            }
        if primary is not None and primary not in outcomes and primary in requested:
            warnings.append(f"primary metric {primary!r} could not be compared")
        verdict = reproduction_verdict(outcomes, primary if primary in outcomes else None)
        passed = None if verdict == "inconclusive" else verdict == "reproduced"
        n_ok = sum(outcomes.values())
        metrics: dict[str, Any] = {name: per_metric[name] for name in per_metric}
        metrics["reproduced_fraction"] = n_ok / len(outcomes) if outcomes else None
        return self._result(
            passed=passed,
            confidence=len(outcomes) / len(requested) if requested else 0.0,
            metrics=metrics,
            warnings=warnings,
            evidence=[
                {
                    "check": "reproduction",
                    "metric": name,
                    **values,
                    "original_run_ids": experiment.run_ids(baseline=True),
                    "reproduction_run_ids": experiment.run_ids(),
                }
                for name, values in per_metric.items()
            ],
            details={"verdict": verdict, "primary_metric": primary, "one_sided": cfg.one_sided},
        )

    @staticmethod
    def _value(
        explicit: dict[str, float] | None, experiment: ExperimentView, name: str, *, baseline: bool, how: str
    ) -> float | None:
        if explicit is not None and name in explicit:
            value = float(explicit[name])
            return value if math.isfinite(value) else None
        return aggregate(experiment.values(name, baseline=baseline), how)
