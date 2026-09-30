"""Baseline / candidate / ablation / sensitivity comparisons with metric provenance and configuration diffs.

A comparison is only recorded together with the structural configuration diff of the two versions it compares
(``engines.lab.experiment_spec.diff_specs``) — results are never compared without tracking what changed. Per-seed
values come from successful runs of each current version; for every run the independent value is preferred
(``evaluator`` > ``platform`` > ``self_reported``) and the source actually used is recorded in
``statistics.metric_source`` (``self_reported`` values are flagged, never silently trusted). The statistics come
from the pre-registered plan of the candidate via :func:`engines.lab.comparison.compare_samples`; the power of the
design (seeds per arm vs. seeds required for the pre-registered minimum effect) is recorded so that null results
are only read as evidence against a hypothesis when the study could have detected the effect.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.experiments.runs import SUCCESS_STATUSES, final_values
from aegis_api.lab.experiments.service import load_experiment, require_current_version, version_spec
from aegis_api.lab.models import Experiment, ExperimentComparison, ExperimentMetric, ExperimentRun, ExperimentVersion
from aegis_api.schemas.common import Page, PageParams
from engines.lab.comparison import COMPARISON_ENGINE_VERSION, align_by_seed, compare_samples
from engines.lab.experiment_spec import StatisticalPlan, check_criterion, diff_specs
from engines.lab.statistics import StatisticsError, required_seeds_estimate

log = structlog.get_logger("aegis.lab.experiments.comparisons")

SOURCE_PREFERENCE = ("evaluator", "platform", "self_reported")
ASSUMED_POWER = 0.8
COMPARISON_TYPE_BY_KIND = {"ablation": "ablation", "sensitivity": "sensitivity"}


def _run_values(db: Session, version: ExperimentVersion, metric: str) -> tuple[list[dict[str, Any]], list[str], int]:
    """Final values of ``metric`` for successful runs of ``version`` → (values, run ids, runs lacking it)."""
    runs = list(
        db.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_version_id == version.id, ExperimentRun.status.in_(list(SUCCESS_STATUSES)))
            .order_by(ExperimentRun.run_number, ExperimentRun.id)
        )
    )
    if not runs:
        return [], [], 0
    rows = db.scalars(
        select(ExperimentMetric).where(
            ExperimentMetric.experiment_run_id.in_([r.id for r in runs]), ExperimentMetric.name == metric
        )
    ).all()
    per_run: dict[uuid.UUID, list[ExperimentMetric]] = {}
    for row in rows:
        per_run.setdefault(row.experiment_run_id, []).append(row)
    values: list[dict[str, Any]] = []
    missing = 0
    for run in runs:
        finals = final_values(per_run.get(run.id, []))
        chosen = next(
            ((source, finals[(metric, source)]) for source in SOURCE_PREFERENCE if (metric, source) in finals), None
        )
        if chosen is None:
            missing += 1
            continue
        source, value = chosen
        values.append({"run_id": str(run.id), "seed": run.seed, "value": value, "source": source})
    return values, [v["run_id"] for v in values], missing


def _source_label(values: list[dict[str, Any]]) -> str:
    sources = {v["source"] for v in values}
    if not sources:
        return "none"
    if len(sources) == 1:
        return next(iter(sources))
    return "mixed"


def _samples(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], plan: StatisticalPlan
) -> tuple[list[float], list[float], list[str]]:
    notes: list[str] = []
    if plan.test == "paired_t":
        b_by_seed = {v["seed"]: v["value"] for v in baseline if v["seed"] is not None}
        c_by_seed = {v["seed"]: v["value"] for v in candidate if v["seed"] is not None}
        b, c, seeds = align_by_seed(b_by_seed, c_by_seed)
        unpaired = (len(b_by_seed) - len(seeds)) + (len(c_by_seed) - len(seeds))
        if unpaired:
            notes.append(f"{unpaired} run(s) without a seed-matched partner were excluded from the paired test")
        return b, c, notes
    order = sorted(baseline, key=lambda v: (v["seed"] if v["seed"] is not None else -1, v["run_id"]))
    order_c = sorted(candidate, key=lambda v: (v["seed"] if v["seed"] is not None else -1, v["run_id"]))
    return [v["value"] for v in order], [v["value"] for v in order_c], notes


def power_assessment(plan: StatisticalPlan, n_baseline: int, n_candidate: int) -> dict[str, Any]:
    """Whether the design could detect the pre-registered minimum effect (normal approximation, power 0.8)."""
    n = min(n_baseline, n_candidate)
    out: dict[str, Any] = {
        "assumed_power": ASSUMED_POWER,
        "alpha": plan.alpha,
        "min_effect_size": plan.min_effect_size,
        "n_per_arm": n,
        "required_per_arm": None,
        "adequately_powered": False,
    }
    if plan.min_effect_size is None or plan.min_effect_size <= 0:
        out["reason"] = "no pre-registered minimum effect size; a null result cannot be interpreted"
        return out
    try:
        required = required_seeds_estimate(
            plan.min_effect_size, plan.alpha, ASSUMED_POWER, paired=plan.test == "paired_t"
        )
    except StatisticsError as exc:
        out["reason"] = f"power could not be computed: {exc}"
        return out
    out["required_per_arm"] = required
    out["adequately_powered"] = n >= required
    out["reason"] = (
        f"n={n} per arm; {required} needed to detect effect {plan.min_effect_size:g} at alpha {plan.alpha:g} "
        f"with power {ASSUMED_POWER}"
    )
    return out


def compare(
    db: Session,
    actor: Actor,
    baseline_experiment_id: uuid.UUID | str,
    candidate_experiment_id: uuid.UUID | str,
    metric: str | None = None,
) -> ExperimentComparison:
    """Compare the current versions of two experiments of the same project on one metric (``evaluation:run``).

    Idempotent: the same versions, metric and run sets return the existing comparison.
    """
    candidate = load_experiment(db, actor, candidate_experiment_id, "evaluation:run")
    baseline = load_experiment(db, actor, baseline_experiment_id, "evaluation:run")
    if baseline.id == candidate.id:
        raise ValidationFailed("An experiment cannot be compared with itself")
    if baseline.project_id != candidate.project_id:
        raise ValidationFailed("Both experiments must belong to the same project")
    b_version = require_current_version(db, baseline)
    c_version = require_current_version(db, candidate)
    b_spec = version_spec(b_version)
    c_spec = version_spec(c_version)
    if metric is None:
        primary = c_spec.primary_metric
        if primary is None:
            raise ValidationFailed("The candidate has no primary metric; name the metric to compare")
        metric = primary.name
    c_metric = c_spec.metric(metric)
    if c_metric is None:
        raise ValidationFailed(f"The candidate does not declare metric {metric!r}")
    b_metric = b_spec.metric(metric)
    if b_metric is None:
        raise ValidationFailed(f"The baseline does not measure metric {metric!r}")
    if b_metric.direction != c_metric.direction:
        raise ValidationFailed(f"Metric {metric!r} has different directions in the two specifications")
    config_diff = diff_specs(b_spec, c_spec)
    b_values, b_run_ids, b_missing = _run_values(db, b_version, metric)
    c_values, c_run_ids, c_missing = _run_values(db, c_version, metric)
    advisory_xact_lock(db, f"experiment-comparison:{b_version.id}:{c_version.id}:{metric}")
    existing = db.scalars(
        select(ExperimentComparison)
        .where(
            ExperimentComparison.baseline_version_id == b_version.id,
            ExperimentComparison.candidate_version_id == c_version.id,
            ExperimentComparison.metric == metric,
        )
        .order_by(ExperimentComparison.created_at.desc())
    ).all()
    for row in existing:
        if sorted(row.baseline_run_ids or []) == sorted(b_run_ids) and sorted(row.candidate_run_ids or []) == sorted(
            c_run_ids
        ):
            return row
    plan = c_spec.statistical_plan
    b_sample, c_sample, notes = _samples(b_values, c_values, plan)
    result = compare_samples(b_sample, c_sample, metric=metric, direction=c_metric.direction, plan=plan)
    warnings = list(result.warnings) + notes
    metric_source = _source_label([*b_values, *c_values])
    if metric_source in ("self_reported", "mixed"):
        warnings.append(
            "values are (partly) self-reported by the experiment code; an independent evaluator has not recomputed "
            "them, so they cannot verify a claim on their own"
        )
    if b_missing or c_missing:
        warnings.append(f"{b_missing} baseline and {c_missing} candidate successful run(s) did not report {metric!r}")
    if candidate.baseline_experiment_id is not None and candidate.baseline_experiment_id != baseline.id:
        warnings.append("the baseline is not the candidate's declared baseline experiment")
    if not any(config_diff[k] for k in ("added", "removed", "changed")):
        warnings.append("the two configurations are identical (this is a replication, not an intervention)")
    stats = result.statistics.model_dump(mode="json")
    power = power_assessment(plan, stats["n_baseline"], stats["n_candidate"])
    criteria = [
        check_criterion(c, stats.get("mean_candidate"), stats.get("mean_baseline"), c_metric.direction).model_dump()
        for c in c_spec.success_criteria
        if c.metric == metric
    ]
    comparison_type = COMPARISON_TYPE_BY_KIND.get(candidate.kind, "baseline")
    statistics: dict[str, Any] = {
        **stats,
        "metric_source": metric_source,
        "values": {"baseline": b_values, "candidate": c_values},
        "plan": plan.model_dump(mode="json"),
        "power": power,
        "criteria": criteria,
        "warnings": warnings,
        "rationale": result.rationale,
        "engine_version": result.engine_version,
        "baseline_spec_hash": b_version.spec_hash,
        "candidate_spec_hash": c_version.spec_hash,
    }
    comparison = ExperimentComparison(
        organization_id=candidate.organization_id,
        workspace_id=candidate.workspace_id,
        project_id=candidate.project_id,
        mission_id=candidate.mission_id or baseline.mission_id,
        baseline_experiment_id=baseline.id,
        candidate_experiment_id=candidate.id,
        baseline_version_id=b_version.id,
        candidate_version_id=c_version.id,
        comparison_type=comparison_type,
        metric=metric,
        direction=c_metric.direction,
        baseline_run_ids=b_run_ids,
        candidate_run_ids=c_run_ids,
        config_diff=config_diff,
        statistics=statistics,
        verdict=result.verdict,
        evaluator_version=COMPARISON_ENGINE_VERSION,
    )
    db.add(comparison)
    db.flush()
    append_evidence(
        db,
        organization_id=comparison.organization_id,
        kind="comparison",
        title=f"Comparison {comparison.id}: {metric} {result.verdict}",
        content={
            "comparison_id": str(comparison.id),
            "comparison_type": comparison_type,
            "baseline_version_id": str(b_version.id),
            "candidate_version_id": str(c_version.id),
            "baseline_spec_hash": b_version.spec_hash,
            "candidate_spec_hash": c_version.spec_hash,
            "metric": metric,
            "direction": c_metric.direction,
            "metric_source": metric_source,
            "verdict": result.verdict,
            "p_value": stats.get("p_value"),
            "effect_size": stats.get("effect_size"),
            "n_baseline": stats.get("n_baseline"),
            "n_candidate": stats.get("n_candidate"),
            "baseline_run_ids": b_run_ids,
            "candidate_run_ids": c_run_ids,
            "config_diff_paths": sorted({*config_diff["added"], *config_diff["removed"], *config_diff["changed"]})[
                :200
            ],
            "engine_version": result.engine_version,
        },
    )
    emit(
        db,
        organization_id=comparison.organization_id,
        type=EventType.EVALUATION_COMPLETED,
        payload={
            "comparison_id": str(comparison.id),
            "baseline_experiment_id": str(baseline.id),
            "candidate_experiment_id": str(candidate.id),
            "metric": metric,
            "verdict": result.verdict,
            "metric_source": metric_source,
            "comparison_type": comparison_type,
        },
        mission_id=comparison.mission_id,
        project_id=comparison.project_id,
        workspace_id=comparison.workspace_id,
        subject_type="comparison",
        subject_id=comparison.id,
        actor=actor,
    )
    return comparison


def get_comparison(db: Session, actor: Actor, comparison_id: uuid.UUID | str) -> ExperimentComparison:
    comparison = get_owned(db, ExperimentComparison, comparison_id, actor, label="Comparison")
    project_id = comparison.project_id
    if project_id is None:
        candidate = db.get(Experiment, comparison.candidate_experiment_id)
        project_id = candidate.project_id if candidate is not None else None
    if project_id is None:
        raise ValidationFailed("The comparison is not attached to a project")
    load_project(db, actor, project_id, "experiment:read")
    return comparison


def list_for_experiment(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    params: PageParams,
    *,
    mapper: Callable[[ExperimentComparison], Any] | None = None,
) -> Page[Any]:
    experiment = load_experiment(db, actor, experiment_id, "experiment:read")
    stmt = (
        select(ExperimentComparison)
        .where(
            or_(
                ExperimentComparison.candidate_experiment_id == experiment.id,
                ExperimentComparison.baseline_experiment_id == experiment.id,
            )
        )
        .order_by(ExperimentComparison.created_at.desc(), ExperimentComparison.id.desc())
    )
    return paginate(db, stmt, params, mapper or (lambda c: c))
