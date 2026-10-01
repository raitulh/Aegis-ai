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
from aegis_api.schemas.platform import BulkFindingUpdate, CommentCreate, CommentOut
from aegis_api.security.context import Principal
from aegis_api.services import finding_service, regression_service, report_service

router = APIRouter(prefix="/api/v1", tags=["Findings"])


def _get_finding(db: Session, finding_id: uuid.UUID, org_id: uuid.UUID) -> Finding:
    finding = db.get(Finding, finding_id)
    if finding is None or finding.organization_id != org_id:
        raise NotFound("Finding not found")
    return finding


SORTS = {
    "risk": (Finding.risk_score.desc(), Finding.created_at.desc()),
    "newest": (Finding.created_at.desc(),),
    "oldest": (Finding.created_at.asc(),),
    "sla": (Finding.sla_due_at.asc().nulls_last(), Finding.risk_score.desc()),
    "number": (Finding.number.desc(),),
}


def _filtered(
    principal: Principal,
    *,
    severity: str | None = None,
    status: str | None = None,
    category: str | None = None,
    system_id: uuid.UUID | None = None,
    risk_level: str | None = None,
    audit_id: uuid.UUID | None = None,
    source: str | None = None,
    tag: str | None = None,
    assignee_id: uuid.UUID | None = None,
    q: str | None = None,
    open_only: bool = False,
):
    from aegis_api.models import FindingOccurrence
    from aegis_api.models.enums import OPEN_FINDING_STATUSES

    stmt = select(Finding).where(Finding.organization_id == principal.organization_id)
    for column, value in (
        (Finding.severity, severity),
        (Finding.category, category),
        (Finding.risk_level, risk_level),
        (Finding.source, source),
    ):
        if value:
            stmt = stmt.where(column == value)
    if status:
        stmt = stmt.where(Finding.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if open_only:
        stmt = stmt.where(Finding.status.in_([str(s) for s in OPEN_FINDING_STATUSES]))
    if system_id:
        stmt = stmt.where(Finding.system_id == system_id)
    if assignee_id:
        stmt = stmt.where(Finding.assignee_id == assignee_id)
    if tag:
        stmt = stmt.where(Finding.tags.contains([tag.lower()]))
    if q:
        like = f"%{q[:100]}%"
        stmt = stmt.where(Finding.title.ilike(like) | Finding.description.ilike(like))
    if audit_id:
        stmt = stmt.where(
            Finding.id.in_(select(FindingOccurrence.finding_id).where(FindingOccurrence.audit_id == audit_id))
        )
    return stmt


@router.get("/findings", response_model=Page[FindingSummary])
def list_findings(
    params: PageParams = Depends(),
    severity: str | None = Query(None),
    status: str | None = Query(None, description="Comma-separated statuses"),
    category: str | None = Query(None),
    system_id: uuid.UUID | None = Query(None),
    risk_level: str | None = Query(None),
    audit_id: uuid.UUID | None = Query(None, description="Findings observed by this audit"),
    source: str | None = Query(None, pattern="^(audit|redteam|runtime|regression|manual)$"),
    tag: str | None = Query(None, max_length=40),
    assignee_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=100),
    open_only: bool = Query(False),
    sort: str = Query("risk", pattern="^(risk|newest|oldest|sla|number)$"),
    principal: Principal = Depends(require("findings:read")),
    db: Session = Depends(get_db),
) -> Page[FindingSummary]:
    stmt = _filtered(
        principal,
        severity=severity,
        status=status,
        category=category,
        system_id=system_id,
        risk_level=risk_level,
        audit_id=audit_id,
        source=source,
        tag=tag,
        assignee_id=assignee_id,
        q=q,
        open_only=open_only,
    )
    return paginate(db, stmt.order_by(*SORTS[sort]), params, FindingSummary.model_validate)


