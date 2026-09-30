"""Mission budgets: checks for callers about to spend, and enforcement that pauses a running mission.

Limits come from ``missions.budget`` (+ time budget and deadline), tightened by the project's
``projects.budget`` ceiling. Spend comes from the mission's atomic SQL counters (read fresh, never from a
possibly stale ORM instance). Project ceilings are additionally enforced on the project's aggregate
spend, computed from the append-only ledgers (the source of truth).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import BudgetExceeded
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.governance.durable import jsonable, on_rollback
from aegis_api.lab.governance.schemas import BudgetDimensionOut, BudgetStateOut, ProjectBudgetOut
from aegis_api.lab.models import ComputeUsage, LabEvent, Mission, ModelUsage, Project, ToolInvocation
from aegis_api.lab.observability import metrics
from engines.lab.budget import (
    MONEY_DIMENSIONS,
    BudgetLimits,
    BudgetSpend,
    BudgetState,
    BudgetVerdict,
    normalize_kind,
    to_money,
)
from engines.lab.states import MissionStatus, assert_transition

log = structlog.get_logger("aegis.lab.governance")


@dataclass(frozen=True)
class BudgetCheck:
    """Result of :func:`check_budget`. ``remaining_usd`` is ``None`` when no money limit applies."""

    ok: bool
    reason: str | None
    remaining_usd: Decimal | None
    utilization: float | None = None
    kind: str = "total"
    dimension: str | None = None
    warnings: tuple[str, ...] = ()
    scope: str = "mission"  # mission | project


# ---------------------------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------------------------
def _fresh_spend(db: Session, mission_id: uuid.UUID) -> tuple[BudgetSpend, str, datetime | None]:
    row = db.execute(
        select(
            Mission.spent_llm_usd,
            Mission.spent_compute_usd,
            Mission.spent_tool_usd,
            Mission.experiment_count,
            Mission.research_task_count,
            Mission.status,
            Mission.started_at,
        ).where(Mission.id == mission_id)
    ).one()
    spend = BudgetSpend(
        llm_usd=Decimal(row[0] or 0),
        compute_usd=Decimal(row[1] or 0),
        tool_usd=Decimal(row[2] or 0),
        experiments=int(row[3] or 0),
        research_tasks=int(row[4] or 0),
    )
    return spend, row[5], row[6]


def _project_limits(project: Project | None) -> BudgetLimits:
    if project is None or not project.budget:
        return BudgetLimits()
    return BudgetLimits.from_budget(project.budget)


def mission_budget_state(db: Session, mission: Mission) -> BudgetState:
    """Mission limits (tightened by the project ceiling) against the mission's current counters."""
    spend, _status, started_at = _fresh_spend(db, mission.id)
    limits = BudgetLimits.from_budget(
        mission.budget, time_budget_seconds=mission.time_budget_seconds, deadline=mission.deadline
    )
    project = db.get(Project, mission.project_id)
    limits = limits.tightest(_project_limits(project))
    return BudgetState(limits=limits, spend=spend, started_at=started_at or mission.started_at)


def project_spend(db: Session, project_id: uuid.UUID) -> BudgetSpend:
    """All-time project spend from the ledgers (model_usage, compute_usage, tool_invocations)."""
    llm = db.scalar(select(func.coalesce(func.sum(ModelUsage.cost_usd), 0)).where(ModelUsage.project_id == project_id))
    compute = db.scalar(
        select(func.coalesce(func.sum(ComputeUsage.cost_usd), 0)).where(ComputeUsage.project_id == project_id)
    )
    tool = db.scalar(
        select(func.coalesce(func.sum(ToolInvocation.cost_usd), 0)).where(ToolInvocation.project_id == project_id)
    )
    return BudgetSpend(llm_usd=Decimal(llm or 0), compute_usd=Decimal(compute or 0), tool_usd=Decimal(tool or 0))


def _project_verdict(
    db: Session, project: Project | None, kind: str, estimated_usd: Decimal
) -> tuple[BudgetVerdict | None, BudgetState | None]:
    limits = _project_limits(project)
    if project is None or all(limits.limit(d) is None for d in MONEY_DIMENSIONS):
        return None, None
    state = BudgetState(limits=limits, spend=project_spend(db, project.id))
    dimension = normalize_kind(kind)
    money_kind = dimension if dimension in MONEY_DIMENSIONS else "total"
    if money_kind == "total" and dimension not in MONEY_DIMENSIONS and estimated_usd <= 0:
        return None, state  # count-only checks never consume project money
    return state.check(money_kind, estimated_usd=estimated_usd), state


def _to_check(verdict: BudgetVerdict, scope: str) -> BudgetCheck:
    return BudgetCheck(
        ok=verdict.ok,
        reason=verdict.reason,
        remaining_usd=verdict.remaining_usd,
        utilization=verdict.utilization,
        kind=verdict.kind,
        dimension=verdict.dimension,
        warnings=verdict.warnings,
        scope=scope,
    )


