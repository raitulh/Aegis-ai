"""Audit endpoints, including the live SSE progress stream."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api import idempotency
from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.deps import get_db, principal_identity, require
from aegis_api.errors import NotFound
from aegis_api.models import Audit, AuditEvent, Claim, Evidence, Report, TestResult
from aegis_api.models.enums import TERMINAL_AUDIT_STATUSES
from aegis_api.ratelimit import expensive_rate_limit_for
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.audits import (
    AuditCompareOut,
    AuditCreate,
    AuditEventOut,
    AuditOut,
    AuditSummary,
    ClaimOut,
    EvidenceOut,
    ReportOut,
    TestMatrixRow,
    TestResultOut,
)
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.findings import AuditFindingOut, FindingSummary
from aegis_api.security.context import Principal
from aegis_api.services import audit_service

router = APIRouter(prefix="/api/v1", tags=["Audits"])
_expensive = expensive_rate_limit_for(principal_identity)


@router.get("/audits", response_model=Page[AuditSummary])
def list_audits(
    params: PageParams = Depends(),
    status: str | None = Query(None),
    system_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("audits:read")),
    db: Session = Depends(get_db),
) -> Page[AuditSummary]:
    stmt = select(Audit).where(Audit.organization_id == principal.organization_id)
    if status:
        stmt = stmt.where(Audit.status == status)
    if system_id:
        stmt = stmt.where(Audit.system_id == system_id)
    return paginate(db, stmt.order_by(Audit.created_at.desc()), params, AuditSummary.model_validate)


@router.post("/audits", response_model=AuditOut, status_code=201, dependencies=[Depends(_expensive)])
def create_audit(
    body: AuditCreate,
    request: Request,
    principal: Principal = Depends(require("audits:run")),
    db: Session = Depends(get_db),
) -> Any:
    """Create (and by default start) an audit. Send ``Idempotency-Key`` to make client retries safe."""
    handle = idempotency.begin(db, principal, request, body)
    if handle.replay is not None:
        return handle.replay
    audit = audit_service.create_audit(db, principal, body, start=body.start)
    out = AuditOut.model_validate(audit)
    idempotency.complete(db, handle, 201, out)
    return out


@router.get("/audits/{audit_id}", response_model=AuditOut)
def get_audit(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:read")), db: Session = Depends(get_db)
) -> AuditOut:
    return AuditOut.model_validate(audit_service.get_audit(db, audit_id, principal.organization_id))


@router.post("/audits/{audit_id}/cancel", response_model=AuditOut)
def cancel_audit(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:cancel")), db: Session = Depends(get_db)
) -> AuditOut:
    audit = audit_service.get_audit(db, audit_id, principal.organization_id)
    return AuditOut.model_validate(audit_service.cancel_audit(db, audit, principal))


@router.post("/audits/{audit_id}/rerun", response_model=AuditOut, dependencies=[Depends(_expensive)])
def rerun_audit(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:run")), db: Session = Depends(get_db)
) -> AuditOut:
    source = audit_service.get_audit(db, audit_id, principal.organization_id)
    from aegis_api.schemas.audits import AuditConfig

    body = AuditCreate.model_validate(
        {
            "system_id": str(source.system_id),
            "name": f"{source.name} (re-run)",
            "categories": source.categories,
            "policy_version_ids": source.policy_version_ids,
            "intensity": source.intensity,
            "config": {k: v for k, v in (source.config or {}).items() if k in AuditConfig.model_fields},
        }
    )
    return AuditOut.model_validate(audit_service.create_audit(db, principal, body, start=True))


@router.get("/audits/{audit_id}/events", response_model=list[AuditEventOut])
def audit_events(
    audit_id: uuid.UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    principal: Principal = Depends(require("audits:read")),
    db: Session = Depends(get_db),
) -> list[AuditEventOut]:
    audit_service.get_audit(db, audit_id, principal.organization_id)
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.audit_id == audit_id, AuditEvent.seq > after)
        .order_by(AuditEvent.seq)
        .limit(limit)
    ).all()
    return [AuditEventOut.model_validate(e) for e in events]


MAX_STREAM_SECONDS = 30 * 60


@router.get("/audits/{audit_id}/stream")
async def stream_audit(
    audit_id: uuid.UUID,
    request: Request,
    after: int = Query(0, ge=0),
    principal: Principal = Depends(require("audits:read")),
) -> StreamingResponse:
    """Server-Sent Events stream of audit progress.

    * Resumable: honours ``Last-Event-ID`` (sent automatically by ``EventSource`` on reconnect) or ``?after=``,
      so a reconnect never replays or skips events.
    * Wakes on Redis pub/sub notifications when configured, otherwise polls; the event log in Postgres is the
      source of truth either way, so it works across API replicas and worker restarts.
    * Sends a heartbeat comment every ``SSE_HEARTBEAT_SECONDS`` and closes after 30 minutes (clients resume).
    * Holds no database connection between polls.
    """
    from aegis_api.realtime import hub

    org_id = principal.organization_id
    header_id = request.headers.get("last-event-id", "")
    last_seq = int(header_id) if header_id.isdigit() else after
    exists = await asyncio.to_thread(_audit_exists, org_id, audit_id)
    if not exists:
        raise NotFound("Audit not found")
    heartbeat = max(5, get_settings().sse_heartbeat_seconds)

    async def event_generator():
        nonlocal last_seq
        hub.connections += 1
        started = time.monotonic()
        last_sent = time.monotonic()
        try:
            yield "retry: 3000\n: aegis audit stream\n\n"
            while True:
                if await request.is_disconnected():
                    break
                events, terminal = await asyncio.to_thread(_fetch_events, org_id, audit_id, last_seq)
                for ev in events:
                    last_seq = max(last_seq, ev["seq"])
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    last_sent = time.monotonic()
                if terminal and not events:
                    yield f"event: done\ndata: {json.dumps({'audit_id': str(audit_id), 'last_seq': last_seq})}\n\n"
                    break
                if time.monotonic() - started > MAX_STREAM_SECONDS:
                    break
                if time.monotonic() - last_sent >= heartbeat:
                    yield ": ping\n\n"
                    last_sent = time.monotonic()
                if not events:
                    await hub.wait(audit_id, timeout=min(heartbeat, 5.0))
        finally:
            hub.connections -= 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _tenant_session(org_id: uuid.UUID) -> Session:
    session = session_factory()()
    session.begin()
    set_tenant(session, org_id, None)
    return session


def _audit_exists(org_id: uuid.UUID, audit_id: uuid.UUID) -> bool:
    session = _tenant_session(org_id)
    try:
        return session.scalar(select(Audit.id).where(Audit.id == audit_id, Audit.organization_id == org_id)) is not None
    finally:
        session.rollback()
        session.close()


def _fetch_events(org_id: uuid.UUID, audit_id: uuid.UUID, after: int) -> tuple[list[dict], bool]:
    session = _tenant_session(org_id)
    try:
        status = session.scalar(select(Audit.status).where(Audit.id == audit_id, Audit.organization_id == org_id))
        if status is None:
            return [], True
        rows = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.audit_id == audit_id, AuditEvent.seq > after)
            .order_by(AuditEvent.seq)
            .limit(500)
        ).all()
        events = [
            {
                "seq": r.seq,
                "type": r.type,
                "stage": r.stage,
                "level": r.level,
                "message": r.message,
                "progress": r.progress,
                "data": r.data,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return events, status in TERMINAL_AUDIT_STATUSES
    finally:
        session.rollback()
        session.close()


@router.get("/audits/{audit_id}/results", response_model=Page[TestResultOut])
def audit_results(
    audit_id: uuid.UUID,
    params: PageParams = Depends(),
    category: str | None = Query(None),
    status: str | None = Query(None),
    principal: Principal = Depends(require("audits:read")),
    db: Session = Depends(get_db),
) -> Page[TestResultOut]:
    audit_service.get_audit(db, audit_id, principal.organization_id)
    stmt = select(TestResult).where(TestResult.audit_id == audit_id)
    if category:
        stmt = stmt.where(TestResult.category == category)
    if status:
        stmt = stmt.where(TestResult.status == status)
    return paginate(db, stmt.order_by(TestResult.created_at), params, TestResultOut.model_validate)


@router.get("/audits/{audit_id}/matrix", response_model=list[TestMatrixRow])
def audit_test_matrix(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:read")), db: Session = Depends(get_db)
) -> list[TestMatrixRow]:
    audit = audit_service.get_audit(db, audit_id, principal.organization_id)
    return [TestMatrixRow(**row) for row in audit_service.audit_matrix(audit)]


@router.get("/audits/{audit_id}/claims", response_model=list[ClaimOut])
def audit_claims(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:read")), db: Session = Depends(get_db)
) -> list[ClaimOut]:
    audit_service.get_audit(db, audit_id, principal.organization_id)
    claims = db.scalars(select(Claim).where(Claim.audit_id == audit_id)).all()
    return [ClaimOut.model_validate(c) for c in claims]


@router.get("/audits/{audit_id}/evidence", response_model=list[EvidenceOut])
def audit_evidence(
    audit_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> list[EvidenceOut]:
    audit_service.get_audit(db, audit_id, principal.organization_id)
    evidence = db.scalars(
        select(Evidence).where(Evidence.audit_id == audit_id, Evidence.deleted_at.is_(None)).order_by(Evidence.seq)
    ).all()
    return [EvidenceOut.model_validate(e) for e in evidence]


@router.get("/audits/{audit_id}/report", response_model=ReportOut)
def audit_report(
    audit_id: uuid.UUID, principal: Principal = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> ReportOut:
    audit_service.get_audit(db, audit_id, principal.organization_id)
    report = db.scalar(select(Report).where(Report.audit_id == audit_id).order_by(Report.created_at.desc()))
    if report is None:
        raise NotFound("Report not available yet")
    return ReportOut.model_validate(report)


@router.get("/audits/{audit_id}/compare/{other_id}", response_model=AuditCompareOut)
def compare(
    audit_id: uuid.UUID,
    other_id: uuid.UUID,
    principal: Principal = Depends(require("audits:read")),
    db: Session = Depends(get_db),
) -> AuditCompareOut:
    a = audit_service.get_audit(db, audit_id, principal.organization_id)
    b = audit_service.get_audit(db, other_id, principal.organization_id)
    return AuditCompareOut(**audit_service.compare_audits(db, a, b))


@router.get("/audits/{audit_id}/findings", response_model=list[AuditFindingOut])
def audit_findings(
    audit_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> list[AuditFindingOut]:
    """Findings observed by this audit, with the severity and counts this audit recorded."""
    audit_service.get_audit(db, audit_id, principal.organization_id)
    from aegis_api.models import Finding, FindingOccurrence

    rows = db.execute(
        select(Finding, FindingOccurrence)
        .join(FindingOccurrence, FindingOccurrence.finding_id == Finding.id)
        .where(FindingOccurrence.audit_id == audit_id, FindingOccurrence.source_type == "audit")
        .order_by(Finding.risk_score.desc())
    ).all()
    return [
        AuditFindingOut(
            **FindingSummary.model_validate(f).model_dump(),
            observed_severity=o.severity,
            observed_risk_level=o.risk_level,
            observed_occurrences=o.occurrences,
            observed_sample_size=o.sample_size,
            first_detected_here=f.audit_id == audit_id,
        )
        for f, o in rows
    ]
