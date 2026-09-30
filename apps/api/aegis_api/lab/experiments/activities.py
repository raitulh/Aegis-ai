"""Workflow activities of the experiments context (LAB_CONTRACT_W2 §B; JSON payloads and results).

Every activity is idempotent: baselines are looked up before they are created, validation only records a new
version when its outcome changed, runs are keyed by ``run_key`` (jobs by ``exprun:<run id>``), completed runs are
not re-ingested, finalisation and comparisons return the existing outcome. Transactions are short
(``tenant_uow``) and never span the sandbox execution, which runs in ``execution.run_job``.
"""

from __future__ import annotations

import uuid
from typing import Any

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.experiments import comparisons, runs, service
from aegis_api.lab.models import ComputeJob
from aegis_api.lab.workflows.registry import ActivityContext, activity


def _id(payload: dict[str, Any], key: str, *, required: bool = True) -> uuid.UUID | None:
    value = payload.get(key)
    if value is None and not required:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"payload.{key} must be an id") from exc


def _required_id(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = _id(payload, key)
    assert value is not None
    return value


@activity("experiments.ensure_baseline", timeout_seconds=180)
def ensure_baseline_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        baseline, created = service.ensure_baseline(
            db,
            ctx.actor,
            mission_id=_id(payload, "mission_id", required=False),
            candidate_experiment_id=_required_id(payload, "candidate_experiment_id"),
            hypothesis_id=_id(payload, "hypothesis_id", required=False),
        )
        return {
            "baseline_experiment_id": str(baseline.id) if baseline is not None else None,
            "created": created,
            "status": baseline.status if baseline is not None else None,
        }


@activity("experiments.validate", timeout_seconds=180)
def validate_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        outcome = service.validate_experiment(db, ctx.actor, _required_id(payload, "experiment_id"))
        return {
            "experiment_id": str(outcome.experiment.id),
            "version_id": str(outcome.version.id),
            "passed": bool(outcome.version.validation_passed),
            "issues": list(outcome.report.get("issues") or []),
            "status": outcome.experiment.status,
            "new_version_created": outcome.new_version_created,
        }


@activity("experiments.schedule_runs", timeout_seconds=300)
def schedule_runs_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    seeds = payload.get("seeds")
    if seeds is not None and (not isinstance(seeds, list) or not all(isinstance(s, int) for s in seeds)):
        raise ValidationFailed("payload.seeds must be a list of integers")
    with tenant_uow(ctx.actor) as db:
        scheduled = runs.schedule_runs(
            db,
            ctx.actor,
            _required_id(payload, "experiment_id"),
            role=str(payload.get("role") or "candidate"),
            seeds=seeds,
            run_key_prefix=str(payload.get("run_key_prefix") or "default"),
        )
        job_ids: list[str] = []
        approval_ids: list[str] = []
        for run in scheduled:
            job = db.get(ComputeJob, run.compute_job_id) if run.compute_job_id else None
            if job is None:
                continue
            job_ids.append(str(job.id))
            if job.approval_id is not None and job.status == "QUEUED":
                approval_ids.append(str(job.approval_id))
        experiment = service.get_experiment(db, ctx.actor, _required_id(payload, "experiment_id"))
        return {
            "run_ids": [str(r.id) for r in scheduled],
            "job_ids": job_ids,
            "approval_ids": approval_ids,
            "status": "awaiting_approval" if approval_ids else experiment.status,
            "experiment_status": experiment.status,
        }


@activity("experiments.complete_run", timeout_seconds=180)
def complete_run_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    job_result = payload.get("job_result")
    if not isinstance(job_result, dict):
        raise ValidationFailed("payload.job_result must be an object")
    with tenant_uow(ctx.actor) as db:
        run = runs.complete_run(db, ctx.actor, _required_id(payload, "experiment_run_id"), job_result)
        manifest = run.environment_manifest or {}
        return {
            "experiment_run_id": str(run.id),
            "status": run.status,
            "reason": run.status_reason,
            "metrics": {
                "self_reported": dict(run.metrics or {}),
                "platform": dict(manifest.get("platform_metrics") or {}),
            },
        }


@activity("experiments.finalize", timeout_seconds=120)
def finalize_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        result = runs.finalize_experiment(db, ctx.actor, _required_id(payload, "experiment_id"))
        return {"status": result.experiment.status, "run_summary": result.summary, "changed": result.changed}


@activity("experiments.compare", timeout_seconds=180)
def compare_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    metric = payload.get("metric")
    if metric is not None and not isinstance(metric, str):
        raise ValidationFailed("payload.metric must be a string")
    with tenant_uow(ctx.actor) as db:
        comparison = comparisons.compare(
            db,
            ctx.actor,
            _required_id(payload, "baseline_experiment_id"),
            _required_id(payload, "candidate_experiment_id"),
            metric,
        )
        return {
            "comparison_id": str(comparison.id),
            "verdict": comparison.verdict,
            "metric": comparison.metric,
            "comparison_type": comparison.comparison_type,
            "statistics": dict(comparison.statistics or {}),
        }


@activity("experiments.list_for_mission", timeout_seconds=60)
def list_for_mission_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    statuses = payload.get("statuses")
    if statuses is not None and (not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses)):
        raise ValidationFailed("payload.statuses must be a list of status names")
    with tenant_uow(ctx.actor) as db:
        rows = service.list_for_mission(db, ctx.actor, _required_id(payload, "mission_id"), statuses)
        return {
            "items": [
                {
                    "id": str(e.id),
                    "status": e.status,
                    "kind": e.kind,
                    "hypothesis_id": str(e.hypothesis_id) if e.hypothesis_id else None,
                    "baseline_experiment_id": str(e.baseline_experiment_id) if e.baseline_experiment_id else None,
                    "current_version_id": str(e.current_version_id) if e.current_version_id else None,
                }
                for e in rows
            ]
        }
