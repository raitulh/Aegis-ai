"""Research activities (idempotent; short transactions; provider/HTTP calls outside transactions).

Payloads and results follow LAB_CONTRACT_W2 §B:

* ``research.literature_search`` {project_id, mission_id?, query, sources?, limit?, research_task_id?}
  → {source_ids, count, research_task_id, status, warnings}. With ``research_task_id`` it executes that task
  (a ``literature_search`` or ``web_research`` task created through the API); otherwise it creates — once per
  workflow run and query — a literature-search task and executes it.
* ``research.deep_research_start`` {research_task_id} → {interaction_id, status, task_status}
* ``research.deep_research_poll`` {research_task_id} → {status, terminal, plan_ready, task_status}
* ``research.deep_research_finalize`` {research_task_id} → {source_ids, report_artifact_id, status, memory_id}
"""

from __future__ import annotations

from typing import Any

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.research import literature
from aegis_api.lab.research.deep_research import DeepResearchService
from aegis_api.lab.research.service import (
    ensure_literature_task,
    get_research_task,
    run_literature_search,
    run_web_research,
)
from aegis_api.lab.workflows.registry import ActivityContext, activity


def _required(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise ValidationFailed(f"'{key}' is required")
    return value


@activity("research.literature_search", timeout_seconds=300, heartbeat_seconds=60)
def literature_search(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("research_task_id")
    if task_id:
        with tenant_uow(ctx.actor) as db:
            kind = get_research_task(db, ctx.actor, task_id).kind
        if kind == "web_research":
            return run_web_research(ctx.actor, task_id).as_dict()
        if kind != "literature_search":
            raise ValidationFailed(f"research.literature_search cannot run a '{kind}' task")
    else:
        query = literature.validate_query(str(_required(payload, "query")))
        sources = literature.validate_sources(payload.get("sources"))
        limit = int(payload.get("limit") or 20)
        if not 1 <= limit <= literature.MAX_LIMIT:
            raise ValidationFailed(f"limit must be between 1 and {literature.MAX_LIMIT}")
        with tenant_uow(ctx.actor) as db:
            task = ensure_literature_task(
                db,
                ctx.actor,
                project_id=_required(payload, "project_id"),
                mission_id=payload.get("mission_id"),
                query=query,
                sources=sources,
                limit=limit,
                workflow_run_id=ctx.workflow_run_id,
            )
            task_id = str(task.id)
    return run_literature_search(ctx.actor, task_id, heartbeat=ctx.heartbeat, is_cancelled=ctx.is_cancelled).as_dict()


@activity("research.deep_research_start", timeout_seconds=120, max_attempts=5)
def deep_research_start(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    return DeepResearchService().start(ctx.actor, _required(payload, "research_task_id")).as_dict()


@activity("research.deep_research_poll", timeout_seconds=120, max_attempts=5)
def deep_research_poll(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    return DeepResearchService().poll(ctx.actor, _required(payload, "research_task_id")).as_dict()


@activity("research.deep_research_finalize", timeout_seconds=300)
def deep_research_finalize(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    return DeepResearchService().finalize(ctx.actor, _required(payload, "research_task_id")).as_dict()
