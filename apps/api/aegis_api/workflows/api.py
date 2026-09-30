"""Engine-neutral workflow API.

Workflow definitions are written once as ``async def workflow(ctx: WorkflowContext) -> dict`` and run on either
engine:

* **Temporal** (production): ``ctx.activity`` → ``workflow.execute_activity``, ``ctx.wait_signal`` → signal +
  ``workflow.wait_condition``, ``ctx.sleep`` → durable timer, ``ctx.child`` → child workflow.
* **Inline durable engine** (development / single node): activity results are memoized in
  ``lab.workflow_steps`` and the workflow is *replayed* from the top on every drive; waits and timers suspend
  the run (``waiting``) until a signal arrives or the timer fires.

Determinism rules for definitions (both engines): no I/O, no clocks (use ``ctx.now()``), no randomness, no
database access — all side effects go through activities. Activity keys must be stable across replays.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_interval_seconds: float = 2.0
    backoff: float = 2.0
    max_interval_seconds: float = 60.0
    non_retryable: tuple[str, ...] = ()


DEFAULT_RETRY = RetryPolicy()
NO_RETRY = RetryPolicy(max_attempts=1)


class ActivityFailure(Exception):
    """An activity failed permanently (after retries). Workflows may catch it and react (e.g. failure analysis)."""

    def __init__(self, activity: str, message: str, *, code: str = "activity_failed", details: Any = None) -> None:
        super().__init__(f"{activity}: {message}")
        self.activity = activity
        self.message = message
        self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"activity": self.activity, "message": self.message, "code": self.code, "details": self.details}


class WorkflowCancelled(BaseException):
    """Raised inside a workflow when cancellation was requested (a BaseException, like Temporal's
    ``asyncio.CancelledError``, so broad ``except Exception`` blocks in workflow code cannot swallow it)."""


class WorkflowContext(Protocol):
    run_id: str
    workflow: str
    input: dict[str, Any]

    async def activity(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        key: str | None = None,
        retry: RetryPolicy = DEFAULT_RETRY,
        timeout_seconds: float = 900.0,
        heartbeat_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def wait_signal(
        self, name: str, *, key: str | None = None, timeout_seconds: float | None = None
    ) -> dict[str, Any] | None: ...

    async def sleep(self, seconds: float, *, key: str | None = None) -> None: ...

    async def child(self, workflow: str, payload: dict[str, Any], *, key: str) -> dict[str, Any]: ...

    def now(self) -> datetime: ...

    async def gather(self, *aws: Awaitable[dict[str, Any]]) -> list[dict[str, Any]]: ...


WorkflowFn = Callable[[WorkflowContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    fn: WorkflowFn
    description: str
    execution_timeout_seconds: int = 7 * 24 * 3600
    tags: tuple[str, ...] = field(default_factory=tuple)
