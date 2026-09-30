"""CustomEvaluator — a user-defined boolean rule over aggregated metrics, e.g.
``{"expression": "accuracy >= 0.9 and latency_ms < 200"}``.

The expression is compiled by :mod:`engines.lab.evaluators.expressions` (a restricted, ``ast``-walking
evaluator — never ``eval``) when the config is validated, so an unsafe expression can never be stored.
Variables are the aggregated metric values of succeeded runs (default mean); metric names that are not valid
identifiers (``val/loss``) are exposed with non-identifier characters replaced by ``_`` (``val_loss``) or through
explicit ``variables`` aliases. Baseline aggregates are exposed as ``baseline_<name>``.

A boolean result becomes ``passed``; a numeric result is reported as ``metrics["value"]`` with ``passed=None``.
A missing variable yields ``passed=None`` and a warning. ``confidence`` is ``min(1, n_min / plan.n_seeds)``
over the metrics the expression reads; the verdict rests on run-reported metrics, so it is not independent
unless all of them are platform- or evaluator-measured.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from engines.lab.evaluators.base import (
    INDEPENDENT_SOURCES,
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    aggregate,
    plan_for,
)
from engines.lab.evaluators.expressions import ExpressionError, compile_expression

_NON_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]")


def variable_name(metric: str) -> str:
    name = _NON_IDENTIFIER.sub("_", metric)
    return name if not name[:1].isdigit() else f"m_{name}"


class CustomConfig(EvaluatorConfig):
    expression: str = Field(min_length=1, max_length=1000)
    aggregate: Literal["mean", "median", "min", "max", "worst"] = "mean"
    variables: dict[str, str] = Field(default_factory=dict, max_length=100)
    include_baseline: bool = True

    @field_validator("variables")
    @classmethod
    def _identifiers(cls, value: dict[str, str]) -> dict[str, str]:
        for alias in value:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", alias):
                raise ValueError(f"variable alias {alias!r} must be an identifier (letters, digits, underscore)")
        return value

    @field_validator("expression")
    @classmethod
    def _safe(cls, value: str) -> str:
        try:
            compile_expression(value)
        except ExpressionError as exc:
            raise ValueError(f"unsafe or invalid expression: {exc}") from exc
        return value


class CustomEvaluator(BaseEvaluator):
    key = "custom"
    version = "1.0.0"
    kind = "custom"
    name = "Custom metric rule"
    description = "Evaluates a restricted boolean expression over aggregated run metrics."
    config_schema = CustomConfig
    independent = False

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: CustomConfig = config
        compiled = compile_expression(cfg.expression)
        metric_names: set[str] = set()
        for run in experiment.runs + experiment.baseline_runs:
            metric_names |= set(run.metrics)
        if experiment.spec:
            metric_names |= set(experiment.spec.metric_names)
        variables: dict[str, float] = {}
        sources: dict[str, str] = {}
        counts: dict[str, int] = {}
        mapping = {variable_name(m): m for m in sorted(metric_names)}
        mapping.update(cfg.variables)
        for var, metric in mapping.items():
            values = experiment.values(metric)
            value = aggregate(values, cfg.aggregate, experiment.direction_of(metric))
            if value is not None:
                variables[var] = value
                sources[var] = experiment.source_of(metric)
                counts[var] = len(values)
            if cfg.include_baseline:
                base = aggregate(experiment.values(metric, baseline=True), "mean")
                if base is not None:
                    variables[f"baseline_{var}"] = base
                    sources[f"baseline_{var}"] = experiment.source_of(metric)
                    counts[f"baseline_{var}"] = len(experiment.values(metric, baseline=True))
        used = sorted(compiled.names)
        evidence: list[dict[str, Any]] = [
            {
                "check": "expression",
                "expression": cfg.expression,
                "variables": {n: variables.get(n) for n in used},
                "aggregate": cfg.aggregate,
                "run_ids": experiment.run_ids(),
            }
        ]
        missing = [n for n in used if n not in variables]
        if missing:
            return self._result(
                passed=None,
                confidence=0.0,
                warnings=[f"variable(s) not available: {', '.join(missing)}"],
                evidence=evidence,
                independent=False,
            )
        try:
            result = compiled.evaluate(variables)
        except ExpressionError as exc:
            return self._result(
                passed=None,
                confidence=0.0,
                warnings=[f"expression failed: {exc}"],
                evidence=evidence,
                independent=False,
            )
        plan = plan_for(experiment, context)
        n_min = min((counts.get(n, 0) for n in used), default=0)
        confidence = min(1.0, n_min / max(plan.n_seeds, 1)) if used else 1.0
        independent = bool(used) and all(sources.get(n) in INDEPENDENT_SOURCES for n in used)
        metrics: dict[str, Any] = {n: variables[n] for n in used}
        warnings: list[str] = []
        if isinstance(result, bool):
            passed: bool | None = result
            metrics["result"] = 1.0 if result else 0.0
        else:
            passed = None
            metrics["value"] = float(result)
            warnings.append("expression is numeric, not boolean: no pass/fail decision")
        evidence[0]["result"] = result
        return self._result(
            passed=passed,
            confidence=confidence,
            metrics=metrics,
            warnings=warnings,
            evidence=evidence,
            independent=independent,
            details={"expression": cfg.expression, "variables_used": used},
        )
