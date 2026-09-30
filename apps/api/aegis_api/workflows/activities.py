"""Activity implementations: thin, idempotent adapters from workflow steps to application services.

Every activity receives the tenant and the launching principal's snapshot (``ActivityContext``); it opens its
own short, RLS-scoped transactions and performs model/network/sandbox I/O outside of them. Automation acts as
the ``workflow`` actor *on behalf of* the launching principal — it can never exceed that principal's
permissions, and human-only actions (autonomy changes, discovery approval, publication) are refused by policy.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.models.lab import (
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    Mission,
    ResearchTask,
    ScientificClaim,
    Strategy,
    StrategyVersion,
    WorkflowRun,
)
from aegis_api.services.lab import (
    approvals,
    benchmarks,
    datasets,
    discoveries,
    environments,
    evaluation,
    events,
    evolution,
    execution,
    failures,
    hypotheses,
    knowledge,
    reports,
    research,
    review,
    strategies,
    verification,
)
from aegis_api.services.lab import experiments as experiment_service
from aegis_api.services.lab import memory as memory_service
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.agents import AgentRunFailed, AgentTask, get_runtime
from aegis_api.services.lab.model_gateway import StructuredOutputError
from aegis_api.workflows.runtime import ActivityContext, activity
from engines.lab import harnesses
from engines.lab.autonomy import LabAction, requires_human
from engines.lab.enums import (
    ApprovalKind,
    ExperimentStatus,
    LabEventType,
    MissionPhase,
    MissionStatus,
    ResearchTaskStatus,
    RunKind,
)
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.prompts.registry import UntrustedBlock
from engines.lab.state_machines import EXPERIMENT, MISSION

log = structlog.get_logger("aegis.workflows.activities")

GATE_KINDS = {
    LabAction.PLAN_EXECUTE: ApprovalKind.PLAN_REVIEW,
    LabAction.EXPERIMENT_EXECUTE: ApprovalKind.EXPERIMENT_EXECUTION,
    LabAction.REPRODUCTION_RUN: ApprovalKind.EXPERIMENT_EXECUTION,
    LabAction.STRATEGY_PROMOTE: ApprovalKind.STRATEGY_PROMOTION,
}


def _uuid(value: Any, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"invalid {label}") from exc


def _mission(db: Any, actx: ActivityContext, mission_id: Any) -> Mission:
    mission = db.get(Mission, _uuid(mission_id, "mission id"))
    if mission is None or mission.organization_id != actx.organization_id:
        raise NotFound("Mission not found")
    return mission


def _instructions(m: Mission) -> str:
    criteria = "; ".join(str(c.get("description")) for c in (m.success_criteria or []) if isinstance(c, dict))
    return (
        f"Objective: {m.objective}\nDomain: {m.domain}\nRisk level: {m.risk_level}\n"
        f"Constraints: {'; '.join(m.constraints) if m.constraints else 'none'}\n"
        f"Success criteria: {criteria or 'not specified'}"
    )


def _agent(
    actx: ActivityContext,
    role: str,
    variables: dict[str, Any],
    *,
    instructions: str,
    mission: Mission | None = None,
    project_id: uuid.UUID | None = None,
    research: list[UntrustedBlock] | None = None,
    purpose: str | None = None,
    optional: bool = False,
    complexity: float = 0.5,
) -> dict[str, Any] | None:
    task = AgentTask(
        organization_id=actx.organization_id,
        role=role,
        variables=variables,
        mission_instructions=instructions,
        actor=actx.actor,
        project_id=mission.project_id if mission else project_id,
        mission_id=mission.id if mission else None,
        workflow_run_id=actx.run_id,
        purpose=purpose,
        research_data=research or [],
        complexity=complexity,
    )
    try:
        result = get_runtime().run(task)
    except (AgentRunFailed, StructuredOutputError, ServiceUnavailable) as exc:
        if optional:
            log.info("optional_agent_skipped", role=role, reason=str(exc)[:200])
            return None
        raise
    return result.as_dict()


# --- workflow bookkeeping (Temporal) --------------------------------------------------------------------------


@activity("workflow.mark")
def workflow_mark(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        run = db.get(WorkflowRun, actx.run_id)
        if run is None:
            return {"ok": False}
        run.status = p["status"]
        if p["status"] == "running":
            run.started_at = run.started_at or utcnow()
        else:
            run.completed_at = utcnow()
        if p.get("result") is not None:
            run.result = p["result"]
        if p.get("error"):
            run.error = str(p["error"])[:4000]
        return {"ok": True}


@activity("workflow.create_child")
def workflow_create_child(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    key = f"{p['parent_run_id']}:{p['key']}"[:160]
    with session_scope(actx.organization_id) as db:
        existing = db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.organization_id == actx.organization_id,
                WorkflowRun.workflow == p["workflow"],
                WorkflowRun.business_key == key,
            )
        )
        if existing is not None:
            return {"run_id": str(existing.id)}
        run = WorkflowRun(
            organization_id=actx.organization_id,
            workflow=p["workflow"],
            business_key=key,
            engine="temporal",
            status="pending",
            input=p.get("input") or {},
            principal=actx.principal.snapshot(),
            parent_run_id=_uuid(p["parent_run_id"]),
        )
        db.add(run)
        db.flush()
        return {"run_id": str(run.id)}


@activity("approval.status")
def approval_status(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        return approvals.status_of(db, actx.organization_id, p["approval_id"])


# --- mission ------------------------------------------------------------------------------------------------


@activity("mission.start")
def mission_start(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        cfg = m.config or {}
        return {
            "mission_id": str(m.id),
            "autonomy_level": m.autonomy_level,
            "max_cycles": m.max_cycles,
            "skip_research": bool(cfg.get("skip_research")),
            "evolution": bool(cfg.get("evolution")),
            "strategy_id": cfg.get("strategy_id"),
            "experiment_template_id": cfg.get("experiment_template_id"),
        }


@activity("mission.phase")
def mission_phase(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        phase = MissionPhase(p["phase"]).value
        if m.phase != phase:
            before = m.phase
            m.phase = phase
            events.emit(
                db,
                organization_id=m.organization_id,
                mission_id=m.id,
                project_id=m.project_id,
                event_type=LabEventType.MISSION_PHASE_CHANGED,
                message=f"Phase: {before or 'start'} → {phase}",
                data={"from": before, "to": phase, "cycle": m.current_cycle},
                actor=actx.actor,
                webhook=False,
            )
        return {"phase": phase}


@activity("mission.cycle")
def mission_cycle(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        m.current_cycle = max(m.current_cycle, int(p["cycle"]))
        return {"cycle": m.current_cycle}


@activity("mission.checkpoint")
def mission_checkpoint(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        return {"status": m.status, "cancel_requested": m.cancel_requested, "autonomy_level": m.autonomy_level}


@activity("mission.gate")
def mission_gate(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    action = LabAction(p["action"])
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        human = requires_human(m.autonomy_level, action)
        result = lab_policy.evaluate(
            db,
            organization_id=m.organization_id,
            action=action.value,
            facts=lab_policy.base_facts(
                db, m.organization_id, actx.actor, mission=m, extra={"gate": p.get("details") or {}}
            ),
            actor=actx.actor,
            resource_type="mission",
            resource_id=str(m.id),
        )
        if result.denied:
            return {"decision": "deny", "reasons": result.reasons}
        if not human and not result.needs_approval:
            return {"decision": "allow"}
        kind = GATE_KINDS.get(action, ApprovalKind.GENERIC)
        approval = approvals.request(
            db,
            organization_id=m.organization_id,
            kind=str(kind),
            resource_type="mission",
            resource_id=f"{m.id}:{action.value}:{json.dumps(p.get('details') or {}, sort_keys=True, default=str)[:24]}"[
                :64
            ],
            title=f"Mission '{m.title[:120]}': approve '{action.value}' (autonomy {m.autonomy_level})",
            requester=actx.actor,
            details={"action": action.value, "details": p.get("details") or {}, "autonomy_level": m.autonomy_level},
            policy_result=result,
            project_id=m.project_id,
            mission_id=m.id,
            workflow_run_id=actx.run_id,
        )
        return {"decision": "approval_required", "approval_id": str(approval.id), "signal": approval.signal_name}


@activity("mission.finish")
def mission_finish(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    status = p["status"]
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        if m.status == status or MISSION.is_terminal(m.status) or m.status == MissionStatus.CANCELLED:
            return {"status": m.status}
        if m.status == MissionStatus.PAUSED and status == MissionStatus.COMPLETED:
            m.status = MISSION.ensure(m.status, MissionStatus.RUNNING)
        m.status = MISSION.ensure(m.status, status)
        m.status_reason = (p.get("reason") or None) and str(p["reason"])[:2000]
        m.completed_at = utcnow()
        if status == MissionStatus.COMPLETED:
            m.phase = MissionPhase.DONE
        event = {
            MissionStatus.COMPLETED: LabEventType.MISSION_COMPLETED,
            MissionStatus.FAILED: LabEventType.MISSION_FAILED,
            MissionStatus.CANCELLED: LabEventType.MISSION_CANCELLED,
        }[MissionStatus(status)]
        events.emit(
            db,
            organization_id=m.organization_id,
            mission_id=m.id,
            project_id=m.project_id,
            event_type=event,
            message=f"Mission {status}" + (f": {p['reason']}" if p.get("reason") else ""),
            data={"summary": p.get("summary") or {}, "cycles": m.current_cycle},
            level="error" if status == MissionStatus.FAILED else "info",
            actor=actx.actor,
        )
        return {"status": m.status}


@activity("mission.create_research")
def mission_create_research(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        key_title = f"Cycle {p['cycle']} literature — {m.title}"[:300]
        existing = db.scalar(
            select(ResearchTask).where(ResearchTask.mission_id == m.id, ResearchTask.title == key_title)
        )
        if existing is not None:
            return {"research_task_id": str(existing.id)}
        mode = (m.config or {}).get("research_mode", "literature_pipeline")
        task = ResearchTask(
            organization_id=m.organization_id,
            project_id=m.project_id,
            mission_id=m.id,
            title=key_title,
            question=m.objective,
            mode=mode if mode in research.MODES else "literature_pipeline",
            status=ResearchTaskStatus.CREATED,
            require_plan_approval=False,
            plan={"search_queries": list(p.get("queries") or (m.config or {}).get("literature_queries") or [])[:10]},
        )
        db.add(task)
        db.flush()
        return {"research_task_id": str(task.id)}


# --- agents: quest / planner ----------------------------------------------------------------------------------


@activity("agent.quest")
def agent_quest(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        db.expunge(m)
    out = _agent(actx, "quest", {"domain": m.domain}, instructions=_instructions(m), mission=m, purpose="brief")
    assert out is not None
    with session_scope(actx.organization_id) as db:
        m2 = _mission(db, actx, p["mission_id"])
        m2.brief = {**out["output"], "agent_run_id": out["run_id"]}
    return {"agent_run_id": out["run_id"]}


@activity("agent.plan")
def agent_plan(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        budget = {**(m.budget or {}), **(m.compute_budget or {})}
        brief = dict(m.brief or {})
        db.expunge(m)
    instructions = _instructions(m) + (f"\nClarified objective: {brief.get('restated_objective')}" if brief else "")
    out = _agent(
        actx,
        "planner",
        {"domain": m.domain, "autonomy_level": m.autonomy_level, "budget_summary": json.dumps(budget) or "none"},
        instructions=instructions,
        mission=m,
        purpose="plan",
    )
    assert out is not None
    with session_scope(actx.organization_id) as db:
        m2 = _mission(db, actx, p["mission_id"])
        m2.plan = {**out["output"], "agent_run_id": out["run_id"]}
        if m2.status == MissionStatus.RUNNING:
            events.emit(
                db,
                organization_id=m2.organization_id,
                mission_id=m2.id,
                project_id=m2.project_id,
                event_type=LabEventType.MISSION_PHASE_CHANGED,
                message=f"Plan ready: {len(out['output'].get('phases', []))} phase(s), "
                f"{len(out['output'].get('search_queries', []))} search quer(ies)",
                data={"agent_run_id": out["run_id"]},
                actor=actx.actor,
                webhook=False,
            )
    return {"summary": out["output"].get("summary"), "search_queries": out["output"].get("search_queries", [])}


# --- research ---------------------------------------------------------------------------------------------------


@activity("research.state")
def research_state(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return research.get_state(actx.organization_id, _uuid(p["research_task_id"]))


@activity("research.transition")
def research_transition(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    task_id = _uuid(p["research_task_id"])
    state = research.get_state(actx.organization_id, task_id)
    if state["status"] == p["status"] or state["status"] in ("completed", "failed", "cancelled"):
        return state
    fields: dict[str, Any] = {}
    if p.get("error"):
        fields["error"] = str(p["error"])[:2000]
    if p["status"] in ("failed", "cancelled", "completed"):
        fields["completed_at"] = utcnow()
    if p["status"] == "running":
        fields["started_at"] = utcnow()
    return research.transition(actx.organization_id, task_id, p["status"], **fields)


@activity("research.plan")
def research_plan(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    task_id = _uuid(p["research_task_id"])
    state = research.get_state(actx.organization_id, task_id)
    queries = list((state.get("plan") or {}).get("search_queries") or [])
    if not queries:
        out = _agent(
            actx,
            "planner",
            {"domain": "research", "autonomy_level": "n/a", "budget_summary": "research task"},
            instructions=f"Research question: {state['question']}",
            project_id=_uuid(state["project_id"]),
            purpose="research-plan",
        )
        assert out is not None
        queries = list(out["output"].get("search_queries") or [])[:10] or [state["question"][:200]]
        research.save_plan(actx.organization_id, task_id, {"search_queries": queries}, out["run_id"])
    result: dict[str, Any] = {"queries": queries}
    if state.get("require_plan_approval"):
        with session_scope(actx.organization_id) as db:
            task = db.get(ResearchTask, task_id)
            assert task is not None
            approval = approvals.request(
                db,
                organization_id=actx.organization_id,
                kind="plan_review",
                resource_type="research_task",
                resource_id=str(task.id),
                title=f"Approve research plan: {task.title[:200]}",
                requester=actx.actor,
                details={"queries": queries, "mode": task.mode},
                project_id=task.project_id,
                mission_id=task.mission_id,
                workflow_run_id=actx.run_id,
            )
            result.update({"approval_id": str(approval.id), "signal": approval.signal_name})
        research.transition(actx.organization_id, task_id, ResearchTaskStatus.PLAN_REVIEW)
    return result


@activity("research.start_deep")
def research_start_deep(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return research.start_deep_research(actx.organization_id, _uuid(p["research_task_id"]), actor=actx.actor)


@activity("research.poll_deep")
def research_poll_deep(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return research.poll_deep_research(actx.organization_id, _uuid(p["research_task_id"]), actor=actx.actor)


@activity("research.complete_deep")
def research_complete_deep(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return research.complete_deep_research(actx.organization_id, _uuid(p["research_task_id"]), actor=actx.actor)


@activity("research.cancel_deep")
def research_cancel_deep(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    research.cancel_provider(actx.organization_id, _uuid(p["research_task_id"]), actor=actx.actor)
    return {"ok": True}


@activity("research.search")
def research_search(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return research.literature_search(actx.organization_id, _uuid(p["research_task_id"]), list(p.get("queries") or []))


@activity("research.literature_review")
def research_literature_review(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    task_id = _uuid(p["research_task_id"])
    state = research.get_state(actx.organization_id, task_id)
    source_ids = list(p.get("source_ids") or [])
    blocks = [
        UntrustedBlock(content=b["content"], source="research_source", source_id=b["source_id"])
        for b in research.sources_as_blocks(actx.organization_id, source_ids)
    ]
    mission = None
    if state.get("mission_id"):
        with session_scope(actx.organization_id) as db:
            mission = _mission(db, actx, state["mission_id"])
            db.expunge(mission)
    out = _agent(
        actx,
        "literature",
        {"queries": ", ".join((state.get("plan") or {}).get("search_queries", [])[:10]) or state["question"][:300]},
        instructions=_instructions(mission) if mission else f"Research question: {state['question']}",
        mission=mission,
        project_id=_uuid(state["project_id"]),
        research=blocks,
        purpose="literature-review",
    )
    assert out is not None
    return research.complete_literature(
        actx.organization_id,
        task_id,
        out["output"],
        agent_run_id=out["run_id"],
        actor=actx.actor,
        source_ids=[b.source_id or "" for b in blocks],
    )


# --- hypotheses -------------------------------------------------------------------------------------------------


def _template_context(db: Any, m: Mission) -> tuple[str, str]:
    """(primary metric, parameter space description) from the mission's template/strategy, if any."""
    cfg = m.config or {}
    primary, space = "primary metric of the mission", "{}"
    if cfg.get("experiment_template_id"):
        exp = db.get(Experiment, _uuid(cfg["experiment_template_id"]))
        ver = db.get(ExperimentVersion, exp.current_version_id) if exp and exp.current_version_id else None
        if ver is not None:
            spec = ExperimentSpec.model_validate(ver.spec)
            if spec.primary_metric:
                primary = f"{spec.primary_metric.name} ({spec.primary_metric.direction})"
            space = json.dumps(
                {"baseline": spec.baseline.parameters if spec.baseline else {}, "candidate_example": spec.parameters}
            )
    if cfg.get("strategy_id"):
        strat = db.get(Strategy, _uuid(cfg["strategy_id"]))
        sv = db.get(StrategyVersion, strat.promoted_version_id) if strat and strat.promoted_version_id else None
        if sv is None and strat is not None:
            sv = db.scalar(
                select(StrategyVersion).where(StrategyVersion.strategy_id == strat.id).order_by(StrategyVersion.version)
            )
        if sv is not None:
            space = json.dumps(
                {
                    "parameter_space": sv.definition.get("parameter_space", []),
                    "current": sv.definition.get("parameters", {}),
                }
            )
    return primary, space


