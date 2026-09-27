"""Evidence and report endpoints (evidence is immutable; sensitive values require a permission)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound
from aegis_api.models import Evidence, EvidenceLink, Report
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.audits import EvidenceOut, EvidenceRevealOut, ReportOut
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.context import Principal
from aegis_api.security.crypto import decrypt_json

router = APIRouter(prefix="/api/v1", tags=["Evidence"])


@router.get("/evidence", response_model=Page[EvidenceOut])
def list_evidence(
    params: PageParams = Depends(),
    kind: str | None = Query(None),
    audit_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("evidence:read")),
    db: Session = Depends(get_db),
) -> Page[EvidenceOut]:
    stmt = select(Evidence).where(Evidence.organization_id == principal.organization_id, Evidence.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(Evidence.kind == kind)
    if audit_id:
        stmt = stmt.where(Evidence.audit_id == audit_id)
    return paginate(db, stmt.order_by(Evidence.created_at.desc()), params, EvidenceOut.model_validate)


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
def get_evidence(
    evidence_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> EvidenceOut:
    ev = db.get(Evidence, evidence_id)
    if ev is None or ev.organization_id != principal.organization_id or ev.deleted_at is not None:
        raise NotFound("Evidence not found")
    return EvidenceOut.model_validate(ev)


@router.get("/evidence/{evidence_id}/graph")
def evidence_graph(
    evidence_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> dict:
    ev = db.get(Evidence, evidence_id)
    if ev is None or ev.organization_id != principal.organization_id:
        raise NotFound("Evidence not found")
    links = db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)).all()
    return {
        "evidence": {"id": str(ev.id), "kind": ev.kind, "title": ev.title},
        "links": [
            {"target_type": lk.target_type, "target_id": str(lk.target_id), "relation": lk.relation} for lk in links
        ],
    }


@router.post("/evidence/{evidence_id}/reveal", response_model=EvidenceRevealOut)
def reveal_evidence(
    evidence_id: uuid.UUID, principal: Principal = Depends(require("evidence:reveal")), db: Session = Depends(get_db)
) -> EvidenceRevealOut:
    ev = db.get(Evidence, evidence_id)
    if ev is None or ev.organization_id != principal.organization_id:
        raise NotFound("Evidence not found")
    from aegis_api.services import audit_log

    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="evidence.revealed",
        resource_type="evidence",
        resource_id=ev.id,
        principal=principal,
    )
    content = decrypt_json(ev.sensitive_ciphertext) if ev.sensitive_ciphertext else ev.content
    return EvidenceRevealOut(id=str(ev.id), content=content)


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(
    report_id: uuid.UUID, principal: Principal = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> ReportOut:
    report = db.get(Report, report_id)
    if report is None or report.organization_id != principal.organization_id:
        raise NotFound("Report not found")
    return ReportOut.model_validate(report)


@router.get("/reports/{report_id}/export")
def export_report(
    report_id: uuid.UUID,
    format: str = Query("json", pattern="^(json|pdf)$"),
    principal: Principal = Depends(require("reports:read")),
    db: Session = Depends(get_db),
):
    report = db.get(Report, report_id)
    if report is None or report.organization_id != principal.organization_id:
        raise NotFound("Report not found")
    if format == "json":
        return PlainTextResponse(
            json.dumps(report.content, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=report-{report_id}.json"},
        )
    from aegis_api.services.pdf_report import render_report_pdf

    pdf = render_report_pdf(report)
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report-{report_id}.pdf"},
    )
