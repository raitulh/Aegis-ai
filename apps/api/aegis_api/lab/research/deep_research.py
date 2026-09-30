"""Gemini Deep Research: background interactions, collaborative plan review, polling and finalization.

Lifecycle of a ``deep_research`` task (``engines.lab.states`` research_task machine)::

    CREATED ──start──▶ RUNNING ─────────────────────────────▶ COMPLETED (finalize)
       │                  ▲                                   ╲─▶ FAILED / CANCELLED
       └─start (collaborative)─▶ PLANNING ─plan ready─▶ PLAN_REVIEW ─approve─▶ APPROVED ─advance─▶ RUNNING
                                    ▲                        │
                                    └────────revise──────────┘

* Everything is keyed by ``provider_interaction_id`` and persisted, so polling survives worker restarts.
* Provider calls (create/get/cancel) are made outside database transactions. A create is *claimed* first
  (``provider_status = "submitting"`` + timestamp, committed) so a crashed or concurrent worker cannot start
  the same paid interaction twice; a stale claim (> :data:`CLAIM_STALE_SECONDS`) may be re-taken.
* Plan decisions are human-only (``research:run``). The follow-up interaction (``previous_interaction_id``)
  is created by :meth:`DeepResearchService.advance` — after commit of the decision and/or by the workflow's
  next ``deep_research_start``/``deep_research_poll`` activity (whichever comes first; the claim de-duplicates).
* Thought content is never stored: the interactions client exposes only compact step summaries, recorded as
  ``thought_summary`` research events for ``thought`` steps.
* Usage is recorded once per interaction (``model_usage`` with ``task_type="research"``), with cost from the
  configured price for ``gemini:<agent>`` (left empty — never invented — when no price is configured).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.access import load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import ApprovalRequired, TransientError
from aegis_api.lab.core.events import EventType, after_commit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.llm.costs import call_cost, load_configs, resolve_price
from aegis_api.lab.llm.errors import LLMError
from aegis_api.lab.llm.providers.gemini.interactions import (
    TERMINAL_STATUSES as PROVIDER_TERMINAL_STATUSES,
)
from aegis_api.lab.llm.providers.gemini.interactions import (
    GeminiInteractionsClient,
    InteractionSnapshot,
)
from aegis_api.lab.llm.schemas import Citation
from aegis_api.lab.models import Approval, ResearchEvent, ResearchTask
from aegis_api.lab.research.service import (
    MAX_EVENT_TEXT,
    MAX_REPORT_CHARS,
    MAX_SUMMARY_CHARS,
    TERMINAL_STATUSES,
    add_event,
    emit_task_event,
    fail_task,
    last_event,
    lock_task,
    record_report_memory,
    transition,
)
from aegis_api.lab.research.sources import SourceInput, upsert_sources
from aegis_api.lab.usage.recorder import record_model_usage
from engines.lab.prompt_security import sanitize_untrusted
from engines.lab.states import ApprovalStatus, ResearchTaskStatus

# Importing the knowledge memory module registers its ``memory.promote`` approval hook; this module is one
# of the hook modules the approvals service loads.
from aegis_api.lab.knowledge import memory as _memory_hooks  # noqa: F401  isort: skip

log = structlog.get_logger("aegis.lab.research.deep")

R = ResearchTaskStatus
SUBMITTING = "submitting"
CLAIM_STALE_SECONDS = 600
MAX_RUNTIME_SECONDS = 60 * 60
MAX_STEP_EVENTS_PER_POLL = 200
MAX_PLAN_CHARS = 60_000
MAX_CITATION_SOURCES = 100
MAX_FEEDBACK_CHARS = 4000
APPROVAL_ACTION = "research.deep_research"
PLAN_APPROVED = "RESEARCH_PLAN_APPROVED"
PLAN_REVISION_REQUESTED = "RESEARCH_PLAN_REVISION_REQUESTED"
EXECUTE_MESSAGE = "The research plan is approved. Carry out the research now and write the full report."
REVISE_MESSAGE = "Please revise the research plan based on this feedback:\n\n{feedback}"


def default_client() -> GeminiInteractionsClient:
    """The Interactions client (raises ``LLMUnavailableError`` when Gemini is not configured)."""
    return GeminiInteractionsClient()


@dataclass
class StartResult:
    research_task_id: str
    interaction_id: str | None
    status: str | None
    task_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_task_id": self.research_task_id,
            "interaction_id": self.interaction_id,
            "status": self.status,
            "task_status": self.task_status,
        }


@dataclass
class PollResult:
    research_task_id: str
    status: str | None
    task_status: str
    terminal: bool
    plan_ready: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_task_id": self.research_task_id,
            "status": self.status,
            "task_status": self.task_status,
            "terminal": self.terminal,
            "plan_ready": self.plan_ready,
        }


@dataclass
class FinalizeResult:
    research_task_id: str
    status: str
    report_artifact_id: str | None = None
    source_ids: list[str] = field(default_factory=list)
    memory_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_task_id": self.research_task_id,
            "status": self.status,
            "report_artifact_id": self.report_artifact_id,
            "source_ids": self.source_ids,
            "memory_id": self.memory_id,
        }


# --- plan state helpers ---------------------------------------------------------------------------
def _pending(task: ResearchTask) -> dict[str, Any] | None:
    action = (task.plan or {}).get("_pending_action")
    return action if isinstance(action, dict) else None


def _set_plan(task: ResearchTask, **updates: Any) -> None:
    task.plan = {**(task.plan or {}), **updates}


def _claim_active(task: ResearchTask) -> bool:
    if task.provider_status != SUBMITTING or task.last_polled_at is None:
        return False
    return utcnow() - task.last_polled_at < timedelta(seconds=CLAIM_STALE_SECONDS)


def _claim(task: ResearchTask) -> None:
    if _claim_active(task):
        raise TransientError("Another worker is submitting this deep research interaction")
    task.provider_status = SUBMITTING
    task.last_polled_at = utcnow()


def _release_claim(task: ResearchTask, previous_status: str | None) -> None:
    if task.provider_status == SUBMITTING:
        task.provider_status = previous_status


def _check_approval(db: Session, actor: Actor, task: ResearchTask) -> None:
    """Raise ``ApprovalRequired`` while the task's policy approval is pending; cancel on a refusal."""
    if task.approval_id is None:
        return
    approval = db.get(Approval, task.approval_id)
    if approval is None or approval.status == ApprovalStatus.APPROVED:
        return
    if approval.status == ApprovalStatus.PENDING:
        raise ApprovalRequired("Deep research is waiting for human approval", approval_id=str(approval.id))
    if task.status not in TERMINAL_STATUSES:
        transition(db, task, R.CANCELLED)
        add_event(db, task, "approval_refused", {"approval_status": approval.status})
        emit_task_event(db, actor, task, EventType.RESEARCH_FAILED, cancelled=True, reason="approval refused")


