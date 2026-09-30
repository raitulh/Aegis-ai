"""Mission engine (application side): definition, versioning, lifecycle and observability.

Only humans set or change a mission's autonomy level, and never above the organization/platform ceiling.
Every definition change appends an immutable ``mission_versions`` snapshot. Launching starts the durable
MissionWorkflow on behalf of the launching principal (the workflow can never exceed that principal's
permissions); pause/resume/cancel are delivered to the workflow as signals/cancellation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import BudgetExceeded, Forbidden, InvalidState, ValidationFailed
from aegis_api.models.lab import (
    AgentRun,
    Approval,
    Experiment,
    ExperimentRun,
    Failure,
    Hypothesis,
    LabEvent,
    Mission,
    MissionVersion,
    WorkflowRun,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, quota_service
from aegis_api.services.lab import events, usage
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from aegis_api.services.lab.common import Actor
from aegis_api.services.lab.workspaces import validate_budget
from engines.lab.autonomy import AutonomyViolation, validate_autonomy_change
from engines.lab.enums import AutonomyLevel, LabEventType, MissionStatus, RiskLevel
from engines.lab.state_machines import MISSION

DEFINITION_FIELDS = (
    "title",
    "objective",
    "domain",
    "constraints",
    "success_criteria",
    "budget",
    "compute_budget",
    "time_budget_seconds",
    "deadline",
    "allowed_tools",
    "risk_level",
    "autonomy_level",
    "approval_policy",
    "config",
    "max_cycles",
)
EDITABLE_STATUSES = frozenset({MissionStatus.DRAFT, MissionStatus.PLANNED, MissionStatus.PAUSED, MissionStatus.FAILED})
KNOWN_TOOLS = frozenset(
    {
        "paper_search",
        "web_search",
        "url_fetch",
        "memory_search",
        "file_search",
        "dataset_search",
        "object_storage",
        "python_execution",
        "experiment_run",
    }
)
CONFIG_KEYS = frozenset(
    {
        "hypotheses_per_cycle",
        "model_pins",
        "max_model_tier",
        "strategy_id",
        "dataset_version_id",
        "environment_id",
        "harness",
        "research_mode",
        "literature_queries",
        "evolution",
        "min_seeds",
        "skip_research",
        "auto_verify",
        "experiment_template_id",
    }
)
MAX_CYCLES_LIMIT = 50


def _snapshot(mission: Mission) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in DEFINITION_FIELDS:
        value = getattr(mission, f)
        out[f] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _append_version(db: Session, mission: Mission, principal: Principal, summary: str) -> MissionVersion:
    number = 1 + int(
        db.scalar(
            select(func.coalesce(func.max(MissionVersion.version), 0)).where(MissionVersion.mission_id == mission.id)
        )
        or 0
    )
    version = MissionVersion(
        organization_id=mission.organization_id,
        mission_id=mission.id,
        version=number,
        snapshot=_snapshot(mission),
        change_summary=summary[:2000],
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(version)
    return version


def _validate_definition(db: Session, principal: Principal, data: dict[str, Any], *, current_autonomy: str) -> None:
    if data.get("autonomy_level") is not None:
        ceiling = quota_service.autonomy_ceiling(db, principal.organization_id)
        if AutonomyLevel(data["autonomy_level"]).rank > AutonomyLevel(ceiling).rank:
            raise Forbidden(f"Autonomy {data['autonomy_level']} exceeds the organization ceiling {ceiling}")
    if "autonomy_level" in data and data["autonomy_level"] is not None and data["autonomy_level"] != current_autonomy:
        ceiling = quota_service.autonomy_ceiling(db, principal.organization_id)
        try:
            validate_autonomy_change(
                principal.actor_type, current_autonomy, data["autonomy_level"], max_allowed=ceiling
            )
        except (AutonomyViolation, ValueError) as exc:
            raise Forbidden(str(exc)) from exc
        if not principal.is_human:
            raise Forbidden("Only interactive human users can set a mission's autonomy level")
    if data.get("risk_level") is not None:
        RiskLevel(data["risk_level"])
    tools = data.get("allowed_tools")
    if tools is not None:
        unknown = set(tools) - KNOWN_TOOLS - {t for t in tools if str(t).startswith("mcp:")}
        if unknown:
            raise ValidationFailed(f"unknown tools: {', '.join(sorted(unknown))}")
    config = data.get("config")
    if config is not None:
        unknown = set(config) - CONFIG_KEYS
        if unknown:
            raise ValidationFailed(f"unknown mission config keys: {', '.join(sorted(unknown))}")
    if data.get("max_cycles") is not None and not 1 <= int(data["max_cycles"]) <= MAX_CYCLES_LIMIT:
        raise ValidationFailed(f"max_cycles must be between 1 and {MAX_CYCLES_LIMIT}")
    for key in ("budget", "compute_budget"):
        if data.get(key) is not None:
            data[key] = validate_budget(data[key])
    for crit in data.get("success_criteria") or []:
        if not isinstance(crit, dict) or not crit.get("description"):
            raise ValidationFailed("each success criterion needs a 'description' (and optionally metric/threshold)")


def create(db: Session, principal: Principal, data: dict[str, Any]) -> Mission:
    principal.require("mission:create")
    project = get_project(db, principal, data["project_id"])
    data = dict(data)
    data.setdefault("autonomy_level", AutonomyLevel.L1_RESEARCH_AUTOMATION.value)
    # Automation (API keys/service accounts) may create missions up to L1; anything higher needs a human.
    baseline = AutonomyLevel.L1_RESEARCH_AUTOMATION.value
    level = AutonomyLevel(data["autonomy_level"])
    _validate_definition(db, principal, data, current_autonomy=baseline if level.rank <= 1 else "L0_ASSISTED")
    mission = Mission(
        organization_id=principal.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        title=str(data["title"])[:300],
        objective=str(data["objective"]),
        domain=str(data.get("domain") or project.domain or "general")[:48],
        constraints=list(data.get("constraints") or []),
        success_criteria=list(data.get("success_criteria") or []),
        budget=data.get("budget") or dict(project.budget or {}),
        compute_budget=data.get("compute_budget") or {},
        time_budget_seconds=data.get("time_budget_seconds"),
        deadline=data.get("deadline"),
        allowed_tools=list(data.get("allowed_tools") or []),
        risk_level=data.get("risk_level") or "medium",
        autonomy_level=data["autonomy_level"],
        approval_policy=data.get("approval_policy") or {},
        config=data.get("config") or {},
        max_cycles=int(data.get("max_cycles") or 1),
        status=MissionStatus.DRAFT,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(mission)
    db.flush()
    _append_version(db, mission, principal, "created")
    events.emit(
        db,
        organization_id=mission.organization_id,
        mission_id=mission.id,
        project_id=mission.project_id,
        event_type=LabEventType.MISSION_CREATED,
        message=f"Mission created: {mission.title}",
        data={"autonomy_level": mission.autonomy_level, "risk_level": mission.risk_level},
        actor=Actor.of(principal),
    )
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.created",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
        after={"title": mission.title, "autonomy_level": mission.autonomy_level},
    )
    return mission


def update(db: Session, principal: Principal, mission_id: uuid.UUID | str, changes: dict[str, Any]) -> Mission:
    principal.require("mission:create")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    if mission.status not in EDITABLE_STATUSES:
        raise InvalidState(f"Mission definition cannot be edited while {mission.status}")
    changes = {k: v for k, v in changes.items() if k in DEFINITION_FIELDS and v is not None}
    _validate_definition(db, principal, changes, current_autonomy=mission.autonomy_level)
    before = _snapshot(mission)
    for key, value in changes.items():
        setattr(mission, key, value)
    if mission.status == MissionStatus.PLANNED:
        mission.status = MISSION.ensure(mission.status, MissionStatus.DRAFT)
    _append_version(db, mission, principal, "updated: " + ", ".join(sorted(changes)))
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.updated",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
        before={k: before[k] for k in changes},
        after={k: _snapshot(mission)[k] for k in changes},
    )
    return mission


def change_autonomy(db: Session, principal: Principal, mission_id: uuid.UUID | str, level: str, reason: str) -> Mission:
    """Human-only; audited; bounded by the org/platform ceiling. Takes effect at the workflow's next checkpoint."""
    principal.require_human("autonomy change")
    principal.require("mission:run")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    ceiling = quota_service.autonomy_ceiling(db, principal.organization_id)
    try:
        new = validate_autonomy_change(principal.actor_type, mission.autonomy_level, level, max_allowed=ceiling)
    except (AutonomyViolation, ValueError) as exc:
        raise Forbidden(str(exc)) from exc
    before = mission.autonomy_level
    mission.autonomy_level = new
    _append_version(db, mission, principal, f"autonomy {before} → {new}: {reason}")
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.autonomy_changed",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
        before={"autonomy_level": before},
        after={"autonomy_level": new, "reason": reason},
    )
    return mission