def check_budget(
    db: Session,
    mission: Mission,
    kind: str,
    *,
    estimated_usd: Decimal | float | int = 0,
    count: int = 0,
) -> BudgetCheck:
    """Would charging ``estimated_usd``/``count`` to ``kind`` stay within the mission (and project) budget?

    ``kind``: ``llm`` | ``compute`` | ``tool`` | ``experiment`` | ``research`` (| ``total``). Never raises for an
    exhausted budget — returns ``ok=False`` with a reason. An invalid stored budget fails closed.
    """
    try:
        dimension = normalize_kind(kind)
        estimate = to_money(estimated_usd)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    try:
        state = mission_budget_state(db, mission)
    except ValueError as exc:
        return BudgetCheck(ok=False, reason=f"invalid_budget_configuration: {exc}", remaining_usd=None, kind=dimension)
    verdict = state.check(kind, estimated_usd=estimate, count=count, now=utcnow())
    check = _to_check(verdict, "mission")
    if not check.ok:
        return check
    try:
        project_verdict, _state = _project_verdict(db, db.get(Project, mission.project_id), kind, estimate)
    except ValueError as exc:
        return BudgetCheck(
            ok=False, reason=f"invalid_project_budget_configuration: {exc}", remaining_usd=None, kind=dimension
        )
    if project_verdict is None:
        return check
    if not project_verdict.ok:
        return BudgetCheck(
            ok=False,
            reason=f"project {project_verdict.reason}",
            remaining_usd=project_verdict.remaining_usd,
            utilization=project_verdict.utilization,
            kind=dimension,
            dimension=f"project_{project_verdict.dimension}",
            warnings=check.warnings,
            scope="project",
        )
    remaining = check.remaining_usd
    if project_verdict.remaining_usd is not None:
        remaining = (
            project_verdict.remaining_usd if remaining is None else min(remaining, project_verdict.remaining_usd)
        )
    return BudgetCheck(
        ok=True,
        reason=None,
        remaining_usd=remaining,
        utilization=check.utilization,
        kind=dimension,
        warnings=check.warnings,
    )


# ---------------------------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------------------------
def _last_event_id(db: Session, mission_id: uuid.UUID, type_: str) -> int:
    return int(
        db.scalar(select(func.max(LabEvent.id)).where(LabEvent.mission_id == mission_id, LabEvent.type == type_)) or 0
    )


def _already_emitted(db: Session, mission_id: uuid.UUID, type_: str, dimension: str, limit: str) -> bool:
    """Whether ``type_`` was already emitted for this (dimension, limit) since the mission last resumed."""
    since = _last_event_id(db, mission_id, EventType.MISSION_RESUMED)
    found = db.scalar(
        select(LabEvent.id)
        .where(
            LabEvent.mission_id == mission_id,
            LabEvent.type == type_,
            LabEvent.id > since,
            LabEvent.payload["dimension"].astext == dimension,
            LabEvent.payload["limit"].astext == limit,
        )
        .limit(1)
    )
    return found is not None


def _limit_label(state: BudgetState | None, dimension: str | None) -> str:
    if state is None or dimension is None or dimension not in (*MONEY_DIMENSIONS, "experiments", "research_tasks"):
        return ""
    limit = state.limits.limit(dimension)
    return "" if limit is None else str(limit)


def _warn_thresholds(db: Session, actor: Actor, mission: Mission, state: BudgetState) -> None:
    for dimension in state.warnings():
        limit = _limit_label(state, dimension)
        if _already_emitted(db, mission.id, EventType.BUDGET_THRESHOLD, dimension, limit):
            continue
        emit(
            db,
            organization_id=mission.organization_id,
            type=EventType.BUDGET_THRESHOLD,
            payload={
                "mission_id": str(mission.id),
                "dimension": dimension,
                "limit": limit,
                "spent": str(state.spend.spent(dimension)),
                "utilization": state.utilization(dimension),
                "threshold": 0.8,
            },
            mission_id=mission.id,
            project_id=mission.project_id,
            workspace_id=mission.workspace_id,
            subject_type="mission",
            subject_id=mission.id,
            actor=actor,
        )