def _record_usage(db: Session, task: ResearchTask, snapshot: InteractionSnapshot) -> None:
    """Record the interaction's usage once (idempotent via a ``usage`` research event)."""
    marker = f"usage:{snapshot.id}"
    exists = db.scalar(
        select(ResearchEvent.id).where(
            ResearchEvent.research_task_id == task.id, ResearchEvent.provider_event_id == marker
        )
    )
    if exists is not None:
        return
    model = snapshot.agent or task.provider_agent or "deep-research"
    price = resolve_price("gemini", model, configs=load_configs(db, task.organization_id))
    cost = call_cost(price, snapshot.usage, local=False)
    success = snapshot.status == "completed"
    record_model_usage(
        db,
        organization_id=task.organization_id,
        provider="gemini",
        model=model,
        task_type="research",
        input_tokens=snapshot.usage.input_tokens,
        output_tokens=snapshot.usage.output_tokens,
        cached_tokens=snapshot.usage.cached_tokens,
        thinking_tokens=snapshot.usage.thinking_tokens,
        cost_usd=cost.usd,
        cost_estimated=cost.estimated,
        cost_basis=cost.basis,
        success=success,
        error_code=None if success else f"interaction_{snapshot.status}"[:64],
        request_id=snapshot.id,
        project_id=task.project_id,
        mission_id=task.mission_id,
        research_task_id=task.id,
        route_reason="deep research agent",
    )
    task.input_tokens += snapshot.usage.input_tokens
    task.output_tokens += snapshot.usage.output_tokens + snapshot.usage.thinking_tokens
    task.cost_usd = (task.cost_usd or Decimal("0")) + (cost.usd or Decimal("0"))
    add_event(
        db,
        task,
        "usage",
        {
            "input_tokens": snapshot.usage.input_tokens,
            "output_tokens": snapshot.usage.output_tokens,
            "thinking_tokens": snapshot.usage.thinking_tokens,
            "cost_usd": str(cost.usd) if cost.usd is not None else None,
            "cost_basis": cost.basis,
        },
        provider_event_id=marker,
    )


