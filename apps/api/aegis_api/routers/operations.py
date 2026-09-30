"""Agents/traces, red team, monitoring and alert endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, principal_identity, require
from aegis_api.errors import NotFound
from aegis_api.models import (
    AgentTrace,
    Alert,
    Monitor,
    RedTeamProbe,
    RedTeamRun,
)
from aegis_api.models.enums import AlertStatus
from aegis_api.ratelimit import expensive_rate_limit_for
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.findings import (
    AgentTraceDetail,
    AgentTraceOut,
    AlertOut,
    MonitorCreate,
    MonitoringIngest,
    MonitoringOverview,
    MonitorOut,
    RedTeamProbeOut,
    RedTeamRunCreate,
    RedTeamRunOut,
    ToolCallOut,
    TraceEventOut,
    TraceIngest,
)
from aegis_api.security.context import Principal
from aegis_api.services import agent_trace_service, monitoring_service, redteam_service

router = APIRouter(prefix="/api/v1", tags=["Operations"])
_expensive = expensive_rate_limit_for(principal_identity)


# --- agents / traces ---------------------------------------------------------------------------
@router.get("/agents/{system_id}/traces", response_model=Page[AgentTraceOut])
def agent_traces(
    system_id: uuid.UUID,
    params: PageParams = Depends(),
    principal: Principal = Depends(require("traces:read")),
    db: Session = Depends(get_db),
) -> Page[AgentTraceOut]:
    stmt = (
        select(AgentTrace)
        .where(AgentTrace.organization_id == principal.organization_id, AgentTrace.system_id == system_id)
        .order_by(AgentTrace.started_at.desc())
    )
    return paginate(db, stmt, params, AgentTraceOut.model_validate)


@router.get("/traces/{trace_id}", response_model=AgentTraceDetail)
def get_trace(
    trace_id: uuid.UUID, principal: Principal = Depends(require("traces:read")), db: Session = Depends(get_db)
) -> AgentTraceDetail:
    trace, events, tools = agent_trace_service.get_trace_detail(db, trace_id, principal.organization_id)
    detail = AgentTraceDetail.model_validate(trace)
    detail.events = [TraceEventOut.model_validate(e) for e in events]
    detail.tool_calls = [ToolCallOut.model_validate(t) for t in tools]
    return detail


@router.post("/traces", response_model=AgentTraceOut, status_code=201)
def ingest_trace(
    body: TraceIngest, principal: Principal = Depends(require("traces:write")), db: Session = Depends(get_db)
) -> AgentTraceOut:
    return AgentTraceOut.model_validate(agent_trace_service.ingest_trace(db, principal.organization_id, body))


# --- red team ----------------------------------------------------------------------------------
@router.get("/redteam/runs", response_model=Page[RedTeamRunOut])
def list_redteam(
    params: PageParams = Depends(),
    principal: Principal = Depends(require("redteam:run")),
    db: Session = Depends(get_db),
) -> Page[RedTeamRunOut]:
    stmt = (
        select(RedTeamRun)
        .where(RedTeamRun.organization_id == principal.organization_id)
        .order_by(RedTeamRun.created_at.desc())
    )
    return paginate(db, stmt, params, RedTeamRunOut.model_validate)


@router.post("/redteam/runs", response_model=RedTeamRunOut, status_code=201, dependencies=[Depends(_expensive)])
def create_redteam(
    body: RedTeamRunCreate, principal: Principal = Depends(require("redteam:run")), db: Session = Depends(get_db)
) -> RedTeamRunOut:
    return RedTeamRunOut.model_validate(redteam_service.create_run(db, principal, body))


@router.get("/redteam/runs/{run_id}", response_model=RedTeamRunOut)
def get_redteam(
    run_id: uuid.UUID, principal: Principal = Depends(require("redteam:run")), db: Session = Depends(get_db)
) -> RedTeamRunOut:
    run = db.get(RedTeamRun, run_id)
    if run is None or run.organization_id != principal.organization_id:
        raise NotFound("Red team run not found")
    return RedTeamRunOut.model_validate(run)


@router.get("/redteam/runs/{run_id}/probes", response_model=list[RedTeamProbeOut])
def redteam_probes(
    run_id: uuid.UUID, principal: Principal = Depends(require("redteam:run")), db: Session = Depends(get_db)
) -> list[RedTeamProbeOut]:
    run = db.get(RedTeamRun, run_id)
    if run is None or run.organization_id != principal.organization_id:
        raise NotFound("Red team run not found")
    probes = db.scalars(
        select(RedTeamProbe).where(RedTeamProbe.run_id == run_id).order_by(RedTeamProbe.depth, RedTeamProbe.created_at)
    ).all()
    return [RedTeamProbeOut.model_validate(p) for p in probes]


# --- monitoring --------------------------------------------------------------------------------
@router.get("/monitoring/overview", response_model=MonitoringOverview)
def monitoring_overview(
    days: int = Query(7, ge=1, le=90),
    system_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("monitoring:read")),
    db: Session = Depends(get_db),
) -> MonitoringOverview:
    data = monitoring_service.overview(db, principal.organization_id, days=days, system_id=system_id)
    data["recent_alerts"] = [AlertOut.model_validate(a) for a in data["recent_alerts"]]
    return MonitoringOverview(**data)


@router.post("/monitoring/events", response_model=Message, status_code=202, dependencies=[Depends(_expensive)])
def ingest_monitoring(
    body: MonitoringIngest, principal: Principal = Depends(require("monitoring:ingest")), db: Session = Depends(get_db)
) -> Message:
    event = monitoring_service.ingest_event(db, principal.organization_id, body)
    return Message(message=f"Recorded (evaluated={event.evaluated})")


@router.get("/monitors", response_model=list[MonitorOut])
def list_monitors(
    principal: Principal = Depends(require("monitoring:read")), db: Session = Depends(get_db)
) -> list[MonitorOut]:
    monitors = db.scalars(select(Monitor).where(Monitor.organization_id == principal.organization_id)).all()
    return [MonitorOut.model_validate(m) for m in monitors]


@router.post("/monitors", response_model=MonitorOut, status_code=201)
def create_monitor(
    body: MonitorCreate, principal: Principal = Depends(require("monitoring:manage")), db: Session = Depends(get_db)
) -> MonitorOut:
    return MonitorOut.model_validate(monitoring_service.create_monitor(db, principal.organization_id, body))


@router.get("/alerts", response_model=Page[AlertOut])
def list_alerts(
    params: PageParams = Depends(),
    status: str | None = Query(None),
    principal: Principal = Depends(require("monitoring:read")),
    db: Session = Depends(get_db),
) -> Page[AlertOut]:
    stmt = select(Alert).where(Alert.organization_id == principal.organization_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    return paginate(db, stmt.order_by(Alert.triggered_at.desc()), params, AlertOut.model_validate)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(
    alert_id: uuid.UUID, principal: Principal = Depends(require("monitoring:manage")), db: Session = Depends(get_db)
) -> AlertOut:
    alert = db.get(Alert, alert_id)
    if alert is None or alert.organization_id != principal.organization_id:
        raise NotFound("Alert not found")
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by_id = principal.fk_user_id
    return AlertOut.model_validate(alert)
