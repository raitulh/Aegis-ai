"""Temporal workflow definitions: one thin ``@workflow.defn(name=kind)`` class per registered flow kind.

Every kind in ``definitions.FLOW_KINDS`` gets a generated class named after the kind (so the sandbox can
re-import it as ``from aegis_api.lab.workflows.temporal_workflows import <Kind>``) with:

* ``@workflow.run run(envelope)`` — records RUNNING/COMPLETED/FAILED/CANCELLED in ``workflow_runs`` through the
  ``workflows.mark_*`` activities and runs the registered flow with a :class:`TemporalWorkflowContext`;
* ``@workflow.signal signal(name, payload, signal_id)`` — queues a signal (de-duplicated by id);
* ``@workflow.query status()`` — what the workflow is waiting for.

This module runs inside Temporal's workflow sandbox and must stay deterministic: no IO, no wall clock, no
randomness. Everything it imports from the application is pure and passed through the sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import Awaitable, Mapping
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    WorkflowAlreadyStartedError,
    is_cancelled_exception,
)
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

with workflow.unsafe.imports_passed_through():
    from aegis_api.lab.workflows import definitions
    from aegis_api.lab.workflows.ctx import (
        ActivityFailed,
        ChildWorkflowFailed,
        StepKeys,
        WorkflowDefinitionError,
        attempts_allowed,
        build_activity_payload,
        describe_exception,
        normalize_mapping,
        normalize_result,
        validate_signal_name,
        validate_step_key,
        validate_workflow_key,
    )
    from aegis_api.lab.workflows.registry import ACTIVITIES, ActivityDef, RetrySpec

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})
ACTIVE = frozenset({"RUNNING", "WAITING"})

# Engine bookkeeping activities (``workflows.*``) default options when the registry lacks them.
_INTERNAL_TIMEOUT = timedelta(seconds=60)
_INTERNAL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=20,
)
_AWAIT_RUN_MIN_SECONDS = 5.0
_AWAIT_RUN_MAX_SECONDS = 60.0


def retry_policy(spec: RetrySpec, max_attempts: int | None = None) -> RetryPolicy:
    """Temporal retry policy equivalent to the registry's :class:`RetrySpec`."""
    return RetryPolicy(
        initial_interval=timedelta(seconds=max(0.001, spec.initial_interval_seconds)),
        backoff_coefficient=max(1.0, spec.backoff_coefficient),
        maximum_interval=timedelta(seconds=max(spec.initial_interval_seconds, spec.max_interval_seconds)),
        maximum_attempts=attempts_allowed(spec, max_attempts),
        non_retryable_error_types=list(spec.non_retryable),
    )


def activity_failed_from_error(name: str, exc: ActivityError) -> ActivityFailed:
    """Map Temporal's ``ActivityError`` onto the engine-agnostic :class:`ActivityFailed`."""
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        record = cause.details[0] if cause.details and isinstance(cause.details[0], dict) else None
        if record is not None:
            return ActivityFailed.from_record(name, record)
        return ActivityFailed(name, cause.type or "ApplicationError", cause.message, non_retryable=cause.non_retryable)
    if isinstance(cause, TemporalTimeoutError):
        return ActivityFailed(name, "ActivityTimeout", cause.message or "activity timed out", non_retryable=False)
    if cause is not None:
        return ActivityFailed(name, type(cause).__name__, str(cause), non_retryable=False)
    return ActivityFailed(name, "ActivityError", str(exc), non_retryable=False)


def _child_failure(kind: str, run_id: str | None, exc: ChildWorkflowError) -> ChildWorkflowFailed:
    cause = exc.cause
    if is_cancelled_exception(exc):
        status = "CANCELLED"
    elif isinstance(cause, TemporalTimeoutError):
        status = "TIMED_OUT"
    else:
        status = "FAILED"
    message = getattr(cause, "message", None) or str(cause or exc)
    return ChildWorkflowFailed(kind, run_id, status, str(message))


