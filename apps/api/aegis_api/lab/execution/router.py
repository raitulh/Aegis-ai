"""HTTP API for sandboxed compute jobs (tag "Execution").

Submissions only admit a job (validation, governance, quota/budget) and return ``202``; the job runs on an
execution worker after the transaction commits — never in the request thread, and generated code never runs
in the API process.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit, require_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.execution import service
from aegis_api.lab.execution.backends import describe_backends
from aegis_api.lab.execution.dispatch import dispatch_in_session
from aegis_api.lab.execution.schemas import (
    BackendInfoOut,
    ComputeJobLogsOut,
    ComputeJobOut,
    ExecutionBackendsOut,
    ExecutionLimitsOut,
    JobSpec,
)
from engines.lab.states import ExecutionStatus

router = APIRouter(prefix="/api/v1", tags=["Execution"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
_READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E, 429: _E, 503: _E}


def _job_key(actor: Actor, header_key: str) -> str:
    """Derive the job-level idempotency key from the HTTP header, scoped to the calling credential."""
    credential = actor.api_key_id or actor.service_account_id or actor.user_id
    digest = hashlib.sha256(f"{credential}:{header_key}".encode()).hexdigest()[:48]
    return f"http:{digest}"


@router.post(
    "/compute-jobs",
    response_model=ComputeJobOut,
    status_code=202,
    summary="Submit a sandboxed compute job",
    description=(
        "Admits a job for execution in the hardened sandbox (non-root, read-only root filesystem, no "
        "capabilities, no network unless an allowlist is approved, bounded CPU/memory/PIDs/disk/time). The "
        "request is validated against the deployment and organization execution policy, the concurrency "
        "quota, the mission compute budget and the governance policy `execution.submit`. When policy requires "
        "approval the job is created `QUEUED` with `approval_id` and starts only after a human approves. "
        "Supports `Idempotency-Key` (replays return the original response and never create a second job). "
        "Requires `experiment:execute` in the project."
    ),
    responses={
        **_WRITE_ERRORS,
        403: {"description": "Missing permission, policy denied (`policy_denied`) or quota exceeded"},
        409: {"description": "Budget exhausted, mission not active or idempotency key reused"},
        503: {"description": "Execution is disabled or its backend is unavailable"},
    },
)
def submit_compute_job(
    body: JobSpec,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    idem: Idempotency = Depends(idempotency),
    _rate: None = Depends(lab_rate_limit("execution")),
    actor: Actor = Depends(require_actor("experiment:execute")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    spec = body
    if spec.idempotency_key is None and idempotency_key:
        spec = spec.model_copy(update={"idempotency_key": _job_key(actor, idempotency_key)})
    job = service.submit_job(db, actor, spec)
    if job.status == ExecutionStatus.QUEUED and job.approval_id is None and not job.cancel_requested:
        # Runs only after this transaction commits (workflow launch or local runner).
        dispatch_in_session(db, actor, job)
    return idem.remember(202, ComputeJobOut.from_job(job))


@router.get(
    "/compute-jobs",
    response_model=CursorPage[ComputeJobOut],
    status_code=200,
    summary="List compute jobs",
    description="Compute jobs of projects you can see, newest first (cursor pagination). Requires `execution:read`.",
    responses=_READ_ERRORS,
)
def list_compute_jobs(
    status: str | None = Query(default=None, max_length=24, description="Filter by status (e.g. RUNNING)"),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    experiment_id: uuid.UUID | None = Query(default=None),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("execution:read")),
    db: Session = Depends(get_db),
) -> CursorPage[ComputeJobOut]:
    return service.list_jobs(
        db,
        actor,
        params,
        status=status.upper() if status else None,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
    )


@router.get(
    "/compute-jobs/{job_id}",
    response_model=ComputeJobOut,
    status_code=200,
    summary="Get a compute job",
    description="Status, security posture, resource usage, cost, outputs (artifact versions) and self-reported "
    "metrics of one job. Requires `execution:read` in the job's project.",
    responses=_READ_ERRORS,
)
def get_compute_job(
    job_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ComputeJobOut:
    return ComputeJobOut.from_job(service.get_job(db, actor, job_id))


@router.post(
    "/compute-jobs/{job_id}/cancel",
    response_model=ComputeJobOut,
    status_code=200,
    summary="Cancel a compute job",
    description="A queued job is cancelled immediately; a running job is killed by its worker at the next "
    "heartbeat (`cancel_requested`). Finished jobs cannot be cancelled (409). Requires `execution:manage` or "
    "`experiment:cancel`.",
    responses=_WRITE_ERRORS,
)
def cancel_compute_job(
    job_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ComputeJobOut:
    return ComputeJobOut.from_job(service.cancel_job(db, actor, job_id))


@router.get(
    "/compute-jobs/{job_id}/logs",
    response_model=ComputeJobLogsOut,
    status_code=200,
    summary="Get compute job logs",
    description="For finished jobs, the tail of the stored log (stdout + stderr, capped at "
    "EXECUTION_MAX_LOG_BYTES); for running jobs, the most recent streamed lines. Log text is untrusted "
    "program output. Requires `execution:read`.",
    responses=_READ_ERRORS,
)
def get_compute_job_logs(
    job_id: uuid.UUID,
    limit_bytes: int = Query(default=256 * 1024, ge=1024, le=10 * 1024 * 1024),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ComputeJobLogsOut:
    return service.job_logs(db, actor, job_id, limit_bytes=limit_bytes)


@router.get(
    "/execution/backends",
    response_model=ExecutionBackendsOut,
    status_code=200,
    summary="Execution backends and limits",
    description="Which execution backends exist, which one is configured (with a health probe), their "
    "capabilities, and the effective sandbox limits for your organization. Requires `execution:read`.",
    responses=_READ_ERRORS,
)
def list_execution_backends(
    actor: Actor = Depends(require_actor("execution:read")), db: Session = Depends(get_db)
) -> ExecutionBackendsOut:
    settings = get_settings()
    limits, org_images = service.execution_limits(db, actor.organization_id)
    backends = [
        BackendInfoOut(
            name=caps.name,
            enabled=caps.enabled,
            configured=caps.name == settings.execution_backend,
            healthy=healthy,
            cpu=caps.cpu,
            gpu=caps.gpu,
            multi_gpu=caps.multi_gpu,
            max_timeout_seconds=caps.max_timeout_seconds,
            network_modes=list(caps.network_modes),
            description=caps.description,
            reason=caps.reason,
        )
        for caps, healthy in describe_backends()
    ]
    return ExecutionBackendsOut(
        configured=settings.execution_backend,
        backends=backends,
        limits=ExecutionLimitsOut(
            default_image=settings.execution_default_image,
            allowed_images=list(settings.execution_allowed_image_list),
            org_allowed_images=org_images,
            max_cpu=limits.max_cpu,
            max_memory_mb=limits.max_memory_mb,
            max_disk_mb=limits.max_disk_mb,
            max_timeout_seconds=limits.max_timeout_seconds,
            default_timeout_seconds=min(settings.execution_default_timeout_seconds, limits.max_timeout_seconds),
            max_output_bytes=settings.execution_max_output_bytes,
            max_output_files=settings.execution_max_output_files,
            max_log_bytes=settings.execution_max_log_bytes,
            gpu_enabled=limits.gpu_allowed,
            egress_available=limits.egress_available,
        ),
    )
