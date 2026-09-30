"""Temporal integration: a thread-owned client bridge for synchronous callers, and the worker factory.

* :class:`TemporalBridge` owns one ``temporalio.client.Client`` on a dedicated background thread with its own
  asyncio loop, so synchronous services (the launcher, after-commit hooks, the scheduler) can start, signal,
  cancel and describe workflows without an event loop of their own. Connection problems raise
  ``ServiceUnavailable`` (callers degrade: runs stay PENDING and the scheduler retries); a short circuit
  breaker avoids paying the connect timeout on every call while Temporal is down.
* :func:`build_worker` builds a ``temporalio.worker.Worker`` for one logical queue (``default`` hosts every
  workflow class and the default-queue activities; ``execution`` hosts sandbox-execution activities).
  Registry activities are wrapped so they receive the same :class:`ActivityContext` as on the local engine,
  and failures are converted into ``ApplicationError`` carrying the engine-agnostic error record
  (non-retryable classification by exception MRO, as on the local engine).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import importlib
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import structlog
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from aegis_api.config import Settings, get_settings
from aegis_api.errors import NotFound, ServiceUnavailable
from aegis_api.lab.core.errors import PermanentError
from aegis_api.lab.observability.metrics import WORKFLOW_ACTIVITY_DURATION
from aegis_api.lab.observability.tracing import span
from aegis_api.lab.workflows.ctx import PayloadError, error_record, normalize_payload, normalize_result
from aegis_api.lab.workflows.definitions import load_flows
from aegis_api.lab.workflows.registry import (
    ACTIVITIES,
    ActivityContext,
    ActivityDef,
    actor_from_payload,
    load_activities,
)

log = structlog.get_logger("aegis.lab.workflows.temporal")

BREAKER_SECONDS = 10.0
CALL_TIMEOUT_SECONDS = 15.0
DEFAULT_ACTIVITY_WORKERS = 16
GRACEFUL_SHUTDOWN_SECONDS = 30.0

# Pure modules shared with workflow code (not re-imported per workflow run inside the sandbox).
SANDBOX_PASSTHROUGH_MODULES: tuple[str, ...] = (
    "aegis_api.lab.workflows.ctx",
    "aegis_api.lab.workflows.definitions",
    "aegis_api.lab.workflows.registry",
    "engines",
    "structlog",
    "pydantic",
    "pydantic_core",
)

_TRANSIENT_RPC = frozenset(
    {
        RPCStatusCode.UNAVAILABLE,
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.RESOURCE_EXHAUSTED,
        RPCStatusCode.ABORTED,
        RPCStatusCode.INTERNAL,
        RPCStatusCode.UNKNOWN,
        RPCStatusCode.CANCELLED,
    }
)


def _map_error(exc: BaseException) -> BaseException:
    if isinstance(exc, RPCError):
        if exc.status == RPCStatusCode.NOT_FOUND:
            return NotFound("Temporal workflow execution not found")
        if exc.status in _TRANSIENT_RPC:
            return ServiceUnavailable(f"Temporal is unavailable ({exc.status.name})")
        return PermanentError(f"Temporal rejected the request ({exc.status.name}): {exc.message}")
    return exc


async def connect_client(settings: Settings | None = None) -> Client:
    """Connect a Temporal client from settings (bounded by the configured connect timeout)."""
    s = settings or get_settings()
    if not s.temporal_address:
        raise ServiceUnavailable("Temporal is not configured (TEMPORAL_ADDRESS is unset)")
    try:
        return await asyncio.wait_for(
            Client.connect(
                s.temporal_address,
                namespace=s.temporal_namespace,
                tls=s.temporal_tls,
                api_key=s.temporal_api_key,
            ),
            timeout=s.temporal_connect_timeout_seconds,
        )
    except Exception as exc:
        raise ServiceUnavailable(f"Cannot connect to Temporal at {s.temporal_address}") from exc


class TemporalBridge:
    """Synchronous facade over an async Temporal client living on a private event-loop thread."""

    def __init__(self, settings: Settings, *, call_timeout: float = CALL_TIMEOUT_SECONDS) -> None:
        self.settings = settings
        self.key = self.config_key(settings)
        self.call_timeout = call_timeout
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Client | None = None
        self._unavailable_until = 0.0

    @staticmethod
    def config_key(settings: Settings) -> str:
        raw = "|".join(
            str(v)
            for v in (
                settings.temporal_address,
                settings.temporal_namespace,
                settings.temporal_tls,
                settings.temporal_api_key or "",
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    # -- loop / client --------------------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _main() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(target=_main, name="aegis-temporal-bridge", daemon=True)
            thread.start()
            ready.wait()
            self._loop, self._thread = loop, thread
            return loop

    async def _client_or_connect(self) -> Client:
        if self._client is None:
            self._client = await connect_client(self.settings)
        return self._client

    async def _invoke[T](self, op: Callable[[Client], Awaitable[T]]) -> T:
        client = await self._client_or_connect()
        try:
            return await op(client)
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    def _trip(self) -> None:
        self._unavailable_until = time.monotonic() + BREAKER_SECONDS
        self._client = None

    def _call[T](self, op: Callable[[Client], Awaitable[T]], *, timeout: float | None = None) -> T:
        if time.monotonic() < self._unavailable_until:
            raise ServiceUnavailable("Temporal is unavailable (recent connection failure)")
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._invoke(op), loop)
        try:
            return future.result(timeout if timeout is not None else self.call_timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            self._trip()
            raise ServiceUnavailable("Temporal call timed out") from exc
        except ServiceUnavailable:
            self._trip()
            raise

    # -- operations -------------------------------------------------------------------------------------
    def start_workflow(
        self,
        kind: str,
        envelope: dict[str, Any],
        workflow_id: str,
        task_queue: str,
        *,
        execution_timeout_seconds: int | None = None,
    ) -> str:
        """Start (or attach to the running) execution ``workflow_id``; returns its run id."""

        async def op(client: Client) -> str:
            handle = await client.start_workflow(
                kind,
                envelope,
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=execution_timeout_seconds) if execution_timeout_seconds else None,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
            return handle.result_run_id or handle.run_id or ""

        return self._call(op)

    def signal(self, workflow_id: str, name: str, payload: dict[str, Any], signal_id: str | None = None) -> None:
        async def op(client: Client) -> None:
            await client.get_workflow_handle(workflow_id).signal("signal", args=[name, payload, signal_id])

        self._call(op)

    def cancel(self, workflow_id: str) -> bool:
        """Request cancellation; False when the execution does not exist."""

        async def op(client: Client) -> None:
            await client.get_workflow_handle(workflow_id).cancel()

        try:
            self._call(op)
        except NotFound:
            return False
        return True

    def describe(self, workflow_id: str) -> dict[str, Any]:
        async def op(client: Client) -> dict[str, Any]:
            desc = await client.get_workflow_handle(workflow_id).describe()
            return {
                "status": desc.status.name if desc.status is not None else None,
                "run_id": desc.run_id,
                "workflow_type": desc.workflow_type,
                "task_queue": desc.task_queue,
                "start_time": desc.start_time.isoformat() if desc.start_time else None,
                "close_time": desc.close_time.isoformat() if desc.close_time else None,
                "history_length": desc.history_length,
            }

        try:
            return self._call(op)
        except NotFound:
            return {"status": "NOT_FOUND"}

    def result(self, workflow_id: str, *, timeout: float = 10.0) -> Any:
        """Result of a COMPLETED execution (raises for other states)."""

        async def op(client: Client) -> Any:
            return await client.get_workflow_handle(workflow_id).result(
                follow_runs=False, rpc_timeout=timedelta(seconds=timeout)
            )

        return self._call(op, timeout=timeout + 5)

    def query_status(self, workflow_id: str) -> dict[str, Any]:
        async def op(client: Client) -> dict[str, Any]:
            value = await client.get_workflow_handle(workflow_id).query("status")
            return dict(value) if isinstance(value, dict) else {}

        return self._call(op)

    def health(self) -> bool:
        async def op(client: Client) -> bool:
            return bool(await client.service_client.check_health())

        try:
            return self._call(op, timeout=self.settings.temporal_connect_timeout_seconds + 2)
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            loop, self._loop = self._loop, None
            self._client = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)


_bridge_lock = threading.Lock()
_bridge: TemporalBridge | None = None


def get_bridge() -> TemporalBridge:
    """Process-wide bridge (rebuilt when the Temporal settings change)."""
    global _bridge
    settings = get_settings()
    key = TemporalBridge.config_key(settings)
    with _bridge_lock:
        if _bridge is None or _bridge.key != key:
            old, _bridge = _bridge, TemporalBridge(settings)
            if old is not None:
                old.close()
        return _bridge


def reset_bridge() -> None:
    global _bridge
    with _bridge_lock:
        old, _bridge = _bridge, None
    if old is not None:
        old.close()


# ---------------------------------------------------------------------------------------------------
# Activities & workers
# ---------------------------------------------------------------------------------------------------
def load_worker_registries() -> None:
    """Import every activity module, the engine's own activities and every flow module."""
    load_activities(strict=False)
    importlib.import_module("aegis_api.lab.workflows.activities")
    load_flows()


