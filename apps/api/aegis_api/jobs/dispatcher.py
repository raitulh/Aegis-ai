"""Job dispatch: Celery when Redis is configured, otherwise a threaded in-process backend.

Both backends run the same service functions with a fresh admin-scoped DB session per job. The inline
backend lets the whole platform run with no broker (development, demos, single-node deployments).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import structlog

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant

log = structlog.get_logger("aegis.jobs")

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=get_settings().inline_job_workers, thread_name_prefix="aegis-job"
            )
    return _executor


def _run_with_session(fn: Callable[..., object], organization_id: uuid.UUID, *args: object) -> None:
    session = session_factory()()
    session.begin()
    set_tenant(session, organization_id, None)
    try:
        fn(session, *args)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("job_failed", job=getattr(fn, "__name__", "job"))
        raise
    finally:
        session.close()


def dispatch(fn: Callable[..., object], organization_id: uuid.UUID, *args: object) -> None:
    """Run a job asynchronously. Celery path is used when configured; else a thread pool."""
    settings = get_settings()
    if settings.effective_job_backend == "celery":
        from aegis_api.jobs.tasks import run_service_job

        run_service_job.delay(f"{fn.__module__}:{fn.__name__}", str(organization_id), [_encode(a) for a in args])
        return
    _pool().submit(_run_with_session, fn, organization_id, *args)


def run_now(fn: Callable[..., object], organization_id: uuid.UUID, *args: object) -> None:
    """Run a job synchronously in the current process (used by tests and the CLI)."""
    _run_with_session(fn, organization_id, *args)


def _encode(value: object) -> object:
    return str(value) if isinstance(value, uuid.UUID) else value


def dispatch_task(dotted: str, *args: str) -> None:
    """Run a registered session-less task asynchronously (the task manages its own short transactions).

    Used for long-running orchestration (workflow drivers, webhook delivery) that must never hold a DB
    transaction open across model calls, network I/O or sandbox execution. ``dotted`` must be a key of
    ``aegis_api.jobs.jobs.TASK_REGISTRY`` (an allowlist — arbitrary callables are never dispatched).
    """
    from aegis_api.jobs.jobs import TASK_REGISTRY

    fn = TASK_REGISTRY.get(dotted)
    if fn is None:
        raise ValueError(f"Unknown task {dotted}")
    settings = get_settings()
    if settings.effective_job_backend == "celery":
        from aegis_api.jobs.tasks import run_task

        run_task.delay(dotted, [str(a) for a in args])
        return
    _pool().submit(_run_task_safely, fn, *args)


def _run_task_safely(fn: Callable[..., object], *args: object) -> None:
    try:
        fn(*args)
    except Exception:
        log.exception("task_failed", task=getattr(fn, "__name__", "task"))
