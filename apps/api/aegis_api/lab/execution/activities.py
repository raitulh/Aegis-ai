"""Execution activities (run on the ``execution`` task queue by workers that can reach the sandbox backend).

* ``execution.run_job`` — blocking: runs / re-attaches to / waits for one compute job and returns its
  :class:`~aegis_api.lab.execution.schemas.JobResult` as JSON. Idempotent: finished jobs return their stored
  result; a job owned by another live worker is followed rather than started twice. Raises
  ``ApprovalRequired`` (non-retryable) while the job waits for a human decision.
* ``execution.cancel_job`` — request cancellation (idempotent for finished jobs).
* ``execution.reconcile_stale_jobs`` — recover jobs whose worker died and jobs whose approval was decided.
"""

from __future__ import annotations

import uuid
from typing import Any

from aegis_api.config import get_settings
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.execution import service
from aegis_api.lab.workflows.registry import ActivityContext, activity


def _job_id(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload["job_id"]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed("payload.job_id must be a compute job id") from exc


@activity(
    "execution.run_job",
    task_queue="execution",
    heartbeat_seconds=60,
    timeout_seconds=get_settings().execution_max_timeout_seconds + 900,
    max_attempts=2,
)
def run_job_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    result = service.run_job(
        ctx.organization_id, _job_id(payload), heartbeat=ctx.heartbeat, is_cancelled=ctx.is_cancelled
    )
    return result.model_dump(mode="json")


@activity("execution.cancel_job", timeout_seconds=60)
def cancel_job_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    with tenant_uow(ctx.actor) as db:
        job = service.get_job(db, ctx.actor, job_id)
        if job.status not in service.FINISHED_STATUSES:
            job = service.cancel_job(db, ctx.actor, job_id)
        return {"job_id": str(job.id), "status": job.status, "cancel_requested": bool(job.cancel_requested)}


@activity("execution.reconcile_stale_jobs", timeout_seconds=600)
def reconcile_stale_jobs_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    limit = int(payload.get("limit") or 100)
    report = service.reconcile_stale_jobs(ctx.organization_id, limit=max(1, min(limit, 1000)))
    return {key: list(values) for key, values in report.items()}