@router.get("/findings/export")
def export_findings(
    format: str = Query("csv", pattern="^(csv|json)$"),
    status: str | None = Query(None),
    system_id: uuid.UUID | None = Query(None),
    audit_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("export:data")),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    """Export findings (CSV or JSON). Data portability: customers can always take their data with them."""
    import json

    stmt = _filtered(principal, status=status, system_id=system_id, audit_id=audit_id)
    findings = db.scalars(stmt.order_by(Finding.risk_score.desc()).limit(50_000)).all()
    rows = [report_service._finding_dict(f) for f in findings]
    from aegis_api.services import audit_log

    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="finding.exported",
        resource_type="finding",
        principal=principal,
        after={"format": format, "count": len(rows)},
    )
    if format == "json":
        return PlainTextResponse(
            json.dumps(rows, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=findings.json"},
        )
    csv = report_service.findings_to_csv(rows)
    return PlainTextResponse(
        csv, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=findings.csv"}
    )


@router.post("/findings/bulk")
def bulk_update(
    body: BulkFindingUpdate, principal: Principal = Depends(require("findings:write")), db: Session = Depends(get_db)
) -> dict:
    changes: dict = {}
    if body.status:
        changes["status"] = body.status
    if body.assignee_id:
        changes["assignee_id"] = uuid.UUID(body.assignee_id)
    if body.priority:
        changes["priority"] = body.priority
    if body.tags is not None:
        changes["tags"] = body.tags
    if body.note:
        changes["note"] = body.note
    if body.status == "accepted_risk":
        from aegis_api.errors import ValidationFailed

        raise ValidationFailed("Risk acceptance requires an individual justification and expiry per finding")
    return finding_service.bulk_update(db, principal, [uuid.UUID(i) for i in body.ids], changes)


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
        tags=body.tags,
        priority=body.priority,
        risk_acceptance=body.risk_acceptance.model_dump() if body.risk_acceptance else None,
    )
    return FindingOut.model_validate(finding)


@router.get("/findings/{finding_id}/transitions")
def finding_transitions(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> dict:
    finding = _get_finding(db, finding_id, principal.organization_id)
    allowed = finding_service.allowed_transitions(finding.status)
    if not principal.has("findings:accept_risk"):
        allowed = [s for s in allowed if s != "accepted_risk"]
    if not principal.has("findings:write"):
        allowed = []
    return {"status": finding.status, "allowed": allowed}


@router.get("/findings/{finding_id}/comments", response_model=list[CommentOut])
def list_comments(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> list[CommentOut]:
    from aegis_api.models import FindingComment

    _get_finding(db, finding_id, principal.organization_id)
    rows = db.scalars(
        select(FindingComment).where(FindingComment.finding_id == finding_id).order_by(FindingComment.created_at)
    ).all()
    return [CommentOut.model_validate(c) for c in rows]


@router.post("/findings/{finding_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(
    finding_id: uuid.UUID,
    body: CommentCreate,
    principal: Principal = Depends(require("findings:comment")),
    db: Session = Depends(get_db),
) -> CommentOut:
    finding = _get_finding(db, finding_id, principal.organization_id)
    return CommentOut.model_validate(finding_service.add_comment(db, principal, finding, body.body))


@router.get("/findings/{finding_id}/explanation")
def explanation(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> dict:
    """Structured explanation assembled deterministically from the finding, its evidence and the remediation
    library. It is a reading aid: the underlying finding and evidence remain the source of truth."""
    from aegis_api.services import explanation_service

    finding = _get_finding(db, finding_id, principal.organization_id)
    return explanation_service.explain(db, finding)


@router.get("/findings/{finding_id}/occurrences")
def occurrences(
    finding_id: uuid.UUID, principal: Principal = Depends(require("findings:read")), db: Session = Depends(get_db)
) -> list[dict]:
    from aegis_api.models import FindingOccurrence

    _get_finding(db, finding_id, principal.organization_id)
    rows = db.scalars(
        select(FindingOccurrence)
        .where(FindingOccurrence.finding_id == finding_id)
        .order_by(FindingOccurrence.observed_at.desc())
        .limit(200)
    ).all()
    return [
        {
            "source_type": o.source_type,
            "source_id": str(o.source_id),
            "audit_id": str(o.audit_id) if o.audit_id else None,
            "severity": o.severity,
            "risk_level": o.risk_level,
            "occurrences": o.occurrences,
            "sample_size": o.sample_size,
            "system_version": o.system_version,
            "observed_at": o.observed_at.isoformat(),
        }
        for o in rows
    ]


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
    # Lifecycle: a fix under test moves through fixed → retesting; the retest result resolves or reopens it.
    if finding.status == "in_remediation":
        finding_service.transition(db, finding, status="fixed", principal=principal, note="Retest requested")
    if finding.status == "fixed":
        finding_service.transition(db, finding, status="retesting", principal=principal, note="Retest started")
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
