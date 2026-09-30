"""Temporal integration: workflow classes, the generic activity, the worker and client operations.

Each lab workflow definition becomes a Temporal workflow type of the same name whose ``run`` executes the
engine-neutral definition against ``TemporalContext``. All I/O happens in the single generic activity
``lab_activity`` (dispatching to the activity registry), so workflow code stays deterministic. Signals use one
generic handler (``lab_signal(name, payload)``); the Temporal workflow id is ``lab-<workflow_run_id>``.

The lab database (``lab.workflow_runs``) remains the system of record for status/results: the workflow marks
itself running at start and finalizes the row through activities.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import structlog
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy as TemporalRetryPolicy
from temporalio.exceptions import ActivityError as TemporalActivityError
from temporalio.exceptions import ApplicationError, ChildWorkflowError

from aegis_api.config import get_settings
from aegis_api.workflows.api import DEFAULT_RETRY, ActivityFailure, RetryPolicy, WorkflowDefinition
from aegis_api.workflows.runtime import CTX_KEY, ActivityError, run_activity

log = structlog.get_logger("aegis.workflows.temporal")

ACTIVITY_NAME = "lab_activity"
SIGNAL_NAME = "lab_signal"


def workflow_id(run_id: uuid.UUID | str) -> str:
    return f"lab-{run_id}"


# --- activity ----------------------------------------------------------------------------------------------


@activity.defn(name=ACTIVITY_NAME)
def lab_activity(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    info = activity.info()
    try:
        return run_activity(name, payload, attempt=info.attempt, heartbeat=lambda d: activity.heartbeat(d))
    except ActivityError as exc:
        raise ApplicationError(exc.message, exc.details, type=exc.code, non_retryable=not exc.retryable) from exc


# --- workflow context --------------------------------------------------------------------------------------


class TemporalContext:
    def __init__(self, wf: Any, envelope: dict[str, Any], workflow_name: str) -> None:
        self._wf = wf
        self.run_id = str(envelope["run_id"])
        self.workflow = workflow_name
        self.input = dict(envelope.get("input") or {})
        self._org = str(envelope["organization_id"])
        self._principal = dict(envelope["principal"])

    def _body(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            CTX_KEY: {
                "organization_id": self._org,
                "principal": self._principal,
                "run_id": self.run_id,
                "workflow": self.workflow,
            },
        }

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
        try:
            result = await workflow.execute_activity(
                ACTIVITY_NAME,
                args=[name, self._body(payload)],
                start_to_close_timeout=timedelta(seconds=timeout_seconds),
                heartbeat_timeout=timedelta(seconds=heartbeat_seconds) if heartbeat_seconds else None,
                retry_policy=TemporalRetryPolicy(
                    initial_interval=timedelta(seconds=retry.initial_interval_seconds),
                    backoff_coefficient=retry.backoff,
                    maximum_interval=timedelta(seconds=retry.max_interval_seconds),
                    maximum_attempts=retry.max_attempts,
                    non_retryable_error_types=list(retry.non_retryable),
                ),
                summary=f"{name}:{key}" if key else name,
            )
        except TemporalActivityError as exc:
            cause = exc.cause
            if isinstance(cause, ApplicationError):
                details = cause.details[0] if cause.details else None
                raise ActivityFailure(
                    name, cause.message, code=cause.type or "activity_failed", details=details
                ) from None
            raise ActivityFailure(name, str(cause or exc), code="activity_failed") from None
        return dict(result or {})

    async def wait_signal(
        self, name: str, *, key: str | None = None, timeout_seconds: float | None = None
    ) -> dict[str, Any] | None:
        queue = self._wf.signals[name]
        try:
            await workflow.wait_condition(
                lambda: bool(queue), timeout=timedelta(seconds=timeout_seconds) if timeout_seconds else None
            )
        except TimeoutError:
            return None
        return dict(queue.pop(0))

    async def sleep(self, seconds: float, *, key: str | None = None) -> None:
        await workflow.sleep(timedelta(seconds=seconds))

    async def child(self, workflow_name: str, payload: dict[str, Any], *, key: str) -> dict[str, Any]:
        created = await self.activity(
            "workflow.create_child",
            {"workflow": workflow_name, "input": payload, "key": key, "parent_run_id": self.run_id},
            key=f"child-create:{key}",
        )
        child_run_id = created["run_id"]
        try:
            result = await workflow.execute_child_workflow(
                workflow_name,
                {"run_id": child_run_id, "organization_id": self._org, "principal": self._principal, "input": payload},
                id=workflow_id(child_run_id),
            )
        except ChildWorkflowError as exc:
            raise ActivityFailure(f"child:{workflow_name}", str(exc.cause or exc), code="child_failed") from None
        return dict(result or {})

    def now(self) -> datetime:
        return workflow.now()

    async def gather(self, *aws: Awaitable[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(await asyncio.gather(*aws))


class LabWorkflowBase:
    """Shared body of every lab workflow type: one generic signal handler and the run protocol that keeps the
    lab database (``lab.workflow_runs``) the system of record."""

    definition: WorkflowDefinition

    def __init__(self) -> None:
        self.signals: dict[str, list[dict[str, Any]]] = defaultdict(list)

    @workflow.signal(name=SIGNAL_NAME)
    def lab_signal(self, name: str, payload: dict[str, Any]) -> None:
        self.signals[name].append(payload)

    async def execute(self, envelope: dict[str, Any]) -> dict[str, Any]:
        defn = self.definition
        ctx = TemporalContext(self, envelope, defn.name)
        await ctx.activity("workflow.mark", {"status": "running"}, key="mark-running")
        try:
            result = await defn.fn(ctx)
        except ActivityFailure as exc:
            await ctx.activity(
                "workflow.mark",
                {"status": "failed", "error": str(exc), "result": {"failure": exc.to_dict()}},
                key="mark-failed",
            )
            raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from None
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.ensure_future(ctx.activity("workflow.mark", {"status": "cancelled"}, key="mark-cancelled"))
            )
            raise
        await ctx.activity("workflow.mark", {"status": "completed", "result": result}, key="mark-completed")
        return result


_CLASSES: dict[str, type] = {}


def _make_workflow_class(defn: WorkflowDefinition) -> type:
    """Build (once) a module-level Temporal workflow type for a definition. Temporal requires workflow classes
    to be globally referenceable, so the class gets a top-level qualname and is registered in this module."""
    cached = _CLASSES.get(defn.name)
    if cached is not None and cached.definition is defn:  # type: ignore[attr-defined]
        return cached
    cls_name = "LabWorkflow_" + "".join(ch if ch.isalnum() else "_" for ch in defn.name)

    async def run(self: LabWorkflowBase, envelope: dict[str, Any]) -> dict[str, Any]:
        return await self.execute(envelope)

    run.__name__ = "run"
    run.__qualname__ = f"{cls_name}.run"
    cls = type(cls_name, (LabWorkflowBase,), {"definition": defn, "run": workflow.run(run), "__module__": __name__})
    cls.__qualname__ = cls_name
    defined = workflow.defn(name=defn.name, sandboxed=False)(cls)
    globals()[cls_name] = defined
    _CLASSES[defn.name] = defined
    return defined


def workflow_classes() -> list[type]:
    from aegis_api.workflows.definitions import WORKFLOWS

    return [_make_workflow_class(d) for d in WORKFLOWS.values()]


# --- client bridge (sync callers → async Temporal client on a dedicated loop) ------------------------------


class _Bridge:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="temporal-bridge")
        self.thread.start()
        self._client: Client | None = None

    def run[T](self, coro: Coroutine[Any, Any, T], timeout: float = 30.0) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def client(self) -> Client:
        if self._client is None:
            self._client = await connect()
        return self._client


_BRIDGE: _Bridge | None = None
_BRIDGE_LOCK = threading.Lock()


def bridge() -> _Bridge:
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = _Bridge()
    return _BRIDGE


async def connect() -> Client:
    s = get_settings()
    if not s.temporal_address:
        raise RuntimeError("TEMPORAL_ADDRESS is not configured")
    return await Client.connect(
        s.temporal_address,
        namespace=s.temporal_namespace,
        tls=s.temporal_tls,
        api_key=s.temporal_api_key,
    )


def start_run(run_id: uuid.UUID, org: uuid.UUID) -> None:
    from aegis_api.db.session import session_scope
    from aegis_api.models.lab import WorkflowRun
    from aegis_api.workflows.definitions import WORKFLOWS

    with session_scope(org) as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.temporal_run_id or run.status != "pending":
            return
        envelope = {
            "run_id": str(run.id),
            "organization_id": str(run.organization_id),
            "principal": run.principal,
            "input": run.input,
        }
        name = run.workflow
    defn = WORKFLOWS[name]
    b = bridge()

    async def _start() -> str | None:
        client = await b.client()
        handle = await client.start_workflow(
            name,
            envelope,
            id=workflow_id(run_id),
            task_queue=get_settings().temporal_task_queue,
            execution_timeout=timedelta(seconds=defn.execution_timeout_seconds),
        )
        return handle.first_execution_run_id or handle.result_run_id

    temporal_run = b.run(_start())
    with session_scope(org) as db:
        run = db.get(WorkflowRun, run_id)
        if run is not None:
            run.temporal_workflow_id = workflow_id(run_id)
            run.temporal_run_id = temporal_run


def deliver_signal(org: uuid.UUID, signal_id: uuid.UUID) -> None:
    from aegis_api.db.base import utcnow
    from aegis_api.db.session import session_scope
    from aegis_api.models.lab import WorkflowSignal

    with session_scope(org) as db:
        sig = db.get(WorkflowSignal, signal_id)
        if sig is None or sig.consumed_at is not None:
            return
        run_id, name, payload = sig.run_id, sig.name, dict(sig.payload or {})
    b = bridge()

    async def _signal() -> None:
        client = await b.client()
        await client.get_workflow_handle(workflow_id(run_id)).signal(SIGNAL_NAME, args=[name, payload])

    b.run(_signal())
    with session_scope(org) as db:
        sig = db.get(WorkflowSignal, signal_id)
        if sig is not None:
            sig.consumed_at = utcnow()


def cancel_run(run_id: uuid.UUID) -> None:
    b = bridge()

    async def _cancel() -> None:
        client = await b.client()
        await client.get_workflow_handle(workflow_id(run_id)).cancel()

    b.run(_cancel())


def health() -> tuple[bool, str]:
    b = bridge()

    async def _check() -> bool:
        client = await b.client()
        return bool(await client.service_client.check_health())

    try:
        return (True, "temporal reachable") if b.run(_check(), timeout=5.0) else (False, "temporal unhealthy")
    except Exception as exc:
        return False, f"temporal unavailable: {type(exc).__name__}"


async def run_worker(*, max_concurrent_activities: int = 16, stop: threading.Event | None = None) -> None:
    """Temporal worker process entrypoint (``python -m aegis_api.processes.temporal_worker``).

    Retries the initial connection with backoff (the server may still be starting) and shuts down gracefully
    when ``stop`` is set (SIGTERM): polling stops and in-flight activities are allowed to finish."""
    from temporalio.worker import Worker

    delay = 1.0
    while True:
        try:
            client = await connect()
            break
        except Exception as exc:
            if stop is not None and stop.is_set():
                return
            log.warning("temporal_connect_retry", error=type(exc).__name__, retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
    with ThreadPoolExecutor(max_workers=max_concurrent_activities, thread_name_prefix="lab-activity") as pool:
        worker = Worker(
            client,
            task_queue=get_settings().temporal_task_queue,
            workflows=workflow_classes(),
            activities=[lab_activity],
            activity_executor=pool,
            max_concurrent_activities=max_concurrent_activities,
            graceful_shutdown_timeout=timedelta(seconds=60),
        )
        log.info("temporal_worker_started", task_queue=get_settings().temporal_task_queue)
        run_task = asyncio.ensure_future(worker.run())
        while not run_task.done():
            if stop is not None and stop.is_set():
                log.info("temporal_worker_stopping")
                await worker.shutdown()
                break
            await asyncio.sleep(0.5)
        await run_task
