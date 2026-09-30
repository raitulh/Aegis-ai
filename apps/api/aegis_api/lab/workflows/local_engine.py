"""Local durable workflow engine: replay-based execution with memoized steps in PostgreSQL.

Used when Temporal is not configured (``settings.effective_workflow_engine == "local"``). It gives flows the
same guarantees the Temporal engine gives them, with PostgreSQL as the history store:

* **Replay.** Every activity result (and ``now()``/``uuid()``, signal consumption, timers, child results) is
  recorded in ``workflow_steps`` under a deterministic step key. Re-running a run re-executes the flow code
  from the top; recorded steps return their stored result instead of executing again, so activities that
  completed before a crash are never executed twice. Only a step that was *in flight* at the crash runs
  again (activities are idempotent by contract).
* **Leases.** An executor *claims* a run by writing a fresh lease token into ``external_run_id`` and keeps
  ``heartbeat_at`` fresh (every ≤ 10 s). Another executor may take over only when the heartbeat is older
  than ``settings.workflow_stale_after_seconds``; every write (steps, status) is fenced by the token, so a
  zombie executor can never overwrite the work of its successor.
* **Retries.** Activities run in worker threads with their :class:`RetrySpec` (bounded exponential backoff,
  non-retryable error classes, start-to-close and heartbeat timeouts). Failures after retries are recorded
  and raised into the flow as :class:`ActivityFailed` — also on replay.
* **Signals** are appended to ``workflow_runs.signals`` by ``launcher.signal_workflow`` and consumed by
  ``wait_signal`` atomically with the step that records the consumption. The run is WAITING meanwhile.
* **Cancellation.** ``cancel_requested`` is observed by the heartbeat supervisor (and signal polls); the flow
  gets ``asyncio.CancelledError`` at its next await and the run ends CANCELLED.
* **Children** run inline in the same event loop (resumed through their parent); detached workflows are
  dispatched independently.

Runs execute inside ``asyncio.run`` on a dedicated thread — never on the API event loop — and never hold a
database transaction open across flow or activity code (every DB interaction is a short tenant unit of work).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import socket
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.events import after_commit
from aegis_api.lab.models import WorkflowRun, WorkflowStep
from aegis_api.lab.observability.metrics import WORKFLOW_ACTIVITY_DURATION
from aegis_api.lab.observability.tracing import span
from aegis_api.lab.workflows import runs
from aegis_api.lab.workflows.ctx import (
    ActivityFailed,
    ActivityTimeout,
    ChildWorkflowFailed,
    StepKeys,
    WorkflowDefinitionError,
    WorkflowDeterminismError,
    attempts_allowed,
    backoff_seconds,
    build_activity_payload,
    describe_exception,
    error_record,
    normalize_mapping,
    normalize_result,
    validate_signal_name,
    validate_step_key,
    validate_workflow_key,
)
from aegis_api.lab.workflows.definitions import UnknownFlowKind, flow_options, get_flow, has_flow
from aegis_api.lab.workflows.registry import ActivityContext, ActivityDef, actor_from_payload, get_activity
from engines.lab.states import WorkflowStatus

log = structlog.get_logger("aegis.lab.workflows.local")

W = WorkflowStatus

# Tunables (module-level so deployments/tests can adjust them; read when an engine is constructed).
HEARTBEAT_INTERVAL_SECONDS = 5.0
ACTIVITY_HEARTBEAT_THROTTLE_SECONDS = 2.0
ACTIVITY_POLL_SECONDS = 0.5
SIGNAL_POLL_MIN_SECONDS = 0.5
SIGNAL_POLL_MAX_SECONDS = 5.0
SLEEP_SLICE_SECONDS = 5.0
ACTIVITY_WORKERS = 8

STEP_NOW = "workflow.now"
STEP_UUID = "workflow.uuid"
STEP_SIGNAL = "workflow.signal"
STEP_SLEEP = "workflow.sleep"
STEP_CHILD = "workflow.child"
STEP_DETACHED = "workflow.detached"


class _LeaseLost(BaseException):
    """Another executor owns the run now; stop without touching its state (BaseException: flows can't catch it)."""


@dataclass
class _Memo:
    step_key: str
    activity: str
    status: str
    result: Any
    error: dict[str, Any] | None
    attempt: int
    started_at: datetime | None


@dataclass
class RunOutcome:
    """What an engine invocation did with a run."""

    workflow_run_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    executed: bool = True  # False: not claimed (terminal, owned by a live executor, missing, other engine)
    lease_lost: bool = False


@dataclass
class _Claim:
    run_id: uuid.UUID
    organization_id: uuid.UUID
    kind: str
    flow_input: dict[str, Any]
    actor_payload: dict[str, Any]
    token: str
    attempt: int
    started_at: datetime
    resumed: bool
    steps: dict[str, _Memo] = field(default_factory=dict)


@dataclass(eq=False)
class _Attempt:
    cancel: threading.Event = field(default_factory=threading.Event)
    last_heartbeat: float = field(default_factory=time.monotonic)


def _parse_error(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return {"type": "Error", "message": value}
    return data if isinstance(data, dict) else {"type": "Error", "message": str(data)}


def _memo_from_row(row: WorkflowStep) -> _Memo:
    return _Memo(
        step_key=row.step_key,
        activity=row.activity,
        status=row.status,
        result=row.result,
        error=_parse_error(row.error),
        attempt=row.attempt or 1,
        started_at=row.started_at,
    )


class LocalWorkflowEngine:
    """Executes/resumes workflow runs of the local engine. Stateless between calls; safe to share."""

    def __init__(
        self,
        *,
        heartbeat_interval: float | None = None,
        stale_after_seconds: float | None = None,
        activity_workers: int | None = None,
        signal_poll_min: float | None = None,
        signal_poll_max: float | None = None,
    ) -> None:
        settings = get_settings()
        self.stale_after = float(stale_after_seconds or settings.workflow_stale_after_seconds)
        default_hb = min(HEARTBEAT_INTERVAL_SECONDS, 10.0, max(0.05, self.stale_after / 3))
        self.heartbeat_interval = float(heartbeat_interval or default_hb)
        self.activity_workers = int(activity_workers or ACTIVITY_WORKERS)
        self.signal_poll_min = float(signal_poll_min or SIGNAL_POLL_MIN_SECONDS)
        self.signal_poll_max = float(signal_poll_max or SIGNAL_POLL_MAX_SECONDS)
        self.owner = f"{socket.gethostname()[:40]}:{os.getpid()}"

    # -- entry points -------------------------------------------------------------------------------
    def run(self, workflow_run_id: uuid.UUID | str, *, organization_id: uuid.UUID, force: bool = False) -> RunOutcome:
        """Execute or resume the run (blocking). Uses a dedicated thread when called from an event loop."""
        run_id = workflow_run_id if isinstance(workflow_run_id, uuid.UUID) else uuid.UUID(str(workflow_run_id))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute(organization_id, run_id, force=force))
        box: dict[str, Any] = {}

        def _main() -> None:
            try:
                box["outcome"] = asyncio.run(self.execute(organization_id, run_id, force=force))
            except BaseException as exc:  # propagate crashes (incl. simulated ones) to the caller
                box["error"] = exc

        thread = threading.Thread(target=_main, name=f"aegis-wf-{str(run_id)[:8]}", daemon=True)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        outcome: RunOutcome = box["outcome"]
        return outcome

    async def execute(
        self,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        *,
        force: bool = False,
        parent: LocalWorkflowContext | None = None,
    ) -> RunOutcome:
        claim = self._claim(organization_id, workflow_run_id, force=force)
        if isinstance(claim, RunOutcome):
            return claim
        return await self._drive(claim, parent=parent)

    # -- claim / heartbeat / finalize (short transactions) -------------------------------------------
    def _claim(self, organization_id: uuid.UUID, run_id: uuid.UUID, *, force: bool) -> _Claim | RunOutcome:
        token = f"local:{self.owner}:{uuid.uuid4().hex[:12]}"
        with tenant_uow(organization_id) as db:
            run = runs.lock_run(db, run_id)
            if run is None:
                return RunOutcome(str(run_id), "NOT_FOUND", executed=False)
            if runs.is_terminal(run.status):
                return RunOutcome(str(run_id), run.status, result=run.result, error=run.error, executed=False)
            if run.engine != runs.ENGINE_LOCAL:
                return RunOutcome(str(run_id), run.status, error="run belongs to another engine", executed=False)
            now = utcnow()
            if run.status == W.PENDING:
                if run.cancel_requested:
                    runs.transition(db, run, W.CANCELLED, error="Cancelled before start", reason="cancel_requested")
                    return RunOutcome(str(run_id), W.CANCELLED, error=run.error, executed=False)
                resumed = False
                runs.transition(db, run, W.RUNNING, reason="started")
            else:
                heartbeat = run.heartbeat_at
                fresh = heartbeat is not None and heartbeat > now - timedelta(seconds=self.stale_after)
                if fresh and not force:
                    return RunOutcome(str(run_id), run.status, executed=False)
                resumed = True
                runs.transition(db, run, W.RUNNING, reason="resumed")
            run.external_run_id = token
            run.heartbeat_at = now
            steps = {
                row.step_key: _memo_from_row(row)
                for row in db.scalars(select(WorkflowStep).where(WorkflowStep.workflow_run_id == run.id))
            }
            claim = _Claim(
                run_id=run.id,
                organization_id=run.organization_id,
                kind=run.kind,
                flow_input=runs.flow_input(run),
                actor_payload=runs.actor_payload(run),
                token=token,
                attempt=run.attempt,
                started_at=run.started_at or now,
                resumed=resumed,
                steps=steps,
            )
        log.info(
            "workflow_claimed",
            workflow_run_id=str(run_id),
            kind=claim.kind,
            resumed=resumed,
            recorded_steps=len(claim.steps),
        )
        return claim

    def _heartbeat(self, claim: _Claim) -> tuple[bool, bool]:
        """Refresh the lease; returns ``(still_owned, cancel_requested)``."""
        with tenant_uow(claim.organization_id) as db:
            row = db.execute(
                update(WorkflowRun)
                .where(
                    WorkflowRun.id == claim.run_id,
                    WorkflowRun.external_run_id == claim.token,
                    WorkflowRun.status.in_(tuple(runs.ACTIVE_STATUSES)),
                )
                .values(heartbeat_at=utcnow())
                .returning(WorkflowRun.cancel_requested)
                .execution_options(synchronize_session=False)
            ).first()
        if row is None:
            return False, False
        return True, bool(row[0])

    def _finalize(
        self,
        claim: _Claim,
        target: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> RunOutcome:
        with tenant_uow(claim.organization_id) as db:
            run = runs.lock_run(db, claim.run_id)
            if run is None or run.external_run_id != claim.token:
                return RunOutcome(str(claim.run_id), run.status if run else "NOT_FOUND", executed=True, lease_lost=True)
            if runs.is_terminal(run.status):
                return RunOutcome(str(claim.run_id), run.status, result=run.result, error=run.error)
            if run.status == W.WAITING and target == W.COMPLETED:
                runs.transition(db, run, W.RUNNING, reason="resumed")
            runs.transition(db, run, target, result=result, error=error, reason=reason)
            run.heartbeat_at = utcnow()
            outcome = RunOutcome(str(claim.run_id), run.status, result=run.result, error=run.error)
        log.info("workflow_finished", workflow_run_id=str(claim.run_id), kind=claim.kind, status=outcome.status)
        return outcome

    # -- driving a claimed run -------------------------------------------------------------------------
    async def _drive(self, claim: _Claim, *, parent: LocalWorkflowContext | None) -> RunOutcome:
        try:
            flow_fn = get_flow(claim.kind)
        except UnknownFlowKind as exc:
            return self._finalize(claim, W.FAILED, error=str(exc), reason="unknown_kind")
        options = flow_options(claim.kind)
        deadline = (
            claim.started_at + timedelta(seconds=options.execution_timeout_seconds)
            if options.execution_timeout_seconds
            else None
        )
        ctx = LocalWorkflowContext(self, claim)
        flow_task: asyncio.Task[Any] = asyncio.ensure_future(flow_fn(ctx, copy.deepcopy(claim.flow_input)))
        ctx.bind_task(flow_task)
        supervisor = asyncio.ensure_future(self._supervise(ctx, flow_task, deadline))
        current = asyncio.current_task()
        try:
            with span("workflow.run", kind=claim.kind, workflow_run_id=str(claim.run_id), engine="local"):
                try:
                    raw = await flow_task
                except _LeaseLost:
                    return RunOutcome(str(claim.run_id), W.RUNNING, lease_lost=True)
                except asyncio.CancelledError:
                    outer = current is not None and current.cancelling() > 0
                    if outer and not flow_task.done():
                        await asyncio.wait({flow_task})  # let the flow finish its cancellation handling
                    reason = ctx.cancel_reason or ("parent" if outer else "requested")
                    if reason == "lease_lost" or (parent is not None and parent.cancel_reason == "lease_lost"):
                        outcome = RunOutcome(str(claim.run_id), W.RUNNING, lease_lost=True)
                    elif reason == "timeout":
                        outcome = self._finalize(
                            claim, W.TIMED_OUT, error="Workflow execution timeout exceeded", reason="timeout"
                        )
                    else:
                        message = "Cancelled by parent workflow" if reason == "parent" else "Cancelled on request"
                        outcome = self._finalize(claim, W.CANCELLED, error=message, reason=reason)
                    if outer:
                        raise
                    return outcome
                except Exception as exc:
                    log.info(
                        "workflow_flow_failed",
                        workflow_run_id=str(claim.run_id),
                        kind=claim.kind,
                        error_type=type(exc).__name__,
                    )
                    return self._finalize(claim, W.FAILED, error=describe_exception(exc), reason="flow_error")
                try:
                    result = normalize_result(raw, what=f"flow {claim.kind}")
                except Exception as exc:
                    return self._finalize(claim, W.FAILED, error=describe_exception(exc), reason="invalid_result")
                return self._finalize(claim, W.COMPLETED, result=result, reason="completed")
        finally:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await supervisor
            ctx.close()

    async def _supervise(
        self, ctx: LocalWorkflowContext, flow_task: asyncio.Task[Any], deadline: datetime | None
    ) -> None:
        """Heartbeat the lease; turn cancel requests, lease loss and the execution deadline into cancellation."""
        while not flow_task.done():
            await asyncio.sleep(self.heartbeat_interval)
            if flow_task.done():
                return
            try:
                owned, cancel_requested = await asyncio.to_thread(self._heartbeat, ctx.claim)
            except Exception:
                log.warning("workflow_heartbeat_failed", workflow_run_id=str(ctx.claim.run_id), exc_info=True)
                continue
            if not owned:
                log.warning("workflow_lease_lost", workflow_run_id=str(ctx.claim.run_id))
                ctx.abort("lease_lost")
                return
            if cancel_requested:
                ctx.abort("requested")
            if deadline is not None and utcnow() >= deadline:
                ctx.abort("timeout")


class LocalWorkflowContext:
    """:class:`~aegis_api.lab.workflows.ctx.WorkflowContext` implementation for the local engine."""

    def __init__(self, engine: LocalWorkflowEngine, claim: _Claim) -> None:
        self.engine = engine
        self.claim = claim
        self.workflow_run_id = str(claim.run_id)
        self.kind = claim.kind
        self.input = copy.deepcopy(claim.flow_input)
        self.actor_payload = dict(claim.actor_payload)
        self.actor = actor_from_payload(self.actor_payload)
        self.cancel_reason: str | None = None
        self._keys = StepKeys()
        self._steps = claim.steps
        self._replay_remaining = sum(1 for m in claim.steps.values() if m.status != runs.STEP_RUNNING)
        self._cancel_flag = threading.Event()
        self._attempts: set[_Attempt] = set()
        self._task: asyncio.Task[Any] | None = None
        self._waits = 0
        self._last_db_heartbeat = 0.0
        self._executor = ThreadPoolExecutor(
            max_workers=engine.activity_workers, thread_name_prefix=f"aegis-wf-act-{self.workflow_run_id[:8]}"
        )

    # -- lifecycle helpers used by the engine -----------------------------------------------------------
    def bind_task(self, task: asyncio.Task[Any]) -> None:
        self._task = task

    def abort(self, reason: str) -> None:
        """Cancel the flow task (once) for ``reason``: requested | timeout | lease_lost."""
        if self.cancel_reason is not None and reason != "lease_lost":
            return
        self.cancel_reason = reason
        self._cancel_flag.set()
        for attempt in list(self._attempts):
            attempt.cancel.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def close(self) -> None:
        """Release the activity pool; still-running (abandoned) attempts observe ``is_cancelled()``."""
        for attempt in list(self._attempts):
            attempt.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- WorkflowContext API ---------------------------------------------------------------------------
    def now(self) -> datetime:
        return datetime.fromisoformat(self._memo_value("now", STEP_NOW, lambda: utcnow().isoformat()))

    def uuid(self) -> str:
        return str(self._memo_value("uuid", STEP_UUID, lambda: str(uuid.uuid4())))

    def cancelled(self) -> bool:
        return self._cancel_flag.is_set()

    def log(self, msg: str, **kw: Any) -> None:
        if self._replay_remaining > 0:
            return
        log.info(msg, workflow_run_id=self.workflow_run_id, kind=self.kind, **kw)

    async def gather(self, *aws: Awaitable[Any]) -> list[Any]:
        return list(await asyncio.gather(*aws))

    async def activity(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        step_key: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        heartbeat_seconds: float | None = None,
    ) -> dict[str, Any]:
        key = validate_step_key(step_key) if step_key is not None else self._keys.next(name)
        memo = self._lookup(key, name)
        if memo is not None and memo.status == runs.STEP_COMPLETED:
            return copy.deepcopy(memo.result) if isinstance(memo.result, dict) else {}
        if memo is not None and memo.status == runs.STEP_FAILED:
            raise ActivityFailed.from_record(name, memo.error)
        try:
            defn = get_activity(name)
        except KeyError as exc:
            raise WorkflowDefinitionError(str(exc)) from exc
        body = build_activity_payload(payload, actor_payload=self.actor_payload, workflow_run_id=self.workflow_run_id)
        limit = attempts_allowed(defn.retry, max_attempts)
        timeout = float(timeout_seconds or defn.timeout_seconds)
        hb_timeout = heartbeat_seconds if heartbeat_seconds is not None else defn.heartbeat_seconds
        attempt = (memo.attempt + 1) if memo is not None else 1
        while True:
            self._write_step(key, name, status=runs.STEP_RUNNING, attempt=attempt)
            started = time.monotonic()
            try:
                raw = await self._run_attempt(defn, body, attempt, timeout, float(hb_timeout) if hb_timeout else None)
                result = normalize_result(raw, what=f"activity {name}")
            except asyncio.CancelledError:
                WORKFLOW_ACTIVITY_DURATION.labels(name, "cancelled").observe(time.monotonic() - started)
                raise
            except Exception as exc:
                outcome = "timeout" if isinstance(exc, ActivityTimeout) else "error"
                WORKFLOW_ACTIVITY_DURATION.labels(name, outcome).observe(time.monotonic() - started)
                record = error_record(exc, defn.retry, activity=name, attempts=attempt)
                exhausted = limit != 0 and attempt >= limit
                if record["non_retryable"] or exhausted:
                    self._write_step(key, name, status=runs.STEP_FAILED, attempt=attempt, error=record)
                    raise ActivityFailed.from_record(name, record) from exc
                delay = backoff_seconds(defn.retry, attempt)
                self.log(
                    "activity_retry",
                    activity=name,
                    step_key=key,
                    attempt=attempt,
                    error_type=record["type"],
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            WORKFLOW_ACTIVITY_DURATION.labels(name, "completed").observe(time.monotonic() - started)
            self._write_step(key, name, status=runs.STEP_COMPLETED, attempt=attempt, result=result)
            stored = self._steps[key]
            if stored.status == runs.STEP_FAILED:
                raise ActivityFailed.from_record(name, stored.error)
            return copy.deepcopy(stored.result) if isinstance(stored.result, dict) else {}

    async def wait_signal(self, name: str, *, timeout_seconds: float | None) -> dict[str, Any] | None:
        validate_signal_name(name)
        if timeout_seconds is not None and timeout_seconds < 0:
            raise WorkflowDefinitionError("timeout_seconds must be >= 0")
        key = self._keys.next(f"signal:{name}")
        memo = self._lookup(key, STEP_SIGNAL)
        if memo is not None and memo.status == runs.STEP_COMPLETED:
            return self._signal_result(memo.result)
        started = (
            memo.started_at
            if memo is not None and memo.started_at
            else self._write_step(
                key, STEP_SIGNAL, status=runs.STEP_RUNNING, result={"timeout_seconds": timeout_seconds}
            )
        )
        deadline = started + timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None
        found, payload = self._consume_signal(name, key)
        if found:
            return payload
        self._enter_wait(name)
        try:
            value = await self._poll_signal(name, key, deadline)
        except (Exception, asyncio.CancelledError):
            # The flow abandoned the wait (e.g. a sibling in gather() failed). Engine-initiated cancellation
            # leaves the status to the finalizer.
            self._waits -= 1
            if self.cancel_reason is None and self._waits == 0:
                with contextlib.suppress(_LeaseLost):
                    self._set_status(W.RUNNING, reason="wait_abandoned")
            raise
        except BaseException:
            self._waits -= 1  # crash / lease loss: record nothing
            raise
        self._waits -= 1
        if self._waits == 0:
            self._set_status(W.RUNNING, reason="signal_received")
        return value

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise WorkflowDefinitionError("sleep seconds must be >= 0")
        key = self._keys.next("sleep")
        memo = self._lookup(key, STEP_SLEEP)
        if memo is not None and memo.status == runs.STEP_COMPLETED:
            return
        started = (
            memo.started_at
            if memo is not None and memo.started_at
            else self._write_step(key, STEP_SLEEP, status=runs.STEP_RUNNING, result={"seconds": seconds})
        )
        deadline = started + timedelta(seconds=seconds)
        while (remaining := (deadline - utcnow()).total_seconds()) > 0:
            await asyncio.sleep(min(remaining, SLEEP_SLICE_SECONDS))
        self._write_step(key, STEP_SLEEP, status=runs.STEP_COMPLETED, result={"seconds": seconds})

    async def child(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        if not has_flow(kind):
            raise WorkflowDefinitionError(f"Unknown workflow kind '{kind}'")
        validate_workflow_key(workflow_key)
        body = normalize_mapping(input, what="child workflow input")
        key = validate_step_key(f"child:{kind}:{workflow_key}", allow_reserved=True)
        memo = self._lookup(key, STEP_CHILD)
        if memo is not None and memo.status == runs.STEP_COMPLETED:
            data = memo.result if isinstance(memo.result, dict) else {}
            return copy.deepcopy(dict(data.get("result") or {}))
        if memo is not None and memo.status == runs.STEP_FAILED:
            raise ChildWorkflowFailed.from_record(memo.error)
        child_id = self._ensure_child(kind, body, workflow_key, subject_type, subject_id, inline=True, step_key=key)
        outcome = await self.engine.execute(self.claim.organization_id, child_id, force=True, parent=self)
        if outcome.lease_lost:
            raise _LeaseLost()
        if outcome.status == W.COMPLETED:
            result = dict(outcome.result or {})
            self._write_step(
                key,
                STEP_CHILD,
                status=runs.STEP_COMPLETED,
                result={"workflow_run_id": str(child_id), "kind": kind, "result": result},
            )
            return copy.deepcopy(result)
        if outcome.status in runs.TERMINAL_STATUSES:
            failure = ChildWorkflowFailed(kind, str(child_id), outcome.status, outcome.error or "")
            self._write_step(key, STEP_CHILD, status=runs.STEP_FAILED, error=failure.to_record())
            raise failure
        raise WorkflowDefinitionError(f"child {kind} ({child_id}) could not be executed (status {outcome.status})")

    async def start_detached(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> str:
        if not has_flow(kind):
            raise WorkflowDefinitionError(f"Unknown workflow kind '{kind}'")
        validate_workflow_key(workflow_key)
        body = normalize_mapping(input, what="detached workflow input")
        key = validate_step_key(f"detached:{kind}:{workflow_key}", allow_reserved=True)
        memo = self._lookup(key, STEP_DETACHED)
        if memo is not None and memo.status == runs.STEP_COMPLETED and isinstance(memo.result, dict):
            return str(memo.result.get("workflow_run_id"))
        child_id = self._ensure_child(kind, body, workflow_key, subject_type, subject_id, inline=False, step_key=key)
        return str(child_id)

    # -- internals: memo / steps -------------------------------------------------------------------------
    def _lookup(self, key: str, activity: str) -> _Memo | None:
        memo = self._steps.get(key)
        if memo is None:
            return None
        if memo.activity != activity:
            raise WorkflowDeterminismError(
                f"step {key!r} was recorded for {memo.activity!r} but the flow now issued {activity!r}"
            )
        if memo.status != runs.STEP_RUNNING and self._replay_remaining > 0:
            self._replay_remaining -= 1
        return memo

    def _memo_value(self, prefix: str, activity: str, produce: Callable[[], str]) -> str:
        key = self._keys.next(prefix)
        memo = self._lookup(key, activity)
        if memo is not None and memo.status == runs.STEP_COMPLETED and isinstance(memo.result, dict):
            return str(memo.result["value"])
        self._write_step(key, activity, status=runs.STEP_COMPLETED, result={"value": produce()})
        stored = self._steps[key].result
        return str(stored["value"]) if isinstance(stored, dict) else ""

    def _assert_owner(self, db: Session) -> None:
        token = db.scalar(
            select(WorkflowRun.external_run_id).where(WorkflowRun.id == self.claim.run_id).with_for_update(read=True)
        )
        if token != self.claim.token:
            raise _LeaseLost()

    def _upsert_step(
        self,
        db: Session,
        key: str,
        activity: str,
        *,
        status: str,
        attempt: int = 1,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> datetime:
        now = utcnow()
        previous = self._steps.get(key)
        started_at = previous.started_at if previous is not None and previous.started_at else now
        if status == runs.STEP_RUNNING and previous is not None and previous.status == runs.STEP_RUNNING:
            started_at = now if activity not in (STEP_SIGNAL, STEP_SLEEP) else started_at
        error_text = json.dumps(error)[:20000] if error is not None else None
        completed_at = None if status == runs.STEP_RUNNING else now
        written = db.execute(
            insert(WorkflowStep)
            .values(
                id=uuid.uuid4(),
                organization_id=self.claim.organization_id,
                workflow_run_id=self.claim.run_id,
                step_key=key,
                activity=activity,
                status=status,
                attempt=attempt,
                result=result,
                error=error_text,
                started_at=started_at,
                completed_at=completed_at,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_workflow_steps_run_key",
                set_={
                    "status": status,
                    "attempt": attempt,
                    "result": result,
                    "error": error_text,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "updated_at": now,
                },
                where=WorkflowStep.status != runs.STEP_COMPLETED,
            )
            .returning(WorkflowStep.id)
        ).first()
        if written is None:
            # The step was already completed (by a previous executor): the recorded result wins.
            row = db.execute(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_run_id == self.claim.run_id, WorkflowStep.step_key == key
                )
            ).scalar_one()
            memo = _memo_from_row(row)
            self._steps[key] = memo
            return memo.started_at or started_at
        self._steps[key] = _Memo(key, activity, status, result, error, attempt, started_at)
        return started_at

    def _write_step(
        self,
        key: str,
        activity: str,
        *,
        status: str,
        attempt: int = 1,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> datetime:
        with tenant_uow(self.claim.organization_id) as db:
            self._assert_owner(db)
            return self._upsert_step(db, key, activity, status=status, attempt=attempt, result=result, error=error)

    def _ensure_child(
        self,
        kind: str,
        body: dict[str, Any],
        workflow_key: str,
        subject_type: str | None,
        subject_id: str | None,
        *,
        inline: bool,
        step_key: str,
    ) -> UUID:
        with tenant_uow(self.claim.organization_id) as db:
            self._assert_owner(db)
            parent = db.get(WorkflowRun, self.claim.run_id)
            if parent is None:
                raise _LeaseLost()
            child, _created = runs.ensure_child_run(
                db,
                parent,
                kind=kind,
                flow_input=body,
                workflow_key=workflow_key,
                subject_type=subject_type,
                subject_id=subject_id,
                inline=inline,
            )
            info = {"workflow_run_id": str(child.id), "kind": kind}
            if inline:
                self._upsert_step(db, step_key, STEP_CHILD, status=runs.STEP_RUNNING, result=info)
            else:
                self._upsert_step(db, step_key, STEP_DETACHED, status=runs.STEP_COMPLETED, result=info)
                if child.status == W.PENDING:
                    org_id, child_id = self.claim.organization_id, child.id
                    after_commit(db, lambda: dispatch_local(org_id, child_id))
            return child.id

    # -- internals: activities ---------------------------------------------------------------------------
    async def _run_attempt(
        self, defn: ActivityDef, body: dict[str, Any], attempt: int, timeout: float, heartbeat_timeout: float | None
    ) -> Any:
        state = _Attempt()
        self._attempts.add(state)
        loop = asyncio.get_running_loop()

        def heartbeat(details: Any = None) -> None:
            state.last_heartbeat = time.monotonic()
            now = time.monotonic()
            if now - self._last_db_heartbeat < ACTIVITY_HEARTBEAT_THROTTLE_SECONDS:
                return
            self._last_db_heartbeat = now
            try:
                owned, cancel_requested = self.engine._heartbeat(self.claim)
            except Exception:
                log.warning("activity_heartbeat_failed", workflow_run_id=self.workflow_run_id, exc_info=True)
                return
            if not owned or cancel_requested:
                state.cancel.set()

        def is_cancelled() -> bool:
            return state.cancel.is_set() or self._cancel_flag.is_set()

        actx = ActivityContext(
            actor=self.actor,
            workflow_run_id=self.claim.run_id,
            attempt=attempt,
            heartbeat=heartbeat,
            is_cancelled=is_cancelled,
        )
        run_id = self.workflow_run_id

        def invoke() -> Any:
            with span("workflow.activity", activity=defn.name, attempt=attempt, workflow_run_id=run_id):
                return defn.fn(actx, copy.deepcopy(body))

        future = loop.run_in_executor(self._executor, invoke)
        started = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({future}, timeout=ACTIVITY_POLL_SECONDS)
                if future in done:
                    return future.result()
                now = time.monotonic()
                if now - started >= timeout:
                    state.cancel.set()
                    raise ActivityTimeout(f"{defn.name} exceeded its start-to-close timeout of {timeout:g}s")
                if heartbeat_timeout and now - state.last_heartbeat > heartbeat_timeout:
                    state.cancel.set()
                    raise ActivityTimeout(f"{defn.name} missed its heartbeat timeout of {heartbeat_timeout:g}s")
        except asyncio.CancelledError:
            state.cancel.set()
            raise
        finally:
            self._attempts.discard(state)

    # -- internals: signals & status -------------------------------------------------------------------
    @staticmethod
    def _signal_result(result: Any) -> dict[str, Any] | None:
        data = result if isinstance(result, dict) else {}
        if data.get("timed_out"):
            return None
        payload = data.get("payload")
        return copy.deepcopy(payload) if isinstance(payload, dict) else {}

    def _consume_signal(self, name: str, key: str) -> tuple[bool, dict[str, Any] | None]:
        """Atomically pop the oldest ``name`` signal from the inbox and record its consumption."""
        with tenant_uow(self.claim.organization_id) as db:
            run = runs.lock_run(db, self.claim.run_id)
            if run is None or run.external_run_id != self.claim.token:
                raise _LeaseLost()
            cancel_requested = run.cancel_requested
            signals = list(run.signals or [])
            for index, entry in enumerate(signals):
                if isinstance(entry, dict) and entry.get("name") == name:
                    signals.pop(index)
                    run.signals = signals
                    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                    self._upsert_step(
                        db,
                        key,
                        STEP_SIGNAL,
                        status=runs.STEP_COMPLETED,
                        result={"payload": payload, "signal_id": entry.get("id"), "timed_out": False},
                    )
                    return True, copy.deepcopy(payload)
        if cancel_requested:
            self.abort("requested")
        return False, None

    async def _poll_signal(self, name: str, key: str, deadline: datetime | None) -> dict[str, Any] | None:
        backoff = self.engine.signal_poll_min
        async with contextlib.AsyncExitStack() as stack:
            listener = await self._open_listener(stack)
            while True:
                if deadline is not None and utcnow() >= deadline:
                    found, payload = self._consume_signal(name, key)
                    if found:
                        return payload
                    self._write_step(
                        key, STEP_SIGNAL, status=runs.STEP_COMPLETED, result={"payload": None, "timed_out": True}
                    )
                    return None
                wait = backoff
                if deadline is not None:
                    wait = max(0.01, min(wait, (deadline - utcnow()).total_seconds()))
                woke = False
                if listener is not None:
                    try:
                        woke = await listener.wait(wait)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        listener = None
                if listener is None and not woke:
                    await asyncio.sleep(wait)
                found, payload = self._consume_signal(name, key)
                if found:
                    return payload
                backoff = min(backoff * 2, self.engine.signal_poll_max)

    async def _open_listener(self, stack: contextlib.AsyncExitStack) -> Any:
        try:
            from aegis_api.lab.events.bus import get_event_bus

            bus = get_event_bus()
            return await stack.enter_async_context(
                bus.listen(runs.workflow_channel(self.claim.organization_id, self.claim.run_id))
            )
        except Exception:
            log.debug("workflow_signal_listener_unavailable", workflow_run_id=self.workflow_run_id, exc_info=True)
            return None

    def _enter_wait(self, name: str) -> None:
        self._waits += 1
        if self._waits == 1:
            self._set_status(W.WAITING, reason=f"waiting_for_signal:{name}")

    def _set_status(self, target: str, *, reason: str) -> None:
        with tenant_uow(self.claim.organization_id) as db:
            run = runs.lock_run(db, self.claim.run_id)
            if run is None or run.external_run_id != self.claim.token:
                raise _LeaseLost()
            if run.status == target or runs.is_terminal(run.status):
                return
            runs.transition(db, run, target, reason=reason)


# ---------------------------------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------------------------------
_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND: dict[uuid.UUID, threading.Thread] = {}


def _background_main(organization_id: uuid.UUID, run_id: uuid.UUID) -> None:
    try:
        LocalWorkflowEngine().run(run_id, organization_id=organization_id)
    except BaseException:
        log.exception("local_workflow_crashed", workflow_run_id=str(run_id))
    finally:
        with _BACKGROUND_LOCK:
            if _BACKGROUND.get(run_id) is threading.current_thread():
                del _BACKGROUND[run_id]


def start_background(organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> bool:
    """Run the workflow on a dedicated daemon thread of this process (skipped if already running here)."""
    with _BACKGROUND_LOCK:
        thread = _BACKGROUND.get(workflow_run_id)
        if thread is not None and thread.is_alive():
            return False
        thread = threading.Thread(
            target=_background_main,
            args=(organization_id, workflow_run_id),
            name=f"aegis-wf-{str(workflow_run_id)[:8]}",
            daemon=True,
        )
        _BACKGROUND[workflow_run_id] = thread
        thread.start()
    return True


def background_runs() -> list[uuid.UUID]:
    with _BACKGROUND_LOCK:
        return [rid for rid, thread in _BACKGROUND.items() if thread.is_alive()]


def wait_for_background(timeout: float = 30.0) -> bool:
    """Join this process's workflow threads (graceful shutdown, tests). True when all finished."""
    deadline = time.monotonic() + timeout
    while True:
        with _BACKGROUND_LOCK:
            threads = [t for t in _BACKGROUND.values() if t.is_alive()]
        if not threads:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        threads[0].join(min(remaining, 0.5))


def run_local_workflow_job(session: Session, workflow_run_id: str) -> None:
    """Job entrypoint (inline dispatcher or Celery: ``aegis_api.lab.workflows.local_engine:run_local_workflow_job``).

    The dispatcher's transaction is released immediately — a workflow must never hold a DB transaction
    open. With the inline job backend the run moves to a dedicated thread so long workflows don't occupy
    the shared job pool; a Celery worker runs it to completion in the task (resumable if interrupted).
    """
    organization_id = session.info.get("org_id")
    session.commit()
    if not isinstance(organization_id, uuid.UUID):
        raise ValueError("run_local_workflow_job requires a tenant-scoped session")
    run_id = uuid.UUID(str(workflow_run_id))
    if get_settings().effective_job_backend == "celery":
        LocalWorkflowEngine().run(run_id, organization_id=organization_id)
    else:
        start_background(organization_id, run_id)


def dispatch_local(organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> None:
    """Hand the run to the job dispatcher (thread pool or Celery)."""
    from aegis_api.jobs import dispatcher

    dispatcher.dispatch(run_local_workflow_job, organization_id, str(workflow_run_id))
