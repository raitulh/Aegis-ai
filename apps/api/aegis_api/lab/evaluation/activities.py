"""Evaluation workflow activities (idempotent; short tenant transactions; bytes are read outside transactions).

* ``evaluation.evaluate_run`` {experiment_run_id, evaluator_key?, config?} → {evaluation_run_ids, passed?,
  evaluations}: one evaluator, or the default per-run set (recomputing evaluators named by the spec's metrics,
  then ``metric`` for pre-registered success criteria).
* ``evaluation.evaluate_experiment`` {experiment_id, evaluator_keys?} → {evaluation_run_ids, passed?, skipped,
  evaluations}: experiment-level evaluators on the current version's runs (default ``metric`` + ``benchmark`` +
  ``statistical`` + ``resource``; baseline comparisons are skipped when there is no baseline).
* ``evaluation.run`` {evaluation_run_id} → the summary of a queued evaluation created by ``POST /evaluations``
  (inputs above the inline limit).

Re-executions inside the same workflow run return the evaluations already recorded (idempotency scope = the
workflow run id), so a crash never duplicates evaluation runs or evaluator-sourced metric rows.
"""

from __future__ import annotations

from typing import Any

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.evaluation import service
from aegis_api.lab.workflows.registry import ActivityContext, activity

MAX_EVALUATORS_PER_ACTIVITY = 20


def _required_id(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"payload requires '{key}'")
    return value.strip()


def _keys(value: Any, label: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValidationFailed(f"'{label}' must be a list of evaluator keys")
    keys = list(dict.fromkeys(value))
    if len(keys) > MAX_EVALUATORS_PER_ACTIVITY:
        raise ValidationFailed(f"at most {MAX_EVALUATORS_PER_ACTIVITY} evaluators per activity")
    return keys


def _scope(ctx: ActivityContext) -> str:
    return f"workflow:{ctx.workflow_run_id}" if ctx.workflow_run_id else "adhoc"


def _result(summaries: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "evaluation_run_ids": [s["evaluation_run_id"] for s in summaries],
        "passed": service.aggregate_passed([s["passed"] for s in summaries]),
        "evaluations": summaries,
        **extra,
    }


@activity("evaluation.evaluate_run", timeout_seconds=900, heartbeat_seconds=120)
def evaluate_run(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one experiment run platform-side (held-out labels never enter the sandbox)."""
    run_id = _required_id(payload, "experiment_run_id")
    key = payload.get("evaluator_key")
    config = payload.get("config")
    if config is not None and not isinstance(config, dict):
        raise ValidationFailed("'config' must be an object")
    if key is not None:
        keys = _keys(key, "evaluator_key") or []
    else:
        if config:
            raise ValidationFailed("'config' requires 'evaluator_key'")
        with tenant_uow(ctx.actor) as db:
            keys = service.default_run_evaluators(db, ctx.actor, run_id)
    summaries: list[dict[str, Any]] = []
    for evaluator_key in keys:
        if ctx.is_cancelled():
            return _result(summaries, cancelled=True)
        summaries.append(
            service.evaluate_detached(
                ctx.actor,
                evaluator_key=evaluator_key,
                scope=_scope(ctx),
                experiment_run_id=run_id,
                config=config if key is not None else None,
            )
        )
        ctx.heartbeat({"evaluated": len(summaries), "total": len(keys)})
    return _result(summaries)


@activity("evaluation.evaluate_experiment", timeout_seconds=1800, heartbeat_seconds=120)
def evaluate_experiment(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Experiment-level evaluators on the current version's runs."""
    experiment_id = _required_id(payload, "experiment_id")
    requested = _keys(payload.get("evaluator_keys"), "evaluator_keys")
    skipped: dict[str, str] = {}
    if requested is None:
        with tenant_uow(ctx.actor) as db:
            keys, skipped = service.default_experiment_evaluators(db, ctx.actor, experiment_id)
    else:
        keys = requested
    summaries: list[dict[str, Any]] = []
    for evaluator_key in keys:
        if ctx.is_cancelled():
            return _result(summaries, skipped=skipped, cancelled=True)
        summaries.append(
            service.evaluate_detached(
                ctx.actor, evaluator_key=evaluator_key, scope=_scope(ctx), experiment_id=experiment_id
            )
        )
        ctx.heartbeat({"evaluated": len(summaries), "total": len(keys)})
    return _result(summaries, skipped=skipped)


@activity("evaluation.run", timeout_seconds=1800, heartbeat_seconds=120)
def run_queued_evaluation(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an evaluation queued by the API (idempotent: finished runs return their summary)."""
    evaluation_run_id = _required_id(payload, "evaluation_run_id")
    summary = service.execute_pending_evaluation(ctx.actor, evaluation_run_id)
    return _result([summary])
