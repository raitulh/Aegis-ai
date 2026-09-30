"""Handing admitted compute jobs to a runner.

A submitted job is only a database row (``QUEUED``); something must call ``service.run_job`` on an execution
worker. :func:`dispatch_in_session` does that as part of the caller's transaction (nothing starts unless the
transaction commits), choosing, in order:

1. an explicitly installed dispatcher (:func:`set_job_dispatcher` — embedding and tests);
2. the durable ``ExecutionJobWorkflow`` through the workflows launcher (local engine or Temporal; the
   workflow runs the ``execution.run_job`` activity on the ``execution`` task queue, survives restarts and
   is idempotent per ``(job, attempt)`` workflow key);
3. only when the workflows context is not installed and the local engine is configured: a small bounded
   thread pool in this process.

Only orchestration ever runs in-process — generated code always runs inside the sandbox backend. When no
runner is available the job stays ``QUEUED`` (reported, never pretended to run) and reconciliation retries.

Callers that run jobs themselves (e.g. experiment workflows calling ``execution.run_job``) do not dispatch.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.errors import ApprovalRequired
from aegis_api.lab.core.events import after_commit
from aegis_api.lab.models import ComputeJob

log = structlog.get_logger("aegis.lab.execution.dispatch")

JobDispatcher = Callable[[uuid.UUID, uuid.UUID], None]  # (organization_id, job_id)

EXECUTION_WORKFLOW = "ExecutionJobWorkflow"
LOCAL_MAX_CONCURRENT_JOBS = 4

_lock = threading.Lock()
_custom: JobDispatcher | None = None
_pool: ThreadPoolExecutor | None = None
_inflight: set[uuid.UUID] = set()


def set_job_dispatcher(dispatcher: JobDispatcher | None) -> None:
    """Install a process-wide dispatcher that takes precedence over workflows (``None`` restores defaults)."""
    global _custom
    with _lock:
        _custom = dispatcher


def _workflow_launcher() -> Callable[..., Any] | None:
    try:
        from aegis_api.lab.workflows.launcher import launch_workflow
    except ImportError:
        return None
    return launch_workflow


def _fallback_dispatcher() -> JobDispatcher | None:
    settings = get_settings()
    if settings.execution_backend == "disabled" or settings.effective_workflow_engine != "local":
        return None
    return local_dispatch


def dispatch_in_session(db: Session, actor: Actor, job: ComputeJob, *, workflow_key: str | None = None) -> str:
    """Arrange for ``job`` to run once the current transaction commits → ``custom|workflow|local|none``."""
    organization_id, job_id = job.organization_id, job.id
    if _custom is not None:
        dispatcher = _custom
        after_commit(db, lambda: _safe_call(dispatcher, organization_id, job_id))
        return "custom"
    launch = _workflow_launcher()
    if launch is not None:
        run = launch(
            db,
            actor,
            EXECUTION_WORKFLOW,
            subject_type="compute_job",
            subject_id=str(job_id),
            input={"job_id": str(job_id)},
            project_id=job.project_id,
            mission_id=job.mission_id,
            workflow_key=workflow_key or f"attempt-{int(job.attempt or 1)}",
        )
        job.workflow_run_id = run.id
        return "workflow"
    fallback = _fallback_dispatcher()
    if fallback is None:
        log.warning("compute_job_not_dispatched", job_id=str(job_id), reason="no runner configured")
        return "none"
    after_commit(db, lambda: _safe_call(fallback, organization_id, job_id))
    return "local"


def _safe_call(dispatcher: JobDispatcher, organization_id: uuid.UUID, job_id: uuid.UUID) -> None:
    try:
        dispatcher(organization_id, job_id)
    except Exception:  # the job stays QUEUED; reconciliation retries
        log.exception("compute_job_dispatch_failed", job_id=str(job_id))


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=LOCAL_MAX_CONCURRENT_JOBS, thread_name_prefix="aegis-exec")
        return _pool


def local_dispatch(organization_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Run the job's orchestration on the local bounded pool (at most once per job per process)."""
    with _lock:
        if job_id in _inflight:
            return
        _inflight.add(job_id)
    _executor().submit(_run_local, organization_id, job_id)


def _run_local(organization_id: uuid.UUID, job_id: uuid.UUID) -> None:
    from aegis_api.lab.execution.service import run_job

    try:
        run_job(organization_id, job_id)
    except ApprovalRequired:
        log.info("compute_job_awaiting_approval", job_id=str(job_id))
    except Exception:
        log.exception("compute_job_run_failed", job_id=str(job_id))
    finally:
        with _lock:
            _inflight.discard(job_id)
