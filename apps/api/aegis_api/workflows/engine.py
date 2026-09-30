"""Inline durable workflow engine (no Temporal required).

Execution model — deterministic replay:
* A *drive* acquires a lease on the run (heartbeat-renewed), then executes the workflow coroutine from the
  top. Completed activities/signals/timers are served from ``lab.workflow_steps``; the first un-memoized
  activity executes (with retries) and its result is persisted before the coroutine continues.
* Waiting for a signal or timer *suspends* the run (status ``waiting``, ``waiting_on``, ``wake_at``) and
  releases the lease. The poller or a signal delivery drives it again later; replay fast-forwards to the
  suspension point.
* A crashed driver's lease expires and the poller resumes the run; an activity interrupted mid-flight is
  re-executed, which is why activities are idempotent.
* Cancellation is checked before every activity and delivered to the workflow as ``WorkflowCancelled``.

The only cross-tenant access is ``poll_due`` (system scheduler) reading run ids/organization ids; every drive
then runs inside the run's tenant (RLS-scoped) session.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import select, text

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope, session_scope
from aegis_api.infrastructure.observability import metrics
from aegis_api.models.lab import WorkflowRun, WorkflowSignal, WorkflowStep
from aegis_api.workflows.api import DEFAULT_RETRY, ActivityFailure, RetryPolicy, WorkflowCancelled
from aegis_api.workflows.runtime import CTX_KEY, ActivityError, run_activity

log = structlog.get_logger("aegis.workflows.inline")

TERMINAL = frozenset({"completed", "failed", "cancelled"})
MAX_CHILD_DEPTH = 4


class _Suspend(BaseException):
    def __init__(self, waiting_on: str, wake_at: datetime | None) -> None:
        super().__init__(waiting_on)
        self.waiting_on = waiting_on
        self.wake_at = wake_at


@dataclass
class _Step:
    status: str
    result: dict[str, Any] | None
    error: str | None


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"[:160]


class InlineContext:
    def __init__(self, run: WorkflowRun, steps: dict[str, _Step], depth: int) -> None:
        self.run_id = str(run.id)
        self.workflow = run.workflow
        self.input = dict(run.input or {})
        self._org = run.organization_id
        self._principal = dict(run.principal or {})
        self._steps = steps
        self._counters: dict[str, int] = {}
        self._depth = depth

    # --- helpers -------------------------------------------------------------------------------------
    def _key(self, prefix: str, key: str | None) -> str:
        if key:
            return f"{prefix}:{key}"[:255]
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}#{n}"[:255]

    def _save(
        self,
        key: str,
        activity: str,
        status: str,
        result: dict[str, Any] | None,
        error: str | None = None,
        attempts: int = 1,
        duration_ms: int | None = None,
    ) -> None:
        with session_scope(self._org) as db:
            db.add(
                WorkflowStep(
                    organization_id=self._org,
                    run_id=uuid.UUID(self.run_id),
                    step_key=key,
                    activity=activity[:80],
                    status=status,
                    attempts=attempts,
                    result=result,
                    error=error,
                    duration_ms=duration_ms,
                )
            )
        self._steps[key] = _Step(status, result, error)

    def _check_cancel(self) -> None:
        with session_scope(self._org) as db:
            cancelled = db.scalar(select(WorkflowRun.cancel_requested).where(WorkflowRun.id == uuid.UUID(self.run_id)))
        if cancelled:
            raise WorkflowCancelled()

    def _memo_time(self, key: str, seconds: float) -> datetime:
        step = self._steps.get(key)
        if step is not None and step.result:
            return datetime.fromisoformat(str(step.result["at"]))
        at = utcnow() + timedelta(seconds=seconds)
        self._save(key, "timer", "completed", {"at": at.isoformat()})
        return at

    # --- WorkflowContext -----------------------------------------------------------------------------
    async def activity(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        key: str | None = None,
        retry: RetryPolicy = DEFAULT_RETRY,
        timeout_seconds: float = 900.0,
        heartbeat_seconds: float | None = None,
    ) -> dict[str, Any]:
        k = self._key(f"act:{name}", key)
        step = self._steps.get(k)
        if step is not None:
            if step.status == "completed":
                return dict(step.result or {})
            info = step.result or {}
            raise ActivityFailure(
                name, step.error or "failed", code=str(info.get("code", "activity_failed")), details=info.get("details")
            )
        self._check_cancel()
        body = {
            **payload,
            CTX_KEY: {
                "organization_id": str(self._org),
                "principal": self._principal,
                "run_id": self.run_id,
                "workflow": self.workflow,
            },
        }
        started = time.perf_counter()
        attempt = 0
        delay = retry.initial_interval_seconds
        while True:
            attempt += 1
            try:
                result = run_activity(name, body, attempt=attempt)
                metrics.WORKFLOW_ACTIVITIES.labels(activity=name, outcome="ok").inc()
                self._save(
                    k,
                    name,
                    "completed",
                    result,
                    attempts=attempt,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                return result
            except ActivityError as exc:
                retryable = exc.retryable and exc.code not in retry.non_retryable
                if retryable and attempt < retry.max_attempts:
                    metrics.WORKFLOW_ACTIVITIES.labels(activity=name, outcome="retry").inc()
                    log.warning("activity_retry", activity=name, attempt=attempt, code=exc.code)
                    time.sleep(min(delay, retry.max_interval_seconds))
                    delay *= retry.backoff
                    self._check_cancel()
                    continue
                metrics.WORKFLOW_ACTIVITIES.labels(activity=name, outcome="failed").inc()
                self._save(
                    k,
                    name,
                    "failed",
                    {"code": exc.code, "details": exc.details},
                    error=exc.message[:4000],
                    attempts=attempt,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                raise ActivityFailure(name, exc.message, code=exc.code, details=exc.details) from None

    async def wait_signal(
        self, name: str, *, key: str | None = None, timeout_seconds: float | None = None
    ) -> dict[str, Any] | None:
        k = self._key(f"sig:{name}", key)
        step = self._steps.get(k)
        if step is not None:
            return None if (step.result or {}).get("timeout") else dict((step.result or {}).get("payload") or {})
        deadline = self._memo_time(f"{k}:deadline", timeout_seconds) if timeout_seconds is not None else None
        payload = _consume_signal(self._org, uuid.UUID(self.run_id), name)
        if payload is not None:
            self._save(k, "signal", "completed", {"payload": payload})
            return payload
        if deadline is not None and utcnow() >= deadline:
            self._save(k, "signal", "completed", {"timeout": True})
            return None
        self._check_cancel()
        raise _Suspend(f"signal:{name}", deadline)

    async def sleep(self, seconds: float, *, key: str | None = None) -> None:
        k = self._key("timer", key)
        if k in self._steps:
            return
        wake = self._memo_time(f"{k}:wake", seconds)
        if utcnow() >= wake:
            self._save(k, "timer", "completed", {"woke": True})
            return
        raise _Suspend(f"timer:{k}", wake)

    async def child(self, workflow: str, payload: dict[str, Any], *, key: str) -> dict[str, Any]:
        k = self._key(f"child:{workflow}", key)
        step = self._steps.get(k)
        if step is not None:
            if step.status == "completed":
                return dict(step.result or {})
            raise ActivityFailure(f"child:{workflow}", step.error or "child workflow failed", code="child_failed")
        if self._depth >= MAX_CHILD_DEPTH:
            raise ActivityFailure(f"child:{workflow}", "maximum child workflow depth exceeded", code="child_depth")
        self._check_cancel()
        child_id = _ensure_child(self, workflow, payload, business_key=f"{self.run_id}:{k}")
        status, result, error = _child_state(self._org, child_id)
        if status not in TERMINAL:
            drive(child_id, self._org, depth=self._depth + 1)
            status, result, error = _child_state(self._org, child_id)
        if status == "completed":
            self._save(k, f"child:{workflow}", "completed", result or {})
            return dict(result or {})
        if status in ("failed", "cancelled"):
            self._save(k, f"child:{workflow}", "failed", {"code": "child_failed"}, error=error or status)
            raise ActivityFailure(f"child:{workflow}", error or status, code="child_failed")
        raise _Suspend(f"child:{child_id}", None)

    def now(self) -> datetime:
        k = self._key("now", None)
        step = self._steps.get(k)
        if step is not None and step.result:
            return datetime.fromisoformat(str(step.result["at"]))
        at = utcnow()
        self._save(k, "now", "completed", {"at": at.isoformat()})
        return at

    async def gather(self, *aws: Awaitable[dict[str, Any]]) -> list[dict[str, Any]]:
        # Sequential on the inline engine (deterministic order); Temporal runs these concurrently.
        results: list[dict[str, Any]] = []
        pending = list(aws)
        try:
            for item in pending:
                results.append(await item)
        finally:
            for item in pending[len(results) + 1 :]:
                close = getattr(item, "close", None)
                if close is not None:
                    close()  # suspended mid-gather: discard the not-yet-started coroutines cleanly
        return results


def _run_inline(awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Run a workflow coroutine synchronously. Inline context operations never yield to an event loop, so the
    coroutine completes (or raises a suspension/cancellation/failure) on the first step — which also makes
    nested child drives possible without an event loop."""
    coro = cast(Coroutine[Any, Any, dict[str, Any]], awaitable)
    try:
        coro.send(None)
    except StopIteration as stop:
        return cast(dict[str, Any], stop.value)
    coro.close()
    raise RuntimeError("inline workflows may only await WorkflowContext operations")


