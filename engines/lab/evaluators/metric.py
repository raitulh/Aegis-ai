"""MetricEvaluator — pre-registered success criteria checked against run metrics aggregated over seeds.

For each criterion the candidate value is the configured aggregate (default mean) over succeeded runs; the
baseline value is the aggregate over baseline runs or, failing that, the spec's reference value. Semantics of
every comparator come from :func:`engines.lab.experiment_spec.check_criterion`.

``passed``: ``True`` when every criterion is satisfied, ``False`` when any is violated, ``None`` when none is
violated but at least one cannot be evaluated (missing data).

``confidence = coverage × sufficiency × provenance`` where ``coverage`` = evaluable criteria / criteria,
``sufficiency`` = min(1, smallest n / required n), ``provenance`` = 1 when every metric used is platform- or
evaluator-measured, else 0.5 (self-reported numbers are weaker evidence). The result is ``independent`` only
when no self-reported metric was used.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from engines.lab.evaluators.base import (
    INDEPENDENT_SOURCES,
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    aggregate,
    criteria_for,
    plan_for,
)
from engines.lab.experiment_spec import check_criterion


class MetricEvaluatorConfig(EvaluatorConfig):
    aggregate: Literal["mean", "median", "min", "max", "worst"] = "mean"
    baseline_aggregate: Literal["mean", "median"] = "mean"
    require_all_seeds: bool = False
    min_runs: int | None = Field(default=None, ge=1)


class MetricEvaluator(BaseEvaluator):
    key = "metric"
    version = "1.0.0"
    kind = "metric"
    name = "Success-criteria metric check"
    description = "Checks pre-registered success criteria against run metrics aggregated over seeds."
    config_schema = MetricEvaluatorConfig
    independent = False

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: MetricEvaluatorConfig = config
        criteria = criteria_for(experiment, context)
        if not criteria:
            return self._result(
                passed=None, confidence=0.0, warnings=["no success criteria to evaluate"], independent=False
            )
        plan = plan_for(experiment, context)
        required_n = cfg.min_runs or plan.n_seeds
        warnings: list[str] = []
        evidence: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        outcomes: list[bool | None] = []
        min_n = None
        sources: set[str] = set()
        for index, criterion in enumerate(criteria):
            name = criterion.metric
            direction = experiment.direction_of(name)
            if direction is None:
                warnings.append(f"metric {name!r} is not declared in the spec; assuming maximize")
                direction = "maximize"
            sources.add(experiment.source_of(name))
            values = experiment.values(name)
            dropped = experiment.non_finite_count(name)
            if dropped:
                warnings.append(f"{dropped} non-finite value(s) of {name!r} excluded")
            n = len(values)
            min_n = n if min_n is None else min(min_n, n)
            baseline_values = experiment.values(name, baseline=True)
            if baseline_values:
                baseline_value = aggregate(baseline_values, cfg.baseline_aggregate)
                baseline_source = "baseline_runs"
            elif experiment.spec and name in experiment.spec.baseline.reference_metrics:
                baseline_value = float(experiment.spec.baseline.reference_metrics[name])
                baseline_source = "reference_values"
            else:
                baseline_value, baseline_source = None, None
            if n < required_n:
                warnings.append(f"{name!r}: {n} succeeded run(s) with a value, {required_n} required")
                check = check_criterion(criterion, None, baseline_value, direction)
                satisfied: bool | None = None
            else:
                value = aggregate(values, cfg.aggregate, direction)
                check = check_criterion(criterion, value, baseline_value, direction)
                satisfied = check.satisfied
                if cfg.require_all_seeds and satisfied:
                    per_seed = [check_criterion(criterion, v, baseline_value, direction).satisfied for v in values]
                    if not all(per_seed):
                        satisfied = False
                        warnings.append(f"{name!r}: criterion holds on the aggregate but not on every seed")
            if satisfied is None and check.reason and n >= required_n:
                warnings.append(f"{name!r}: criterion not evaluable ({check.reason})")
            outcomes.append(satisfied)
            metrics[f"criterion_{index}"] = {
                **check.model_dump(),
                "satisfied": satisfied,
                "n": n,
                "aggregate": cfg.aggregate,
                "baseline_source": baseline_source,
            }
            evidence.append(
                {
                    "check": "success_criterion",
                    "criterion": criterion.model_dump(),
                    "value": check.value,
                    "baseline_value": baseline_value,
                    "baseline_source": baseline_source,
                    "satisfied": satisfied,
                    "n": n,
                    "metric_source": experiment.source_of(name),
                    "run_ids": experiment.run_ids(),
                    "baseline_run_ids": experiment.run_ids(baseline=True),
                }
            )
        evaluable = [o for o in outcomes if o is not None]
        if any(o is False for o in outcomes):
            passed: bool | None = False
        elif len(evaluable) == len(outcomes):
            passed = True
        else:
            passed = None
        coverage = len(evaluable) / len(outcomes)
        sufficiency = min(1.0, (min_n or 0) / required_n)
        independent = bool(sources) and sources <= INDEPENDENT_SOURCES
        provenance = 1.0 if independent else 0.5
        if not independent:
            warnings.append("verdict relies on self-reported metrics; an independent evaluator is recommended")
        metrics["criteria_satisfied"] = float(sum(1 for o in outcomes if o is True))
        metrics["criteria_total"] = float(len(outcomes))
        return self._result(
            passed=passed,
            confidence=coverage * sufficiency * provenance,
            metrics=metrics,
            warnings=warnings,
            evidence=evidence,
            independent=independent,
            details={"coverage": coverage, "sufficiency": sufficiency, "metric_sources": sorted(sources)},
        )
