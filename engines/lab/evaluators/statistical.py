"""StatisticalEvaluator — does the evidence comply with the pre-registered statistical plan?

The evaluator recomputes the comparisons itself (it does not trust statistics reported elsewhere) and checks:

``n_seeds``           both arms have at least ``plan.n_seeds`` distinct succeeded seeds;
``seeds_declared``    every run seed was pre-registered in ``spec.seeds`` (undeclared seeds suggest seed
                      shopping); declared seeds without a succeeded run are reported as a warning;
``correction``        a multiple-comparison correction is applied whenever more than one comparison is tested;
``significance``      the primary metric's adjusted p-value is ``<= plan.alpha``;
``ci_direction``      the CI of ``candidate - baseline`` excludes 0 on the improving side;
``min_effect``        ``|effect size| >= plan.min_effect_size`` (only when the plan sets one).

``passed`` is ``True`` only when every applicable check passes, ``None`` with insufficient data.
``confidence = min(1, n_min / plan.n_seeds)`` — how completely the plan's data requirement is met.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from engines.lab.comparison import MetricSamples, compare_many
from engines.lab.evaluators.base import (
    INDEPENDENT_SOURCES,
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    criteria_for,
    plan_for,
    primary_metric_name,
)
from engines.lab.evaluators.benchmark import samples_for


class StatisticalConfig(EvaluatorConfig):
    metrics: list[str] = Field(default_factory=list, max_length=50)
    require_correction: bool = True
    require_ci_excludes_zero: bool = True
    require_declared_seeds: bool = True


class StatisticalEvaluator(BaseEvaluator):
    key = "statistical"
    version = "1.0.0"
    kind = "statistical"
    name = "Statistical plan compliance"
    description = (
        "Checks seeds, multiple-comparison correction, adjusted significance, CI direction and minimum effect "
        "size against the pre-registered statistical plan."
    )
    config_schema = StatisticalConfig
    independent = False

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: StatisticalConfig = config
        plan = plan_for(experiment, context)
        primary = primary_metric_name(experiment)
        names = list(cfg.metrics)
        if not names:
            names = [primary] if primary else []
            for criterion in criteria_for(experiment, context):
                if criterion.metric not in names:
                    names.append(criterion.metric)
        if not names:
            return self._result(passed=None, confidence=0.0, warnings=["no metrics to analyse"])
        primary = primary if primary in names else names[0]
        warnings: list[str] = []
        checks: list[dict[str, Any]] = []

        cand_seeds = {r.seed for r in experiment.runs if r.succeeded and r.seed is not None}
        base_seeds = {r.seed for r in experiment.baseline_runs if r.succeeded and r.seed is not None}
        n_cand = len(cand_seeds) or sum(1 for r in experiment.runs if r.succeeded)
        n_base = len(base_seeds) or sum(1 for r in experiment.baseline_runs if r.succeeded)
        checks.append(
            {
                "check": "n_seeds",
                "passed": n_cand >= plan.n_seeds and n_base >= plan.n_seeds,
                "detail": f"candidate {n_cand}, baseline {n_base}, required {plan.n_seeds}",
            }
        )
        if experiment.spec is not None and experiment.spec.seeds:
            declared = set(experiment.spec.seeds)
            undeclared = sorted((cand_seeds | base_seeds) - declared)
            missing = sorted(declared - cand_seeds)
            if missing:
                warnings.append(f"declared seed(s) without a succeeded candidate run: {missing[:20]}")
            if cfg.require_declared_seeds:
                checks.append(
                    {
                        "check": "seeds_declared",
                        "passed": not undeclared,
                        "detail": f"undeclared seeds: {undeclared[:20]}" if undeclared else "all seeds pre-registered",
                    }
                )

        paired = plan.test == "paired_t"
        items = []
        for name in names:
            direction = experiment.direction_of(name) or "maximize"
            base, cand, _ = samples_for(experiment, name, paired=paired)
            items.append(MetricSamples(metric=name, direction=direction, baseline=base, candidate=cand))
        results = compare_many(items, plan)
        by_metric = {r.metric: r for r in results}
        tested = sum(1 for r in results if r.verdict != "insufficient_data")
        if cfg.require_correction:
            checks.append(
                {
                    "check": "correction",
                    "passed": tested <= 1 or plan.correction != "none",
                    "detail": f"{tested} comparison(s) tested with correction {plan.correction!r}",
                }
            )
        main = by_metric[primary]
        insufficient = main.verdict == "insufficient_data"
        if insufficient:
            warnings.append(f"{primary}: {main.rationale}")
        else:
            p = main.p_value_for_decision
            checks.append(
                {
                    "check": "significance",
                    "passed": p is not None and p <= plan.alpha,
                    "detail": f"adjusted p {p} vs alpha {plan.alpha}",
                }
            )
            if cfg.require_ci_excludes_zero:
                excludes = main.ci_excludes_zero_in_improving_direction
                checks.append(
                    {
                        "check": "ci_direction",
                        "passed": bool(excludes),
                        "detail": (
                            f"{main.statistics.ci_level:.0%} CI [{main.statistics.ci_low}, {main.statistics.ci_high}] "
                            f"for a {main.direction} metric"
                        ),
                    }
                )
            if plan.min_effect_size is not None:
                effect = main.statistics.effect_size
                # A null effect with p == 0 means zero within-arm variance (an infinite standardised effect).
                infinite_effect = effect is None and main.statistics.p_value == 0.0
                checks.append(
                    {
                        "check": "min_effect",
                        "passed": infinite_effect or (effect is not None and abs(effect) >= plan.min_effect_size),
                        "detail": f"|{main.statistics.effect_size_kind}| {effect} vs minimum {plan.min_effect_size}",
                    }
                )
        for result in results:
            warnings.extend(f"{result.metric}: {w}" for w in result.warnings)
        failed = [c["check"] for c in checks if not c["passed"]]
        if insufficient:
            passed: bool | None = None if not failed else False
        else:
            passed = not failed
        sufficiency = min(1.0, min(n_cand, n_base) / max(plan.n_seeds, 1))
        sources = {experiment.source_of(n) for n in names}
        return self._result(
            passed=passed,
            confidence=sufficiency,
            metrics={
                "checks_passed": float(sum(1 for c in checks if c["passed"])),
                "checks_total": float(len(checks)),
                "family_size": float(max(1, tested)),
                primary: {
                    "verdict": main.verdict,
                    "p_value": main.statistics.p_value,
                    "p_value_adjusted": main.statistics.p_value_adjusted,
                    "ci_low": main.statistics.ci_low,
                    "ci_high": main.statistics.ci_high,
                    "effect_size": main.statistics.effect_size,
                },
            },
            warnings=warnings,
            evidence=[{"check": c["check"], "passed": c["passed"], "detail": c["detail"]} for c in checks]
            + [
                {"check": "comparison", "metric": r.metric, "verdict": r.verdict, "rationale": r.rationale}
                for r in results
            ],
            independent=sources <= INDEPENDENT_SOURCES,
            details={"failed_checks": failed, "plan": plan.model_dump(), "primary_metric": primary},
        )
