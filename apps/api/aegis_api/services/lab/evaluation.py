"""Evaluation service: runs versioned platform evaluators over *measured* run metrics.

Baseline and candidate samples come from completed runs of the same experiment version (one value per seed).
Each evaluator result is persisted (``lab.evaluation_runs``, immutable) with its fingerprint and sealed as
evidence; per-metric baseline-vs-candidate comparisons with CIs, tests, corrected p-values and effect sizes are
stored as ``lab.run_comparisons``. Self-reported metrics lower confidence and are always flagged.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound
from aegis_api.models.lab import (
    EvaluationRun,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    RunComparison,
)
from aegis_api.services.lab import events, evidence, graph
from aegis_api.services.lab.common import Actor
from engines.lab.enums import ExperimentStatus, HypothesisStatus, LabEventType, RunKind
from engines.lab.evaluation.base import EvaluationContext
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY, STANDARD_SUITE
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.state_machines import EXPERIMENT, HYPOTHESIS


def _samples(runs: list[ExperimentRun]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in sorted(runs, key=lambda x: (x.seed, str(x.id))):
        for k, v in (r.metrics or {}).items():
            if isinstance(v, int | float):
                out[k].append(float(v))
    return dict(out)


def _resources(runs: list[ExperimentRun]) -> dict[str, Any]:
    if not runs:
        return {}
    runtimes = [float(r.resources.get("runtime_seconds") or 0.0) for r in runs if r.resources]
    peaks = [float(r.resources["peak_memory_mb"]) for r in runs if r.resources and r.resources.get("peak_memory_mb")]
    return {
        "runtime_seconds": max(runtimes) if runtimes else 0.0,
        "peak_memory_mb": max(peaks) if peaks else None,
        "timed_out": any(r.resources.get("timed_out") for r in runs if r.resources),
        "oom_killed": any(r.resources.get("oom_killed") for r in runs if r.resources),
        "exit_code": 0 if all(r.status == "completed" for r in runs) else 1,
        "output_bytes": sum(int(r.resources.get("output_bytes") or 0) for r in runs if r.resources),
    }


def evaluate_experiment(
    organization_id: uuid.UUID,
    experiment_id: uuid.UUID,
    *,
    actor: Actor,
    suite: tuple[str, ...] = STANDARD_SUITE,
    independent: bool = False,
    evaluated_by: str = "platform",
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = ExperimentSpec.model_validate(version.spec)
        runs = list(
            db.scalars(
                select(ExperimentRun).where(
                    ExperimentRun.experiment_version_id == version.id, ExperimentRun.status == "completed"
                )
            ).all()
        )
        baseline_runs = [r for r in runs if r.run_kind == RunKind.BASELINE]
        candidate_runs = [r for r in runs if r.run_kind == RunKind.CANDIDATE]
        repro_runs = [r for r in runs if r.run_kind == RunKind.REPRODUCTION]
        context = EvaluationContext(
            baseline=_samples(baseline_runs),
            candidate=_samples(candidate_runs),
            reproduction=_samples(repro_runs) if repro_runs else None,
            resources=_resources(candidate_runs),
            self_reported=any(r.self_reported for r in candidate_runs + baseline_runs),
            seed=int(str(experiment.id.int)[:9]),
        )
        results = []
        for key in suite:
            evaluator = DEFAULT_REGISTRY.get(key)
            results.append(evaluator.evaluate(spec, {}, context))
        comparisons: dict[str, Any] = {}
        stat_rows: list[dict[str, Any]] = []
        for r in results:
            if r.evaluator_key == "benchmark":
                comparisons = dict(r.details.get("comparisons") or {})
            if r.evaluator_key == "statistical":
                stat_rows = list(r.details.get("rows") or [])
        stored: list[dict[str, Any]] = []
        for r in results:
            record = evidence.seal(
                db,
                organization_id=organization_id,
                mission_id=experiment.mission_id,
                project_id=experiment.project_id,
                kind="evaluation",
                title=f"{r.evaluator_key}@{r.evaluator_version} → {r.verdict}: {experiment.title}"[:300],
                content={
                    "experiment_id": str(experiment.id),
                    "experiment_version": version.version,
                    "evaluator": r.evaluator_key,
                    "fingerprint": r.fingerprint,
                    "verdict": r.verdict,
                    "metrics": r.metrics,
                    "warnings": r.warnings,
                    "evidence": r.evidence,
                    "run_ids": sorted(str(x.id) for x in runs),
                },
                confidence=r.confidence,
            )
            row = EvaluationRun(
                organization_id=organization_id,
                project_id=experiment.project_id,
                experiment_id=experiment.id,
                experiment_version_id=version.id,
                run_ids=sorted(str(x.id) for x in runs),
                evaluator_key=r.evaluator_key,
                evaluator_version=r.evaluator_version,
                evaluator_fingerprint=r.fingerprint,
                verdict=r.verdict,
                passed=r.passed,
                confidence=r.confidence,
                metrics=r.metrics,
                warnings=r.warnings,
                evidence=r.evidence,
                details=r.details,
                evidence_id=record.id,
                independent=independent,
                evaluated_by=evaluated_by,
            )
            db.add(row)
            db.flush()
            stored.append(
                {
                    "evaluation_run_id": str(row.id),
                    "evaluator": r.evaluator_key,
                    "verdict": r.verdict,
                    "passed": r.passed,
                    "confidence": r.confidence,
                    "warnings": r.warnings,
                    "evidence_id": str(record.id),
                }
            )
            graph.link_refs(
                db,
                organization_id=organization_id,
                project_id=experiment.project_id,
                source=("experiment", str(experiment.id), experiment.title),
                target=("evidence", str(record.id), f"{r.evaluator_key} evaluation"),
                relation="evaluated_by",
            )
        rows_by_metric = {row["metric"]: row for row in stat_rows if "metric" in row}
        bench_id = next((s["evaluation_run_id"] for s in stored if s["evaluator"] == "benchmark"), None)
        for metric, comp in comparisons.items():
            stat_row = rows_by_metric.get(metric, {})
            db.add(
                RunComparison(
                    organization_id=organization_id,
                    experiment_id=experiment.id,
                    evaluation_run_id=uuid.UUID(bench_id) if bench_id else None,
                    metric=metric,
                    direction="max" if comp.get("direction") == "maximize" else "min",
                    baseline_run_ids=[str(r.id) for r in baseline_runs],
                    candidate_run_ids=[str(r.id) for r in candidate_runs],
                    baseline_mean=comp.get("baseline_mean"),
                    candidate_mean=comp.get("candidate_mean"),
                    delta=comp.get("delta"),
                    relative_change=comp.get("relative_change"),
                    ci_low=_finite(comp.get("ci_low")),
                    ci_high=_finite(comp.get("ci_high")),
                    test=stat_row.get("test"),
                    p_value=_finite(stat_row.get("p_value")),
                    p_adjusted=_finite(stat_row.get("p_adjusted")),
                    effect_size=_finite(stat_row.get("effect_size_g")),
                    config_diff=_diff(spec.baseline.parameters if spec.baseline else {}, spec.parameters),
                )
            )
        ablations = _ablations(runs, spec)
        verdicts = {s["evaluator"]: s["passed"] for s in stored}
        supported = verdicts.get("benchmark") is True and verdicts.get("statistical") is True
        refuted = verdicts.get("statistical") is False and verdicts.get("benchmark") is False
        outcome = "supported" if supported else "refuted" if refuted else "inconclusive"
        if experiment.status == ExperimentStatus.RUNNING:
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.COMPLETED)
        if experiment.status == ExperimentStatus.COMPLETED:
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.EVALUATING)
        experiment.outcome = {
            **(experiment.outcome or {}),
            "evaluation": outcome,
            "verdicts": verdicts,
            "self_reported": context.self_reported,
            "ablations": ablations,
            "n_baseline": len(baseline_runs),
            "n_candidate": len(candidate_runs),
        }
        if experiment.hypothesis_id:
            h = db.get(Hypothesis, experiment.hypothesis_id)
            if h is not None:
                if h.status == HypothesisStatus.EXPERIMENT_DESIGNED:
                    h.status = HYPOTHESIS.ensure(h.status, HypothesisStatus.TESTING)
                if h.status == HypothesisStatus.TESTING:
                    h.status = HYPOTHESIS.ensure(
                        h.status,
                        HypothesisStatus.SUPPORTED
                        if supported
                        else HypothesisStatus.REJECTED
                        if refuted
                        else HypothesisStatus.INCONCLUSIVE,
                    )
                    h.outcome_summary = f"experiment {experiment.id}: {outcome} (verdicts {verdicts})"
        if experiment.mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=experiment.mission_id,
                project_id=experiment.project_id,
                event_type=LabEventType.EVALUATION_COMPLETED,
                message=f"Evaluation of '{experiment.title}': {outcome}",
                data={"experiment_id": str(experiment.id), "verdicts": verdicts, "outcome": outcome},
                actor=actor,
            )
        return {
            "experiment_id": str(experiment.id),
            "outcome": outcome,
            "evaluations": stored,
            "comparisons": comparisons,
            "statistical_rows": stat_rows,
            "self_reported": context.self_reported,
            "ablations": ablations,
            "baseline": context.baseline,
            "candidate": context.candidate,
        }


def _finite(value: Any) -> float | None:
    if isinstance(value, int | float) and value == value and value not in (float("inf"), float("-inf")):
        return float(value)
    return None


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    keys = set(a) | set(b)
    return {k: {"baseline": a.get(k), "candidate": b.get(k)} for k in sorted(keys) if a.get(k) != b.get(k)}


def _ablations(runs: list[ExperimentRun], spec: ExperimentSpec) -> list[dict[str, Any]]:
    primary = spec.primary_metric
    if primary is None:
        return []
    by_variant: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        if r.run_kind == RunKind.ABLATION and isinstance((r.metrics or {}).get(primary.name), int | float):
            by_variant[r.variant or "ablation"].append(float(r.metrics[primary.name]))
    cand = [
        float(r.metrics[primary.name])
        for r in runs
        if r.run_kind == RunKind.CANDIDATE and primary.name in (r.metrics or {})
    ]
    base_mean = statistics.fmean(cand) if cand else None
    return [
        {
            "variant": v,
            "metric": primary.name,
            "mean": statistics.fmean(vals),
            "n": len(vals),
            "delta_vs_candidate": (statistics.fmean(vals) - base_mean) if base_mean is not None else None,
        }
        for v, vals in sorted(by_variant.items())
    ]


def list_evaluations(db: Any, experiment_id: uuid.UUID) -> list[EvaluationRun]:
    return list(
        db.scalars(
            select(EvaluationRun).where(EvaluationRun.experiment_id == experiment_id).order_by(EvaluationRun.created_at)
        ).all()
    )


def comparisons_for(db: Any, experiment_id: uuid.UUID) -> list[RunComparison]:
    return list(
        db.scalars(
            select(RunComparison).where(RunComparison.experiment_id == experiment_id).order_by(RunComparison.created_at)
        ).all()
    )
