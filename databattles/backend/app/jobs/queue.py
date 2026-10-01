"""PostgreSQL-backed background job queue.

* enqueue() is transactional: the job becomes visible only if the caller commits.
* Jobs are claimed with SELECT ... FOR UPDATE SKIP LOCKED so several workers can run safely.
* Idempotency keys make enqueueing safe to retry (duplicates are ignored).
* Transient failures retry with exponential backoff; permanent failures go straight to `dead`.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.enums import JobStatus
from app.models.system import AppErrorLog, Job

logger = logging.getLogger("databattles.jobs")

JobHandler = Callable[[Session, dict[str, Any]], None]
_HANDLERS: dict[str, JobHandler] = {}


class PermanentJobError(Exception):
    """Deterministic failure — retrying will not help."""


class TransientJobError(Exception):
    """Infrastructure hiccup — safe to retry."""


def job_handler(kind: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        _HANDLERS[kind] = fn
        return fn

    return decorator


def handlers() -> dict[str, JobHandler]:
    return _HANDLERS


def enqueue(db: Session, kind: str, payload: dict[str, Any] | None = None, *, idempotency_key: str | None = None,
            run_after: datetime | None = None, max_attempts: int = 3) -> None:
    values: dict[str, Any] = {
        "kind": kind,
        "payload": payload or {},
        "status": JobStatus.queued,
        "max_attempts": max_attempts,
        "idempotency_key": idempotency_key,
        "run_after": run_after or utcnow(),
    }
    from app.core.ids import uuid7

    values["id"] = uuid7()
    stmt = insert(Job).values(**values)
    if idempotency_key:
        stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])
    db.execute(stmt)


def claim_next(db: Session, worker_id: str, kinds: list[str] | None = None) -> Job | None:
    now = utcnow()
    stmt = (
        select(Job)
        .where(Job.status.in_([JobStatus.queued, JobStatus.failed]), Job.run_after <= now)
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    job = db.scalar(stmt)
    if job is None:
        db.rollback()
        return None
    job.status = JobStatus.running
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    db.commit()
    return job


def run_job(db: Session, job: Job) -> bool:
    handler = _HANDLERS.get(job.kind)
    if handler is None:
        _finish(db, job, JobStatus.dead, f"no handler for job kind {job.kind!r}")
        return False
    try:
        handler(db, {**job.payload, "_attempt": job.attempts, "_max_attempts": job.max_attempts})
        db.commit()
    except PermanentJobError as exc:
        db.rollback()
        _finish(db, job, JobStatus.dead, str(exc)[:1000])
        return False
    except Exception as exc:  # noqa: BLE001 — every failure must be recorded, never lost
        db.rollback()
        logger.exception("job_failed", extra={"job_id": str(job.id), "kind": job.kind})
        retryable = not isinstance(exc, PermanentJobError)
        detail = f"{type(exc).__name__}: {exc}"[:1000]
        if retryable and job.attempts < job.max_attempts:
            backoff = timedelta(seconds=min(3600, 10 * (2 ** (job.attempts - 1))))
            db.execute(update(Job).where(Job.id == job.id).values(
                status=JobStatus.failed, last_error=detail, run_after=utcnow() + backoff, locked_at=None, locked_by=None))
            db.commit()
        else:
            _finish(db, job, JobStatus.dead, detail)
            db.add(AppErrorLog(error_type=type(exc).__name__, message=str(exc)[:500], path=f"job:{job.kind}",
                               source="worker", request_id=str(job.id)))
            db.commit()
            logger.error("job_dead", extra={"job_id": str(job.id), "kind": job.kind, "trace": traceback.format_exc()[-2000:]})
        return False
    _finish(db, job, JobStatus.succeeded, None)
    return True


def _finish(db: Session, job: Job, status: str, error: str | None) -> None:
    db.execute(update(Job).where(Job.id == job.id).values(
        status=status, last_error=error, finished_at=utcnow(), locked_at=None, locked_by=None))
    db.commit()


def recover_stale(db: Session, older_than: timedelta = timedelta(minutes=15)) -> int:
    """Return jobs whose worker died mid-run to the queue."""
    cutoff = utcnow() - older_than
    res = db.execute(update(Job).where(Job.status == JobStatus.running, Job.locked_at < cutoff).values(
        status=JobStatus.failed, locked_at=None, locked_by=None, last_error="recovered after worker timeout"))
    db.commit()
    return res.rowcount or 0


def drain(db_factory: Callable[[], Session], *, worker_id: str = "inline", max_jobs: int = 1000,
          include_delayed: bool = False) -> int:
    """Process queued jobs until none remain (tests, CLI, embedded worker)."""
    processed = 0
    while processed < max_jobs:
        with db_factory() as db:
            if include_delayed:
                db.execute(update(Job).where(Job.status.in_([JobStatus.queued, JobStatus.failed]))
                           .values(run_after=utcnow()))
                db.commit()
            job = claim_next(db, worker_id)
            if job is None:
                return processed
            run_job(db, job)
            processed += 1
    return processed


def queue_stats(db: Session) -> dict[str, Any]:
    from sqlalchemy import func

    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    oldest = db.scalar(select(func.min(Job.created_at)).where(or_(Job.status == JobStatus.queued, Job.status == JobStatus.failed)))
    dead_24h = db.scalar(select(func.count()).select_from(Job).where(
        Job.status == JobStatus.dead, Job.finished_at > utcnow() - timedelta(hours=24))) or 0
    return {
        "by_status": {status: count for status, count in rows},
        "oldest_pending_age_seconds": int((utcnow() - oldest).total_seconds()) if oldest else 0,
        "dead_last_24h": dead_24h,
    }