def _apply_exceeded(
    db: Session,
    actor: Actor,
    mission_id: uuid.UUID,
    *,
    kind: str,
    dimension: str,
    limit: str,
    details: dict[str, Any],
) -> bool:
    """Pause the mission if RUNNING and emit BUDGET_EXCEEDED (+ MISSION_PAUSED). Idempotent."""
    db.flush()
    mission = db.execute(
        select(Mission).where(Mission.id == mission_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if mission is None:
        return False
    paused = False
    if mission.status == MissionStatus.RUNNING:
        assert_transition("mission", mission.status, MissionStatus.PAUSED)
        mission.status = MissionStatus.PAUSED
        mission.status_reason = f"budget_exceeded:{kind}"
        mission.paused_at = utcnow()
        db.flush()
        paused = True

    def mission_event(type_: EventType, payload: dict[str, Any]) -> None:
        emit(
            db,
            organization_id=mission.organization_id,
            type=type_,
            payload=payload,
            mission_id=mission.id,
            project_id=mission.project_id,
            workspace_id=mission.workspace_id,
            subject_type="mission",
            subject_id=mission.id,
            actor=actor,
        )

    if paused or not _already_emitted(db, mission.id, EventType.BUDGET_EXCEEDED, dimension, limit):
        mission_event(
            EventType.BUDGET_EXCEEDED,
            {**details, "dimension": dimension, "limit": limit, "kind": kind, "mission_paused": paused},
        )
    if paused:
        mission_event(
            EventType.MISSION_PAUSED, {"reason": mission.status_reason, "previous_status": MissionStatus.RUNNING}
        )
        audit(
            db,
            actor,
            AuditAction.MISSION_PAUSED,
            "mission",
            mission.id,
            before={"status": MissionStatus.RUNNING},
            after={"status": MissionStatus.PAUSED, "reason": mission.status_reason, **details},
        )
    return paused


def enforce_budget(
    db: Session,
    actor: Actor,
    mission: Mission,
    kind: str,
    *,
    estimated_usd: Decimal | float | int = 0,
    count: int = 0,
) -> BudgetCheck:
    """:func:`check_budget`, and when not ok: pause the RUNNING mission, emit ``BUDGET_EXCEEDED`` and raise
    :class:`BudgetExceeded`. The pause survives a rollback of the caller's transaction. At ≥ 80 % of a limit
    a ``BUDGET_THRESHOLD`` warning is emitted once per (dimension, limit)."""
    check = check_budget(db, mission, kind, estimated_usd=estimated_usd, count=count)
    state: BudgetState | None
    try:
        state = mission_budget_state(db, mission)
    except ValueError:
        state = None
    if check.ok:
        if state is not None and state.warnings():
            _warn_thresholds(db, actor, mission, state)
        return check

    dimension = check.dimension or "configuration"
    metrics.BUDGET_EXCEEDED.labels(check.kind).inc()
    limit = ""
    if check.scope == "project":
        try:
            project_limits = _project_limits(db.get(Project, mission.project_id))
            raw = project_limits.limit(dimension.removeprefix("project_"))
            limit = "" if raw is None else str(raw)
        except (ValueError, KeyError):
            limit = ""
    else:
        limit = _limit_label(state, dimension)
    details = jsonable(
        {
            "reason": check.reason,
            "scope": check.scope,
            "remaining_usd": check.remaining_usd,
            "utilization": check.utilization,
            "estimated_usd": estimated_usd,
            "count": count,
        }
    )
    mission_id, kind_name = mission.id, check.kind

    def apply(session: Session) -> None:
        _apply_exceeded(session, actor, mission_id, kind=kind_name, dimension=dimension, limit=limit, details=details)

    apply(db)
    on_rollback(db, mission.organization_id, apply, user_id=actor.user_id, name="budget_exceeded")
    log.info("mission_budget_exceeded", mission_id=str(mission.id), kind=kind_name, dimension=dimension)
    raise BudgetExceeded(
        check.reason or "Budget exceeded",
        details={"mission_id": str(mission.id), "kind": kind_name, "dimension": dimension, **details},
    )


# ---------------------------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------------------------
def budget_view(db: Session, actor: Actor, mission_id: uuid.UUID | str) -> BudgetStateOut:
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    load_project(db, actor, mission.project_id, "mission:read")
    now = utcnow()
    try:
        state = mission_budget_state(db, mission)
    except ValueError as exc:
        raise ValidationFailed(f"The mission budget is invalid: {exc}") from exc
    verdict = state.check("total", now=now)
    time = state.to_dict(now)
    project = db.get(Project, mission.project_id)
    project_out: ProjectBudgetOut | None = None
    try:
        _, project_state = _project_verdict(db, project, "total", Decimal("0"))
    except ValueError:
        project_state = None
    if project is not None and project_state is not None:
        project_out = ProjectBudgetOut(
            project_id=str(project.id),
            limits={d: float(v) for d in MONEY_DIMENSIONS if (v := project_state.limits.limit(d)) is not None},
            spent_usd={d: float(project_state.spend.spent(d)) for d in MONEY_DIMENSIONS},
            exceeded=project_state.exceeded(),
        )
    _, current_status, _ = _fresh_spend(db, mission.id)
    return BudgetStateOut(
        mission_id=str(mission.id),
        mission_status=current_status,
        ok=verdict.ok and not state.exceeded(),
        reason=verdict.reason or (f"{', '.join(state.exceeded())} budget exhausted" if state.exceeded() else None),
        warnings=state.warnings(),
        exceeded=state.exceeded(),
        dimensions=[
            BudgetDimensionOut(
                dimension=s.dimension,
                limit=s.limit,
                spent=s.spent,
                remaining=s.remaining,
                utilization=s.utilization,
                status=s.status,
            )
            for s in state.dimensions()
        ],
        time_budget_seconds=time["time_budget_seconds"],
        deadline=time["deadline"],
        started_at=time["started_at"],
        elapsed_seconds=time.get("elapsed_seconds"),
        time_remaining_seconds=time.get("time_remaining_seconds"),
        time_exceeded=bool(time.get("time_exceeded")),
        deadline_passed=bool(time.get("deadline_passed")),
        project=project_out,
    )