def _record_steps(db: Session, task: ResearchTask, snapshot: InteractionSnapshot) -> int:
    """Research events for steps not seen before (compact summaries; thoughts as ``thought_summary``)."""
    prefix = f"{snapshot.id}:step:"
    recorded = int(
        db.scalar(
            select(func.count(ResearchEvent.id)).where(
                ResearchEvent.research_task_id == task.id, ResearchEvent.provider_event_id.like(f"{prefix}%")
            )
        )
        or 0
    )
    new_steps = snapshot.steps[recorded : recorded + MAX_STEP_EVENTS_PER_POLL]
    for offset, step in enumerate(new_steps):
        kind = str(step.get("type") or "unknown")[:40]
        add_event(
            db,
            task,
            "thought_summary" if kind == "thought" else "step",
            {"step_type": kind, "summary": str(step.get("summary") or "")[:MAX_EVENT_TEXT], "index": recorded + offset},
            provider_event_id=f"{prefix}{recorded + offset}",
        )
    return len(new_steps)


def _interaction_started_at(db: Session, task: ResearchTask) -> Any:
    event = last_event(db, task, "interaction_created")
    return event.created_at if event is not None else task.started_at


# --- the service ----------------------------------------------------------------------------------
class DeepResearchService:
    """Worker-side operations; each method uses short tenant transactions around the provider calls."""

    def __init__(self, client_factory: Callable[[], GeminiInteractionsClient] | None = None) -> None:
        self._client_factory = client_factory

    def _client(self) -> GeminiInteractionsClient:
        return (self._client_factory or default_client)()

    # -- start ---------------------------------------------------------------------------------
    def start(self, actor: Actor, task_id: uuid.UUID | str) -> StartResult:
        """Create the initial interaction (planning when collaborative). Idempotent."""
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.kind != "deep_research":
                raise ValidationFailed("Not a deep-research task")
            load_project(db, actor, task.project_id, "research:run")
            if task.status in TERMINAL_STATUSES:
                return self._result(task)
            if _pending(task) is not None:
                needs_advance = True
            elif task.provider_interaction_id is not None or task.status != R.CREATED:
                return self._result(task)
            else:
                needs_advance = False
                _check_approval(db, actor, task)
                if task.status in TERMINAL_STATUSES:
                    return self._result(task)
                previous_status = task.provider_status
                _claim(task)
                query = task.query
                collaborative = bool(task.parameters.get("collaborative_planning"))
                agent = task.provider_agent
        if needs_advance:
            return self.advance(actor, task_id)
        try:
            snapshot = self._client().create_deep_research(query, agent=agent, collaborative_planning=collaborative)
        except LLMError as exc:
            self._submission_failed(actor, task_id, previous_status, exc)
            raise
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status == R.CANCELLED:
                _release_claim(task, None)
                after_commit(db, lambda: cancel_provider_interaction(task.organization_id, task.id, snapshot.id))
                return self._result(task)
            task.provider = "gemini"
            task.provider_agent = snapshot.agent or agent
            task.provider_interaction_id = snapshot.id
            task.provider_status = snapshot.status
            task.last_polled_at = utcnow()
            transition(db, task, R.PLANNING if collaborative else R.RUNNING)
            if collaborative:
                task.plan_status = "AWAITING_PLAN"
                _set_plan(task, revision=0, history=[{"interaction_id": snapshot.id, "phase": "planning"}])
            add_event(
                db,
                task,
                "interaction_created",
                {"interaction_id": snapshot.id, "phase": "planning" if collaborative else "research"},
            )
            emit_task_event(db, actor, task, EventType.RESEARCH_STARTED, collaborative_planning=collaborative)
            return self._result(task)

    def _submission_failed(
        self, actor: Actor, task_id: uuid.UUID | str, previous_status: str | None, exc: LLMError
    ) -> None:
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            _release_claim(task, previous_status)
            if not exc.retryable:
                fail_task(db, actor, task, f"Deep research could not be started: {exc.message}")

    @staticmethod
    def _result(task: ResearchTask) -> StartResult:
        return StartResult(
            research_task_id=str(task.id),
            interaction_id=task.provider_interaction_id,
            status=task.provider_status,
            task_status=task.status,
        )

    # -- plan decisions ------------------------------------------------------------------------
    def advance(self, actor: Actor, task_id: uuid.UUID | str) -> StartResult:
        """Carry out a pending plan decision: start the research (approved) or a revised plan."""
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            action = _pending(task)
            if action is None or task.status in TERMINAL_STATUSES:
                return self._result(task)
            kind = action.get("kind")
            expected = R.APPROVED if kind == "execute" else R.PLANNING
            if task.status != expected:
                return self._result(task)
            previous_status = task.provider_status
            _claim(task)
            previous_interaction = task.provider_interaction_id
            feedback = sanitize_untrusted(str(action.get("feedback") or ""), MAX_FEEDBACK_CHARS).strip()
            if kind == "execute":
                message = EXECUTE_MESSAGE + (f"\n\nReviewer guidance:\n{feedback}" if feedback else "")
            else:
                message = REVISE_MESSAGE.format(feedback=feedback or "Improve the plan.")
            agent = task.provider_agent
        try:
            snapshot = self._client().create_deep_research(
                message,
                agent=agent,
                collaborative_planning=kind != "execute",
                previous_interaction_id=previous_interaction,
            )
        except LLMError as exc:
            self._submission_failed(actor, task_id, previous_status, exc)
            raise
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status == R.CANCELLED:
                _release_claim(task, None)
                after_commit(db, lambda: cancel_provider_interaction(task.organization_id, task.id, snapshot.id))
                return self._result(task)
            task.provider_interaction_id = snapshot.id
            task.provider_status = snapshot.status
            task.last_polled_at = utcnow()
            history = list((task.plan or {}).get("history") or [])
            history.append({"interaction_id": snapshot.id, "phase": "research" if kind == "execute" else "planning"})
            _set_plan(task, _pending_action=None, history=history)
            if kind == "execute":
                transition(db, task, R.RUNNING)
            else:
                task.plan_status = "AWAITING_PLAN"
            add_event(
                db,
                task,
                "interaction_created",
                {"interaction_id": snapshot.id, "phase": "research" if kind == "execute" else "planning"},
            )
            emit_task_event(db, actor, task, EventType.SEARCH_PROGRESS, phase=task.status.lower())
            return self._result(task)

    # -- poll ----------------------------------------------------------------------------------
    def poll(self, actor: Actor, task_id: uuid.UUID | str) -> PollResult:
        """Fetch the interaction and persist progress. Returns whether it is terminal / a plan is ready."""
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.kind != "deep_research":
                raise ValidationFailed("Not a deep-research task")
            if task.status in TERMINAL_STATUSES:
                return self._poll_result(task)
            if task.status == R.PLAN_REVIEW:
                return self._poll_result(task)
            pending = _pending(task) is not None
            interaction_id = task.provider_interaction_id
            if not pending and (interaction_id is None or task.provider_status == SUBMITTING):
                return self._poll_result(task)
        if pending:
            self.advance(actor, task_id)
            with tenant_uow(actor) as db:
                return self._poll_result(lock_task(db, actor, task_id))
        assert interaction_id is not None
        snapshot = self._client().get(interaction_id)
        cancel_after: str | None = None
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status in TERMINAL_STATUSES or task.provider_interaction_id != interaction_id:
                return self._poll_result(task)
            new_steps = _record_steps(db, task, snapshot)
            if snapshot.status != task.provider_status:
                add_event(db, task, "status_change", {"from": task.provider_status, "to": snapshot.status})
            task.provider_status = snapshot.status
            task.poll_count += 1
            task.last_polled_at = utcnow()
            if new_steps:
                emit_task_event(
                    db, actor, task, EventType.SEARCH_PROGRESS, provider_status=snapshot.status, new_steps=new_steps
                )
            if snapshot.is_terminal:
                _record_usage(db, task, snapshot)
                if snapshot.status == "completed":
                    self._completed(db, actor, task, snapshot)
                else:
                    message = (snapshot.error or {}).get("message") or snapshot.status
                    fail_task(db, actor, task, f"Deep research {snapshot.status}: {message}")
            elif snapshot.requires_action:
                fail_task(db, actor, task, "Deep research requested an unsupported client action")
                cancel_after = interaction_id
            else:
                started = _interaction_started_at(db, task)
                if started is not None and utcnow() - started > timedelta(seconds=MAX_RUNTIME_SECONDS):
                    fail_task(db, actor, task, f"Deep research exceeded {MAX_RUNTIME_SECONDS // 60} minutes")
                    cancel_after = interaction_id
            if cancel_after is not None:
                organization_id, task_uuid, cancel_id = task.organization_id, task.id, cancel_after
                after_commit(db, lambda: cancel_provider_interaction(organization_id, task_uuid, cancel_id))
            return self._poll_result(task)

    def _completed(self, db: Session, actor: Actor, task: ResearchTask, snapshot: InteractionSnapshot) -> None:
        citations = [c.model_dump() for c in snapshot.citations[:MAX_CITATION_SOURCES]]
        if task.status == R.PLANNING:
            revision = int((task.plan or {}).get("revision") or 0) + 1
            _set_plan(
                task,
                text=sanitize_untrusted(snapshot.text, MAX_PLAN_CHARS),
                interaction_id=snapshot.id,
                revision=revision,
                ready_at=utcnow().isoformat(),
                citations=citations,
            )
            task.plan_status = "PENDING_REVIEW"
            transition(db, task, R.PLAN_REVIEW)
            add_event(db, task, "plan_ready", {"revision": revision, "chars": len(snapshot.text)})
            emit_task_event(db, actor, task, EventType.RESEARCH_PLAN_READY, revision=revision)
            return
        task.report = sanitize_untrusted(snapshot.text, MAX_REPORT_CHARS)
        task.summary = " ".join(snapshot.text.split())[:MAX_SUMMARY_CHARS] or None
        add_event(
            db,
            task,
            "report_ready",
            {"chars": len(snapshot.text), "citations": citations},
            provider_event_id=f"report:{snapshot.id}",
        )

    @staticmethod
    def _poll_result(task: ResearchTask) -> PollResult:
        report_ready = task.status == R.RUNNING and task.provider_status == "completed"
        return PollResult(
            research_task_id=str(task.id),
            status=task.provider_status,
            task_status=task.status,
            terminal=task.status in TERMINAL_STATUSES or report_ready,
            plan_ready=task.status == R.PLAN_REVIEW,
        )

    # -- finalize ------------------------------------------------------------------------------
    def finalize(self, actor: Actor, task_id: uuid.UUID | str) -> FinalizeResult:
        """Report → artifact (markdown), citations → sources, provenance evidence, LITERATURE memory → COMPLETED."""
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status == R.COMPLETED:
                return self._stored_finalize(db, task)
            if task.status in TERMINAL_STATUSES:
                return FinalizeResult(research_task_id=str(task.id), status=task.status)
            if task.status != R.RUNNING or task.provider_status != "completed":
                raise ValidationFailed(
                    f"Deep research is not ready to finalize (status {task.status}, provider {task.provider_status})"
                )
            need_fetch = task.report is None or last_event(db, task, "report_ready") is None
            interaction_id = task.provider_interaction_id
        snapshot: InteractionSnapshot | None = None
        if need_fetch:
            assert interaction_id is not None
            snapshot = self._client().get(interaction_id)
        with tenant_uow(actor) as db:
            task = lock_task(db, actor, task_id)
            if task.status == R.COMPLETED:
                return self._stored_finalize(db, task)
            if task.status in TERMINAL_STATUSES:
                return FinalizeResult(research_task_id=str(task.id), status=task.status)
            if snapshot is not None:
                self._completed(db, actor, task, snapshot)
            event = last_event(db, task, "report_ready")
            citations = [Citation.model_validate(c) for c in (event.payload.get("citations") if event else []) or []]
            return self._finalize_in(db, actor, task, citations)

    def _finalize_in(
        self, db: Session, actor: Actor, task: ResearchTask, citations: list[Citation]
    ) -> FinalizeResult:
        from aegis_api.lab.data.artifacts import create_artifact_version

        report = task.report or ""
        document = _report_markdown(task, report, citations)
        version = create_artifact_version(
            db,
            actor,
            project_id=task.project_id,
            kind="report",
            name=f"Deep research report: {task.title}"[:300],
            data=document.encode("utf-8"),
            mime_type="text/markdown",
            filename=f"deep-research-{task.id}.md",
            mission_id=task.mission_id,
            metadata={
                "research_task_id": str(task.id),
                "interaction_id": task.provider_interaction_id,
                "agent": task.provider_agent,
                "citations": len(citations),
            },
            description="Gemini Deep Research report (provider-generated, untrusted content)",
        )
        rows = (
            upsert_sources(
                db,
                actor,
                task.project_id,
                [SourceInput.from_citation(c) for c in citations[:MAX_CITATION_SOURCES]],
                research_task_id=task.id,
                discovered_by="deep_research",
            )
            if citations
            else []
        )
        source_ids = list(dict.fromkeys(str(r.id) for r in rows))
        evidence = append_evidence(
            db,
            organization_id=task.organization_id,
            kind="research_report",
            title=f"Deep research report for task {task.id}",
            content={
                "research_task_id": str(task.id),
                "interaction_id": task.provider_interaction_id,
                "agent": task.provider_agent,
                "artifact_version_id": str(version.id),
                "report_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                "citation_urls": [c.url for c in citations[:MAX_CITATION_SOURCES]],
                "source_ids": source_ids,
            },
            storage_key=version.storage_key,
        )
        memory_id = record_report_memory(
            db, actor, task, report, source_ids=source_ids, origin="deep_research", artifact_version_id=version.id
        )
        task.report_artifact_id = version.id
        task.source_count = len(source_ids)
        transition(db, task, R.COMPLETED)
        add_event(
            db,
            task,
            "finalized",
            {
                "report_artifact_id": str(version.id),
                "source_ids": source_ids,
                "memory_id": memory_id,
                "evidence_id": str(evidence.id),
            },
        )
        emit_task_event(
            db,
            actor,
            task,
            EventType.RESEARCH_COMPLETED,
            source_count=len(source_ids),
            report_artifact_id=str(version.id),
        )
        return FinalizeResult(
            research_task_id=str(task.id),
            status=task.status,
            report_artifact_id=str(version.id),
            source_ids=source_ids,
            memory_id=memory_id,
        )

    @staticmethod
    def _stored_finalize(db: Session, task: ResearchTask) -> FinalizeResult:
        event = last_event(db, task, "finalized")
        payload = event.payload if event is not None else {}
        return FinalizeResult(
            research_task_id=str(task.id),
            status=task.status,
            report_artifact_id=str(task.report_artifact_id) if task.report_artifact_id else None,
            source_ids=[str(s) for s in payload.get("source_ids") or []],
            memory_id=payload.get("memory_id"),
        )


