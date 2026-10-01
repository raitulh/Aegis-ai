"""Periodic maintenance: the platform's heartbeat.

Runs every ``SCHEDULER_INTERVAL_SECONDS`` under Celery beat (``aegis.maintenance_tick``) or, with the inline
backend, on a background thread in the API process. A Postgres advisory lock guarantees that only one tick
runs at a time across every API replica and beat instance.

Each task is independent and failure-isolated (one failing task never blocks the others) and works across
tenants with the owner connection — this is the "cross-tenant maintenance" use of the admin session.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import lazyload

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope, get_admin_engine

log = structlog.get_logger("aegis.maintenance")
ADVISORY_LOCK_KEY = 0x4AE615  # arbitrary, stable


def deliver_webhooks() -> int:
    from aegis_api.services import webhook_service

    with admin_session_scope() as s:
        return webhook_service.deliver_pending(s, limit=100)


def reap_stale_runs() -> dict[str, int]:
    """Recover work whose worker died: re-queue (or fail) audits with a stale heartbeat, re-dispatch audits
    stuck in ``queued`` without an active job, and fail stale red-team / regression runs."""
    from aegis_api.jobs import dispatcher, ledger
    from aegis_api.jobs.jobs import run_audit_job
    from aegis_api.models import Audit, AuditEvent, JobRun, RedTeamRun, RegressionRun

    settings = get_settings()
    now = utcnow()
    stale_before = now - timedelta(seconds=settings.job_lease_timeout_seconds)
    requeued = failed = redispatched = runs_failed = 0
    with admin_session_scope() as s:
        stale = s.scalars(
            select(Audit)
            .options(lazyload(Audit.system))
            .where(Audit.status == "running", Audit.heartbeat_at < stale_before)
            .with_for_update(skip_locked=True, of=Audit)
        ).all()
        for audit in stale:
            seq = (
                s.scalar(
                    select(AuditEvent.seq)
                    .where(AuditEvent.audit_id == audit.id)
                    .order_by(AuditEvent.seq.desc())
                    .limit(1)
                )
                or 0
            ) + 1
            if audit.attempts < settings.audit_max_attempts and not audit.cancel_requested:
                audit.status = "queued"
                audit.lease_owner = None
                message = f"Worker lost (no heartbeat since {audit.heartbeat_at:%H:%M:%S}Z); audit re-queued"
                requeued += 1
            else:
                audit.status = "failed"
                audit.error_code = "worker_lost"
                audit.error_message = "The worker executing this audit stopped responding"
                audit.completed_at = now
                message = "Worker lost and retry budget exhausted; audit failed"
                failed += 1
            s.add(
                AuditEvent(
                    organization_id=audit.organization_id,
                    audit_id=audit.id,
                    seq=seq,
                    type="audit.recovered" if audit.status == "queued" else "audit.failed",
                    level="warning" if audit.status == "queued" else "error",
                    message=message,
                    progress=audit.progress,
                    data={"attempts": audit.attempts},
                )
            )
        requeue_ids = [(a.id, a.organization_id) for a in stale if a.status == "queued"]
        # Audits queued for a while with no active ledger job (e.g. dispatch failed after commit).
        orphan_before = now - timedelta(minutes=5)
        orphans = s.execute(
            select(Audit.id, Audit.organization_id).where(Audit.status == "queued", Audit.updated_at < orphan_before)
        ).all()
        for audit_id, org_id in orphans:
            key = ledger.default_key(dispatcher.job_name(run_audit_job), org_id, [str(audit_id)])
            job = s.scalar(select(JobRun).where(JobRun.idempotency_key == key))
            if job is None or job.status not in ledger.ACTIVE or (job.updated_at and job.updated_at < stale_before):
                requeue_ids.append((audit_id, org_id))
        for model in (RedTeamRun, RegressionRun):
            result = s.execute(
                update(model)
                .where(
                    model.status == "running",
                    (model.heartbeat_at < stale_before)
                    | (model.heartbeat_at.is_(None) & (model.started_at < stale_before)),
                )
                .values(status="failed", error="Worker lost (no heartbeat)", completed_at=now)
            )
            runs_failed += int(getattr(result, "rowcount", 0) or 0)
    for audit_id, org_id in dict.fromkeys(requeue_ids):
        if dispatcher.dispatch(run_audit_job, org_id, str(audit_id), force=True):
            redispatched += 1
    return {"requeued": requeued, "failed": failed, "redispatched": redispatched, "runs_failed": runs_failed}


def retry_due_jobs() -> int:
    """Inline backend only: re-drive transient failures whose backoff has elapsed (Celery retries itself)."""
    if get_settings().effective_job_backend != "inline":
        return 0
    from aegis_api.jobs import dispatcher, ledger

    jobs = ledger.due_retries()
    for job in jobs:
        dispatcher.redrive(job)
    return len(jobs)


def purge_expired_sandboxes() -> int:
    """Delete guest demo workspaces after they expire. Evidence/audit-log purges are permitted only here,
    on the owner connection, via the purge GUC."""
    from aegis_api.models import Organization, User

    cutoff = utcnow() - timedelta(hours=get_settings().retention_sandbox_hours_grace)
    with admin_session_scope() as s:
        s.execute(text("select set_config('aegis.allow_evidence_delete', 'on', true)"))
        orgs = s.scalars(
            select(Organization).where(Organization.is_sandbox.is_(True), Organization.expires_at < cutoff).limit(25)
        ).all()
        ids = [o.id for o in orgs]
        if not ids:
            return 0
        guest_users = s.scalars(
            select(User.id).where(User.is_guest.is_(True), User.default_organization_id.in_(ids))
        ).all()
        s.execute(delete(Organization).where(Organization.id.in_(ids)))
        if guest_users:
            s.execute(delete(User).where(User.id.in_(guest_users)))
    log.info("sandboxes_purged", count=len(ids))
    return len(ids)


def prune_idempotency_keys() -> int:
    from aegis_api.models import IdempotencyKey

    with admin_session_scope() as s:
        result = s.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < utcnow()))
        return int(getattr(result, "rowcount", 0) or 0)


def apply_retention() -> dict[str, int]:
    from aegis_api.services import retention_service

    return retention_service.apply_all()


def expire_risk_acceptances() -> int:
    from aegis_api.services import finding_service

    return finding_service.expire_risk_acceptances()


def expire_runtime_approvals() -> int:
    from aegis_api.models import RuntimeApproval

    with admin_session_scope() as s:
        result = s.execute(
            update(RuntimeApproval)
            .where(RuntimeApproval.status == "pending", RuntimeApproval.expires_at < utcnow())
            .values(status="expired", updated_at=utcnow())
        )
        return int(getattr(result, "rowcount", 0) or 0)


def run_assurance_schedules() -> int:
    from aegis_api.services import assurance_service

    return assurance_service.run_due_schedules()


def usage_alerts() -> int:
    from aegis_api.services import usage_alert_service

    return usage_alert_service.send_threshold_alerts()


TASKS: dict[str, Callable[[], Any]] = {
    "deliver_webhooks": deliver_webhooks,
    "reap_stale_runs": reap_stale_runs,
    "retry_due_jobs": retry_due_jobs,
    "purge_expired_sandboxes": purge_expired_sandboxes,
    "prune_idempotency_keys": prune_idempotency_keys,
    "apply_retention": apply_retention,
    "expire_risk_acceptances": expire_risk_acceptances,
    "expire_runtime_approvals": expire_runtime_approvals,
    "run_assurance_schedules": run_assurance_schedules,
    "usage_alerts": usage_alerts,
}


def tick(only: list[str] | None = None) -> dict[str, Any]:
    """Run maintenance tasks once. Returns per-task results; skips entirely if another tick holds the lock."""
    results: dict[str, Any] = {}
    with get_admin_engine().connect() as conn:
        locked = conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar()
        conn.commit()
        if not locked:
            return {"skipped": "another maintenance tick is running"}
        try:
            for name, fn in TASKS.items():
                if only and name not in only:
                    continue
                started = time.perf_counter()
                try:
                    results[name] = fn()
                except Exception as exc:
                    log.exception("maintenance_task_failed", task=name)
                    results[name] = {"error": type(exc).__name__}
                elapsed = (time.perf_counter() - started) * 1000
                if elapsed > 5000:
                    log.warning("maintenance_task_slow", task=name, ms=round(elapsed))
        finally:
            conn.execute(text("select pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()
    return results


class InlineScheduler:
    """Background thread running :func:`tick` periodically (inline job backend only)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="aegis-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        interval = max(5, get_settings().scheduler_interval_seconds)
        while not self._stop.wait(interval):
            try:
                tick()
            except Exception:
                log.exception("maintenance_tick_failed")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None


scheduler = InlineScheduler()
