"""Runtime Agent Guard endpoints: ingestion, synchronous decisions, approvals, overview and traces."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api import idempotency
from aegis_api.db.base import utcnow
from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound
from aegis_api.models import AISystem, RuntimeApproval, RuntimeEvent
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.platform import (
    ApprovalDecision,
    ApprovalOut,
    RuntimeDecisionOut,
    RuntimeEventOut,
    RuntimeIngest,
    RuntimeIngestOut,
    RuntimeModeUpdate,
)
from aegis_api.security.context import Principal
from aegis_api.services import entitlements, runtime_service, system_service
from engines.runtime.schema import EVENT_TYPES, SCHEMA_VERSION, RuntimeEventIn

router = APIRouter(prefix="/api/v1", tags=["Runtime"])


@router.get("/runtime/schema")
def runtime_schema(principal: Principal = Depends(require("runtime:read"))) -> dict[str, Any]:
    """The normalized runtime event schema every adapter maps to."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_types": EVENT_TYPES,
        "modes": {
            "observe": "Record telemetry and decisions; never interfere.",
            "audit": "Record, and turn policy violations into findings with evidence.",
            "enforce": "Return block / require_approval decisions to the agent; queue approvals.",
        },
        "decisions": ["allow", "flag", "require_approval", "block"],
    }


@router.post("/runtime/events", response_model=RuntimeIngestOut, status_code=202)
def ingest_events(
    body: RuntimeIngest,
    request: Request,
    principal: Principal = Depends(require("runtime:ingest")),
    db: Session = Depends(get_db),
) -> Any:
    """Ingest a batch of runtime events (idempotent per ``event_id``). Counts toward the monthly
    runtime-event quota."""
    handle = idempotency.begin(db, principal, request, body)
    if handle.replay is not None:
        return handle.replay
    entitlements.check_quota(db, principal.organization_id, "runtime_event", increment=len(body.events))
    decisions = runtime_service.process(db, principal, body.events)
    out = RuntimeIngestOut(
        accepted=sum(1 for d in decisions if not d["duplicate"]),
        duplicates=sum(1 for d in decisions if d["duplicate"]),
        decisions=[RuntimeDecisionOut(**d) for d in decisions],
    )
    idempotency.complete(db, handle, 202, out)
    return out


@router.post("/runtime/check", response_model=RuntimeDecisionOut)
def check_event(
    body: RuntimeEventIn,
    principal: Principal = Depends(require("runtime:decide")),
    db: Session = Depends(get_db),
) -> RuntimeDecisionOut:
    """Synchronous decision for one action *before* the agent performs it (use in enforce mode).

    Never refused for quota reasons: a security control must keep answering."""
    return RuntimeDecisionOut(**runtime_service.process(db, principal, [body], synchronous=True)[0])


@router.get("/runtime/events", response_model=Page[RuntimeEventOut])
def list_events(
    params: PageParams = Depends(),
    system_id: uuid.UUID | None = Query(None),
    event_type: str | None = Query(None, max_length=48),
    decision: str | None = Query(None, max_length=20),
    agent: str | None = Query(None, max_length=160),
    trace_id: str | None = Query(None, max_length=80),
    hours: int = Query(24 * 7, ge=1, le=24 * 365),
    principal: Principal = Depends(require("runtime:read")),
    db: Session = Depends(get_db),
) -> Page[RuntimeEventOut]:
    stmt = select(RuntimeEvent).where(
        RuntimeEvent.organization_id == principal.organization_id,
        RuntimeEvent.occurred_at >= utcnow() - timedelta(hours=hours),
    )
    for column, value in (
        (RuntimeEvent.system_id, system_id),
        (RuntimeEvent.event_type, event_type),
        (RuntimeEvent.decision, decision),
        (RuntimeEvent.agent_name, agent),
        (RuntimeEvent.trace_id, trace_id),
    ):
        if value:
            stmt = stmt.where(column == value)
    return paginate(db, stmt.order_by(RuntimeEvent.occurred_at.desc()), params, RuntimeEventOut.model_validate)


@router.get("/runtime/overview")
def runtime_overview(
    hours: int = Query(24, ge=1, le=24 * 90),
    system_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("runtime:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return runtime_service.overview(db, principal.organization_id, hours=hours, system_id=system_id)


@router.get("/runtime/traces/{trace_id}", response_model=list[RuntimeEventOut])
def runtime_trace(
    trace_id: str, principal: Principal = Depends(require("runtime:read")), db: Session = Depends(get_db)
) -> list[RuntimeEventOut]:
    rows = db.scalars(
        select(RuntimeEvent)
        .where(RuntimeEvent.organization_id == principal.organization_id, RuntimeEvent.trace_id == trace_id[:80])
        .order_by(RuntimeEvent.occurred_at)
        .limit(1000)
    ).all()
    if not rows:
        raise NotFound("Trace not found")
    return [RuntimeEventOut.model_validate(r) for r in rows]


@router.get("/runtime/approvals", response_model=Page[ApprovalOut])
def list_approvals(
    params: PageParams = Depends(),
    status: str | None = Query(None, max_length=16),
    principal: Principal = Depends(require("runtime:read")),
    db: Session = Depends(get_db),
) -> Page[ApprovalOut]:
    stmt = select(RuntimeApproval).where(RuntimeApproval.organization_id == principal.organization_id)
    if status:
        stmt = stmt.where(RuntimeApproval.status == status)
    return paginate(db, stmt.order_by(RuntimeApproval.created_at.desc()), params, ApprovalOut.model_validate)


@router.get("/runtime/approvals/{approval_id}", response_model=ApprovalOut)
def get_approval(
    approval_id: uuid.UUID, principal: Principal = Depends(require("runtime:read")), db: Session = Depends(get_db)
) -> ApprovalOut:
    """Agents in enforce mode poll this until the status leaves ``pending``."""
    return ApprovalOut.model_validate(runtime_service.get_approval(db, approval_id, principal.organization_id))


@router.post("/runtime/approvals/{approval_id}/decision", response_model=ApprovalOut)
def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    principal: Principal = Depends(require("approvals:decide")),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    approval = runtime_service.get_approval(db, approval_id, principal.organization_id)
    return ApprovalOut.model_validate(runtime_service.decide_approval(db, principal, approval, body.approve, body.note))


@router.put("/systems/{system_id}/runtime-mode")
def set_runtime_mode(
    system_id: uuid.UUID,
    body: RuntimeModeUpdate,
    principal: Principal = Depends(require("runtime:manage")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    system = system_service.get_system(db, system_id, principal.organization_id)
    runtime_service.set_mode(db, principal, system, body.mode)
    return {"system_id": str(system.id), "mode": system.runtime_mode}


@router.get("/systems/{system_id}/runtime/verify")
def verify_runtime_chain(
    system_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Verify the system's runtime-decision evidence chain."""
    system: AISystem = system_service.get_system(db, system_id, principal.organization_id)
    return runtime_service.verify_runtime_chain(db, system)
