"""Research tasks: Gemini Deep Research (background agent interactions) and the literature pipeline.

Deep Research runs as a *background* interaction owned by the provider: the ResearchWorkflow starts it,
persists the interaction id, then polls it on durable timers (no worker blocks for the duration), records
progress events, supports cancellation, and on completion stores the report as an artifact, records every
citation as a research source, meters usage and ingests the report for retrieval.

The literature pipeline searches scholarly indexes (arXiv, Crossref), records sources with provenance and has
the LiteratureAgent synthesize findings that must cite recorded source ids.

Research output is untrusted data: it is sanitized, injection-scored and only reaches later agents fenced.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import InvalidState, NotFound
from aegis_api.infrastructure.llm.base import LLMError
from aegis_api.models.lab import Mission, ResearchEvent, ResearchSource, ResearchTask
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, feature_service
from aegis_api.services.lab import artifacts, events, knowledge
from aegis_api.services.lab import memory as memory_service
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from aegis_api.services.lab.common import Actor
from aegis_api.services.lab.model_gateway import CallContext, get_gateway
from engines.lab.agents.schemas import LiteratureReview
from engines.lab.enums import LabEventType, ResearchTaskStatus
from engines.lab.security.prompt_injection import detect, sanitize
from engines.lab.state_machines import RESEARCH_TASK

log = structlog.get_logger("aegis.lab.research")

MODES = ("deep_research", "literature_pipeline")


def create(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID | str,
    title: str,
    question: str,
    mode: str,
    mission_id: uuid.UUID | None = None,
    require_plan_approval: bool = True,
) -> ResearchTask:
    from aegis_api.workflows import client as workflow_client

    principal.require("research:run")
    project = get_project(db, principal, project_id)
    if mode not in MODES:
        raise InvalidState(f"mode must be one of {MODES}")
    if mode == "deep_research":
        feature_service.require(db, principal.organization_id, "deep_research")
    if mission_id:
        get_scoped(db, principal, Mission, mission_id, label="Mission")
    task = ResearchTask(
        organization_id=principal.organization_id,
        project_id=project.id,
        mission_id=mission_id,
        title=title[:300],
        question=question,
        mode=mode,
        status=ResearchTaskStatus.CREATED,
        require_plan_approval=require_plan_approval,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(task)
    db.flush()
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="research",
        business_key=f"research:{task.id}",
        payload={"research_task_id": str(task.id)},
        principal=principal,
    )
    task.workflow_run_id = run.id
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.research.created",
        resource_type="research_task",
        resource_id=task.id,
        principal=principal,
        after={"mode": mode, "title": task.title},
    )
    return task


def _event(
    db: Session, task: ResearchTask, event_type: str, data: dict[str, Any], provider_event_id: str | None = None
) -> None:
    task.event_seq += 1
    db.add(
        ResearchEvent(
            organization_id=task.organization_id,
            research_task_id=task.id,
            seq=task.event_seq,
            event_type=event_type[:40],
            provider_event_id=provider_event_id,
            data=data,
        )
    )


def transition(organization_id: uuid.UUID, task_id: uuid.UUID, status: str, **fields: Any) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None:
            raise NotFound("Research task not found")
        task.status = RESEARCH_TASK.ensure(task.status, status)
        for k, v in fields.items():
            setattr(task, k, v)
        _event(
            db, task, f"status.{status}", {k: v for k, v in fields.items() if isinstance(v, str | int | float | bool)}
        )
        return {
            "status": task.status,
            "mode": task.mode,
            "question": task.question,
            "project_id": str(task.project_id),
            "mission_id": str(task.mission_id) if task.mission_id else None,
            "require_plan_approval": task.require_plan_approval,
        }


def get_state(organization_id: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None:
            raise NotFound("Research task not found")
        return {
            "status": task.status,
            "mode": task.mode,
            "question": task.question,
            "title": task.title,
            "plan": task.plan,
            "project_id": str(task.project_id),
            "mission_id": str(task.mission_id) if task.mission_id else None,
            "require_plan_approval": task.require_plan_approval,
            "interaction_id": task.provider_interaction_id,
        }


def save_plan(organization_id: uuid.UUID, task_id: uuid.UUID, plan: dict[str, Any], agent_run_id: str) -> None:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        assert task is not None
        task.plan = {**plan, "agent_run_id": agent_run_id}
        _event(db, task, "plan", {"queries": plan.get("search_queries", [])[:20]})


# --- Deep Research -------------------------------------------------------------------------------------------


def start_deep_research(organization_id: uuid.UUID, task_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None:
            raise NotFound("Research task not found")
        if task.provider_interaction_id:
            return {"interaction_id": task.provider_interaction_id, "resumed": True}
        project_id, mission_id = task.project_id, task.mission_id
        question, plan = task.question, task.plan or {}
    ctx = CallContext(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        research_task_id=task_id,
        actor=actor,
    )
    provider, candidate = get_gateway().research_agent(ctx)
    brief = question
    if plan.get("search_queries"):
        brief += "\n\nFocus areas:\n- " + "\n- ".join(str(q)[:300] for q in plan["search_queries"][:10])
    snapshot = provider.start_agent(
        agent=candidate.model,
        input_text=brief,
        system="Produce a rigorous, well-cited research report. Distinguish peer-reviewed from preprint sources. "
        "State uncertainty explicitly. Do not overclaim.",
    )
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        assert task is not None
        task.provider = candidate.provider
        task.provider_agent = candidate.model
        task.provider_interaction_id = snapshot.id
        task.provider_status = snapshot.status
        task.started_at = task.started_at or utcnow()
        _event(db, task, "provider.started", {"interaction_id": snapshot.id, "agent": candidate.model})
        if mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.RESEARCH_STARTED,
                message=f"Deep Research started: {task.title}",
                data={"research_task_id": str(task_id), "agent": candidate.model},
                actor=actor,
            )
    return {"interaction_id": snapshot.id, "status": snapshot.status}


def poll_deep_research(organization_id: uuid.UUID, task_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None or not task.provider_interaction_id:
            raise NotFound("Research interaction not found")
        interaction_id, project_id, mission_id = task.provider_interaction_id, task.project_id, task.mission_id
        previous_steps = int((task.plan or {}).get("_observed_steps", 0))
    ctx = CallContext(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        research_task_id=task_id,
        actor=actor,
        enforce_budget=False,
    )
    provider, _candidate = get_gateway().research_agent(ctx)
    snapshot = provider.get_interaction(interaction_id)
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        assert task is not None
        task.provider_status = snapshot.status
        steps = len(snapshot.step_types)
        if steps > previous_steps:
            new_types = snapshot.step_types[previous_steps:]
            _event(db, task, "provider.progress", {"steps": steps, "new_step_types": new_types[:20]})
            task.plan = {**(task.plan or {}), "_observed_steps": steps}
            if mission_id:
                events.emit(
                    db,
                    organization_id=organization_id,
                    mission_id=mission_id,
                    project_id=project_id,
                    event_type=LabEventType.SEARCH_PROGRESS,
                    message=f"Deep Research progress: {steps} step(s) ({', '.join(sorted(set(new_types)))[:200]})",
                    data={"research_task_id": str(task_id), "steps": steps},
                    actor=actor,
                    webhook=False,
                )
    return {"status": snapshot.status, "steps": len(snapshot.step_types), "errors": snapshot.errors[:3]}


def complete_deep_research(organization_id: uuid.UUID, task_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None or not task.provider_interaction_id:
            raise NotFound("Research interaction not found")
        if task.status == ResearchTaskStatus.COMPLETED:
            return {"status": task.status, "report_artifact_id": str(task.report_artifact_id)}
        interaction_id, project_id, mission_id, started = (
            task.provider_interaction_id,
            task.project_id,
            task.mission_id,
            task.started_at,
        )
    ctx = CallContext(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        research_task_id=task_id,
        actor=actor,
        enforce_budget=False,
    )
    gateway = get_gateway()
    provider, candidate = gateway.research_agent(ctx)
    snapshot = provider.get_interaction(interaction_id)
    latency = int((utcnow() - started).total_seconds() * 1000) if started else 0
    cost = gateway.record_agent_usage(
        ctx,
        candidate,
        input_tokens=snapshot.usage.input_tokens,
        output_tokens=snapshot.usage.output_tokens,
        latency_ms=latency,
        success=snapshot.status == "completed",
        interaction_id=interaction_id,
        error_code=None if snapshot.status == "completed" else snapshot.status,
    )
    if snapshot.status != "completed":
        transition(
            organization_id,
            task_id,
            ResearchTaskStatus.FAILED,
            error=f"provider status {snapshot.status}: {snapshot.errors[:2]}"[:2000],
            completed_at=utcnow(),
        )
        return {"status": "failed", "provider_status": snapshot.status}
    text, truncated = sanitize(snapshot.text, max_chars=500_000)
    injection = detect(text[:200_000])
    artifact_id, _, digest = artifacts.store(
        organization_id,
        data=text.encode(),
        name=f"deep-research-{task_id}.md",
        kind="research_report",
        content_type="text/markdown",
        project_id=project_id,
        mission_id=mission_id,
        retention_class="evidence",
        created_by=f"{candidate.provider}:{candidate.model}",
        metadata={"interaction_id": interaction_id, "truncated": truncated, "injection": injection.to_dict()},
    )
    hits = [
        {"source_type": "web", "url": c.url, "title": c.title or c.url, "snippet": "", "provider": "deep_research"}
        for c in snapshot.citations
        if c.url
    ]
    with session_scope(organization_id) as db:
        sources = knowledge.record_sources(
            db, organization_id=organization_id, project_id=project_id, hits=hits, research_task_id=task_id
        )
        task = db.get(ResearchTask, task_id)
        assert task is not None
        task.status = RESEARCH_TASK.ensure(task.status, ResearchTaskStatus.COMPLETED)
        task.report = text[:200_000]
        task.report_artifact_id = artifact_id
        task.citations_count = len(sources)
        task.input_tokens = snapshot.usage.input_tokens
        task.output_tokens = snapshot.usage.output_tokens
        task.cost_usd = cost
        task.completed_at = utcnow()
        _event(db, task, "completed", {"citations": len(sources), "sha256": digest, "injection_score": injection.score})
        if mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.RESEARCH_COMPLETED,
                message=f"Deep Research completed with {len(sources)} cited source(s)",
                data={"research_task_id": str(task_id), "artifact_id": str(artifact_id), "citations": len(sources)},
                actor=actor,
            )
        return {
            "status": "completed",
            "report_artifact_id": str(artifact_id),
            "source_ids": [str(s.id) for s in sources],
        }


def cancel_provider(organization_id: uuid.UUID, task_id: uuid.UUID, *, actor: Actor) -> None:
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None or not task.provider_interaction_id:
            return
        interaction_id, project_id = task.provider_interaction_id, task.project_id
    try:
        provider, _ = get_gateway().research_agent(
            CallContext(organization_id=organization_id, project_id=project_id, actor=actor, enforce_budget=False)
        )
        provider.cancel_interaction(interaction_id)
    except LLMError as exc:
        log.warning("deep_research_cancel_failed", kind=exc.kind.value)


# --- literature pipeline ---------------------------------------------------------------------------------------


def literature_search(
    organization_id: uuid.UUID, task_id: uuid.UUID, queries: list[str], *, per_query: int = 5
) -> dict[str, Any]:
    from aegis_api.infrastructure.search import SearchError, search_providers

    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        if task is None:
            raise NotFound("Research task not found")
        project_id, mission_id = task.project_id, task.mission_id
    hits: list[dict[str, Any]] = []
    errors: list[str] = []
    started = time.perf_counter()
    for query in [q for q in queries if q.strip()][:8]:
        for name, provider in search_providers().items():
            try:
                hits.extend(h.to_dict() for h in provider.search(query, limit=per_query))
            except SearchError as exc:
                errors.append(f"{name}: {exc}")
    with session_scope(organization_id) as db:
        sources = knowledge.record_sources(
            db, organization_id=organization_id, project_id=project_id, hits=hits, research_task_id=task_id
        )
        task = db.get(ResearchTask, task_id)
        assert task is not None
        _event(
            db,
            task,
            "search",
            {
                "queries": queries[:8],
                "hits": len(hits),
                "errors": errors[:5],
                "ms": int((time.perf_counter() - started) * 1000),
            },
        )
        if mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.SEARCH_PROGRESS,
                message=f"Literature search: {len(sources)} source(s) from {len(queries[:8])} quer(ies)"
                + (f"; {len(errors)} provider error(s)" if errors else ""),
                data={"research_task_id": str(task_id), "sources": len(sources)},
                webhook=False,
            )
        return {"source_ids": [str(s.id) for s in sources], "errors": errors}


def sources_as_blocks(organization_id: uuid.UUID, source_ids: list[str], limit: int = 25) -> list[dict[str, str]]:
    with session_scope(organization_id) as db:
        rows = db.scalars(
            select(ResearchSource).where(ResearchSource.id.in_([uuid.UUID(s) for s in source_ids[:limit]]))
        ).all()
        return [
            {
                "source_id": str(s.id),
                "content": f"Title: {s.title}\nType: {s.source_type}\nDate: {s.publication_date or 'n.d.'}\n"
                f"DOI: {s.doi or '-'}\nURL: {s.url or '-'}\nAbstract/snippet: {(s.snippet or '')[:1500]}",
            }
            for s in rows
        ]


def complete_literature(
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    review: dict[str, Any],
    *,
    agent_run_id: str,
    actor: Actor,
    source_ids: list[str],
) -> dict[str, Any]:
    parsed = LiteratureReview.model_validate(review)
    known = set(source_ids)
    findings: list[dict[str, Any]] = []
    dropped = 0
    for f in parsed.findings:
        cited = [s for s in f.source_ids if s in known]
        if not cited:
            dropped += 1  # uncited or hallucinated citations are not accepted as findings
            continue
        findings.append({"statement": f.statement, "source_ids": cited, "confidence": f.confidence})
    lines = ["# Literature review\n", f"Queries: {', '.join(parsed.queries)}\n", "## Findings\n"]
    lines += [
        f"- {item['statement']} (sources: {', '.join(item['source_ids'])}; confidence {item['confidence']:.2f})"
        for item in findings
    ]
    lines += [
        "\n## Gaps\n",
        *[f"- {g}" for g in parsed.gaps],
        "\n## Contradictions\n",
        *[f"- {c}" for c in parsed.contradictions],
    ]
    if dropped:
        lines.append(f"\n_{dropped} finding(s) were dropped because they did not cite a retrieved source._")
    text = "\n".join(lines)
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        assert task is not None
        project_id, mission_id = task.project_id, task.mission_id
    artifact_id, _, _ = artifacts.store(
        organization_id,
        data=text.encode(),
        name=f"literature-review-{task_id}.md",
        kind="research_report",
        content_type="text/markdown",
        project_id=project_id,
        mission_id=mission_id,
        retention_class="evidence",
        created_by=f"agent_run:{agent_run_id}",
    )
    with session_scope(organization_id) as db:
        task = db.get(ResearchTask, task_id)
        assert task is not None
        task.status = RESEARCH_TASK.ensure(task.status, ResearchTaskStatus.COMPLETED)
        task.report = text
        task.report_artifact_id = artifact_id
        task.citations_count = len({s for item in findings for s in item["source_ids"]})
        task.completed_at = utcnow()
        _event(db, task, "completed", {"findings": len(findings), "dropped": dropped})
        if mission_id:
            for item in findings[:20]:
                memory_service.propose(
                    db,
                    organization_id=organization_id,
                    actor=actor,
                    scope="mission",
                    category="literature",
                    content=item["statement"],
                    source="literature_review",
                    source_ref={"research_task_id": str(task_id), "source_ids": item["source_ids"]},
                    project_id=project_id,
                    mission_id=mission_id,
                    confidence=float(item["confidence"]),
                    provenance={"agent_run_id": agent_run_id},
                )
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.RESEARCH_COMPLETED,
                message=f"Literature review completed: {len(findings)} cited finding(s)"
                + (f", {dropped} uncited dropped" if dropped else ""),
                data={"research_task_id": str(task_id), "artifact_id": str(artifact_id)},
                actor=actor,
            )
        return {"status": "completed", "findings": findings, "report_artifact_id": str(artifact_id)}


def list_tasks(
    db: Session, principal: Principal, *, project_id: uuid.UUID | None = None, mission_id: uuid.UUID | None = None
) -> Select[ResearchTask]:
    stmt = select(ResearchTask).where(ResearchTask.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(ResearchTask.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(ResearchTask.project_id == project_id)
    if mission_id:
        stmt = stmt.where(ResearchTask.mission_id == mission_id)
    return stmt.order_by(ResearchTask.created_at.desc())


def task_events(db: Session, task: ResearchTask, after: int = 0) -> list[ResearchEvent]:
    return list(
        db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.research_task_id == task.id, ResearchEvent.seq > after)
            .order_by(ResearchEvent.seq)
            .limit(500)
        ).all()
    )


def source_count(db: Session, task: ResearchTask) -> int:
    return int(db.scalar(select(func.count(ResearchSource.id)).where(ResearchSource.research_task_id == task.id)) or 0)