def _report_markdown(task: ResearchTask, report: str, citations: list[Citation]) -> str:
    lines = [
        f"# {task.title}",
        "",
        f"- Research question: {' '.join(task.query.split())[:2000]}",
        f"- Agent: {task.provider_agent or 'unknown'}",
        f"- Interaction: {task.provider_interaction_id or 'unknown'}",
        f"- Generated: {utcnow().isoformat()}",
        "",
        "> Provider-generated report. Treat its content as untrusted until verified.",
        "",
        report.strip(),
        "",
    ]
    if citations:
        lines.extend(["## Sources", ""])
        lines.extend(f"{i}. [{(c.title or c.url)[:300]}]({c.url})" for i, c in enumerate(citations, start=1))
        lines.append("")
    return "\n".join(lines)


# --- human plan review ----------------------------------------------------------------------------
def _signal(db: Session, task: ResearchTask, decision: str) -> None:
    if task.workflow_run_id is None:
        return
    try:
        from aegis_api.lab.workflows.launcher import signal_workflow
    except ImportError:
        return
    try:
        with db.begin_nested():
            signal_workflow(db, task.workflow_run_id, "research_plan", {"decision": decision, "task_id": str(task.id)})
    except Exception:
        log.warning("research_plan_signal_failed", research_task_id=str(task.id), exc_info=True)


