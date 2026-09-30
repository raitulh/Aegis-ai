"""Research tasks: creation (quota, feature, consent, policy, approvals, budget), lifecycle and execution.

Creating a task never runs research in the request thread: the task is persisted and a ``ResearchWorkflow``
is launched after commit (when the workflow engine has that flow registered; otherwise the task stays
``CREATED`` until one is). Execution happens in activities (:mod:`aegis_api.lab.research.activities`):

* ``literature_search`` — OpenAlex/arXiv search → de-duplicated sources with deterministic trust metadata;
* ``web_research`` — a grounded model call (Google Search built-in tool via the LLM gateway) → report + cited
  web sources;
* ``deep_research`` — Gemini Deep Research (see :mod:`aegis_api.lab.research.deep_research`).

External calls are made outside database transactions; state changes happen in short units of work.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import ModelUnavailable, PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.features import ensure_feature
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import allows_external_llm
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_by_id, paginate_keyset
from aegis_api.lab.llm.providers.gemini.interactions import TERMINAL_STATUSES as PROVIDER_TERMINAL_STATUSES
from aegis_api.lab.models import Mission, ResearchEvent, ResearchTask
from aegis_api.lab.research import literature
from aegis_api.lab.research.schemas import ResearchTaskCreate
from aegis_api.lab.research.sources import SourceInput, upsert_sources
from aegis_api.lab.usage.recorder import increment_mission_spend
from engines.lab.prompt_security import DATA_HANDLING_POLICY, sanitize_untrusted
from engines.lab.states import MemoryCategory, ResearchTaskStatus, assert_transition

log = structlog.get_logger("aegis.lab.research")

R = ResearchTaskStatus
TERMINAL_STATUSES = frozenset({R.COMPLETED, R.FAILED, R.CANCELLED})
RESEARCH_KINDS = ("literature_search", "deep_research", "web_research")
WORKFLOW_KIND = "ResearchWorkflow"
MAX_EVENT_TEXT = 500
MAX_REPORT_CHARS = 500_000
MAX_SUMMARY_CHARS = 2000
RESEARCH_CANCELLED = "RESEARCH_CANCELLED"
WEB_RESEARCH_SYSTEM = (
    "You are the research assistant of an AI scientist lab. Answer the research question with a concise, "
    "well-structured report grounded in current, authoritative web sources (peer-reviewed papers, preprints, "
    "official datasets and documentation). Cite every factual statement. State uncertainty, disagreements and "
    "open problems explicitly. Never invent sources, numbers or results.\n\n" + DATA_HANDLING_POLICY
)


# --- helpers --------------------------------------------------------------------------------------
def _uuid(value: Any, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{label} not found") from exc


def add_event(
    db: Session,
    task: ResearchTask,
    type_: str,
    payload: dict[str, Any] | None = None,
    *,
    provider_event_id: str | None = None,
) -> ResearchEvent:
    """Append a research event (``seq`` allocated under a per-task advisory lock)."""
    advisory_xact_lock(db, f"research-events:{task.id}")
    seq = int(db.scalar(select(func.max(ResearchEvent.seq)).where(ResearchEvent.research_task_id == task.id)) or 0)
    event = ResearchEvent(
        id=uuid.uuid4(),
        organization_id=task.organization_id,
        research_task_id=task.id,
        seq=seq + 1,
        type=type_[:48],
        payload=payload or {},
        provider_event_id=(provider_event_id or None) and provider_event_id[:255],
    )
    db.add(event)
    db.flush()
    return event


def last_event(db: Session, task: ResearchTask, type_: str) -> ResearchEvent | None:
    return db.scalar(
        select(ResearchEvent)
        .where(ResearchEvent.research_task_id == task.id, ResearchEvent.type == type_)
        .order_by(ResearchEvent.seq.desc())
        .limit(1)
    )


def emit_task_event(db: Session, actor: Actor, task: ResearchTask, type_: str, **payload: Any) -> None:
    emit(
        db,
        organization_id=task.organization_id,
        type=type_,
        payload={"research_task_id": str(task.id), "kind": task.kind, "status": task.status, **payload},
        mission_id=task.mission_id,
        project_id=task.project_id,
        workspace_id=task.workspace_id,
        subject_type="research_task",
        subject_id=task.id,
        actor=actor,
    )


def transition(db: Session, task: ResearchTask, target: str) -> None:
    assert_transition("research_task", task.status, target)
    task.status = target
    now = utcnow()
    if target in (R.RUNNING, R.PLANNING) and task.started_at is None:
        task.started_at = now
    if target in TERMINAL_STATUSES:
        task.completed_at = now
    db.flush()


def lock_task(db: Session, actor: Actor, task_id: uuid.UUID | str) -> ResearchTask:
    task = get_owned(db, ResearchTask, task_id, actor, label="Research task")
    db.flush()
    return db.execute(
        select(ResearchTask)
        .where(ResearchTask.id == task.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def fail_task(db: Session, actor: Actor, task: ResearchTask, error: str) -> None:
    """Mark a non-terminal task FAILED and announce it (idempotent)."""
    if task.status in TERMINAL_STATUSES:
        return
    transition(db, task, R.FAILED)
    task.error = error[:4000]
    add_event(db, task, "failed", {"error": task.error[:MAX_EVENT_TEXT]})
    emit_task_event(db, actor, task, EventType.RESEARCH_FAILED, error=task.error[:MAX_EVENT_TEXT])


# --- creation -------------------------------------------------------------------------------------
def deep_research_configured() -> bool:
    return bool(get_settings().gemini_api_key)


def _launch_workflow(db: Session, actor: Actor, task: ResearchTask) -> None:
    """Start the ResearchWorkflow after commit (skipped when the workflow engine has no such flow yet)."""
    try:
        from aegis_api.lab.workflows.definitions import has_flow
        from aegis_api.lab.workflows.launcher import launch_workflow
    except ImportError:
        log.warning("research_workflow_unavailable", research_task_id=str(task.id), reason="engine not installed")
        return
    if not has_flow(WORKFLOW_KIND):
        log.info("research_workflow_unavailable", research_task_id=str(task.id), reason="flow not registered")
        return
    run = launch_workflow(
        db,
        actor,
        WORKFLOW_KIND,
        subject_type="research_task",
        subject_id=task.id,
        input={
            "research_task_id": str(task.id),
            "project_id": str(task.project_id),
            "mission_id": str(task.mission_id) if task.mission_id else None,
            "kind": task.kind,
            "queries": [task.query] if task.kind == "literature_search" else [],
            "approval_id": str(task.approval_id) if task.approval_id else None,
        },
        project_id=task.project_id,
        mission_id=task.mission_id,
    )
    task.workflow_run_id = run.id
    db.flush()


def create_research_task(db: Session, actor: Actor, data: ResearchTaskCreate) -> ResearchTask:
    """Validate, authorize and persist a research task, then launch its workflow (see module docstring)."""
    project = load_project(db, actor, data.project_id, "research:run")
    mission: Mission | None = None
    if data.mission_id is not None:
        mission = get_owned(db, Mission, data.mission_id, actor, label="Mission")
        if mission.project_id != project.id:
            raise ValidationFailed("The mission belongs to a different project")
    params = data.parameters
    if data.kind == "literature_search":
        literature.validate_query(data.query)
        literature.validate_sources(params.sources)
    from aegis_api.lab.governance.quotas import check_quota

    check_quota(db, actor.organization_id, "max_research_jobs", increment=1)

    decision = None
    if data.kind in ("deep_research", "web_research") and not allows_external_llm(db, actor.organization_id):
        raise PolicyDenied(
            f"{data.kind.replace('_', ' ').capitalize()} sends the research question to an external model provider. "
            "Enable external model processing for the organization (settings → data_processing.allow_external_llm)."
        )
    if data.kind == "deep_research":
        ensure_feature(db, actor.organization_id, "deep_research")
        if not deep_research_configured():
            raise ModelUnavailable("Gemini Deep Research is not configured (GEMINI_API_KEY is not set)")
        from aegis_api.lab.governance.policies import evaluate_policy

        context: dict[str, Any] = {"external_provider": "gemini"}
        if params.estimated_cost_usd is not None:
            context["estimated_cost_usd"] = params.estimated_cost_usd
        decision = evaluate_policy(
            db,
            actor,
            "research.deep_research",
            context,
            project_id=project.id if mission is None else None,
            mission=mission,
        )
        if decision.denied:
            raise PolicyDenied("; ".join(decision.reasons) or "Deep research is denied by policy")
    if mission is not None:
        from aegis_api.lab.governance.budgets import enforce_budget

        enforce_budget(db, actor, mission, "research", count=1, estimated_usd=params.estimated_cost_usd or 0)

    collaborative = data.kind == "deep_research" and params.collaborative_planning
    task = ResearchTask(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission else None,
        kind=data.kind,
        title=data.title,
        query=data.query,
        parameters=params.model_dump(mode="json", exclude_none=True),
        plan_status="AWAITING_PLAN" if collaborative else "NOT_REQUIRED",
        status=R.CREATED,
        provider="gemini" if data.kind in ("deep_research", "web_research") else None,
        provider_agent=(params.agent or get_settings().gemini_deep_research_agent)
        if data.kind == "deep_research"
        else None,
        agent_run_id=actor.agent_run_id,
        created_by_id=actor.user_id,
    )
    db.add(task)
    db.flush()
    increment_mission_spend(db, mission.id if mission else None, research_tasks=1)
    add_event(db, task, "created", {"kind": task.kind, "by": actor.as_dict()})
    audit(
        db,
        actor,
        AuditAction.RESEARCH_STARTED,
        "research_task",
        task.id,
        after={
            "kind": task.kind,
            "title": task.title,
            "project_id": str(project.id),
            "mission_id": str(mission.id) if mission else None,
            "parameters": task.parameters,
        },
    )
    _launch_workflow(db, actor, task)
    if decision is not None and decision.requires_approval:
        from aegis_api.lab.governance.approvals import request_approval

        approval = request_approval(
            db,
            actor,
            action="research.deep_research",
            subject_type="research_task",
            subject_id=task.id,
            title=f"Deep research: {task.title[:200]}",
            payload={
                "query": task.query[:1000],
                "agent": task.provider_agent,
                "collaborative_planning": collaborative,
                "reasons": list(decision.reasons),
            },
            risk_level=mission.risk_level if mission else "MEDIUM",
            estimated_cost_usd=Decimal(str(params.estimated_cost_usd or 0)),
            decision=decision,
            project_id=project.id,
            mission_id=mission.id if mission else None,
            workflow_run_id=task.workflow_run_id,
            signal_name="approval" if task.workflow_run_id else None,
            required_permission=decision.approver_permission or "approval:decide",
        )
        task.approval_id = approval.id
        add_event(db, task, "approval_requested", {"approval_id": str(approval.id)})
        db.flush()
    return task


# --- reads ----------------------------------------------------------------------------------------
def get_research_task(db: Session, actor: Actor, task_id: uuid.UUID | str) -> ResearchTask:
    task = get_owned(db, ResearchTask, task_id, actor, label="Research task")
    load_project(db, actor, task.project_id, "research:read")
    return task


def list_research_tasks(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    status: str | None = None,
    kind: str | None = None,
    mapper: Any = None,
) -> CursorPage[Any]:
    stmt = select(ResearchTask).where(ResearchTask.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "research:read")
        stmt = stmt.where(ResearchTask.project_id == project.id)
    else:
        actor.require("research:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(ResearchTask.project_id.in_(visible))
    if mission_id is not None:
        stmt = stmt.where(ResearchTask.mission_id == _uuid(mission_id, "Mission"))
    if status is not None:
        if status not in R.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(R.__members__)}")
        stmt = stmt.where(ResearchTask.status == status)
    if kind is not None:
        if kind not in RESEARCH_KINDS:
            raise ValidationFailed(f"kind must be one of {', '.join(RESEARCH_KINDS)}")
        stmt = stmt.where(ResearchTask.kind == kind)
    return paginate_keyset(
        db, stmt, params, time_col=ResearchTask.created_at, id_col=ResearchTask.id, mapper=mapper or (lambda t: t)
    )


def list_research_events(
    db: Session, actor: Actor, task_id: uuid.UUID | str, params: CursorParams, *, mapper: Any = None
) -> CursorPage[Any]:
    task = get_research_task(db, actor, task_id)
    stmt = select(ResearchEvent).where(ResearchEvent.research_task_id == task.id)
    return paginate_by_id(db, stmt, params, id_col=ResearchEvent.seq, mapper=mapper or (lambda e: e))


# --- cancellation ---------------------------------------------------------------------------------
def cancel_research_task(
    db: Session, actor: Actor, task_id: uuid.UUID | str, *, reason: str | None = None
) -> ResearchTask:
    """Cancel a task: pending approval withdrawn, workflow cancelled, provider interaction cancelled after commit."""
    task = lock_task(db, actor, task_id)
    load_project(db, actor, task.project_id, "research:run")
    if task.status == R.CANCELLED:
        return task
    transition(db, task, R.CANCELLED)
    clean_reason = " ".join((reason or "").split())[:2000] or None
    task.plan = {**(task.plan or {}), "_pending_action": None} if task.plan else task.plan
    add_event(db, task, "cancelled", {"reason": clean_reason, "by": actor.as_dict()})
    if task.approval_id is not None:
        _withdraw_approval(db, actor, task, clean_reason)
    if task.workflow_run_id is not None:
        _cancel_workflow(db, actor, task, task.workflow_run_id)
    interaction_id = task.provider_interaction_id
    provider_done = task.provider_status in PROVIDER_TERMINAL_STATUSES
    if task.kind == "deep_research" and interaction_id and not provider_done:
        from aegis_api.lab.core.events import after_commit
        from aegis_api.lab.research.deep_research import cancel_provider_interaction

        organization_id = task.organization_id
        after_commit(db, lambda: cancel_provider_interaction(organization_id, task.id, interaction_id))
    audit(db, actor, RESEARCH_CANCELLED, "research_task", task.id, after={"reason": clean_reason})
    emit_task_event(db, actor, task, EventType.RESEARCH_FAILED, cancelled=True, reason=clean_reason)
    return task


def _withdraw_approval(db: Session, actor: Actor, task: ResearchTask, reason: str | None) -> None:
    from aegis_api.lab.governance.approvals import cancel_approval
    from aegis_api.lab.models import Approval
    from engines.lab.states import ApprovalStatus

    approval = db.get(Approval, task.approval_id)
    if approval is None or approval.status != ApprovalStatus.PENDING:
        return
    try:
        with db.begin_nested():
            cancel_approval(
                db, Actor.system(task.organization_id, "system:research-cancel"), approval.id, reason=reason
            )
    except Exception:
        log.warning("research_approval_withdraw_failed", research_task_id=str(task.id), exc_info=True)


def _cancel_workflow(db: Session, actor: Actor, task: ResearchTask, workflow_run_id: uuid.UUID) -> None:
    try:
        from aegis_api.lab.workflows.launcher import cancel_workflow
    except ImportError:
        return
    try:
        with db.begin_nested():
            cancel_workflow(db, actor, workflow_run_id)
    except Exception:
        log.warning("research_workflow_cancel_failed", research_task_id=str(task.id), exc_info=True)


# --- literature search (worker side) --------------------------------------------------------------
@dataclass
class SearchOutcome:
    research_task_id: str
    status: str
    source_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_task_id": self.research_task_id,
            "status": self.status,
            "source_ids": self.source_ids,
            "count": len(self.source_ids),
            "warnings": self.warnings,
        }


def activity_key(workflow_run_id: uuid.UUID | None, query: str, sources: list[str], limit: int) -> str:
    raw = f"{workflow_run_id}|{query}|{','.join(sources)}|{limit}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stored_outcome(db: Session, task: ResearchTask) -> SearchOutcome:
    event = last_event(db, task, "search_completed")
    payload = event.payload if event is not None else {}
    return SearchOutcome(
        research_task_id=str(task.id),
        status=task.status,
        source_ids=[str(s) for s in payload.get("source_ids") or []],
        warnings=[str(w) for w in payload.get("warnings") or []],
    )


def ensure_literature_task(
    db: Session,
    actor: Actor,
    *,
    project_id: uuid.UUID | str,
    mission_id: uuid.UUID | str | None,
    query: str,
    sources: list[str],
    limit: int,
    workflow_run_id: uuid.UUID | None,
) -> ResearchTask:
    """The task a workflow-driven search writes into (look-before-create keyed by run + query)."""
    project = load_project(db, actor, project_id, "research:run")
    mission = get_owned(db, Mission, mission_id, actor, label="Mission") if mission_id else None
    if mission is not None and mission.project_id != project.id:
        raise ValidationFailed("The mission belongs to a different project")
    key = activity_key(workflow_run_id, query, sources, limit)
    advisory_xact_lock(db, f"research-literature:{project.id}:{key}")
    existing = db.scalar(
        select(ResearchTask).where(
            ResearchTask.project_id == project.id,
            ResearchTask.kind == "literature_search",
            ResearchTask.parameters["activity_key"].astext == key,
        )
    )
    if existing is not None:
        return existing
    task = ResearchTask(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission else None,
        kind="literature_search",
        title=f"Literature search: {query[:200]}",
        query=query,
        parameters={"sources": sources, "limit": limit, "activity_key": key},
        status=R.CREATED,
        workflow_run_id=workflow_run_id,
        agent_run_id=actor.agent_run_id,
        created_by_id=actor.user_id,
    )
    db.add(task)
    db.flush()
    increment_mission_spend(db, mission.id if mission else None, research_tasks=1)
    add_event(db, task, "created", {"kind": task.kind, "by": actor.as_dict()})
    return task


def run_literature_search(
    actor: Actor,
    task_id: uuid.UUID | str,
    *,
    heartbeat: Callable[[Any], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> SearchOutcome:
    """Execute a literature-search task (idempotent: a finished task returns its stored outcome)."""
    with tenant_uow(actor) as db:
        task = lock_task(db, actor, task_id)
        if task.kind != "literature_search":
            raise ValidationFailed("Not a literature-search task")
        load_project(db, actor, task.project_id, "research:run")
        if task.status in TERMINAL_STATUSES:
            return _stored_outcome(db, task)
        if task.status == R.CREATED:
            transition(db, task, R.RUNNING)
            add_event(db, task, "search_started", {"sources": task.parameters.get("sources")})
            emit_task_event(db, actor, task, EventType.RESEARCH_STARTED)
        query = task.query
        sources = task.parameters.get("sources")
        limit = int(task.parameters.get("limit") or 20)
    if is_cancelled is not None and is_cancelled():
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status not in TERMINAL_STATUSES:
                transition(db, task, R.CANCELLED)
                add_event(db, task, "cancelled", {"reason": "workflow cancelled"})
            return _stored_outcome(db, task)
    if heartbeat is not None:
        heartbeat({"stage": "searching", "research_task_id": str(task_id)})
    result = literature.search_literature(query, sources, limit)
    if heartbeat is not None:
        heartbeat({"stage": "storing", "records": len(result.records)})
    with tenant_uow(actor) as db:
        task = lock_task(db, actor, task_id)
        if task.status in TERMINAL_STATUSES:
            return _stored_outcome(db, task)
        per_source = {o.source: {"count": o.count, "error": o.error} for o in result.outcomes}
        emit_task_event(db, actor, task, EventType.SEARCH_PROGRESS, per_source=per_source)
        if result.all_failed:
            add_event(db, task, "search_completed", {"source_ids": [], "warnings": result.warnings})
            fail_task(db, actor, task, "All literature sources failed: " + "; ".join(result.warnings))
            return _stored_outcome(db, task)
        rows = upsert_sources(
            db,
            actor,
            task.project_id,
            [SourceInput.from_paper(r) for r in result.records],
            research_task_id=task.id,
            discovered_by="search",
        )
        source_ids = list(dict.fromkeys(str(r.id) for r in rows))
        add_event(
            db,
            task,
            "search_completed",
            {"source_ids": source_ids, "warnings": result.warnings, "per_source": per_source},
        )
        task.source_count = len(source_ids)
        counts = ", ".join(f"{o.source}: {o.count}" for o in result.outcomes if o.error is None)
        task.summary = f"{len(source_ids)} source(s) ({counts})" + (" — partial results" if result.warnings else "")
        transition(db, task, R.COMPLETED)
        emit_task_event(db, actor, task, EventType.RESEARCH_COMPLETED, source_count=len(source_ids))
        return _stored_outcome(db, task)


# --- web research (worker side) -------------------------------------------------------------------
def run_web_research(actor: Actor, task_id: uuid.UUID | str) -> SearchOutcome:
    """Grounded web research through the LLM gateway (Google Search tool). Idempotent per task."""
    from aegis_api.lab.llm.gateway import LLMCallContext, get_gateway
    from aegis_api.lab.llm.schemas import LLMMessage, LLMRequest, TaskType

    with tenant_uow(actor) as db:
        task = lock_task(db, actor, task_id)
        if task.kind != "web_research":
            raise ValidationFailed("Not a web-research task")
        load_project(db, actor, task.project_id, "research:run")
        if task.status in TERMINAL_STATUSES:
            return _stored_outcome(db, task)
        if task.status == R.CREATED:
            transition(db, task, R.RUNNING)
            emit_task_event(db, actor, task, EventType.RESEARCH_STARTED)
        ctx = LLMCallContext.for_actor(
            actor, project_id=task.project_id, mission_id=task.mission_id, research_task_id=task.id
        )
        request = LLMRequest(
            task_type=TaskType.RESEARCH,
            system=WEB_RESEARCH_SYSTEM,
            messages=[LLMMessage(role="user", content=f"Research question: {task.query}")],
            builtin_tools=["google_search"],
            tier="default",
        )
    try:
        response = get_gateway().generate(request, ctx)
    except Exception as exc:
        retryable = bool(getattr(exc, "retryable", False))
        if not retryable:
            with tenant_uow(actor) as db:
                task = lock_task(db, actor, task_id)
                fail_task(db, actor, task, f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}"[:1000])
        raise
    with tenant_uow(actor) as db:
        task = lock_task(db, actor, task_id)
        if task.status in TERMINAL_STATUSES:
            return _stored_outcome(db, task)
        report = sanitize_untrusted(response.text, MAX_REPORT_CHARS)
        rows = (
            upsert_sources(
                db,
                actor,
                task.project_id,
                [SourceInput.from_citation(c) for c in response.citations[:100]],
                research_task_id=task.id,
                discovered_by="search",
            )
            if response.citations
            else []
        )
        source_ids = list(dict.fromkeys(str(r.id) for r in rows))
        task.report = report
        task.summary = " ".join(report.split())[:MAX_SUMMARY_CHARS] or None
        task.source_count = len(source_ids)
        task.input_tokens += response.usage.input_tokens
        task.output_tokens += response.usage.output_tokens + response.usage.thinking_tokens
        task.cost_usd = (task.cost_usd or Decimal("0")) + (response.cost_usd or Decimal("0"))
        add_event(
            db,
            task,
            "search_completed",
            {"source_ids": source_ids, "warnings": [], "model": response.model, "provider": response.provider},
        )
        record_report_memory(db, actor, task, report, source_ids=source_ids, origin="web_research")
        transition(db, task, R.COMPLETED)
        emit_task_event(db, actor, task, EventType.RESEARCH_COMPLETED, source_count=len(source_ids))
        return _stored_outcome(db, task)


def record_report_memory(
    db: Session,
    actor: Actor,
    task: ResearchTask,
    report: str,
    *,
    source_ids: list[str],
    origin: str,
    artifact_version_id: uuid.UUID | None = None,
) -> str | None:
    """Store a research report summary as LITERATURE memory (external content: the write policy decides
    its status — mission scope may be ACTIVE-but-untrusted, broader scopes need review). Returns the id or
    ``None`` when the actor may not write memory (recorded as a research event)."""
    from aegis_api.errors import Forbidden
    from aegis_api.lab.knowledge.memory import write_memory
    from aegis_api.lab.knowledge.schemas import MemoryWrite

    summary = " ".join(report.split())[:MAX_SUMMARY_CHARS]
    if not summary:
        return None
    try:
        with db.begin_nested():
            memory = write_memory(
                db,
                actor,
                MemoryWrite(
                    category=MemoryCategory.LITERATURE,
                    scope="mission" if task.mission_id else "project",
                    project_id=str(task.project_id),
                    mission_id=str(task.mission_id) if task.mission_id else None,
                    title=f"{origin.replace('_', ' ').capitalize()}: {task.title}"[:500],
                    content=summary,
                    source_type="external",
                    source_ref={
                        "research_task_id": str(task.id),
                        "source_ids": source_ids[:50],
                        "artifact_version_id": str(artifact_version_id) if artifact_version_id else None,
                    },
                    confidence=0.5,
                    provenance={
                        "source_uri": f"research-task:{task.id}",
                        "retrieved_at": utcnow().isoformat(),
                        "provider": task.provider,
                        "provider_agent": task.provider_agent,
                        "origin": origin,
                    },
                    tags=["literature", origin],
                ),
            )
    except Forbidden as exc:
        add_event(db, task, "memory_skipped", {"reason": exc.message[:MAX_EVENT_TEXT]})
        return None
    add_event(db, task, "memory_written", {"memory_id": str(memory.id), "status": memory.status})
    return str(memory.id)
