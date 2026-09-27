"""Findings, remediation and regression endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound
from aegis_api.models import (
    Evidence,
    EvidenceLink,
    Finding,
    FindingEvent,
    Remediation,
)
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.audits import EvidenceOut
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.findings import (
    FindingEventOut,
    FindingOut,
    FindingSummary,
    FindingUpdate,
    RegressionRunOut,
    RemediationCreate,
    RemediationOut,
    RemediationRecommendationOut,
)
from aegis_api.security.context import Principal
from aegis_api.services import finding_service, regression_service, report_service

router = APIRouter(prefix="/api/v1", tags=["Findings"])


def _get_finding(db: Session, finding_id: uuid.UUID, org_id: uuid.UUID) -> Finding:
    finding = db.get(Finding, finding_id)
    if finding is None or finding.organization_id != org_id:
        raise NotFound("Finding not found")
    return finding


@router.get("/findings", response_model=Page[FindingSummary])
def list_findings(
    params: PageParams = Depends(),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    category: str | None = Query(None),
    system_id: uuid.UUID | None = Query(None),
    risk_level: str | None = Query(None),
    principal: Principal = Depends(require("findings:read")),
    db: Session = Depends(get_db),
) -> Page[FindingSummary]:
    stmt = select(Finding).where(Finding.organization_id == principal.organization_id)
    for column, value in (
        (Finding.severity, severity),
        (Finding.status, status),
        (Finding.category, category),
        (Finding.risk_level, risk_level),
    ):
        if value:
            stmt = stmt.where(column == value)
    if system_id:
        stmt = stmt.where(Finding.system_id == system_id)
    from engines.common.types import SEVERITY_RANK  # noqa: F401

    return paginate(
        db, stmt.order_by(Finding.risk_score.desc(), Finding.created_at.desc()), params, FindingSummary.model_validate
    )


@router.get("/findings/export")
def export_findings(
    principal: Principal = Depends(require("export:data")), db: Session = Depends(get_db)
) -> PlainTextResponse:
    findings = db.scalars(
        select(Finding).where(Finding.organization_id == principal.organization_id).order_by(Finding.risk_score.desc())
    ).all()
    csv = report_service.findings_to_csv([report_service._finding_dict(f) for f in findings])
    return PlainTextResponse(
        csv, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=findings.csv"}
    )


@router.get("/findings/{finding_id}", response_model=FindingOut)
def get_finding(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> FindingOut:
    return FindingOut.model_validate(_get_finding(db, finding_id, principal.organization_id))


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def update_finding(
    finding_id: uuid.UUID,
    body: FindingUpdate,
    principal: Principal = Depends(require("findings:write")),
    db: Session = Depends(get_db),
) -> FindingOut:
    finding = _get_finding(db, finding_id, principal.organization_id)
    if body.status == "accepted_risk":
        principal.require("findings:accept_risk")
    assignee = uuid.UUID(body.assignee_id) if body.assignee_id else None
    finding_service.transition(
        db,
        finding,
        status=body.status,
        principal=principal,
        note=body.note,
        assignee_id=assignee,
        due_date=body.due_date,
        request_id=principal.request_id,
    )
    return FindingOut.model_validate(finding)


@router.get("/findings/{finding_id}/events", response_model=list[FindingEventOut])
def finding_events(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> list[FindingEventOut]:
    _get_finding(db, finding_id, principal.organization_id)
    events = db.scalars(
        select(FindingEvent).where(FindingEvent.finding_id == finding_id).order_by(FindingEvent.created_at)
    ).all()
    return [FindingEventOut.model_validate(e) for e in events]


@router.get("/findings/{finding_id}/evidence", response_model=list[EvidenceOut])
def finding_evidence(
    finding_id: uuid.UUID, principal: Principal = Depends(require("evidence:read")), db: Session = Depends(get_db)
) -> list[EvidenceOut]:
    _get_finding(db, finding_id, principal.organization_id)
    ev_ids = db.scalars(
        select(EvidenceLink.evidence_id).where(
            EvidenceLink.target_type == "finding", EvidenceLink.target_id == finding_id
        )
    ).all()
    evidence = db.scalars(
        select(Evidence).where(Evidence.id.in_(ev_ids), Evidence.deleted_at.is_(None)).order_by(Evidence.seq)
    ).all()
    return [EvidenceOut.model_validate(e) for e in evidence]


@router.get("/findings/{finding_id}/remediations", response_model=list[RemediationOut])
def finding_remediations(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> list[RemediationOut]:
    _get_finding(db, finding_id, principal.organization_id)
    rems = db.scalars(
        select(Remediation).where(Remediation.finding_id == finding_id).order_by(Remediation.created_at)
    ).all()
    return [RemediationOut.model_validate(r) for r in rems]


@router.get("/findings/{finding_id}/recommendation", response_model=RemediationRecommendationOut)
def recommendation(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> RemediationRecommendationOut:
    finding = _get_finding(db, finding_id, principal.organization_id)
    return RemediationRecommendationOut(**regression_service.recommend_for_finding(finding))


@router.post("/findings/{finding_id}/remediations", response_model=RemediationOut, status_code=201)
def create_remediation(
    finding_id: uuid.UUID,
    body: RemediationCreate,
    principal: Principal = Depends(require("remediations:write")),
    db: Session = Depends(get_db),
) -> RemediationOut:
    finding = _get_finding(db, finding_id, principal.organization_id)
    remediation = regression_service.create_remediation(db, principal, finding, body)
    db.flush()
    return RemediationOut.model_validate(remediation)


@router.post("/remediations/{remediation_id}/apply", response_model=RemediationOut)
def apply_remediation(
    remediation_id: uuid.UUID,
    principal: Principal = Depends(require("remediations:approve")),
    db: Session = Depends(get_db),
) -> RemediationOut:
    remediation = db.get(Remediation, remediation_id)
    if remediation is None or remediation.organization_id != principal.organization_id:
        raise NotFound("Remediation not found")
    updated, _ = regression_service.approve_and_apply(db, principal, remediation)
    return RemediationOut.model_validate(updated)


@router.post("/findings/{finding_id}/regression-test", response_model=Message, status_code=201)
def create_regression_test(
    finding_id: uuid.UUID, principal: Principal = Depends(require("tests:write")), db: Session = Depends(get_db)
) -> Message:
    finding = _get_finding(db, finding_id, principal.organization_id)
    test = regression_service.create_regression_test(db, principal, finding)
    return Message(message=f"Regression test created: {test.name}")


@router.post("/findings/{finding_id}/retest", response_model=RegressionRunOut, status_code=201)
def retest(
    finding_id: uuid.UUID, principal: Principal = Depends(require("audits:run")), db: Session = Depends(get_db)
) -> RegressionRunOut:
    finding = _get_finding(db, finding_id, principal.organization_id)
    from aegis_api.models import AISystem

    system = db.get(AISystem, finding.system_id)
    if system is None:
        raise NotFound("System not found")
    # Ensure a regression test exists for this finding.
    from aegis_api.models import RegressionTest

    if not db.scalar(select(RegressionTest.id).where(RegressionTest.finding_id == finding.id)):
        regression_service.create_regression_test(db, principal, finding)
    run = regression_service.run_regression(db, principal, system, finding)
    return RegressionRunOut.model_validate(run)


@router.get("/regression-runs/{run_id}", response_model=RegressionRunOut)
def get_regression_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> RegressionRunOut:
    from aegis_api.models import RegressionRun

    run = db.get(RegressionRun, run_id)
    if run is None or run.organization_id != principal.organization_id:
        raise NotFound("Regression run not found")
    return RegressionRunOut.model_validate(run)
