"""ResourceEvaluator — platform-measured resource usage versus the resources that were requested.

Per job (``ExperimentView.resource_usage``):

* ``cpu_efficiency = cpu_seconds / (wall_seconds × requested_cpu)`` (requested CPU from the usage record or
  ``spec.resources.cpu``); the evaluator reports the mean over jobs;
* ``memory_ratio = peak_memory_mb / requested_memory_mb``; the maximum over jobs drives the OOM-risk rating
  (``high`` ≥ ``oom_risk_high`` (default 0.9), ``medium`` ≥ ``oom_risk_medium`` (0.75), else ``low``;
  ``oom`` when a job was OOM-killed);
* ``total_cost_usd`` and, when the primary metric improved over the baseline, ``cost_per_unit_improvement =
  total cost / direction-aware improvement of the mean``.

``passed`` is ``False`` on any OOM kill or timeout, or when a configured limit is violated
(``min_cpu_efficiency``, ``max_memory_ratio``, ``max_cost_usd``, ``max_cost_per_unit_improvement``); ``None``
without usage data. Usage is measured by the platform, so the evaluator is independent;
``confidence = jobs with complete measurements / jobs``.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from engines.lab.evaluators.base import (
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    primary_metric_name,
)


class ResourceConfig(EvaluatorConfig):
    metric: str | None = None
    min_cpu_efficiency: float | None = Field(default=None, ge=0)
    max_memory_ratio: float | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_cost_per_unit_improvement: float | None = Field(default=None, ge=0)
    oom_risk_high: float = Field(default=0.9, gt=0)
    oom_risk_medium: float = Field(default=0.75, gt=0)
    low_efficiency_warning: float = Field(default=0.2, ge=0)


class ResourceEvaluator(BaseEvaluator):
    key = "resource"
    version = "1.0.0"
    kind = "resource"
    name = "Resource efficiency"
    description = "CPU and memory efficiency versus request, OOM risk, total cost and cost per unit of improvement."
    config_schema = ResourceConfig
    independent = True

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: ResourceConfig = config
        usage = experiment.resource_usage
        if not usage:
            return self._result(passed=None, confidence=0.0, warnings=["no resource usage recorded"])
        spec_resources = experiment.spec.resources if experiment.spec else None
        warnings: list[str] = []
        efficiencies: list[float] = []
        ratios: list[float] = []
        cost = 0.0
        cost_known = False
        complete = 0
        evidence: list[dict[str, Any]] = []
        for index, u in enumerate(usage):
            req_cpu = u.requested_cpu or (spec_resources.cpu if spec_resources else None)
            req_mem = u.requested_memory_mb or (float(spec_resources.memory_mb) if spec_resources else None)
            efficiency = None
            if u.cpu_seconds is not None and u.wall_seconds and req_cpu:
                efficiency = u.cpu_seconds / (u.wall_seconds * req_cpu)
                efficiencies.append(efficiency)
            ratio = None
            if u.peak_memory_mb is not None and req_mem:
                ratio = u.peak_memory_mb / req_mem
                ratios.append(ratio)
            if u.cost_usd is not None:
                cost += u.cost_usd
                cost_known = True
            if efficiency is not None and ratio is not None:
                complete += 1
            evidence.append(
                {
                    "check": "usage",
                    "run_id": u.run_id or f"job-{index}",
                    "cpu_efficiency": efficiency,
                    "memory_ratio": ratio,
                    "cost_usd": u.cost_usd,
                    "oom_killed": u.oom_killed,
                    "timed_out": u.timed_out,
                    "exit_code": u.exit_code,
                }
            )
        oom = any(u.oom_killed for u in usage)
        timed_out = any(u.timed_out for u in usage)
        max_ratio = max(ratios) if ratios else None
        mean_efficiency = math.fsum(efficiencies) / len(efficiencies) if efficiencies else None
        if oom:
            risk = "oom"
            warnings.append("at least one job was killed for exceeding its memory limit")
        elif max_ratio is None:
            risk = "unknown"
        elif max_ratio >= cfg.oom_risk_high:
            risk = "high"
            warnings.append(f"peak memory reached {max_ratio:.0%} of the request: high OOM risk")
        elif max_ratio >= cfg.oom_risk_medium:
            risk = "medium"
        else:
            risk = "low"
        if timed_out:
            warnings.append("at least one job hit its timeout")
        if mean_efficiency is not None and mean_efficiency < cfg.low_efficiency_warning:
            warnings.append(f"mean CPU efficiency {mean_efficiency:.0%}: CPU request looks over-provisioned")
        metric = cfg.metric or primary_metric_name(experiment)
        improvement = None
        cost_per_unit = None
        if metric is not None:
            cand = experiment.values(metric)
            base = experiment.values(metric, baseline=True)
            if cand and base:
                delta = math.fsum(cand) / len(cand) - math.fsum(base) / len(base)
                improvement = delta if (experiment.direction_of(metric) or "maximize") == "maximize" else -delta
                if improvement > 0 and cost_known:
                    cost_per_unit = cost / improvement
                elif improvement <= 0:
                    warnings.append(f"no improvement on {metric!r}: cost per unit improvement undefined")
        violations: list[str] = []
        if oom:
            violations.append("oom_killed")
        if timed_out:
            violations.append("timed_out")
        if (
            cfg.min_cpu_efficiency is not None
            and mean_efficiency is not None
            and mean_efficiency < cfg.min_cpu_efficiency
        ):
            violations.append("cpu_efficiency")
        if cfg.max_memory_ratio is not None and max_ratio is not None and max_ratio > cfg.max_memory_ratio:
            violations.append("memory_ratio")
        if cfg.max_cost_usd is not None and cost_known and cost > cfg.max_cost_usd:
            violations.append("cost")
        limit = cfg.max_cost_per_unit_improvement
        if limit is not None and cost_per_unit is not None and cost_per_unit > limit:
            violations.append("cost_per_unit_improvement")
        metrics: dict[str, Any] = {
            "jobs": float(len(usage)),
            "cpu_efficiency": mean_efficiency,
            "max_memory_ratio": max_ratio,
            "total_cost_usd": cost if cost_known else None,
            "improvement": improvement,
            "cost_per_unit_improvement": cost_per_unit,
        }
        return self._result(
            passed=not violations,
            confidence=complete / len(usage),
            metrics=metrics,
            warnings=warnings,
            evidence=evidence,
            details={"oom_risk": risk, "violations": violations, "metric": metric},
        )
