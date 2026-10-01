"""Job dispatch: Celery when Redis is configured, otherwise a threaded in-process backend.

Both backends execute the same service functions through :func:`execute`, which claims the job in the
ledger, runs it in a fresh tenant-scoped (RLS) session, and records success or a classified failure.
Celery retries transient failures with exponential backoff; the inline backend's retries are re-driven by
the maintenance scheduler. Workers are assumed to crash at any point: entity jobs are additionally guarded by
their own state machine (e.g. an audit can only be claimed once), and results are written atomically.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.jobs import ledger

log = structlog.get_logger("aegis.jobs")

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.current_thread().name}"


def job_name(fn: Callable[..., object]) -> str:
    return f"{fn.__module__}:{fn.__name__}"


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=get_settings().inline_job_workers, thread_name_prefix="aegis-job"
            )
    return _executor


def shutdown(wait: bool = True) -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait, cancel_futures=False)
            _executor = None


def _run_with_session(fn: Callable[..., object], organization_id: uuid.UUID | None, *args: object) -> None:
    session = session_factory()()
    session.begin()
    set_tenant(session, organization_id, None)
    try:
        fn(session, *args)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute(
    job_id: uuid.UUID | None, fn: Callable[..., object], organization_id: uuid.UUID | None, *args: Any
) -> int | None:
    """Run one attempt of a job. Returns a retry delay (seconds) when a transient failure should be retried."""
    import structlog.contextvars as ctx

    attempt = 1
    if job_id is not None:
        claimed = ledger.claim(job_id, worker_name())
        if claimed is None:
            log.info("job_claim_skipped", job_id=str(job_id), job=job_name(fn))
            return None
        attempt = claimed
    ctx.bind_contextvars(job_id=str(job_id) if job_id else None, job=job_name(fn), tenant=str(organization_id))
    started = time.perf_counter()
    try:
        _run_with_session(fn, organization_id, *args)
    except Exception as exc:
        duration = int((time.perf_counter() - started) * 1000)
        log.exception("job_error", attempt=attempt)
        _observe(job_name(fn), "failed", duration)
        if job_id is None:
            raise
        return ledger.fail(job_id, exc, attempt, duration)
    finally:
        ctx.unbind_contextvars("job_id", "job", "tenant")
    duration = int((time.perf_counter() - started) * 1000)
    _observe(job_name(fn), "succeeded", duration)
    if job_id is not None:
        ledger.succeed(job_id, duration)
    return None


def _observe(job: str, outcome: str, duration_ms: int) -> None:
    from aegis_api.observability import metrics

    with contextlib.suppress(Exception):  # metrics are best-effort
        metrics.JOB_DURATION.labels(job=job.rsplit(":", 1)[-1], outcome=outcome).observe(duration_ms / 1000)


def dispatch(
    fn: Callable[..., object],
    organization_id: uuid.UUID | None,
    *args: object,
    idempotency_key: str | None = None,
    force: bool = False,
) -> uuid.UUID | None:
    """Run a job asynchronously (after the caller's transaction commits). Returns the ledger id, or None when
    an equivalent job is already active/succeeded and the dispatch was skipped."""
    settings = get_settings()
    name = job_name(fn)
    encoded = [_encode(a) for a in args]
    job_id = ledger.enqueue(name, organization_id, encoded, key=idempotency_key, force=force)
    if job_id is None:
        return None
    if settings.effective_job_backend == "celery":
        from aegis_api.jobs.tasks import run_service_job

        run_service_job.apply_async(
            args=(name, str(organization_id) if organization_id else None, encoded, str(job_id)),
            task_id=str(job_id) + f"-{uuid.uuid4().hex[:6]}",
        )
        return job_id
    _pool().submit(_inline, job_id, fn, organization_id, *args)
    return job_id


def _inline(job_id: uuid.UUID, fn: Callable[..., object], organization_id: uuid.UUID | None, *args: object) -> None:
    try:
        execute(job_id, fn, organization_id, *args)
    except Exception:  # pragma: no cover - already recorded in the ledger
        log.exception("inline_job_crashed")


def run_now(fn: Callable[..., object], organization_id: uuid.UUID, *args: object) -> None:
    """Run a job synchronously in the current process (used by tests and the CLI)."""
    _run_with_session(fn, organization_id, *args)


def redrive(job: Any) -> None:
    """Re-execute a ledger job whose retry is due (inline backend; Celery schedules its own retries)."""
    from aegis_api.jobs.jobs import JOB_REGISTRY

    fn = JOB_REGISTRY.get(job.job)
    if fn is None:
        return
    org = job.organization_id
    _pool().submit(_inline, job.id, fn, org, *job.args)


def _encode(value: object) -> object:
    return str(value) if isinstance(value, uuid.UUID) else value
