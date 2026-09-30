"""Periodic maintenance process: ``python -m aegis_api.lab.workflows.scheduler``.

Every ``SCHEDULER_INTERVAL_SECONDS`` (default 15) it runs the tasks that are due. Each task holds a global
PostgreSQL advisory lock while it runs, so any number of scheduler replicas can be deployed and only one acts
per task at a time. Cross-tenant iteration lists organization ids with the owner connection (ids only); all
work then happens in tenant (RLS-scoped) sessions, one organization at a time.

Tasks:

* ``start_pending``   — start PENDING workflow runs older than 30 s (both engines);
* ``resume_stale``    — resume local runs with a stale heartbeat / reconcile Temporal runs;
* ``deliver_signals`` — Temporal signal and cancellation re-delivery;
* ``reconcile_jobs``  — ``execution.reconcile_stale_jobs``;
* ``expire_approvals``— every 15 minutes, ``governance.expire_approvals`` (organizations with overdue approvals);
* ``retention_purge`` — daily, per organization retention policy;
* ``rollup_usage``    — daily, ``governance.rollup_usage`` for the previous and the current month;
* ``mcp_health``      — ``aegis_api.lab.tools.mcp.registry.run_health_checks`` when that module exists.

Activities owned by other contexts are resolved lazily from the activity registry and skipped (logged once)
when their module is not installed.
"""

from __future__ import annotations

import importlib
import inspect
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import FrameType
from typing import Any

import structlog
from sqlalchemy import func, select

from aegis_api.db.base import utcnow
from aegis_api.db.session import get_admin_engine
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.observability.metrics import WORKFLOW_ACTIVITY_DURATION
from aegis_api.lab.workflows import maintenance
from aegis_api.lab.workflows.registry import ACTIVITIES, ActivityContext, ActivityDef, actor_to_payload, load_activities

log = structlog.get_logger("aegis.lab.scheduler")

DEFAULT_INTERVAL_SECONDS = 15.0
DEFAULT_METRICS_PORT = 9103
DAILY = 24 * 3600.0
MCP_HEALTH_EVERY_SECONDS = 300.0
EXPIRE_APPROVALS_EVERY_SECONDS = 900.0
LOCK_PREFIX = "lab-scheduler:"


@dataclass
class ScheduledTask:
    name: str
    fn: Callable[[], dict[str, Any]]
    every_seconds: float | None = None  # None → every tick
    last_run: float | None = None

    def due(self, now: float) -> bool:
        if self.every_seconds is None or self.last_run is None:
            return True
        return now - self.last_run >= self.every_seconds


@dataclass
class TaskResult:
    status: str  # ok | locked | error | skipped
    detail: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0


@contextmanager
def task_lock(name: str) -> Iterator[bool]:
    """Session-level advisory try-lock held on a dedicated owner connection for the task's duration."""
    engine = get_admin_engine()
    with engine.connect() as conn:
        key = func.hashtextextended(f"{LOCK_PREFIX}{name}", 0)
        acquired = bool(conn.execute(select(func.pg_try_advisory_lock(key))).scalar())
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(select(func.pg_advisory_unlock(key)))
                conn.commit()


# ---------------------------------------------------------------------------------------------------
# Activities owned by other contexts
# ---------------------------------------------------------------------------------------------------
_missing_logged: set[str] = set()


def find_activity(name: str) -> ActivityDef | None:
    if name not in ACTIVITIES:
        try:
            load_activities(strict=False)
        except Exception:
            log.warning("scheduler_activity_modules_failed", exc_info=True)
    defn = ACTIVITIES.get(name)
    if defn is None and name not in _missing_logged:
        _missing_logged.add(name)
        log.info("scheduler_activity_unavailable", activity=name)
    return defn


def run_org_activity(defn: ActivityDef, organization_id: uuid.UUID, extra: dict[str, Any] | None = None) -> Any:
    """Invoke a registry activity directly for one organization as the platform system actor."""
    actor = Actor.system(organization_id, label=f"scheduler:{defn.name}")
    payload = {
        **(extra or {}),
        "organization_id": str(organization_id),
        "actor": actor_to_payload(actor),
        "workflow_run_id": None,
    }
    return defn.fn(ActivityContext(actor=actor), payload)


