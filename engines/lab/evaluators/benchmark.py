"""BenchmarkEvaluator — baseline vs candidate across seeds under the pre-registered statistical plan.

The primary metric (or ``config.metric``) and optional ``guard_metrics`` are compared as one family with
:func:`engines.lab.comparison.compare_many` (multiplicity correction from the plan). For ``paired_t`` runs are
paired by seed. The evaluator passes when the primary verdict is ``improved`` — which already requires the
effect to reach ``min_effect`` / ``min_delta`` — and no guard metric ``regressed``; it returns ``passed=None``
when the primary comparison has insufficient data.

``confidence = sufficiency × strength`` with ``sufficiency = min(1, n_min / plan.n_seeds)`` and ``strength =
1 - p_adjusted`` for a decided verdict (improved/regressed) or ``0.5`` when there is no significant difference.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from engines.lab.comparison import ComparisonResult, MetricSamples, align_by_seed, compare_many
from engines.lab.evaluators.base import (
    INDEPENDENT_SOURCES,
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    plan_for,
    primary_metric_name,
)


class BenchmarkConfig(EvaluatorConfig):
    metric: str | None = None
    guard_metrics: list[str] = Field(default_factory=list, max_length=20)
    min_effect: float | None = Field(default=None, ge=0)
    min_delta: float | None = Field(default=None, ge=0)
    pair_by_seed: bool = True


def samples_for(
    experiment: ExperimentView, metric: str, *, paired: bool
) -> tuple[list[float], list[float], list[int] | None]:
    """Baseline and candidate samples for ``metric`` (paired by seed when ``paired``)."""
    if paired:
        base, cand, seeds = align_by_seed(
            experiment.values_by_seed(metric, baseline=True), experiment.values_by_seed(metric)
        )
        return base, cand, seeds
    return experiment.values(metric, baseline=True), experiment.values(metric), None


class BenchmarkEvaluator(BaseEvaluator):
    key = "benchmark"
    version = "1.0.0"
    kind = "benchmark"
    name = "Baseline vs candidate benchmark"
    description = (
        "Compares candidate runs against baseline runs across seeds with the pre-registered test and "
        "multiple-comparison correction; passes only on a significant, practically relevant improvement."
    )
    config_schema = BenchmarkConfig
    independent = False

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: BenchmarkConfig = config
        plan = plan_for(experiment, context)
        primary = cfg.metric or primary_metric_name(experiment)
        if primary is None:
            return self._result(passed=None, confidence=0.0, warnings=["no metric configured and no primary metric"])
        warnings: list[str] = []
        metric_names = [primary] + [g for g in cfg.guard_metrics if g != primary]
        paired = plan.test == "paired_t" and cfg.pair_by_seed
        items: list[MetricSamples] = []
        seeds_used: dict[str, list[int] | None] = {}
        for name in metric_names:
            direction = experiment.direction_of(name)
            if direction is None:
                warnings.append(f"metric {name!r} is not declared in the spec; assuming maximize")
                direction = "maximize"
            base, cand, seeds = samples_for(experiment, name, paired=paired)
            seeds_used[name] = seeds
            items.append(
                MetricSamples(
                    metric=name,
                    direction=direction,
                    baseline=base,
                    candidate=cand,
                    min_effect=cfg.min_effect if name == primary else None,
                    min_delta=cfg.min_delta if name == primary else None,
                )
            )
        results = compare_many(items, plan)
        primary_result = results[0]
        guards = results[1:]
        for result in results:
            warnings.extend(f"{result.metric}: {w}" for w in result.warnings)
        if primary_result.verdict == "insufficient_data":
            passed: bool | None = None
        else:
            passed = primary_result.verdict == "improved" and not any(g.verdict == "regressed" for g in guards)
        regressed_guards = [g.metric for g in guards if g.verdict == "regressed"]
        if regressed_guards:
            warnings.append(f"guard metric(s) regressed: {', '.join(regressed_guards)}")
        s = primary_result.statistics
        sufficiency = min(1.0, min(s.n_baseline, s.n_candidate) / max(plan.n_seeds, 1))
        p = primary_result.p_value_for_decision
        if primary_result.verdict in ("improved", "regressed") and p is not None:
            strength = 1.0 - p
        elif primary_result.verdict == "no_significant_difference":
            strength = 0.5
        else:
            strength = 0.0
        sources = {experiment.source_of(n) for n in metric_names}
        independent = sources <= INDEPENDENT_SOURCES
        return self._result(
            passed=passed,
            confidence=sufficiency * strength,
            metrics={r.metric: _summary(r) for r in results},
            warnings=warnings,
            evidence=[
                {
                    "check": "comparison",
                    "metric": r.metric,
                    "verdict": r.verdict,
                    "statistics": r.statistics.model_dump(),
                    "rationale": r.rationale,
                    "paired_seeds": seeds_used.get(r.metric),
                    "run_ids": experiment.run_ids(),
                    "baseline_run_ids": experiment.run_ids(baseline=True),
                    "engine_version": r.engine_version,
                }
                for r in results
            ],
            independent=independent,
            details={
                "primary_metric": primary,
                "verdict": primary_result.verdict,
                "guard_verdicts": {g.metric: g.verdict for g in guards},
                "rationale": primary_result.rationale,
            },
        )


def _summary(result: ComparisonResult) -> dict[str, Any]:
    s = result.statistics
    return {
        "verdict": result.verdict,
        "p_value": s.p_value,
        "p_value_adjusted": s.p_value_adjusted,
        "effect_size": s.effect_size,
        "effect_size_kind": s.effect_size_kind,
        "delta": s.delta,
        "ci_low": s.ci_low,
        "ci_high": s.ci_high,
        "n_baseline": s.n_baseline,
        "n_candidate": s.n_candidate,
    }
