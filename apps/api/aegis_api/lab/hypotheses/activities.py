"""Workflow activities of the hypotheses context (LAB_CONTRACT_W2 §B; payloads and results are JSON).

All activities are idempotent: selection keeps already selected hypotheses, ``mark_designed`` is a no-op once
designed, and ``conclude`` replays the stored conclusion for the same comparisons.
"""

from __future__ import annotations

import uuid
from typing import Any

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.hypotheses import service
from aegis_api.lab.workflows.registry import ActivityContext, activity


def _id(payload: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(f"payload.{key} must be an id") from exc


def _statuses(payload: dict[str, Any]) -> list[str] | None:
    raw = payload.get("statuses")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ValidationFailed("payload.statuses must be a list of status names")
    return raw


@activity("hypotheses.select", timeout_seconds=120)
def select_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    top_k = payload.get("top_k", 3)
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ValidationFailed("payload.top_k must be an integer")
    with tenant_uow(ctx.actor) as db:
        selection = service.select_hypotheses(db, ctx.actor, _id(payload, "mission_id"), top_k)
        return {
            "selected_ids": list(selection.result.selected_ids),
            "newly_selected_ids": list(selection.result.newly_selected_ids),
            "ranking": [
                {"id": r.id, "score": r.score, "rank": r.rank, "eligible": r.eligible, "reason": r.reason}
                for r in selection.result.ranking
            ],
            "strategy_version_id": str(selection.strategy_version_id) if selection.strategy_version_id else None,
        }


@activity("hypotheses.mark_designed", timeout_seconds=60)
def mark_designed_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        hypothesis = service.mark_designed(db, ctx.actor, _id(payload, "hypothesis_id"))
        return {"hypothesis_id": str(hypothesis.id), "status": hypothesis.status}


@activity("hypotheses.conclude", timeout_seconds=120)
def conclude_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    raw_ids = payload.get("comparison_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValidationFailed("payload.comparison_ids must be a non-empty list")
    with tenant_uow(ctx.actor) as db:
        comparisons = service.load_comparisons(db, ctx.actor, [str(c) for c in raw_ids])
        hypothesis = service.conclude_hypothesis(db, ctx.actor, _id(payload, "hypothesis_id"), comparisons)
        conclusion = service.latest_conclusion(hypothesis) or {}
        return {
            "hypothesis_id": str(hypothesis.id),
            "status": hypothesis.status,
            "rationale": conclusion.get("rationale") or hypothesis.status_reason,
            "comparison_ids": conclusion.get("comparison_ids") or [str(c) for c in raw_ids],
        }


@activity("hypotheses.list_for_mission", timeout_seconds=60)
def list_for_mission_activity(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    with tenant_uow(ctx.actor) as db:
        rows = service.list_for_mission(db, ctx.actor, _id(payload, "mission_id"), _statuses(payload))
        return {
            "items": [
                {
                    "id": str(h.id),
                    "status": h.status,
                    "statement": h.statement,
                    "selection_rank": h.selection_rank,
                    "metric": (h.measurable_prediction or {}).get("metric"),
                }
                for h in rows
            ]
        }
