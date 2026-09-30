"""Scheduler: periodic maintenance, run as a singleton (distributed lock).

* approvals: expire overdue requests (and signal the waiting workflows);
* Temporal reconciliation: start runs whose post-commit start failed; re-deliver undelivered signals;
* stale work: fail agent runs past their deadline, mark sandbox jobs whose heartbeat stopped, reap orphaned
  sandbox containers;
* hourly: retention, daily usage roll-ups, MCP server health, expired refresh tokens.

    python -m aegis_api.processes.scheduler
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import timedelta

import structlog
from sqlalchemy import select, text

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope, session_scope
from aegis_api.infrastructure.locks import distributed_lock
from aegis_api.models.lab import AgentRun, ExecutionJob, MCPServer
from aegis_api.processes.common import organization_ids, setup
from aegis_api.services.lab import approvals, billing, retention
from engines.lab.enums import AgentRunStatus, ExecutionStatus

log = structlog.get_logger("aegis.scheduler")

STALE_JOB_GRACE = timedelta(minutes=10)


def expire_approvals() -> int:
    total = 0
    for org in organization_ids():
        with session_scope(org) as db:
            total += approvals.expire_due(db)
    return total


def reconcile_temporal() -> dict[str, int]:
    settings = get_settings()
    if settings.effective_workflow_engine != "temporal":
        return {"started": 0, "signals": 0}
    from aegis_api.workflows import temporal

    started = signals = 0
    with admin_session_scope() as db:
        runs = db.execute(
            text(
                "SELECT id, organization_id FROM lab.workflow_runs WHERE engine = 'temporal' AND status = 'pending' "
                "AND temporal_run_id IS NULL AND created_at < now() - interval '30 seconds' LIMIT 50"
            )
        ).all()
        pending_signals = db.execute(
            text(
                "SELECT s.id, s.organization_id FROM lab.workflow_signals s JOIN lab.workflow_runs r ON r.id = s.run_id "
                "WHERE r.engine = 'temporal' AND s.consumed_at IS NULL AND s.created_at < now() - interval '10 seconds' "
                "AND r.status NOT IN ('completed', 'failed', 'cancelled') LIMIT 100"
            )
        ).all()
    for run_id, org in runs:
        try:
            temporal.start_run(run_id, org)
            started += 1
        except Exception:
            log.warning("temporal_reconcile_start_failed", run_id=str(run_id))
    for sig_id, org in pending_signals:
        try:
            temporal.deliver_signal(org, sig_id)
            signals += 1
        except Exception:
            log.warning("temporal_reconcile_signal_failed", signal_id=str(sig_id))
    return {"started": started, "signals": signals}


def fail_stale_work() -> dict[str, int]:
    now = utcnow()
    agents = jobs = 0
    for org in organization_ids():
        with session_scope(org) as db:
            for run in db.scalars(
                select(AgentRun).where(
                    AgentRun.status.in_(
                        [
                            AgentRunStatus.PLANNING,
                            AgentRunStatus.EXECUTING,
                            AgentRunStatus.WAITING_TOOL,
                            AgentRunStatus.EVALUATING,
                        ]
                    ),
                    AgentRun.deadline_at < now - STALE_JOB_GRACE,
                )
            ).all():
                run.status = AgentRunStatus.FAILED
                run.error = "abandoned: exceeded deadline without completing (worker interruption)"
                run.completed_at = now
                agents += 1
            for job in db.scalars(
                select(ExecutionJob).where(
                    ExecutionJob.status.in_([ExecutionStatus.PROVISIONING, ExecutionStatus.RUNNING]),
                    ExecutionJob.started_at.is_not(None),
                )
            ).all():
                started = job.started_at or now
                if started + timedelta(seconds=job.timeout_seconds) + STALE_JOB_GRACE < now:
                    job.status = ExecutionStatus.FAILED
                    job.error = "abandoned: no completion recorded within timeout + grace (worker interruption)"
                    job.completed_at = now
                    jobs += 1
    return {"agent_runs": agents, "execution_jobs": jobs}


def reap_sandboxes() -> int:
    try:
        from aegis_api.infrastructure.execution.registry import get_backend

        backend = get_backend()
        reaper = getattr(backend, "reap_orphans", None)
        return int(reaper(max_age_seconds=get_settings().execution_max_timeout_seconds + 900)) if reaper else 0
    except Exception:
        log.warning("sandbox_reap_failed")
        return 0


def hourly() -> dict[str, object]:
    out: dict[str, object] = {}
    today = utcnow()
    for org in organization_ids():
        try:
            with session_scope(org) as db:
                out[f"retention:{org}"] = retention.apply(db, org)
            with session_scope(org) as db:
                billing.rollup_day(db, org, today)
                billing.rollup_day(db, org, today - timedelta(days=1))
            _mcp_health(org)
        except Exception:
            log.exception("hourly_maintenance_failed", organization_id=str(org))
    with admin_session_scope() as db:
        out["refresh_tokens"] = retention.purge_expired_refresh_tokens(db)
    return out


def _mcp_health(org: uuid.UUID) -> None:
    from aegis_api.services.lab import mcp

    with session_scope(org) as db:
        servers = [s.id for s in db.scalars(select(MCPServer).where(MCPServer.status == "active")).all()]
    for server_id in servers:
        try:
            mcp.discover(org, server_id)
        except Exception:
            log.warning("mcp_health_failed", server_id=str(server_id))


TASKS: list[tuple[str, float, Callable[[], object]]] = [
    ("expire_approvals", 30.0, expire_approvals),
    ("reconcile_temporal", 30.0, reconcile_temporal),
    ("fail_stale_work", 300.0, fail_stale_work),
    ("reap_sandboxes", 600.0, reap_sandboxes),
    ("hourly", 3600.0, hourly),
]


def run_forever() -> None:
    stopper = setup("scheduler")
    last: dict[str, float] = {}
    while not stopper.stopped:
        with distributed_lock("scheduler-leader", ttl_seconds=90) as leader:
            if leader:
                now = time.monotonic()
                for name, interval, fn in TASKS:
                    if now - last.get(name, 0.0) < interval:
                        continue
                    last[name] = now
                    try:
                        result = fn()
                        log.info("scheduled_task_done", task=name, result=str(result)[:300])
                    except Exception:
                        log.exception("scheduled_task_failed", task=name)
        stopper.wait(10.0)


if __name__ == "__main__":
    run_forever()