def list_missions(
    db: Session, principal: Principal, *, project_id: uuid.UUID | None = None, status: str | None = None
) -> Select[Mission]:
    stmt = select(Mission).where(Mission.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Mission.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Mission.project_id == project_id)
    if status:
        stmt = stmt.where(Mission.status == status)
    return stmt.order_by(Mission.created_at.desc())


def launch(db: Session, principal: Principal, mission_id: uuid.UUID | str) -> tuple[Mission, WorkflowRun]:
    from aegis_api.workflows import client as workflow_client

    principal.require("mission:run")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    if mission.status == MissionStatus.RUNNING:
        run = db.get(WorkflowRun, mission.workflow_run_id) if mission.workflow_run_id else None
        if run is not None and run.status not in ("completed", "failed", "cancelled"):
            return mission, run  # idempotent re-launch
    if mission.status not in (MissionStatus.DRAFT, MissionStatus.PLANNED, MissionStatus.APPROVED, MissionStatus.FAILED):
        raise InvalidState(f"Mission cannot be launched from status '{mission.status}'")
    budget = usage.check_mission_budget(db, mission)
    if not budget.allowed:
        raise BudgetExceeded("Mission budget is already exhausted", details=budget.to_dict())
    ceiling = quota_service.autonomy_ceiling(db, principal.organization_id)
    if AutonomyLevel(mission.autonomy_level).rank > AutonomyLevel(ceiling).rank:
        raise Forbidden(f"Mission autonomy {mission.autonomy_level} exceeds the current ceiling {ceiling}")
    path = {
        MissionStatus.DRAFT: [MissionStatus.PLANNED, MissionStatus.APPROVED, MissionStatus.RUNNING],
        MissionStatus.PLANNED: [MissionStatus.APPROVED, MissionStatus.RUNNING],
        MissionStatus.APPROVED: [MissionStatus.RUNNING],
        MissionStatus.FAILED: [MissionStatus.RUNNING],
    }[MissionStatus(mission.status)]
    for target in path:
        mission.status = MISSION.ensure(mission.status, target)
    mission.cancel_requested = False
    mission.status_reason = None
    mission.started_at = mission.started_at or utcnow()
    attempt = 1 + int(
        db.scalar(
            select(func.count(WorkflowRun.id)).where(
                WorkflowRun.organization_id == mission.organization_id,
                WorkflowRun.workflow == "mission",
                WorkflowRun.business_key.like(f"mission:{mission.id}:%"),
            )
        )
        or 0
    )
    run = workflow_client.start(
        db,
        organization_id=mission.organization_id,
        workflow="mission",
        business_key=f"mission:{mission.id}:{attempt}",
        payload={"mission_id": str(mission.id)},
        principal=principal,
    )
    mission.workflow_run_id = run.id
    events.emit(
        db,
        organization_id=mission.organization_id,
        mission_id=mission.id,
        project_id=mission.project_id,
        event_type=LabEventType.MISSION_STARTED,
        message=f"Mission launched (attempt {attempt}) at autonomy {mission.autonomy_level}",
        data={"workflow_run_id": str(run.id), "engine": run.engine, "attempt": attempt},
        actor=Actor.of(principal),
    )
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.launched",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
        after={"workflow_run_id": str(run.id), "autonomy_level": mission.autonomy_level},
    )
    return mission, run