def _consume_signal(org: uuid.UUID, run_id: uuid.UUID, name: str) -> dict[str, Any] | None:
    with session_scope(org) as db:
        sig = db.scalar(
            select(WorkflowSignal)
            .where(WorkflowSignal.run_id == run_id, WorkflowSignal.name == name, WorkflowSignal.consumed_at.is_(None))
            .order_by(WorkflowSignal.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if sig is None:
            return None
        sig.consumed_at = utcnow()
        return dict(sig.payload or {})


def _ensure_child(parent: InlineContext, workflow: str, payload: dict[str, Any], *, business_key: str) -> uuid.UUID:
    with session_scope(parent._org) as db:
        existing = db.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.organization_id == parent._org,
                WorkflowRun.workflow == workflow,
                WorkflowRun.business_key == business_key[:160],
            )
        )
        if existing:
            return existing
        child = WorkflowRun(
            organization_id=parent._org,
            workflow=workflow,
            business_key=business_key[:160],
            engine="inline",
            status="pending",
            input=payload,
            principal=parent._principal,
            parent_run_id=uuid.UUID(parent.run_id),
        )
        db.add(child)
        db.flush()
        return child.id


def _child_state(org: uuid.UUID, run_id: uuid.UUID) -> tuple[str, dict[str, Any] | None, str | None]:
    with session_scope(org) as db:
        row = db.execute(
            select(WorkflowRun.status, WorkflowRun.result, WorkflowRun.error).where(WorkflowRun.id == run_id)
        ).one()
        return str(row[0]), row[1], row[2]