def _heartbeat(details: Any = None) -> None:
    if details is None:
        activity.heartbeat()
        return
    try:
        activity.heartbeat(normalize_payload(details))
    except PayloadError:
        activity.heartbeat()


def temporal_activity(defn: ActivityDef) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a registry activity as a synchronous Temporal activity named ``defn.name``."""

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        info = activity.info()
        raw_actor = payload.get("actor") if isinstance(payload, dict) else None
        if not isinstance(raw_actor, dict):
            raise ApplicationError("activity payload lacks the workflow actor", type="PayloadError", non_retryable=True)
        run_id = payload.get("workflow_run_id")
        ctx = ActivityContext(
            actor=actor_from_payload(raw_actor),
            workflow_run_id=uuid.UUID(str(run_id)) if run_id else None,
            attempt=info.attempt,
            heartbeat=_heartbeat,
            is_cancelled=activity.is_cancelled,
        )
        started = time.monotonic()
        try:
            with span("workflow.activity", activity=defn.name, attempt=info.attempt, engine="temporal"):
                result = normalize_result(defn.fn(ctx, payload), what=f"activity {defn.name}")
        except ApplicationError:
            WORKFLOW_ACTIVITY_DURATION.labels(defn.name, "error").observe(time.monotonic() - started)
            raise
        except Exception as exc:
            WORKFLOW_ACTIVITY_DURATION.labels(defn.name, "error").observe(time.monotonic() - started)
            record = error_record(exc, defn.retry, activity=defn.name, attempts=info.attempt)
            raise ApplicationError(
                record["message"], record, type=record["type"], non_retryable=bool(record["non_retryable"])
            ) from exc
        WORKFLOW_ACTIVITY_DURATION.labels(defn.name, "completed").observe(time.monotonic() - started)
        return result

    safe = "".join(ch if ch.isalnum() else "_" for ch in defn.name)
    execute.__name__ = f"activity_{safe}"
    execute.__qualname__ = execute.__name__
    return activity.defn(name=defn.name)(execute)


def sandbox_restrictions() -> SandboxRestrictions:
    return SandboxRestrictions.default.with_passthrough_modules(*SANDBOX_PASSTHROUGH_MODULES)


def queue_name(queue: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    if queue == "default":
        return s.temporal_task_queue
    if queue == "execution":
        return s.temporal_execution_task_queue
    raise ValueError(f"Unknown workflow queue '{queue}' (expected 'default' or 'execution')")


def build_worker(
    client: Client,
    queue: str = "default",
    *,
    task_queue: str | None = None,
    max_concurrent_activities: int | None = None,
    graceful_shutdown_seconds: float = GRACEFUL_SHUTDOWN_SECONDS,
    identity: str | None = None,
) -> Worker | None:
    """A worker for logical ``queue`` (``default``: workflows + default activities; ``execution``).

    Returns ``None`` when nothing is registered for the queue (e.g. no execution backend installed).
    """
    from aegis_api.lab.workflows.temporal_workflows import workflow_classes

    load_worker_registries()
    target = task_queue or queue_name(queue)
    defs = sorted((d for d in ACTIVITIES.values() if d.task_queue == queue), key=lambda d: d.name)
    workflows = workflow_classes() if queue == "default" else []
    if not defs and not workflows:
        log.warning("temporal_worker_empty_queue", queue=queue, task_queue=target)
        return None
    workers = max(1, int(max_concurrent_activities or DEFAULT_ACTIVITY_WORKERS))
    log.info(
        "temporal_worker_built",
        queue=queue,
        task_queue=target,
        workflows=len(workflows),
        activities=len(defs),
        max_concurrent_activities=workers,
    )
    return Worker(
        client,
        task_queue=target,
        workflows=workflows,
        activities=[temporal_activity(d) for d in defs],
        activity_executor=ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"aegis-temporal-{queue}"),
        max_concurrent_activities=workers,
        workflow_runner=SandboxedWorkflowRunner(restrictions=sandbox_restrictions()),
        graceful_shutdown_timeout=timedelta(seconds=graceful_shutdown_seconds),
        identity=identity,
    )