def _continue_after_commit(db: Session, actor: Actor, task: ResearchTask) -> None:
    """Carry out the decision right after commit (the workflow's next activity would do it otherwise)."""
    task_id = task.id

    def run() -> None:
        try:
            DeepResearchService().advance(actor, task_id)
        except Exception:
            log.warning("research_plan_continuation_failed", research_task_id=str(task_id), exc_info=True)

    after_commit(db, run)


def _decision_task(db: Session, actor: Actor, task_id: uuid.UUID | str) -> ResearchTask:
    actor.require_human("research plan review")
    task = lock_task(db, actor, task_id)
    load_project(db, actor, task.project_id, "research:run")
    if task.kind != "deep_research":
        raise ValidationFailed("Only deep-research tasks have a research plan")
    return task


def approve_plan(db: Session, actor: Actor, task_id: uuid.UUID | str, feedback: str | None = None) -> ResearchTask:
    """Approve the plan (PLAN_REVIEW → APPROVED); the research interaction starts right after."""
    task = _decision_task(db, actor, task_id)
    from engines.lab.states import assert_transition

    assert_transition("research_task", task.status, R.APPROVED)
    clean = " ".join((feedback or "").split())[:MAX_FEEDBACK_CHARS] or None
    now = utcnow().isoformat()
    transition(db, task, R.APPROVED)
    task.plan_status = "APPROVED"
    _set_plan(
        task,
        approved_by=str(actor.user_id),
        approved_at=now,
        approval_feedback=clean,
        _pending_action={"kind": "execute", "feedback": clean, "requested_at": now},
    )
    add_event(db, task, "plan_approved", {"by": actor.as_dict(), "feedback": clean})
    audit(db, actor, PLAN_APPROVED, "research_task", task.id, after={"revision": (task.plan or {}).get("revision")})
    _signal(db, task, "approved")
    _continue_after_commit(db, actor, task)
    return task