# --- leasing -----------------------------------------------------------------------------------------------


def _acquire(org: uuid.UUID, run_id: uuid.UUID, owner: str, lease: int) -> bool:
    with session_scope(org) as db:
        got = db.execute(
            text(
                "UPDATE lab.workflow_runs SET lease_owner = :o, lease_until = now() + make_interval(secs => :l), "
                "status = CASE WHEN status IN ('pending', 'waiting') THEN 'running' ELSE status END, "
                "attempts = attempts + 1, started_at = coalesce(started_at, now()), updated_at = now() "
                "WHERE id = :id AND status IN ('pending', 'running', 'waiting') "
                "AND (lease_until IS NULL OR lease_until < now() OR lease_owner = :o) RETURNING id"
            ),
            {"o": owner, "l": lease, "id": run_id},
        ).first()
        return got is not None


class _Heartbeat(threading.Thread):
    def __init__(self, org: uuid.UUID, run_id: uuid.UUID, owner: str, lease: int) -> None:
        super().__init__(daemon=True, name=f"wf-heartbeat-{run_id}")
        self.org, self.run_id, self.owner, self.lease = org, run_id, owner, lease
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(max(5.0, self.lease / 3)):
            try:
                with session_scope(self.org) as db:
                    db.execute(
                        text(
                            "UPDATE lab.workflow_runs SET lease_until = now() + make_interval(secs => :l) "
                            "WHERE id = :id AND lease_owner = :o"
                        ),
                        {"l": self.lease, "id": self.run_id, "o": self.owner},
                    )
            except Exception:
                log.warning("workflow_heartbeat_failed", run_id=str(self.run_id))

    def stop(self) -> None:
        self._stop_event.set()


def _finish(
    org: uuid.UUID,
    run_id: uuid.UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    waiting_on: str | None = None,
    wake_at: datetime | None = None,
) -> uuid.UUID | None:
    with session_scope(org) as db:
        run = db.get(WorkflowRun, run_id)
        assert run is not None
        run.status = status
        run.lease_owner = None
        run.lease_until = None
        run.waiting_on = waiting_on
        run.wake_at = wake_at
        if result is not None:
            run.result = result
        if error is not None:
            run.error = error[:4000]
        if status in TERMINAL:
            run.completed_at = utcnow()
            if run.started_at:
                metrics.WORKFLOW_DURATION.labels(workflow=run.workflow, status=status).observe(
                    (run.completed_at - run.started_at).total_seconds()
                )
            if run.parent_run_id:
                return run.parent_run_id  # the parent re-reads the child's state when it is driven again
    return None