@activity("hypothesis.generate")
def hypothesis_generate(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        primary, space = _template_context(db, m)
        guidance = "none"
        if (m.config or {}).get("strategy_id"):
            strat = db.get(Strategy, _uuid(m.config["strategy_id"]))
            guidance = (strat.description or "none") if strat else "none"
        db.expunge(m)
    blocks = [
        UntrustedBlock(content=b["content"], source="research_source", source_id=b["source_id"])
        for b in research.sources_as_blocks(actx.organization_id, list(p.get("source_ids") or []))
    ]
    vectors, model = knowledge.embed_texts(actx.organization_id, [m.objective])
    with session_scope(actx.organization_id) as db:
        lessons = memory_service.search(
            db,
            actx.organization_id,
            m.objective,
            query_vector=vectors[0] if vectors else None,
            embedding_model=model,
            project_id=m.project_id,
            mission_id=m.id,
            limit=5,
        )
    blocks += [UntrustedBlock(content=x["content"], source="memory", source_id=x["id"]) for x in lessons]
    count = int((m.config or {}).get("hypotheses_per_cycle", 3))
    out = _agent(
        actx,
        "hypothesis",
        {
            "count": max(1, min(count, 8)),
            "domain": m.domain,
            "primary_metric": primary,
            "parameter_space": space,
            "strategy_guidance": guidance,
        },
        instructions=_instructions(m),
        mission=m,
        research=blocks,
        purpose="hypotheses",
        complexity=0.7,
    )
    assert out is not None
    ids = hypotheses.persist_generated(
        actx.organization_id,
        project_id=m.project_id,
        mission_id=m.id,
        agent_run_id=_uuid(out["run_id"]),
        output=out["output"],
        actor=actx.actor,
    )
    return {"hypothesis_ids": ids, "agent_run_id": out["run_id"]}


@activity("hypothesis.critique")
def hypothesis_critique(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    ids = list(p["hypothesis_ids"])
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        rows = {
            str(h.id): h for h in db.scalars(select(Hypothesis).where(Hypothesis.id.in_([_uuid(i) for i in ids]))).all()
        }
        listing = [
            {
                "index": i,
                "statement": rows[h].statement,
                "prediction": rows[h].measurable_prediction,
                "assumptions": rows[h].assumptions,
                "parameters": rows[h].parameters,
            }
            for i, h in enumerate(ids)
            if h in rows
        ]
        db.expunge(m)
    out = _agent(
        actx,
        "hypothesis_critic",
        {"hypotheses": json.dumps(listing)},
        instructions=_instructions(m),
        mission=m,
        purpose="critique",
    )
    assert out is not None
    return hypotheses.apply_critiques(actx.organization_id, ids, out["output"])


@activity("hypothesis.select")
def hypothesis_select(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        count = int((m.config or {}).get("select_count", 1))
    selected = hypotheses.select_top(
        actx.organization_id,
        hypothesis_ids=list(p["hypothesis_ids"]),
        count=max(1, min(count, 3)),
        actor=actx.actor,
        mission_id=_uuid(p["mission_id"]),
    )
    return {"selected": selected}


# --- experiments ------------------------------------------------------------------------------------------------


@activity("experiment.prepare")
def experiment_prepare(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    """Template path: clone the mission's validated template with the hypothesis' parameters. Otherwise the
    ExperimentDesigner and Coding agents produce a spec (validated) and code (static-checked)."""
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        h = db.get(Hypothesis, _uuid(p["hypothesis_id"]))
        if h is None or h.organization_id != actx.organization_id:
            raise NotFound("Hypothesis not found")
        existing = db.scalar(select(Experiment).where(Experiment.hypothesis_id == h.id, Experiment.mission_id == m.id))
        if existing is not None:
            ev = db.get(ExperimentVersion, existing.current_version_id)
            ready = bool(ev and (ev.validation or {}).get("valid") and ev.code_artifact_id)
            return {"experiment_id": str(existing.id), "ready": ready}
        cfg = m.config or {}
        template_id = cfg.get("experiment_template_id")
        statement, prediction, params = h.statement, h.measurable_prediction, dict(h.parameters or {})
        db.expunge(m)
    if template_id:
        with session_scope(actx.organization_id) as db:
            template = db.get(Experiment, _uuid(template_id))
            tv = (
                db.get(ExperimentVersion, template.current_version_id)
                if template and template.current_version_id
                else None
            )
            if template is None or tv is None or template.organization_id != actx.organization_id:
                raise NotFound("Template experiment not found")
            spec = dict(tv.spec)
            base_params = dict(spec.get("parameters") or {})
            spec["parameters"] = {**base_params, **{k: v for k, v in params.items() if k in base_params}}
            spec["hypothesis_id"] = str(p["hypothesis_id"])
            spec["hypothesis_statement"] = statement[:4000]
            parsed, validation = experiment_service.validate_spec(db, actx.organization_id, spec)
            experiment = Experiment(
                organization_id=actx.organization_id,
                project_id=m.project_id,
                mission_id=m.id,
                hypothesis_id=_uuid(p["hypothesis_id"]),
                title=f"{statement[:200]}"[:300],
                status=ExperimentStatus.DRAFT,
            )
            db.add(experiment)
            db.flush()
            experiment_service._add_version(
                db,
                experiment,
                parsed.model_dump(mode="json") if parsed else spec,
                validation,
                code_artifact_id=tv.code_artifact_id,
                code_sha256=tv.code_sha256,
                change_note=f"from template {template.id} with hypothesis parameters",
            )
            h2 = db.get(Hypothesis, _uuid(p["hypothesis_id"]))
            valid = bool(validation.get("valid"))
            if h2 is not None and valid and h2.status == "selected":
                h2.status = "experiment_designed"
            events.emit(
                db,
                organization_id=actx.organization_id,
                mission_id=m.id,
                project_id=m.project_id,
                event_type=LabEventType.EXPERIMENT_DESIGNED,
                message=f"Experiment prepared from template: {experiment.title[:200]}",
                data={"experiment_id": str(experiment.id), "valid": valid},
                actor=actx.actor,
            )
            return {
                "experiment_id": str(experiment.id),
                "ready": valid,
                "issues": [i["code"] for i in validation.get("issues", []) if i.get("severity") == "error"],
            }
    # agent path
    with session_scope(actx.organization_id) as db:
        envs = [environments.environment_dict(e) for e in environments.list_environments(db, actx.organization_id)]
        limits, _ = environments.validator_inputs(db, actx.organization_id)
        from aegis_api.models.lab import Dataset

        ds = [
            {"dataset_id": str(d.id), "dataset_version_id": str(d.current_version_id), "name": d.name}
            for d in db.scalars(
                select(Dataset).where(Dataset.project_id == m.project_id, Dataset.current_version_id.is_not(None))
            ).all()
        ]
    variables = {
        "hypothesis": f"{statement}\nPrediction: {json.dumps(prediction)}\nParameters to test: {json.dumps(params)}",
        "spec_contract": json.dumps(experiment_service.spec_contract())[:12000],
        "environments": json.dumps(
            [{"environment_id": e["id"], "image": e["image"], "packages": e["packages"]} for e in envs]
        ),
        "harnesses": json.dumps({k: v.description for k, v in harnesses.harnesses().items()}),
        "datasets": json.dumps(ds) if ds else "none",
        "resource_limits": json.dumps(
            {
                "max_cpu": limits.max_cpu,
                "max_memory_mb": limits.max_memory_mb,
                "max_timeout_seconds": limits.max_timeout_seconds,
                "network": "none",
            }
        ),
    }
    design = _agent(
        actx,
        "experiment_designer",
        variables,
        instructions=_instructions(m),
        mission=m,
        purpose="design",
        complexity=0.7,
    )
    assert design is not None
    persisted = experiment_service.persist_design(
        actx.organization_id,
        project_id=m.project_id,
        mission_id=m.id,
        hypothesis_id=_uuid(p["hypothesis_id"]),
        agent_run_id=_uuid(design["run_id"]),
        spec_raw=design["output"]["spec"],
        title=statement[:280],
        defaults={"min_seeds": (m.config or {}).get("min_seeds")},
        actor=actx.actor,
    )
    if not persisted["valid"]:
        return {
            "experiment_id": persisted["experiment_id"],
            "ready": False,
            "issues": [i["code"] for i in persisted["validation"].get("issues", []) if i.get("severity") == "error"],
        }
    return _generate_code(actx, m, _uuid(persisted["experiment_id"]), previous_error="none")


def _generate_code(
    actx: ActivityContext, m: Mission, experiment_id: uuid.UUID, *, previous_error: str
) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = dict(version.spec)
    for attempt in range(2):
        code = _agent(
            actx,
            "coding",
            {
                "spec": json.dumps(spec),
                "io_contract": json.dumps(experiment_service.IO_CONTRACT),
                "previous_error": previous_error,
            },
            instructions=_instructions(m),
            mission=m,
            purpose=f"code:{attempt}",
        )
        assert code is not None
        attached = experiment_service.attach_code(
            actx.organization_id,
            experiment_id=experiment_id,
            bundle=code["output"],
            agent_run_id=_uuid(code["run_id"]),
            actor=actx.actor,
        )
        with session_scope(actx.organization_id) as db:
            version = db.get(ExperimentVersion, _uuid(attached["version_id"]))
            assert version is not None
            files, _ = experiment_service.load_code(actx.organization_id, version)
            check = execution.static_check(files, ExperimentSpec.model_validate(version.spec))
        if check["passed"]:
            return {"experiment_id": str(experiment_id), "ready": True}
        previous_error = "Static safety checks failed: " + json.dumps(check["evidence"])[:3000]
    return {"experiment_id": str(experiment_id), "ready": False, "issues": ["static_check_failed"]}


@activity("experiment.queue")
def experiment_queue(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return experiment_service.queue(actx.organization_id, _uuid(p["experiment_id"]))


@activity("experiment.plan_runs")
def experiment_plan_runs(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return experiment_service.plan_runs(actx.organization_id, _uuid(p["experiment_id"]))


@activity("experiment.preflight")
def experiment_preflight(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return execution.preflight(
        actx.organization_id,
        experiment_id=_uuid(p["experiment_id"]),
        run_count=int(p.get("runs", 1)),
        actor=actx.actor,
        workflow_run_id=actx.run_id,
        action=p.get("action", "experiment.execute"),
    )


@activity("experiment.set_status")
def experiment_set_status(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    experiment_id = _uuid(p["experiment_id"])
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        current = experiment.status
    target = p["status"]
    if current == target:
        return {"status": current}
    if not EXPERIMENT.can(current, target):
        return {"status": current, "skipped": True}
    return {"status": experiment_service.set_status(actx.organization_id, experiment_id, target, error=p.get("error"))}


@activity("execution.run")
def execution_run(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return execution.execute_run(actx.organization_id, _uuid(p["run_id"]), actor=actx.actor)


@activity("experiment.recover")
def experiment_recover(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    """Apply one bounded, auto-applicable recovery (e.g. more time/memory within limits) as a new version."""
    experiment_id = _uuid(p["experiment_id"])
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None
        if experiment.retry_count >= 1:
            return {"retry": False, "reason": "retry budget exhausted"}
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = dict(version.spec)
    for analysis in p.get("failures") or []:
        for action in analysis.get("recovery_actions", []):
            patched = failures.apply_recovery_patch(spec, action)
            if patched is None:
                continue
            with session_scope(actx.organization_id) as db:
                parsed, validation = experiment_service.validate_spec(db, actx.organization_id, patched)
                if not validation.get("valid"):
                    continue
                experiment = db.get(Experiment, experiment_id)
                assert experiment is not None
                if EXPERIMENT.can(experiment.status, ExperimentStatus.FAILED):
                    experiment.status = ExperimentStatus.FAILED
                experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.RETRYING)
                experiment.retry_count += 1
                experiment_service._add_version(
                    db,
                    experiment,
                    parsed.model_dump(mode="json") if parsed else patched,
                    validation,
                    code_artifact_id=version.code_artifact_id,
                    code_sha256=version.code_sha256,
                    change_note=f"recovery: {action['kind']}",
                )
                experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.QUEUED)
                experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.RUNNING)
            return {"retry": True, "action": action["kind"]}
    return {"retry": False, "reason": "no auto-applicable recovery"}


@activity("experiment.completion")
def experiment_completion(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    experiment_id = _uuid(p["experiment_id"])
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        runs = list(db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_version_id == version.id)).all())
        done = [r for r in runs if r.status == "completed"]
        base = [r for r in done if r.run_kind == RunKind.BASELINE]
        cand = [r for r in done if r.run_kind == RunKind.CANDIDATE]
        evaluable = len(base) >= 2 and len(cand) >= 2
        if evaluable and experiment.status in (ExperimentStatus.RUNNING,):
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.COMPLETED)
        return {
            "evaluable": evaluable,
            "completed": len(done),
            "total": len(runs),
            "reason": None if evaluable else f"only {len(base)} baseline / {len(cand)} candidate runs completed",
        }


# --- failures ---------------------------------------------------------------------------------------------------


@activity("failure.analyze")
def failure_analyze(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    mission_id = _uuid(p["mission_id"]) if p.get("mission_id") else None
    project_id = None
    spec: dict[str, Any] | None = None
    experiment_id = _uuid(p["experiment_id"]) if p.get("experiment_id") else None
    run_id = _uuid(p["experiment_run_id"]) if p.get("experiment_run_id") else None
    with session_scope(actx.organization_id) as db:
        if experiment_id:
            experiment = db.get(Experiment, experiment_id)
            if experiment is not None:
                project_id, mission_id = experiment.project_id, mission_id or experiment.mission_id
                version = (
                    db.get(ExperimentVersion, experiment.current_version_id) if experiment.current_version_id else None
                )
                spec = dict(version.spec) if version else None
        if project_id is None and mission_id:
            project_id = _mission(db, actx, mission_id).project_id
    analysis = failures.analyze(
        actx.organization_id,
        signal={"stage": p.get("stage", "execution"), **(p.get("signal") or {})},
        actor=actx.actor,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        experiment_run_id=run_id,
        spec=spec,
    )
    if mission_id is not None:
        with session_scope(actx.organization_id) as db:
            m = _mission(db, actx, mission_id)
            db.expunge(m)
        diag = _agent(
            actx,
            "failure_analyzer",
            {
                "classification": json.dumps(analysis["classification"]),
                "spec": json.dumps(spec or {})[:8000],
                "logs_excerpt": str((p.get("signal") or {}).get("stderr_tail") or "")[-3000:],
            },
            instructions=_instructions(m),
            mission=m,
            purpose="diagnose",
            optional=True,
        )
        if diag is not None:
            failures.attach_diagnosis(
                actx.organization_id, _uuid(analysis["failure_id"]), diag["output"], diag["run_id"]
            )
            analysis["diagnosis_run_id"] = diag["run_id"]
    lesson = failures.record_lesson(
        actx.organization_id,
        _uuid(analysis["failure_id"]),
        actor=actx.actor,
        recovery_kind=(analysis["recovery_actions"][0]["kind"] if analysis["recovery_actions"] else None),
        resolved=None,
        context=f"stage={p.get('stage')}",
    )
    return {**analysis, "lesson": lesson}


# --- evaluation / verification ----------------------------------------------------------------------------------


@activity("evaluation.evaluate")
def evaluation_evaluate(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evaluation.evaluate_experiment(actx.organization_id, _uuid(p["experiment_id"]), actor=actx.actor)


@activity("evaluation.claims")
def evaluation_claims(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    ids = verification.extract_claims(
        actx.organization_id, _uuid(p["experiment_id"]), p["evaluation"], actor=actx.actor
    )
    return {"claim_ids": ids}


@activity("evaluation.interpret")
def evaluation_interpret(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, _uuid(p["experiment_id"]))
        if experiment is None:
            raise NotFound("Experiment not found")
        m = db.get(Mission, experiment.mission_id) if experiment.mission_id else None
        if m is not None:
            db.expunge(m)
        project_id = experiment.project_id
    out = _agent(
        actx,
        "statistical_analyst",
        {"tests": json.dumps(p.get("rows") or [])[:10000]},
        instructions=_instructions(m) if m else "Interpret the statistical results.",
        mission=m,
        project_id=project_id,
        purpose="interpret",
        optional=True,
    )
    if out is None:
        return {"skipped": True}
    with session_scope(actx.organization_id) as db:
        experiment = db.get(Experiment, _uuid(p["experiment_id"]))
        assert experiment is not None
        experiment.outcome = {
            **(experiment.outcome or {}),
            "interpretation": {"source": "model_assisted", "agent_run_id": out["run_id"], **out["output"]},
        }
    return {"agent_run_id": out["run_id"]}


@activity("evaluation.review")
def evaluation_review(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return review.review_experiment(actx.organization_id, _uuid(p["experiment_id"]), actor=actx.actor)


@activity("verification.create")
def verification_create(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        claim = db.get(ScientificClaim, _uuid(p["claim_id"]))
        if claim is None or claim.organization_id != actx.organization_id:
            raise NotFound("Claim not found")
        from aegis_api.models.lab import Verification

        existing = db.scalar(
            select(Verification).where(
                Verification.claim_id == claim.id, Verification.status.in_(["pending", "running"])
            )
        )
        v = existing or verification.create_verification(db, claim, requested_by=actx.actor.label)
        return {"verification_id": str(v.id)}


@activity("verification.begin")
def verification_begin(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return verification.begin(actx.organization_id, _uuid(p["verification_id"]), actor=actx.actor)


@activity("verification.plan_reproductions")
def verification_plan(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_ids": verification.plan_reproductions(actx.organization_id, _uuid(p["verification_id"]), int(p["count"]))
    }


@activity("verification.checks")
def verification_checks(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return verification.run_checks(
        actx.organization_id,
        _uuid(p["verification_id"]),
        reproduction_run_ids=list(p.get("reproduction_run_ids") or []),
        actor=actx.actor,
    )


@activity("verification.assess")
def verification_assess(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        from aegis_api.models.lab import Verification

        v = db.get(Verification, _uuid(p["verification_id"]))
        assert v is not None
        claim = db.get(ScientificClaim, v.claim_id)
        assert claim is not None
        statement, project_id = claim.statement, claim.project_id
    out = _agent(
        actx,
        "verifier",
        {"claim": statement, "evidence_summary": json.dumps(p.get("checks") or {})[:8000]},
        instructions="Assess only whether the recorded evidence supports the claim.",
        project_id=project_id,
        purpose="verify",
        optional=True,
    )
    if out is None:
        return {"skipped": True}
    verification.record_model_assessment(
        actx.organization_id, _uuid(p["verification_id"]), out["output"], out["run_id"]
    )
    return {"agent_run_id": out["run_id"]}


@activity("verification.decide")
def verification_decide(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return verification.decide(
        actx.organization_id,
        _uuid(p["verification_id"]),
        reproductions_passed=int(p["reproductions_passed"]),
        reproductions_failed=int(p["reproductions_failed"]),
        actor=actx.actor,
    )


@activity("verification.fail")
def verification_fail(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    verification.fail(actx.organization_id, _uuid(p["verification_id"]), str(p.get("error", "")))
    return {"ok": True}


# --- discoveries ------------------------------------------------------------------------------------------------


@activity("discovery.start")
def discovery_start(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    """Start the DiscoveryWorkflow as an independent run (human review may take days; the mission continues)."""
    from aegis_api.workflows import client as workflow_client

    with session_scope(actx.organization_id) as db:
        run = workflow_client.start(
            db,
            organization_id=actx.organization_id,
            workflow="discovery",
            business_key=f"discovery:{p['claim_id']}",
            payload={"claim_id": str(p["claim_id"])},
            principal=actx.principal,
        )
        return {"workflow_run_id": str(run.id)}


@activity("discovery.create")
def discovery_create(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        d, approval = discoveries.create_from_claim(
            db,
            organization_id=actx.organization_id,
            claim_id=_uuid(p["claim_id"]),
            actor=actx.actor,
            workflow_run_id=actx.run_id,
            on_behalf_of=actx.launcher_actor_id,
        )
        return {
            "discovery_id": str(d.id),
            "status": d.status,
            "approval_id": str(approval.id) if approval else None,
            "signal": approval.signal_name if approval else None,
        }


@activity("discovery.outcome")
def discovery_outcome(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    from aegis_api.models.lab import Discovery

    with session_scope(actx.organization_id) as db:
        d = db.get(Discovery, _uuid(p["discovery_id"]))
        if d is None:
            raise NotFound("Discovery not found")
        return {"status": d.status}


# --- evolution --------------------------------------------------------------------------------------------------


@activity("evolution.create")
def evolution_create(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        cfg = m.config or {}
        evo_cfg = dict(cfg.get("evolution") or {}) if isinstance(cfg.get("evolution"), dict) else {}
        run = evolution.create_run(
            db,
            actx.principal,
            strategy_id=cfg["strategy_id"],
            config={k: v for k, v in evo_cfg.items() if k != "max_generations"},
            max_generations=int(evo_cfg.get("max_generations", 2)),
            mode="experiment",
            experiment_template_id=_uuid(p["experiment_id"]),
            benchmark=None,
            mission_id=m.id,
        )
        run.workflow_run_id = actx.run_id
        return {"evolution_run_id": str(run.id)}


@activity("evolution.info")
def evolution_info(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    from aegis_api.models.lab import EvolutionRun

    with session_scope(actx.organization_id) as db:
        run = db.get(EvolutionRun, _uuid(p["evolution_run_id"]))
        if run is None or run.organization_id != actx.organization_id:
            raise NotFound("Evolution run not found")
        return {"mode": run.config.get("mode", "experiment"), "max_generations": run.max_generations}


@activity("evolution.pending")
def evolution_pending(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return {"version_ids": evolution.pending_candidates(actx.organization_id, _uuid(p["evolution_run_id"]))}


@activity("evolution.benchmark")
def evolution_benchmark(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evolution.evaluate_benchmark(actx.organization_id, _uuid(p["evolution_run_id"]), _uuid(p["version_id"]))


@activity("evolution.prepare")
def evolution_prepare(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evolution.prepare_candidate_experiment(
        actx.organization_id, _uuid(p["evolution_run_id"]), _uuid(p["version_id"]), actor=actx.actor
    )


@activity("evolution.record")
def evolution_record(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evolution.record_candidate_result(actx.organization_id, _uuid(p["version_id"]), _uuid(p["experiment_id"]))


@activity("evolution.step")
def evolution_step(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evolution.generation_step(actx.organization_id, _uuid(p["evolution_run_id"]), actor=actx.actor)


@activity("evolution.best")
def evolution_best(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return evolution.best_candidate(actx.organization_id, _uuid(p["evolution_run_id"]))


@activity("evolution.promote")
def evolution_promote(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    from aegis_api.models.lab import EvolutionRun

    with session_scope(actx.organization_id) as db:
        run = db.get(EvolutionRun, _uuid(p["evolution_run_id"]))
        assert run is not None
        mission = db.get(Mission, run.mission_id) if run.mission_id else None
        result = strategies.promote(
            db,
            strategy_id=run.strategy_id,
            version_id=_uuid(p["version_id"]),
            actor=actx.actor,
            organization_id=actx.organization_id,
            reason=f"evolution run {run.id}",
            mission=mission,
            approval_id=_uuid(p["approval_id"]) if p.get("approval_id") else None,
        )
        if result.get("approval_required") and not p.get("approval_id"):
            approval = approvals.request(
                db,
                organization_id=actx.organization_id,
                kind="strategy_promotion",
                resource_type="strategy_version",
                resource_id=str(p["version_id"]),
                title=f"Promote evolved strategy version {p['version_id']}",
                requester=actx.actor,
                details={"gate": result.get("gate"), "policy": result.get("policy")},
                project_id=run.project_id,
                mission_id=run.mission_id,
                workflow_run_id=actx.run_id,
            )
            result.update({"approval_id": str(approval.id), "signal": approval.signal_name})
        return result


@activity("evolution.finish")
def evolution_finish(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    evolution.finish(actx.organization_id, _uuid(p["evolution_run_id"]), p["status"])
    return {"ok": True}


# --- reports ----------------------------------------------------------------------------------------------------


@activity("report.facts")
def report_facts(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return reports.facts(actx.organization_id, _uuid(p["mission_id"]))


@activity("report.narrative")
def report_narrative(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    with session_scope(actx.organization_id) as db:
        m = _mission(db, actx, p["mission_id"])
        db.expunge(m)
    facts = p["facts"]
    out = _agent(
        actx,
        "report",
        {
            "report_facts": json.dumps(facts["sections"])[:20000],
            "evidence_ids": ", ".join(facts["known_evidence_ids"][:200]),
        },
        instructions=_instructions(m),
        mission=m,
        purpose="report",
        optional=True,
    )
    return {"output": out["output"], "agent_run_id": out["run_id"]} if out else {"output": None, "agent_run_id": None}


@activity("report.assemble")
def report_assemble(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return reports.assemble(
        actx.organization_id,
        _uuid(p["mission_id"]),
        p["facts"],
        narrative=p.get("narrative"),
        narrative_run_id=_uuid(p["narrative_run_id"]) if p.get("narrative_run_id") else None,
        actor=actx.actor,
    )


# --- datasets / artifacts / benchmarks ----------------------------------------------------------------------------


@activity("dataset.profile")
def dataset_profile(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return datasets.profile_files(actx.organization_id, list(p["files"]))


@activity("dataset.register")
def dataset_register(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return datasets.register_version(
        actx.organization_id,
        dataset_id=_uuid(p["dataset_id"]),
        files=list(p["files"]),
        profile=p["profile"],
        parent_version_id=_uuid(p["parent_version_id"]) if p.get("parent_version_id") else None,
        transformations=list(p.get("transformations") or []),
        license=p.get("license"),
        source=p.get("source"),
        created_by_id=actx.principal.user_id if actx.principal else None,
    )


@activity("artifact.fetch_url")
def artifact_fetch_url(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    version_id = knowledge.fetch_url_document(actx.organization_id, _uuid(p["document_id"]))
    return {"document_version_id": str(version_id) if version_id else None}


@activity("artifact.process_document")
def artifact_process_document(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return knowledge.process_document_version(actx.organization_id, _uuid(p["document_version_id"]))


@activity("benchmark.execute")
def benchmark_execute(actx: ActivityContext, p: dict[str, Any]) -> dict[str, Any]:
    return benchmarks.execute(
        actx.organization_id,
        _uuid(p["benchmark_run_id"]),
        actor=actx.actor,
        project_id=_uuid(p["project_id"]) if p.get("project_id") else None,
    )