def pause(db: Session, principal: Principal, mission_id: uuid.UUID | str, reason: str | None = None) -> Mission:
    principal.require("mission:run")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    mission.status = MISSION.ensure(mission.status, MissionStatus.PAUSED)
    mission.status_reason = reason
    events.emit(
        db,
        organization_id=mission.organization_id,
        mission_id=mission.id,
        project_id=mission.project_id,
        event_type=LabEventType.MISSION_PAUSED,
        message="Mission paused" + (f": {reason}" if reason else ""),
        actor=Actor.of(principal),
    )
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.paused",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
    )
    return mission


def resume(db: Session, principal: Principal, mission_id: uuid.UUID | str) -> Mission:
    from aegis_api.workflows import client as workflow_client

    principal.require("mission:run")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    if mission.status != MissionStatus.PAUSED:
        raise InvalidState("Only paused missions can be resumed")
    mission.status = MISSION.ensure(mission.status, MissionStatus.RUNNING)
    mission.status_reason = None
    if mission.workflow_run_id:
        workflow_client.signal(db, mission.workflow_run_id, "mission.resume", {"by": principal.actor_id})
    events.emit(
        db,
        organization_id=mission.organization_id,
        mission_id=mission.id,
        project_id=mission.project_id,
        event_type=LabEventType.MISSION_RESUMED,
        message="Mission resumed",
        actor=Actor.of(principal),
    )
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.resumed",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
    )
    return mission