# --- driving ------------------------------------------------------------------------------------------------


def drive(run_id: uuid.UUID, org: uuid.UUID, *, depth: int = 0) -> str:
    from aegis_api.workflows.definitions import WORKFLOWS

    settings = get_settings()
    owner = _owner()
    lease = settings.workflow_lease_seconds
    if not _acquire(org, run_id, owner, lease):
        return "busy"
    heartbeat = _Heartbeat(org, run_id, owner, lease)
    heartbeat.start()
    parent: uuid.UUID | None = None
    outcome = "running"
    try:
        with session_scope(org) as db:
            run = db.get(WorkflowRun, run_id)
            assert run is not None
            steps = {
                s.step_key: _Step(s.status, s.result, s.error)
                for s in db.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run_id)).all()
            }
            definition = WORKFLOWS.get(run.workflow)
            cancel_requested = run.cancel_requested
            db.expunge(run)
        if definition is None:
            parent = _finish(org, run_id, status="failed", error=f"unknown workflow '{run.workflow}'")
            return "failed"
        if cancel_requested:
            parent = _finish(org, run_id, status="cancelled", error="cancelled")
            return "cancelled"
        ctx = InlineContext(run, steps, depth)
        structlog.contextvars.bind_contextvars(workflow=run.workflow, workflow_run_id=str(run_id))
        try:
            result = _run_inline(definition.fn(ctx))
            parent = _finish(org, run_id, status="completed", result=result if isinstance(result, dict) else {})
            outcome = "completed"
        except _Suspend as s:
            _finish(org, run_id, status="waiting", waiting_on=s.waiting_on, wake_at=s.wake_at)
            outcome = "waiting"
        except WorkflowCancelled:
            parent = _finish(org, run_id, status="cancelled", error="cancelled")
            outcome = "cancelled"
        except ActivityFailure as exc:
            parent = _finish(org, run_id, status="failed", error=str(exc), result={"failure": exc.to_dict()})
            outcome = "failed"
        except Exception as exc:
            log.exception("workflow_crashed", workflow=run.workflow, run_id=str(run_id))
            parent = _finish(org, run_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:4000])
            outcome = "failed"
        finally:
            structlog.contextvars.unbind_contextvars("workflow", "workflow_run_id")
    finally:
        heartbeat.stop()
    if parent is not None and depth == 0:
        _kick(parent, org)
    return outcome


def _kick(run_id: uuid.UUID, org: uuid.UUID) -> None:
    from aegis_api.jobs.dispatcher import dispatch_task

    try:
        dispatch_task("aegis_api.workflows.engine:drive_inline_run", str(run_id), str(org))
    except Exception:
        log.warning("workflow_kick_failed", run_id=str(run_id))


def drive_inline_run(run_id: str, organization_id: str) -> str:
    """Task entrypoint (dispatcher/Celery). Drives until the run completes or suspends."""
    return drive(uuid.UUID(run_id), uuid.UUID(organization_id))


def poll_due(limit: int = 50) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """System scheduler: find inline runs that are ready to make progress (all tenants; ids only)."""
    with admin_session_scope() as db:
        rows = db.execute(
            text(
                """
                SELECT r.id, r.organization_id FROM lab.workflow_runs r
                WHERE r.engine = 'inline' AND (
                    r.status = 'pending'
                    OR (r.status = 'running' AND (r.lease_until IS NULL OR r.lease_until < now()))
                    OR (r.status = 'waiting' AND (
                        (r.wake_at IS NOT NULL AND r.wake_at <= now())
                        OR r.cancel_requested
                        OR EXISTS (SELECT 1 FROM lab.workflow_signals s WHERE s.run_id = r.id
                                   AND s.consumed_at IS NULL AND r.waiting_on = 'signal:' || s.name)
                        OR (r.waiting_on LIKE 'child:%' AND EXISTS (
                            SELECT 1 FROM lab.workflow_runs c WHERE c.parent_run_id = r.id
                            AND c.status IN ('completed', 'failed', 'cancelled')
                            AND r.waiting_on = 'child:' || c.id::text))
                    ))
                )
                ORDER BY r.updated_at
                LIMIT :n
                """
            ),
            {"n": limit},
        ).all()
    return [(r[0], r[1]) for r in rows]