def _per_org(org_ids: list[uuid.UUID], fn: Callable[[uuid.UUID], Any]) -> dict[str, Any]:
    ok = failed = 0
    totals: dict[str, int] = {}
    for org_id in org_ids:
        try:
            result = fn(org_id)
        except Exception:
            failed += 1
            log.warning("scheduler_org_task_failed", organization_id=str(org_id), exc_info=True)
            continue
        ok += 1
        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
    return {"organizations": ok, "failed": failed, **totals}


def _activity_task(name: str, org_ids: Callable[[], list[uuid.UUID]]) -> Callable[[], dict[str, Any]]:
    def task() -> dict[str, Any]:
        defn = find_activity(name)
        if defn is None:
            return {"skipped": "activity not registered"}
        return _per_org(org_ids(), lambda org: run_org_activity(defn, org))

    task.__name__ = f"task_{name.replace('.', '_')}"
    return task


# ---------------------------------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------------------------------
def task_start_pending() -> dict[str, Any]:
    return _per_org(maintenance.organization_ids_with_open_workflows(), maintenance.start_pending_runs)


def task_resume_stale() -> dict[str, Any]:
    return _per_org(maintenance.organization_ids_with_open_workflows(), maintenance.resume_stale_runs)


def task_deliver_signals() -> dict[str, Any]:
    return _per_org(maintenance.organization_ids_with_open_workflows(), maintenance.deliver_pending_signals)


def task_retention_purge() -> dict[str, Any]:
    return _per_org(maintenance.organization_ids(), maintenance.retention_purge)


def _orgs_with_open_compute_jobs() -> list[uuid.UUID]:
    from aegis_api.lab.models import ComputeJob
    from engines.lab.states import EXECUTION_TERMINAL

    return maintenance.organization_ids_where(
        select(ComputeJob.organization_id).where(ComputeJob.status.notin_(tuple(EXECUTION_TERMINAL))).distinct()
    )


def _orgs_with_overdue_approvals() -> list[uuid.UUID]:
    from aegis_api.lab.models import Approval
    from engines.lab.states import ApprovalStatus

    return maintenance.organization_ids_where(
        select(Approval.organization_id)
        .where(Approval.status == ApprovalStatus.PENDING, Approval.expires_at <= utcnow())
        .distinct()
    )


def usage_periods(now: datetime | None = None) -> list[str]:
    """``YYYY-MM`` of the previous and the current month (the previous month is re-rolled so late ledger
    entries are counted before the period is finalized)."""
    current = now or utcnow()
    previous = (current.replace(day=1) - timedelta(days=1)).replace(day=1)
    return [previous.strftime("%Y-%m"), current.strftime("%Y-%m")]


def task_rollup_usage() -> dict[str, Any]:
    defn = find_activity("governance.rollup_usage")
    if defn is None:
        return {"skipped": "activity not registered"}
    periods = usage_periods()

    def rollup(org: uuid.UUID) -> dict[str, int]:
        for period in periods:
            run_org_activity(defn, org, {"period": period})
        return {"periods": len(periods)}

    return _per_org(maintenance.organization_ids(), rollup)


def _resolve_mcp_health() -> Callable[..., Any] | None:
    try:
        module = importlib.import_module("aegis_api.lab.tools.mcp.registry")
    except ModuleNotFoundError:
        return None
    fn = getattr(module, "run_health_checks", None)
    return fn if callable(fn) else None


def task_mcp_health() -> dict[str, Any]:
    """Supports ``run_health_checks()``, ``run_health_checks(organization_id)`` and
    ``run_health_checks(db, organization_id)`` (tenant session)."""
    fn = _resolve_mcp_health()
    if fn is None:
        return {"skipped": "aegis_api.lab.tools.mcp.registry.run_health_checks not available"}
    params = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not params:
        result = fn()
        return result if isinstance(result, dict) else {"ran": True}
    if len(params) == 1:
        return _per_org(maintenance.organization_ids(), fn)

    def with_session(org: uuid.UUID) -> Any:
        with tenant_uow(org) as db:
            return fn(db, org)

    return _per_org(maintenance.organization_ids(), with_session)


