"""Failure-intelligence workflow activities (idempotent; one short tenant transaction each).

* ``failures.record_from_run`` {experiment_run_id} → {failure_id, failure_type, status, recovery_action, lesson_id}
  (idempotent per run).
* ``failures.record`` {stage, signals, project_id, mission_id?, experiment_id?, experiment_run_id?, compute_job_id?,
  agent_run_id?, strategy_version_id?, title?} → {failure_id, failure_type, status, recovery_action, lesson_id}
  (idempotent per workflow run + request).
* ``failures.apply_recovery`` {failure_id, source?} → {status, experiment_id?, new_version_id?, approval_id?,
  failure_status, recovery_status}.
* ``failures.recovery_outcome`` {failure_id, succeeded?} → the failure summary (outcome assessed from the runs
  of the recovery version when ``succeeded`` is omitted).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.failures import recovery, service
from aegis_api.lab.models import Failure
from aegis_api.lab.workflows.registry import ActivityContext, activity

_RECORD_KEYS = (
    "stage",
    "signals",
    "project_id",
    "mission_id",
    "experiment_id",
    "experiment_run_id",
    "compute_job_id",
    "agent_run_id",
    "strategy_version_id",
    "title",
)


def _required_id(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"payload requires '{key}'")
    return value.strip()


def failure_summary(failure: Failure) -> dict[str, Any]:
    action = dict(failure.recovery_action or {})
    return {
        "failure_id": str(failure.id),
        "failure_type": failure.failure_type,
        "status": failure.status,
        "signature": failure.signature,
        "recovery_status": failure.recovery_status,
        "recovery_action": {
            "action": action.get("action"),
            "spec_patch": action.get("spec_patch") or {},
            "requires_approval": action.get("requires_approval"),
            "automatic": action.get("automatic"),
            "status": action.get("status"),
        },
        "lesson_id": str(failure.lesson_id) if failure.lesson_id else None,
    }


@activity("failures.record_from_run", timeout_seconds=300, max_attempts=5)
def record_from_run(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Classify and record the failure of a finished experiment run (one failure per run)."""
    run_id = _required_id(payload, "experiment_run_id")
    with tenant_uow(ctx.actor) as db:
        failure = service.record_failure_from_run(db, ctx.actor, run_id)
        return failure_summary(failure)


@activity("failures.record", timeout_seconds=300, max_attempts=5)
def record(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a failure observed by a workflow (design validation, statistics, reproduction, tools, agents…)."""
    body = {key: payload[key] for key in _RECORD_KEYS if payload.get(key) is not None}
    if "stage" not in body or "project_id" not in body:
        raise ValidationFailed("payload requires 'stage' and 'project_id'")
    scope = str(ctx.workflow_run_id) if ctx.workflow_run_id else "adhoc"
    digest = hashlib.sha256(
        json.dumps({"scope": scope, "body": body}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    with tenant_uow(ctx.actor) as db:
        failure = service.record_failure(db, ctx.actor, body, idempotency_key=f"activity:{digest}")
        return failure_summary(failure)


@activity("failures.apply_recovery", timeout_seconds=300, max_attempts=3)
def apply_recovery(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the proposed recovery (new experiment version) or request the approval it needs (idempotent)."""
    failure_id = _required_id(payload, "failure_id")
    source = payload.get("source", "rule")
    if source not in ("rule", "model"):
        raise ValidationFailed("'source' must be 'rule' or 'model'")
    with tenant_uow(ctx.actor) as db:
        result = recovery.apply_recovery(db, ctx.actor, failure_id, source=source)
        return result.as_dict()


@activity("failures.recovery_outcome", timeout_seconds=120, max_attempts=5)
def recovery_outcome(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Record the outcome of a recovery test (assessed from the recovery version's runs unless given)."""
    failure_id = _required_id(payload, "failure_id")
    succeeded = payload.get("succeeded")
    if succeeded is not None and not isinstance(succeeded, bool):
        raise ValidationFailed("'succeeded' must be a boolean")
    with tenant_uow(ctx.actor) as db:
        failure = service.get_failure(db, ctx.actor, failure_id, permission="failure:write")
        if failure.status != "RECOVERING" and failure.recovery_status in ("succeeded", "failed"):
            return failure_summary(failure)  # already recorded (re-execution)
        if succeeded is None:
            failure = recovery.assess_recovery(db, ctx.actor, failure_id)
        else:
            failure = recovery.record_recovery_outcome(
                db, ctx.actor, failure_id, succeeded, reason=payload.get("reason")
            )
        return failure_summary(failure)