class TemporalWorkflowContext:
    """:class:`~aegis_api.lab.workflows.ctx.WorkflowContext` on top of the Temporal workflow APIs."""

    def __init__(self, owner: Any, kind: str, envelope: Mapping[str, Any]) -> None:
        self._owner = owner
        self.kind = kind
        self.workflow_run_id = str(envelope["workflow_run_id"])
        self.organization_id = str(envelope.get("organization_id") or "")
        self.input: dict[str, Any] = copy.deepcopy(dict(envelope.get("input") or {}))
        self.actor_payload: dict[str, Any] = dict(envelope.get("actor") or {})
        self._queues: dict[str, Any] = dict(envelope.get("task_queues") or {})
        self._keys = StepKeys()
        self._results: dict[str, Any] = {}
        self._failures: dict[str, dict[str, Any]] = {}
        self._cancelled = False
        self._waits = 0
        self.waiting_for: list[str] = []

    # -- determinism helpers -----------------------------------------------------------------------------
    def now(self) -> datetime:
        return workflow.now()

    def uuid(self) -> str:
        return str(workflow.uuid4())

    def cancelled(self) -> bool:
        return self._cancelled

    def log(self, msg: str, **kw: Any) -> None:
        workflow.logger.info("%s %s", msg, kw if kw else "")

    async def gather(self, *aws: Awaitable[Any]) -> list[Any]:
        return list(await asyncio.gather(*aws))

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise WorkflowDefinitionError("sleep seconds must be >= 0")
        await asyncio.sleep(seconds)

    # -- activities ------------------------------------------------------------------------------------------
    def _task_queue_for(self, defn: ActivityDef) -> str:
        if defn.task_queue == "execution":
            return str(self._queues.get("execution") or workflow.info().task_queue)
        return workflow.info().task_queue

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
        if key in self._results:
            return copy.deepcopy(self._results[key])
        if key in self._failures:
            raise ActivityFailed.from_record(name, self._failures[key])
        defn = ACTIVITIES.get(name)
        if defn is None:
            raise WorkflowDefinitionError(f"Unknown activity '{name}'")
        body = build_activity_payload(payload, actor_payload=self.actor_payload, workflow_run_id=self.workflow_run_id)
        heartbeat = heartbeat_seconds if heartbeat_seconds is not None else defn.heartbeat_seconds
        try:
            result = await workflow.execute_activity(
                name,
                body,
                task_queue=self._task_queue_for(defn),
                start_to_close_timeout=timedelta(seconds=float(timeout_seconds or defn.timeout_seconds)),
                heartbeat_timeout=timedelta(seconds=float(heartbeat)) if heartbeat else None,
                retry_policy=retry_policy(defn.retry, max_attempts),
                summary=key,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                self._cancelled = True
                raise asyncio.CancelledError() from exc
            failure = activity_failed_from_error(name, exc)
            self._failures[key] = failure.to_record()
            raise failure from exc
        value = dict(result) if isinstance(result, dict) else {}
        self._results[key] = value
        return copy.deepcopy(value)

    async def internal(self, name: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Engine bookkeeping activity (``workflows.*``) on this workflow's task queue."""
        defn = ACTIVITIES.get(name)
        body = build_activity_payload(payload, actor_payload=self.actor_payload, workflow_run_id=self.workflow_run_id)
        result = await workflow.execute_activity(
            name,
            body,
            task_queue=workflow.info().task_queue,
            start_to_close_timeout=timedelta(seconds=defn.timeout_seconds) if defn else _INTERNAL_TIMEOUT,
            retry_policy=retry_policy(defn.retry) if defn else _INTERNAL_RETRY,
        )
        return dict(result) if isinstance(result, dict) else {}

    # -- signals -----------------------------------------------------------------------------------------------
    async def wait_signal(self, name: str, *, timeout_seconds: float | None) -> dict[str, Any] | None:
        validate_signal_name(name)
        if timeout_seconds is not None and timeout_seconds < 0:
            raise WorkflowDefinitionError("timeout_seconds must be >= 0")
        self._keys.next(f"signal:{name}")
        queue: dict[str, list[dict[str, Any]]] = self._owner._signals
        if not queue.get(name):
            self._waits += 1
            self.waiting_for.append(name)
            if self._waits == 1:
                await self.internal("workflows.mark_waiting", {"signal": name})
            received = False
            try:
                await workflow.wait_condition(lambda: bool(queue.get(name)), timeout=timeout_seconds)
                received = True
            except TimeoutError:
                received = False
            finally:
                self._waits -= 1
                self.waiting_for.remove(name)
            if self._waits == 0:
                await self.internal(
                    "workflows.mark_running",
                    {
                        "external_run_id": workflow.info().run_id,
                        "reason": "signal_received" if received else "signal_timeout",
                    },
                )
            if not received:
                return None
        entry = queue[name].pop(0)
        return copy.deepcopy(entry)

    # -- children ------------------------------------------------------------------------------------------
    async def _register_child(
        self,
        kind: str,
        body: dict[str, Any],
        workflow_key: str,
        subject_type: str | None,
        subject_id: str | None,
        *,
        detached: bool,
    ) -> dict[str, Any]:
        return await self.internal(
            "workflows.register_child",
            {
                "kind": kind,
                "workflow_key": workflow_key,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "input": body,
                "detached": detached,
            },
        )

    async def _await_existing_run(self, kind: str, run_id: str) -> dict[str, Any]:
        """Wait (durable timers + status polls) for a child that is already executing elsewhere."""
        delay = _AWAIT_RUN_MIN_SECONDS
        while True:
            status = await self.internal("workflows.get_run_status", {"target_workflow_run_id": run_id})
            state = str(status.get("status"))
            if state == "COMPLETED":
                return dict(status.get("result") or {})
            if state in TERMINAL:
                raise ChildWorkflowFailed(kind, run_id, state, str(status.get("error") or ""))
            await asyncio.sleep(delay)
            delay = min(delay * 2, _AWAIT_RUN_MAX_SECONDS)

    async def child(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in definitions.FLOW_KINDS:
            raise WorkflowDefinitionError(f"Unknown workflow kind '{kind}'")
        validate_workflow_key(workflow_key)
        body = normalize_mapping(input, what="child workflow input")
        key = f"child:{kind}:{workflow_key}"
        if key in self._results:
            return copy.deepcopy(self._results[key])
        if key in self._failures:
            raise ChildWorkflowFailed.from_record(self._failures[key])
        reg = await self._register_child(kind, body, workflow_key, subject_type, subject_id, detached=False)
        run_id = str(reg["workflow_run_id"])
        state = str(reg.get("status"))
        try:
            if state == "COMPLETED":
                result = dict(reg.get("result") or {})
            elif state in TERMINAL:
                raise ChildWorkflowFailed(kind, run_id, state, str(reg.get("error") or ""))
            elif state in ACTIVE:
                result = await self._await_existing_run(kind, run_id)
            else:
                try:
                    raw = await workflow.execute_child_workflow(
                        kind,
                        reg["envelope"],
                        id=str(reg["temporal_workflow_id"]),
                        task_queue=workflow.info().task_queue,
                        parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                        execution_timeout=_timeout_of(kind),
                    )
                    result = dict(raw) if isinstance(raw, dict) else {}
                except WorkflowAlreadyStartedError:
                    result = await self._await_existing_run(kind, run_id)
                except ChildWorkflowError as exc:
                    raise _child_failure(kind, run_id, exc) from exc
        except ChildWorkflowFailed as failure:
            self._failures[key] = failure.to_record()
            raise
        self._results[key] = result
        return copy.deepcopy(result)

    async def start_detached(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> str:
        if kind not in definitions.FLOW_KINDS:
            raise WorkflowDefinitionError(f"Unknown workflow kind '{kind}'")
        validate_workflow_key(workflow_key)
        body = normalize_mapping(input, what="detached workflow input")
        key = f"detached:{kind}:{workflow_key}"
        if key in self._results:
            return str(self._results[key])
        reg = await self._register_child(kind, body, workflow_key, subject_type, subject_id, detached=True)
        run_id = str(reg["workflow_run_id"])
        if reg.get("status") == "PENDING":
            with contextlib.suppress(WorkflowAlreadyStartedError):
                await workflow.start_child_workflow(
                    kind,
                    reg["envelope"],
                    id=str(reg["temporal_workflow_id"]),
                    task_queue=workflow.info().task_queue,
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                    execution_timeout=_timeout_of(kind),
                )
        self._results[key] = run_id
        return run_id


def _timeout_of(kind: str) -> timedelta | None:
    seconds = definitions.flow_options(kind).execution_timeout_seconds
    return timedelta(seconds=seconds) if seconds else None


# ---------------------------------------------------------------------------------------------------
# Shared workflow method bodies
# ---------------------------------------------------------------------------------------------------
def _init_state(instance: Any) -> None:
    instance._signals = {}
    instance._seen_signal_ids = set()
    instance._ctx = None


def _receive_signal(instance: Any, name: str, payload: dict[str, Any] | None, signal_id: str | None) -> None:
    if not isinstance(name, str) or not name:
        return
    if signal_id:
        if signal_id in instance._seen_signal_ids:
            return
        instance._seen_signal_ids.add(signal_id)
    instance._signals.setdefault(name, []).append(dict(payload) if isinstance(payload, dict) else {})


def _status(instance: Any) -> dict[str, Any]:
    ctx: TemporalWorkflowContext | None = instance._ctx
    return {
        "kind": ctx.kind if ctx else None,
        "workflow_run_id": ctx.workflow_run_id if ctx else None,
        "waiting_for": list(ctx.waiting_for) if ctx else [],
        "pending_signals": {name: len(items) for name, items in instance._signals.items() if items},
        "cancelled": ctx.cancelled() if ctx else False,
    }


async def _run_flow(instance: Any, kind: str, envelope: dict[str, Any]) -> dict[str, Any]:
    ctx = TemporalWorkflowContext(instance, kind, envelope)
    instance._ctx = ctx
    state = await ctx.internal(
        "workflows.mark_running", {"external_run_id": workflow.info().run_id, "reason": "started"}
    )
    if state.get("terminal"):
        # The run was cancelled (or otherwise ended) before this execution started: nothing to do.
        return {"skipped": True, "status": state.get("status")}
    fn = definitions.FLOW_KINDS.get(kind)
    if fn is None:
        message = f"No flow is registered for workflow kind '{kind}'"
        await ctx.internal("workflows.mark_failed", {"error": message})
        raise ApplicationError(message, type="UnknownFlowKind", non_retryable=True)
    try:
        raw = await fn(ctx, copy.deepcopy(ctx.input))
        result = normalize_result(raw, what=f"flow {kind}")
    except asyncio.CancelledError:
        ctx._cancelled = True
        await ctx.internal("workflows.mark_cancelled", {"reason": "requested"})
        raise
    except Exception as exc:
        if is_cancelled_exception(exc):
            ctx._cancelled = True
            await ctx.internal("workflows.mark_cancelled", {"reason": "requested"})
            raise
        message = describe_exception(exc)
        await ctx.internal("workflows.mark_failed", {"error": message})
        raise ApplicationError(message, type=type(exc).__name__, non_retryable=True) from exc
    await ctx.internal("workflows.mark_completed", {"result": result})
    return result


# ---------------------------------------------------------------------------------------------------
# Class generation
# ---------------------------------------------------------------------------------------------------
_CLASSES: dict[str, type] = {}


def _build_workflow_class(kind: str) -> type:
    async def run(self: Any, envelope: dict[str, Any]) -> dict[str, Any]:
        return await _run_flow(self, kind, envelope)

    def signal(self: Any, name: str, payload: dict[str, Any] | None = None, signal_id: str | None = None) -> None:
        _receive_signal(self, name, payload, signal_id)

    def status(self: Any) -> dict[str, Any]:
        return _status(self)

    def init(self: Any) -> None:
        _init_state(self)

    for fn, attr in ((run, "run"), (signal, "signal"), (status, "status"), (init, "__init__")):
        fn.__name__ = attr
        fn.__qualname__ = f"{kind}.{attr}"
    namespace: dict[str, Any] = {
        "__module__": __name__,
        "__qualname__": kind,
        "__doc__": f"Temporal workflow for {kind} (generated).",
        "__init__": init,
        "run": workflow.run(run),
        "signal": workflow.signal(name="signal")(signal),
        "status": workflow.query(name="status")(status),
    }
    cls = type(kind, (), namespace)
    return workflow.defn(name=kind)(cls)


def workflow_class(kind: str) -> type:
    """The Temporal workflow class for ``kind`` (generated once per process / sandbox)."""
    cls = _CLASSES.get(kind)
    if cls is None:
        definitions.validate_kind(kind)
        if kind in globals():
            raise ValueError(f"Workflow kind '{kind}' collides with a module attribute")
        cls = _build_workflow_class(kind)
        _CLASSES[kind] = cls
        globals()[kind] = cls
    return cls


def workflow_classes(kinds: list[str] | None = None) -> list[type]:
    """Workflow classes for ``kinds`` (default: every registered flow kind)."""
    return [workflow_class(kind) for kind in sorted(kinds if kinds is not None else definitions.FLOW_KINDS)]


def __getattr__(name: str) -> Any:
    # Lets the sandbox import generated classes by name: ``from <this module> import <Kind>``.
    if name in definitions.FLOW_KINDS:
        return workflow_class(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