def default_tasks(interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> list[ScheduledTask]:
    return [
        ScheduledTask("start_pending", task_start_pending),
        ScheduledTask("resume_stale", task_resume_stale),
        ScheduledTask("deliver_signals", task_deliver_signals),
        ScheduledTask("reconcile_jobs", _activity_task("execution.reconcile_stale_jobs", _orgs_with_open_compute_jobs)),
        ScheduledTask(
            "expire_approvals",
            _activity_task("governance.expire_approvals", _orgs_with_overdue_approvals),
            every_seconds=EXPIRE_APPROVALS_EVERY_SECONDS,
        ),
        ScheduledTask("retention_purge", task_retention_purge, every_seconds=DAILY),
        ScheduledTask("rollup_usage", task_rollup_usage, every_seconds=DAILY),
        ScheduledTask("mcp_health", task_mcp_health, every_seconds=MCP_HEALTH_EVERY_SECONDS),
    ]


class Scheduler:
    def __init__(self, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS, tasks: list[ScheduledTask] | None = None):
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.tasks = tasks if tasks is not None else default_tasks(self.interval_seconds)

    def run_task(self, task: ScheduledTask) -> TaskResult:
        started = time.monotonic()
        try:
            with task_lock(task.name) as acquired:
                if not acquired:
                    return TaskResult("locked")
                detail = task.fn() or {}
        except Exception as exc:
            duration = time.monotonic() - started
            WORKFLOW_ACTIVITY_DURATION.labels(f"scheduler.{task.name}", "error").observe(duration)
            log.exception("scheduler_task_failed", task=task.name)
            return TaskResult("error", {"error": type(exc).__name__}, duration)
        duration = time.monotonic() - started
        task.last_run = time.monotonic()
        WORKFLOW_ACTIVITY_DURATION.labels(f"scheduler.{task.name}", "completed").observe(duration)
        status = "skipped" if isinstance(detail.get("skipped"), str) else "ok"
        return TaskResult(status, detail, duration)

    def run_once(self, *, force: bool = False) -> dict[str, TaskResult]:
        """Run every due task once (``force``: every task regardless of its period)."""
        now = time.monotonic()
        results: dict[str, TaskResult] = {}
        for task in self.tasks:
            if force or task.due(now):
                results[task.name] = self.run_task(task)
        return results

    def run_forever(self, stop: threading.Event) -> None:
        log.info("scheduler_started", interval_seconds=self.interval_seconds, tasks=[t.name for t in self.tasks])
        while not stop.is_set():
            tick: datetime = utcnow()
            results = self.run_once()
            busy = {name: r.detail for name, r in results.items() if r.status == "ok" and _has_work(r.detail)}
            if busy:
                log.info("scheduler_tick", at=tick.isoformat(), **busy)
            stop.wait(self.interval_seconds)
        log.info("scheduler_stopped")


def _has_work(detail: dict[str, Any]) -> bool:
    return any(
        isinstance(v, int) and not isinstance(v, bool) and v > 0
        for k, v in detail.items()
        if k not in ("organizations", "failed")
    ) or bool(detail.get("failed"))


# ---------------------------------------------------------------------------------------------------
# Process entrypoint
# ---------------------------------------------------------------------------------------------------
def main() -> int:
    """``python -m aegis_api.lab.workflows.scheduler`` — loop until SIGTERM/SIGINT."""
    from prometheus_client import start_http_server

    from aegis_api.config import get_settings
    from aegis_api.lab.observability.metrics import REGISTRY, register_runtime_collectors
    from aegis_api.lab.observability.telemetry import configure_telemetry, shutdown_telemetry
    from aegis_api.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_telemetry(service_name=f"{settings.otel_service_name}-scheduler")
    register_runtime_collectors()
    interval = max(0.5, settings.scheduler_interval_seconds)
    metrics_port = settings.metrics_port or DEFAULT_METRICS_PORT
    start_http_server(metrics_port, registry=REGISTRY)

    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        log.info("scheduler_stopping", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    Scheduler(interval_seconds=interval).run_forever(stop)
    # Local-engine workflows resumed by this process keep running on their own threads; give them time.
    from aegis_api.lab.workflows.local_engine import wait_for_background

    wait_for_background(timeout=30.0)
    shutdown_telemetry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