def cancel(db: Session, principal: Principal, mission_id: uuid.UUID | str, reason: str | None = None) -> Mission:
    from aegis_api.workflows import client as workflow_client

    principal.require("mission:cancel")
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    mission.status = MISSION.ensure(mission.status, MissionStatus.CANCELLED)
    mission.cancel_requested = True
    mission.status_reason = reason or "cancelled by user"
    mission.completed_at = utcnow()
    if mission.workflow_run_id:
        workflow_client.cancel(db, mission.workflow_run_id)
    for approval in db.scalars(
        select(Approval).where(Approval.mission_id == mission.id, Approval.status == "pending")
    ).all():
        approval.status = "cancelled"
        approval.decision_reason = "mission cancelled"
        approval.decided_at = utcnow()
    events.emit(
        db,
        organization_id=mission.organization_id,
        mission_id=mission.id,
        project_id=mission.project_id,
        event_type=LabEventType.MISSION_CANCELLED,
        message="Mission cancelled" + (f": {reason}" if reason else ""),
        actor=Actor.of(principal),
    )
    audit_log.record(
        db,
        organization_id=mission.organization_id,
        action="lab.mission.cancelled",
        resource_type="mission",
        resource_id=mission.id,
        principal=principal,
        after={"reason": reason},
    )
    return mission


def versions(db: Session, mission: Mission) -> list[MissionVersion]:
    return list(
        db.scalars(
            select(MissionVersion).where(MissionVersion.mission_id == mission.id).order_by(MissionVersion.version)
        ).all()
    )


def observability(db: Session, mission: Mission) -> dict[str, Any]:
    """Operational view of a mission: phase, agents, costs, experiments, failures, approvals, workflow state."""

    def count_by(model: Any, column: Any) -> dict[str, int]:
        return {
            str(k): int(v)
            for k, v in db.execute(
                select(column, func.count(model.id)).where(model.mission_id == mission.id).group_by(column)
            ).all()
        }

    workflow = db.get(WorkflowRun, mission.workflow_run_id) if mission.workflow_run_id else None
    agent_totals = db.execute(
        select(
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.input_tokens), 0),
            func.coalesce(func.sum(AgentRun.output_tokens), 0),
        ).where(AgentRun.mission_id == mission.id)
    ).one()
    budget = usage.check_mission_budget(db, mission)
    return {
        "mission_id": str(mission.id),
        "status": mission.status,
        "phase": mission.phase,
        "cycle": mission.current_cycle,
        "max_cycles": mission.max_cycles,
        "autonomy_level": mission.autonomy_level,
        "workflow": {
            "id": str(workflow.id),
            "engine": workflow.engine,
            "status": workflow.status,
            "waiting_on": workflow.waiting_on,
            "attempts": workflow.attempts,
        }
        if workflow
        else None,
        "agents": {
            "runs": int(agent_totals[0]),
            "by_status": count_by(AgentRun, AgentRun.status),
            "by_role": count_by(AgentRun, AgentRun.role),
            "input_tokens": int(agent_totals[1]),
            "output_tokens": int(agent_totals[2]),
        },
        "hypotheses": count_by(Hypothesis, Hypothesis.status),
        "experiments": count_by(Experiment, Experiment.status),
        "experiment_runs": count_by(ExperimentRun, ExperimentRun.status),
        "failures": count_by(Failure, Failure.failure_type),
        "approvals_pending": int(
            db.scalar(
                select(func.count(Approval.id)).where(Approval.mission_id == mission.id, Approval.status == "pending")
            )
            or 0
        ),
        "usage": usage.summary(db, mission.organization_id, mission_id=mission.id),
        "budget": budget.to_dict(),
        "events": int(db.scalar(select(func.count(LabEvent.id)).where(LabEvent.mission_id == mission.id)) or 0),
        "evidence": {"head_hash": mission.evidence_head_hash, "records": mission.evidence_seq},
    }


def get(db: Session, principal: Principal, mission_id: uuid.UUID | str) -> Mission:
    return get_scoped(db, principal, Mission, mission_id, label="Mission")