def revise_plan(db: Session, actor: Actor, task_id: uuid.UUID | str, feedback: str) -> ResearchTask:
    """Request a revised plan (PLAN_REVIEW → PLANNING) with the reviewer's feedback."""
    task = _decision_task(db, actor, task_id)
    clean = " ".join((feedback or "").split())[:MAX_FEEDBACK_CHARS]
    if not clean:
        raise ValidationFailed("Feedback is required to revise the plan")
    from engines.lab.states import assert_transition

    assert_transition("research_task", task.status, R.PLANNING)
    now = utcnow().isoformat()
    transition(db, task, R.PLANNING)
    task.plan_status = "REVISION_REQUESTED"
    revisions = list((task.plan or {}).get("revision_requests") or [])
    revisions.append({"by": str(actor.user_id), "at": now, "feedback": clean})
    _set_plan(
        task,
        revision_requests=revisions,
        _pending_action={"kind": "revise", "feedback": clean, "requested_at": now},
    )
    add_event(db, task, "plan_revision_requested", {"by": actor.as_dict(), "feedback": clean})
    audit(db, actor, PLAN_REVISION_REQUESTED, "research_task", task.id, after={"feedback": clean[:500]})
    _signal(db, task, "revise")
    _continue_after_commit(db, actor, task)
    return task


