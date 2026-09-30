"""Activity registry shared by the Temporal worker and the local durable workflow engine.

Workflow code is deterministic and never performs IO; every side effect is an *activity*: a plain, sync,
idempotent function registered here by name. Domain packages declare their activities next to their
services (``aegis_api.lab.<context>.activities``)::

    @activity("experiments.schedule_runs", timeout_seconds=120)
    def schedule_runs(ctx: ActivityContext, payload: dict) -> dict:
        with tenant_uow(ctx.actor) as db:
            ...
        return {"run_ids": [...]}

Rules for activities:

* input and output are JSON-serializable dicts (they cross the Temporal payload boundary);
* idempotent: re-running with the same payload must not duplicate work (use deterministic keys);
* short DB transactions only (``tenant_uow``), never held across network/LLM/execution calls;
* long-running activities call ``ctx.heartbeat(details)`` and honour ``ctx.is_cancelled()``;
* raise ``PermanentError``/policy errors for non-retryable failures (listed in ``non_retryable``).
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from aegis_api.lab.core.actor import Actor

log = structlog.get_logger("aegis.lab.workflows")

TaskQueueKind = Literal["default", "execution"]

DEFAULT_NON_RETRYABLE: tuple[str, ...] = (
    "PermanentError",
    "PolicyDenied",
    "ApprovalRequired",
    "BudgetExceeded",
    "QuotaExceeded",
    "ValidationFailed",
    "InvalidTransition",
    "InvalidTransitionError",
    "NotFound",
    "Forbidden",
    "FeatureDisabled",
)


@dataclass(frozen=True)
class RetrySpec:
    max_attempts: int = 3
    initial_interval_seconds: float = 2.0
    backoff_coefficient: float = 2.0
    max_interval_seconds: float = 60.0
    non_retryable: tuple[str, ...] = DEFAULT_NON_RETRYABLE


@dataclass
class ActivityContext:
    """Runtime context handed to every activity (engine-agnostic)."""

    actor: Actor
    workflow_run_id: uuid.UUID | None = None
    attempt: int = 1
    heartbeat: Callable[[Any], None] = field(default=lambda _details=None: None)
    is_cancelled: Callable[[], bool] = field(default=lambda: False)

    @property
    def organization_id(self) -> uuid.UUID:
        return self.actor.organization_id


ActivityFn = Callable[[ActivityContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ActivityDef:
    name: str
    fn: ActivityFn
    timeout_seconds: int = 300
    heartbeat_seconds: int | None = None
    retry: RetrySpec = RetrySpec()
    task_queue: TaskQueueKind = "default"


ACTIVITIES: dict[str, ActivityDef] = {}


def activity(
    name: str,
    *,
    timeout_seconds: int = 300,
    heartbeat_seconds: int | None = None,
    max_attempts: int = 3,
    initial_interval_seconds: float = 2.0,
    max_interval_seconds: float = 60.0,
    non_retryable: tuple[str, ...] = DEFAULT_NON_RETRYABLE,
    task_queue: TaskQueueKind = "default",
) -> Callable[[ActivityFn], ActivityFn]:
    def decorator(fn: ActivityFn) -> ActivityFn:
        if name in ACTIVITIES and ACTIVITIES[name].fn is not fn:
            raise ValueError(f"Activity '{name}' is already registered")
        ACTIVITIES[name] = ActivityDef(
            name=name,
            fn=fn,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=heartbeat_seconds,
            retry=RetrySpec(
                max_attempts=max_attempts,
                initial_interval_seconds=initial_interval_seconds,
                max_interval_seconds=max_interval_seconds,
                non_retryable=non_retryable,
            ),
            task_queue=task_queue,
        )
        return fn

    return decorator


# Modules that register activities. The workers import all of them at start-up.
ACTIVITY_MODULES: tuple[str, ...] = (
    "aegis_api.lab.missions.activities",
    "aegis_api.lab.agents.activities",
    "aegis_api.lab.research.activities",
    "aegis_api.lab.knowledge.activities",
    "aegis_api.lab.hypotheses.activities",
    "aegis_api.lab.experiments.activities",
    "aegis_api.lab.execution.activities",
    "aegis_api.lab.evaluation.activities",
    "aegis_api.lab.failures.activities",
    "aegis_api.lab.strategies.activities",
    "aegis_api.lab.verification.activities",
    "aegis_api.lab.governance.activities",
    "aegis_api.lab.data.activities",
)


def load_activities(strict: bool = True) -> dict[str, ActivityDef]:
    for module in ACTIVITY_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if strict and exc.name == module:
                raise
            if exc.name != module:
                raise
            log.warning("activity_module_missing", module=module)
    return ACTIVITIES


def get_activity(name: str) -> ActivityDef:
    if name not in ACTIVITIES:
        load_activities(strict=False)
    try:
        return ACTIVITIES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown activity '{name}'") from exc


# -- actor (de)serialization across the workflow boundary ---------------------------------------
def actor_to_payload(actor: Actor) -> dict[str, Any]:
    return {
        "kind": actor.kind,
        "organization_id": str(actor.organization_id),
        "permissions": sorted(actor.permissions),
        "label": actor.label,
        "user_id": str(actor.user_id) if actor.user_id else None,
        "role": actor.role,
        "api_key_id": str(actor.api_key_id) if actor.api_key_id else None,
        "service_account_id": str(actor.service_account_id) if actor.service_account_id else None,
        "agent_run_id": str(actor.agent_run_id) if actor.agent_run_id else None,
        "agent_role": actor.agent_role,
        "workflow_run_id": str(actor.workflow_run_id) if actor.workflow_run_id else None,
        "autonomy_level": actor.autonomy_level,
        "request_id": actor.request_id,
        "trace_id": actor.trace_id,
        "auth_method": actor.auth_method,
        "project_ids": sorted(actor.project_ids),
    }


def actor_from_payload(data: dict[str, Any]) -> Actor:
    def _uuid(value: Any) -> uuid.UUID | None:
        return uuid.UUID(value) if value else None

    return Actor(
        kind=data["kind"],
        organization_id=uuid.UUID(data["organization_id"]),
        permissions=frozenset(data.get("permissions") or ()),
        label=data.get("label") or data["kind"],
        user_id=_uuid(data.get("user_id")),
        role=data.get("role"),
        api_key_id=_uuid(data.get("api_key_id")),
        service_account_id=_uuid(data.get("service_account_id")),
        agent_run_id=_uuid(data.get("agent_run_id")),
        agent_role=data.get("agent_role"),
        workflow_run_id=_uuid(data.get("workflow_run_id")),
        autonomy_level=data.get("autonomy_level"),
        request_id=data.get("request_id"),
        trace_id=data.get("trace_id"),
        auth_method=data.get("auth_method"),
        project_ids=frozenset(data.get("project_ids") or ()),
    )
