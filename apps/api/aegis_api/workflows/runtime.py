"""Activity registry and execution helpers shared by the inline engine and the Temporal worker.

Activities are plain synchronous functions ``fn(actx, payload) -> dict``. They perform all I/O (database in
short transactions, model calls, sandbox execution, object storage) and must be idempotent: a retried or
replayed activity must not duplicate side effects (they use natural/idempotency keys).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import DBAPIError, OperationalError

from aegis_api.errors import AppError
from aegis_api.security.context import Principal, principal_from_snapshot
from aegis_api.services.lab.common import Actor

CTX_KEY = "__ctx__"


@dataclass
class ActivityContext:
    organization_id: uuid.UUID
    principal: Principal
    run_id: uuid.UUID
    workflow: str
    attempt: int = 1
    heartbeat: Callable[[dict[str, Any]], None] = field(default=lambda _details: None)

    @property
    def actor(self) -> Actor:
        return Actor.workflow(self.run_id, self.workflow)

    @property
    def launched_by(self) -> Actor:
        return Actor.of(self.principal)


ActivityFn = Callable[[ActivityContext, dict[str, Any]], dict[str, Any]]
ACTIVITIES: dict[str, ActivityFn] = {}


def activity(name: str) -> Callable[[ActivityFn], ActivityFn]:
    def register(fn: ActivityFn) -> ActivityFn:
        if name in ACTIVITIES:
            raise ValueError(f"duplicate activity '{name}'")
        ACTIVITIES[name] = fn
        return fn

    return register


class ActivityError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.details = details


def classify(exc: BaseException) -> ActivityError:
    """Map exceptions to retryable/non-retryable activity errors. Business/authorization errors never retry."""
    if isinstance(exc, ActivityError):
        return exc
    if isinstance(exc, AppError):
        retryable = exc.status_code in (429, 503)
        return ActivityError(exc.message, code=exc.code, retryable=retryable, details=exc.details)
    from aegis_api.infrastructure.execution.base import ExecutionError, ExecutionPolicyError
    from aegis_api.infrastructure.llm.base import LLMError
    from aegis_api.infrastructure.storage import StorageError
    from aegis_api.services.lab.agents import AgentRunFailed

    if isinstance(exc, LLMError):
        return ActivityError(str(exc), code=exc.code, retryable=exc.retryable)
    if isinstance(exc, AgentRunFailed):
        return ActivityError(
            str(exc), code=exc.code, retryable=exc.retryable, details={"agent_run_id": str(exc.run_id)}
        )
    if isinstance(exc, ExecutionPolicyError):
        return ActivityError(str(exc), code="execution_policy", retryable=False)
    if isinstance(exc, ExecutionError):
        return ActivityError(str(exc), code="execution_error", retryable=exc.transient)
    if isinstance(exc, StorageError):
        return ActivityError(str(exc), code="storage_error", retryable=exc.transient)
    if isinstance(exc, OperationalError | DBAPIError):
        return ActivityError(f"database error: {type(exc).__name__}", code="database_error", retryable=True)
    if isinstance(exc, KeyError | ValueError | TypeError | AssertionError):
        return ActivityError(f"{type(exc).__name__}: {exc}"[:1000], code="activity_bug", retryable=False)
    return ActivityError(f"{type(exc).__name__}: {exc}"[:1000], code="activity_error", retryable=True)


def build_context(
    name: str, payload: dict[str, Any], *, attempt: int = 1, heartbeat: Callable[[dict[str, Any]], None] | None = None
) -> tuple[ActivityContext, dict[str, Any]]:
    meta = payload.get(CTX_KEY) or {}
    body = {k: v for k, v in payload.items() if k != CTX_KEY}
    principal = principal_from_snapshot(dict(meta["principal"]))
    actx = ActivityContext(
        organization_id=uuid.UUID(str(meta["organization_id"])),
        principal=principal,
        run_id=uuid.UUID(str(meta["run_id"])),
        workflow=str(meta.get("workflow", "")),
        attempt=attempt,
    )
    if heartbeat is not None:
        actx.heartbeat = heartbeat
    if principal.organization_id != actx.organization_id:
        raise ActivityError("principal/tenant mismatch", code="tenant_mismatch", retryable=False)
    return actx, body


def run_activity(
    name: str, payload: dict[str, Any], *, attempt: int = 1, heartbeat: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    from aegis_api.workflows import activities as _activities  # noqa: F401 - registers activities

    fn = ACTIVITIES.get(name)
    if fn is None:
        raise ActivityError(f"unknown activity '{name}'", code="unknown_activity", retryable=False)
    actx, body = build_context(name, payload, attempt=attempt, heartbeat=heartbeat)
    try:
        result = fn(actx, body)
    except Exception as exc:
        raise classify(exc) from exc
    return result if isinstance(result, dict) else {"value": result}
