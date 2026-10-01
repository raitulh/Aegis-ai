"""Background-job ledger (``job_runs``): idempotent enqueue, attempt tracking, failure classification.

Every dispatched job gets a deterministic idempotency key (by default ``<job>:<org>:<args>``), so dispatching
the same entity job twice is recognised and skipped. Workers move a job ``queued → running`` atomically before
executing it; a redelivered message for a job that already succeeded (or is being run by a live worker) is a
no-op. Failures are classified:

* ``transient`` (connection/timeout/provider unavailable) → retried with exponential backoff up to
  ``max_attempts``, then ``dead`` (the dead-letter state; visible to operators and re-drivable);
* ``permanent`` (validation, missing entity, programming error) → ``failed`` immediately.

The ledger is cross-tenant operational data and is written with the owner connection (maintenance layer).
"""

from __future__ import annotations

import socket
import uuid
from datetime import timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, OperationalError

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope
from aegis_api.models import JobRun

log = structlog.get_logger("aegis.jobs")

ACTIVE = ("queued", "running", "retrying")
BACKOFF_BASE_SECONDS = 15
BACKOFF_CAP_SECONDS = 15 * 60


def default_key(job: str, organization_id: uuid.UUID | None, args: list[Any]) -> str:
    return ":".join([job, str(organization_id or "system"), *(str(a) for a in args)])[:200]


def classify(exc: BaseException) -> str:
    from engines.providers.base import ProviderUnavailable

    transient = (
        OperationalError,
        httpx.TransportError,
        ConnectionError,
        TimeoutError,
        socket.timeout,
        ProviderUnavailable,
    )
    if isinstance(exc, transient):
        return "transient"
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return "transient"
    return "permanent"


def backoff_seconds(attempt: int) -> int:
    return int(min(BACKOFF_BASE_SECONDS * (2 ** max(attempt - 1, 0)), BACKOFF_CAP_SECONDS))


def enqueue(
    job: str,
    organization_id: uuid.UUID | None,
    args: list[Any],
    *,
    key: str | None = None,
    max_attempts: int | None = None,
    force: bool = False,
) -> uuid.UUID | None:
    """Record a job. Returns its id, or None when an equivalent job is already active or has succeeded.

    ``force`` re-arms a finished (failed/dead/succeeded) job with the same key — used by the stale-run
    reaper and explicit operator re-drives."""
    key = key or default_key(job, organization_id, args)
    attempts = max_attempts or get_settings().job_max_attempts
    with admin_session_scope() as s:
        inserted = s.execute(
            insert(JobRun)
            .values(
                organization_id=organization_id,
                job=job,
                idempotency_key=key,
                args=[str(a) for a in args],
                status="queued",
                max_attempts=attempts,
            )
            .on_conflict_do_nothing(constraint="uq_job_runs_idempotency_key")
            .returning(JobRun.id)
        ).scalar()
        if inserted is not None:
            return inserted
        existing = s.scalar(select(JobRun).where(JobRun.idempotency_key == key))
        if existing is None:
            return None
        if existing.status in ACTIVE and not force:
            log.info("job_duplicate_skipped", job=job, key=key, status=existing.status)
            return None
        if existing.status == "succeeded" and not force:
            return None
        existing.status = "queued"
        existing.attempts = 0 if force else existing.attempts
        existing.error = None
        existing.error_class = None
        existing.next_attempt_at = None
        existing.updated_at = utcnow()
        return existing.id


def claim(job_id: uuid.UUID, worker: str) -> int | None:
    """Move a job to ``running`` (or take over a stale run). Returns the attempt number or None to skip."""
    now = utcnow()
    stale_before = now - timedelta(seconds=get_settings().job_lease_timeout_seconds)
    with admin_session_scope() as s:
        row = s.execute(
            update(JobRun)
            .where(
                JobRun.id == job_id,
                or_(
                    JobRun.status.in_(["queued", "retrying"]),
                    (JobRun.status == "running") & (JobRun.updated_at < stale_before),
                ),
            )
            .values(status="running", attempts=JobRun.attempts + 1, worker=worker, started_at=now, updated_at=now)
            .returning(JobRun.attempts)
        ).first()
    return int(row.attempts) if row else None


def succeed(job_id: uuid.UUID, duration_ms: int) -> None:
    with admin_session_scope() as s:
        s.execute(
            update(JobRun)
            .where(JobRun.id == job_id)
            .values(status="succeeded", finished_at=utcnow(), duration_ms=duration_ms, error=None, updated_at=utcnow())
        )


def fail(job_id: uuid.UUID, exc: BaseException, attempt: int, duration_ms: int) -> int | None:
    """Record a failure. Returns the retry delay in seconds when the job should be retried, else None."""
    kind = classify(exc)
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with admin_session_scope() as s:
        record = s.get(JobRun, job_id)
        if record is None:
            return None
        retry = kind == "transient" and attempt < record.max_attempts
        delay = backoff_seconds(attempt) if retry else None
        record.status = "retrying" if retry else ("dead" if kind == "transient" else "failed")
        record.error_class = kind
        record.error = message
        record.duration_ms = duration_ms
        record.finished_at = None if retry else utcnow()
        record.next_attempt_at = (utcnow() + timedelta(seconds=delay)) if delay is not None else None
        record.updated_at = utcnow()
    log.warning("job_failed", job_id=str(job_id), error_class=kind, attempt=attempt, retry_in=delay)
    return delay


def due_retries(limit: int = 50) -> list[JobRun]:
    with admin_session_scope() as s:
        rows = s.scalars(
            select(JobRun)
            .where(JobRun.status == "retrying", JobRun.next_attempt_at <= utcnow())
            .order_by(JobRun.next_attempt_at)
            .limit(limit)
        ).all()
        for row in rows:
            s.expunge(row)
        return list(rows)
