"""Missions (lifecycle, events, live SSE stream, observability, evidence), workflow runs, agents and prompts."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.deps import get_db, rate_limited, require
from aegis_api.idempotency import Idempotency, idempotency
from aegis_api.infrastructure.eventbus import get_event_bus, mission_channel
from aegis_api.infrastructure.observability import metrics
from aegis_api.models.lab import Agent, AgentRun, AgentVersion, LabEvent, Mission, PromptTemplateRecord, WorkflowRun
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.lab import (
    Accepted,
    AgentIn,
    AgentOut,
    AgentRunOut,
    AgentVersionIn,
    AgentVersionOut,
    AutonomyIn,
    EventOut,
    MissionIn,
    MissionOut,
    MissionUpdate,
    MissionVersionOut,
    PromptIn,
    ReasonIn,
    WorkflowRunOut,
)
from aegis_api.security.context import Principal
from aegis_api.services.lab import agents as agent_service
from aegis_api.services.lab import events as event_service
from aegis_api.services.lab import evidence, missions, prompts
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from engines.lab.agents.roles import ROLE_SPECS
from engines.lab.autonomy import LEVEL_DESCRIPTIONS
from engines.lab.enums import AutonomyLevel

router = APIRouter(prefix="/api/v1", tags=["Missions"])
TERMINAL_MISSION = frozenset({"completed", "failed", "cancelled", "archived"})


def _accept(idem: Idempotency, db: Session, body: Accepted) -> dict[str, Any]:
    payload = body.model_dump()
    idem.complete(db, 202, payload)
    return payload


@router.get("/autonomy-levels")
def autonomy_levels(_: Principal = Depends(require("mission:read"))) -> list[dict[str, Any]]:
    return [{"level": lvl.value, "rank": lvl.rank, "description": LEVEL_DESCRIPTIONS[lvl]} for lvl in AutonomyLevel]


@router.get("/missions", response_model=Page[MissionOut])
def list_missions(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=16),
    principal: Principal = Depends(require("mission:read")),
    db: Session = Depends(get_db),
) -> Page[MissionOut]:
    stmt = missions.list_missions(db, principal, project_id=project_id, status=status)
    return paginate(db, stmt, params, MissionOut.model_validate)


@router.post("/missions", response_model=MissionOut, status_code=201)
def create_mission(
    body: MissionIn, principal: Principal = Depends(require("mission:create")), db: Session = Depends(get_db)
) -> MissionOut:
    return MissionOut.model_validate(missions.create(db, principal, body.model_dump(exclude_none=True)))


@router.get("/missions/{mission_id}", response_model=MissionOut)
def get_mission(
    mission_id: uuid.UUID, principal: Principal = Depends(require("mission:read")), db: Session = Depends(get_db)
) -> MissionOut:
    return MissionOut.model_validate(missions.get(db, principal, mission_id))


@router.patch("/missions/{mission_id}", response_model=MissionOut)
def update_mission(
    mission_id: uuid.UUID,
    body: MissionUpdate,
    if_match: int | None = Header(default=None, alias="If-Match"),
    principal: Principal = Depends(require("mission:create")),
    db: Session = Depends(get_db),
) -> MissionOut:
    """Optimistic concurrency: send ``If-Match: <lock_version>``; a stale version returns 409."""
    if if_match is not None:
        current = missions.get(db, principal, mission_id)
        if current.lock_version != if_match:
            from aegis_api.errors import Conflict

            raise Conflict(
                "Mission was modified by someone else (lock_version mismatch)", code="concurrent_modification"
            )
    return MissionOut.model_validate(missions.update(db, principal, mission_id, body.model_dump(exclude_unset=True)))


@router.post("/missions/{mission_id}/autonomy", response_model=MissionOut)
def change_autonomy(
    mission_id: uuid.UUID,
    body: AutonomyIn,
    principal: Principal = Depends(require("mission:run")),
    db: Session = Depends(get_db),
) -> MissionOut:
    return MissionOut.model_validate(
        missions.change_autonomy(db, principal, mission_id, body.autonomy_level, body.reason)
    )


@router.post("/missions/{mission_id}/launch", response_model=Accepted, status_code=202)
def launch_mission(
    mission_id: uuid.UUID,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("mission:run")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("research")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    mission, run = missions.launch(db, principal, mission_id)
    return _accept(idem, db, Accepted(id=str(mission.id), status=mission.status, workflow_run_id=str(run.id)))


@router.post("/missions/{mission_id}/pause", response_model=MissionOut)
def pause_mission(
    mission_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require("mission:run")),
    db: Session = Depends(get_db),
) -> MissionOut:
    return MissionOut.model_validate(missions.pause(db, principal, mission_id, body.reason))


@router.post("/missions/{mission_id}/resume", response_model=MissionOut)
def resume_mission(
    mission_id: uuid.UUID, principal: Principal = Depends(require("mission:run")), db: Session = Depends(get_db)
) -> MissionOut:
    return MissionOut.model_validate(missions.resume(db, principal, mission_id))


@router.post("/missions/{mission_id}/cancel", response_model=MissionOut)
def cancel_mission(
    mission_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require("mission:cancel")),
    db: Session = Depends(get_db),
) -> MissionOut:
    return MissionOut.model_validate(missions.cancel(db, principal, mission_id, body.reason))


@router.get("/missions/{mission_id}/versions", response_model=list[MissionVersionOut])
def mission_versions(
    mission_id: uuid.UUID, principal: Principal = Depends(require("mission:read")), db: Session = Depends(get_db)
) -> list[MissionVersionOut]:
    mission = missions.get(db, principal, mission_id)
    return [MissionVersionOut.model_validate(v) for v in missions.versions(db, mission)]


@router.get("/missions/{mission_id}/observability")
def mission_observability(
    mission_id: uuid.UUID, principal: Principal = Depends(require("mission:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return missions.observability(db, missions.get(db, principal, mission_id))


@router.get("/missions/{mission_id}/events", response_model=list[EventOut])
def mission_events(
    mission_id: uuid.UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require("mission:read")),
    db: Session = Depends(get_db),
) -> list[EventOut]:
    mission = missions.get(db, principal, mission_id)
    return [
        EventOut.model_validate(e)
        for e in event_service.list_after(db, principal.organization_id, mission.id, after_seq=after, limit=limit)
    ]


@router.get("/missions/{mission_id}/events/stream", response_class=StreamingResponse)
def mission_event_stream(
    mission_id: uuid.UUID,
    request: Request,
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    principal: Principal = Depends(require("mission:read")),
) -> StreamingResponse:
    """Server-Sent Events. Each event's ``id`` is its per-mission sequence number; reconnect with the standard
    ``Last-Event-ID`` header (or ``?after=``) to resume without gaps. Events are read from the database; the
    event bus only wakes the stream up, so no event is ever lost. No database transaction or connection is held
    while the stream is idle: every poll is its own short, tenant-scoped transaction."""
    with session_scope(principal.organization_id, principal.user_id) as check:
        mission = missions.get(check, principal, mission_id)
        org, mid = principal.organization_id, mission.id
    try:
        cursor = int(last_event_id) if last_event_id else (after or 0)
    except ValueError:
        cursor = after or 0
    settings = get_settings()

    def stream() -> Iterator[bytes]:
        nonlocal cursor
        metrics.ACTIVE_STREAMS.inc()
        subscription = get_event_bus().subscribe(mission_channel(str(org), str(mid)))
        started = time.monotonic()
        last_beat = time.monotonic()
        try:
            yield b"retry: 3000\n\n"
            while time.monotonic() - started < settings.event_stream_max_seconds:
                with session_scope(org) as s:
                    batch = event_service.list_after(s, org, mid, after_seq=cursor, limit=500)
                    status = s.scalar(select(Mission.status).where(Mission.id == mid))
                    payloads = [event_service.serialize(e) for e in batch]
                for payload in payloads:
                    cursor = int(payload["id"])
                    data = json.dumps(payload, default=str)
                    yield f"id: {cursor}\nevent: {payload['event_type']}\ndata: {data}\n\n".encode()
                if not payloads and status in TERMINAL_MISSION:
                    yield b"event: stream_end\ndata: {}\n\n"
                    return
                if time.monotonic() - last_beat >= settings.event_stream_heartbeat_seconds:
                    last_beat = time.monotonic()
                    yield b": keep-alive\n\n"
                subscription.get(timeout=min(2.0, settings.event_stream_heartbeat_seconds))
        finally:
            subscription.close()
            metrics.ACTIVE_STREAMS.dec()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/missions/{mission_id}/evidence")
def mission_evidence(
    mission_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    mission = missions.get(db, principal, mission_id)
    return [
        {
            "id": str(e.id),
            "seq": e.seq,
            "kind": e.kind,
            "title": e.title,
            "content_hash": e.content_hash,
            "prev_hash": e.prev_hash,
            "chain_hash": e.chain_hash,
            "confidence_level": e.confidence_level,
            "created_at": e.created_at.isoformat(),
        }
        for e in evidence.chain(db, principal.organization_id, evidence.scope_for(mission_id=mission.id))
    ]


@router.get("/missions/{mission_id}/evidence/verify")
def verify_mission_evidence(
    mission_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    mission = missions.get(db, principal, mission_id)
    return evidence.verify(db, principal.organization_id, evidence.scope_for(mission_id=mission.id))


# --- workflow runs ---------------------------------------------------------------------------------------------


@router.get("/workflow-runs", response_model=Page[WorkflowRunOut])
def list_workflow_runs(
    params: PageParams = Depends(),
    workflow: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=16),
    principal: Principal = Depends(require("mission:read")),
    db: Session = Depends(get_db),
) -> Page[WorkflowRunOut]:
    stmt = select(WorkflowRun).where(WorkflowRun.organization_id == principal.organization_id)
    if workflow:
        stmt = stmt.where(WorkflowRun.workflow == workflow)
    if status:
        stmt = stmt.where(WorkflowRun.status == status)
    return paginate(db, stmt.order_by(WorkflowRun.created_at.desc()), params, WorkflowRunOut.model_validate)


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunOut)
def get_workflow_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("mission:read")), db: Session = Depends(get_db)
) -> WorkflowRunOut:
    return WorkflowRunOut.model_validate(get_scoped(db, principal, WorkflowRun, run_id, label="Workflow run"))


# --- agents / agent runs / prompts --------------------------------------------------------------------------------


@router.get("/agent-roles")
def agent_roles(_: Principal = Depends(require("agent:read"))) -> list[dict[str, Any]]:
    return [
        {
            "role": spec.role.value,
            "description": spec.description,
            "task_class": spec.task_class.value,
            "prompt": spec.prompt,
            "default_tools": list(spec.default_tools),
            "max_steps": spec.max_steps,
            "timeout_seconds": spec.timeout_seconds,
        }
        for spec in ROLE_SPECS.values()
    ]


@router.get("/agents", response_model=list[AgentOut])
def list_agents(principal: Principal = Depends(require("agent:read")), db: Session = Depends(get_db)) -> list[AgentOut]:
    rows = db.scalars(
        select(Agent).where(Agent.organization_id == principal.organization_id).order_by(Agent.name)
    ).all()
    return [AgentOut.model_validate(a) for a in rows]


@router.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(
    body: AgentIn, principal: Principal = Depends(require("agent:manage")), db: Session = Depends(get_db)
) -> AgentOut:
    project = get_project(db, principal, body.project_id) if body.project_id else None
    agent, _version = agent_service.create_agent(
        db,
        principal,
        role=body.role,
        name=body.name,
        description=body.description,
        config=body.config,
        project_id=project.id if project else None,
    )
    return AgentOut.model_validate(agent)


@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionOut])
def agent_versions(
    agent_id: uuid.UUID, principal: Principal = Depends(require("agent:read")), db: Session = Depends(get_db)
) -> list[AgentVersionOut]:
    agent = get_scoped(db, principal, Agent, agent_id, label="Agent")
    rows = db.scalars(
        select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version)
    ).all()
    return [AgentVersionOut.model_validate(v) for v in rows]


@router.post("/agents/{agent_id}/versions", response_model=AgentVersionOut, status_code=201)
def create_agent_version(
    agent_id: uuid.UUID,
    body: AgentVersionIn,
    principal: Principal = Depends(require("agent:manage")),
    db: Session = Depends(get_db),
) -> AgentVersionOut:
    agent = get_scoped(db, principal, Agent, agent_id, label="Agent")
    return AgentVersionOut.model_validate(
        agent_service.new_version(db, principal, agent, config=body.config, change_note=body.change_note)
    )


@router.get("/agent-runs", response_model=Page[AgentRunOut])
def list_agent_runs(
    params: PageParams = Depends(),
    mission_id: uuid.UUID | None = Query(default=None),
    role: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=16),
    principal: Principal = Depends(require("agent:read")),
    db: Session = Depends(get_db),
) -> Page[AgentRunOut]:
    stmt = select(AgentRun).where(AgentRun.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where((AgentRun.project_id.is_(None)) | (AgentRun.project_id.in_(visible)))
    if mission_id:
        stmt = stmt.where(AgentRun.mission_id == mission_id)
    if role:
        stmt = stmt.where(AgentRun.role == role)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    return paginate(db, stmt.order_by(AgentRun.created_at.desc()), params, AgentRunOut.model_validate)


@router.get("/agent-runs/{run_id}", response_model=AgentRunOut)
def get_agent_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("agent:read")), db: Session = Depends(get_db)
) -> AgentRunOut:
    return AgentRunOut.model_validate(get_scoped(db, principal, AgentRun, run_id, label="Agent run"))


@router.get("/prompts")
def list_prompts(
    principal: Principal = Depends(require("agent:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return prompts.catalog(db, principal.organization_id)


@router.post("/prompts", status_code=201)
def register_prompt(
    body: PromptIn, principal: Principal = Depends(require("agent:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = prompts.register(
        db,
        principal,
        name=body.name,
        version=body.version,
        template=body.template,
        variables=body.variables,
        description=body.description,
    )
    return {"id": str(row.id), "name": row.name, "version": row.version, "sha256": row.sha256}


@router.post("/prompts/{prompt_id}/retire")
def retire_prompt(
    prompt_id: uuid.UUID, principal: Principal = Depends(require("agent:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = db.get(PromptTemplateRecord, prompt_id)
    if row is None or row.organization_id != principal.organization_id:
        from aegis_api.errors import NotFound

        raise NotFound("Prompt template not found")
    prompts.set_status(db, principal, row, "retired")
    return {"id": str(row.id), "status": row.status}


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(
    event_id: uuid.UUID, principal: Principal = Depends(require("mission:read")), db: Session = Depends(get_db)
) -> EventOut:
    event = db.get(LabEvent, event_id)
    if event is None or event.organization_id != principal.organization_id:
        from aegis_api.errors import NotFound

        raise NotFound("Event not found")
    if event.mission_id:
        missions.get(db, principal, event.mission_id)
    return EventOut.model_validate(event)