# --- cancellation & approval hook -----------------------------------------------------------------
def cancel_provider_interaction(organization_id: uuid.UUID, task_id: uuid.UUID, interaction_id: str) -> None:
    """Best-effort provider cancel (after commit); the outcome is recorded as a research event."""
    try:
        snapshot = default_client().cancel(interaction_id)
        status: str | None = snapshot.status
        error = None
    except Exception as exc:
        status, error = None, type(exc).__name__
        log.warning("deep_research_cancel_failed", research_task_id=str(task_id), error=error)
    try:
        with tenant_uow(organization_id) as db:
            task = db.get(ResearchTask, task_id)
            if task is not None:
                if status is not None and status in PROVIDER_TERMINAL_STATUSES:
                    task.provider_status = status
                add_event(db, task, "provider_cancel", {"interaction_id": interaction_id, "status": status, "error": error})
    except Exception:
        log.warning("deep_research_cancel_record_failed", research_task_id=str(task_id), exc_info=True)


def on_research_approval(db: Session, actor: Actor, approval: Approval) -> None:
    """Approval hook (``research.deep_research``): record the decision; a refusal cancels the task."""
    if approval.subject_type != "research_task":
        return
    try:
        task_uuid = uuid.UUID(approval.subject_id)
    except ValueError:
        return
    task = db.get(ResearchTask, task_uuid)
    if task is None or task.organization_id != approval.organization_id or task.status in TERMINAL_STATUSES:
        return
    if approval.status == ApprovalStatus.APPROVED:
        add_event(db, task, "approval_granted", {"approval_id": str(approval.id)})
        return
    if approval.status in (ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED):
        transition(db, task, R.CANCELLED)
        add_event(db, task, "approval_refused", {"approval_id": str(approval.id), "status": approval.status})
        emit_task_event(db, actor, task, EventType.RESEARCH_FAILED, cancelled=True, reason="approval refused")


def _register_hooks() -> None:
    from aegis_api.lab.governance.approvals import register_approval_hook

    register_approval_hook(APPROVAL_ACTION, on_research_approval)


_register_hooks()
