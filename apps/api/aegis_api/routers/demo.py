"""Public, read-only demo endpoints (no auth) powering the landing page and guided /demo tour."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.deps import _raw_session
from aegis_api.errors import NotFound
from aegis_api.models import AISystem, Audit, Evidence, Finding, Organization
from aegis_api.ratelimit import public_rate_limit

router = APIRouter(prefix="/api/v1/demo", tags=["Demo"], dependencies=[Depends(public_rate_limit)])


def _reference_org(db: Session) -> Organization:
    from aegis_api.config import get_settings

    org = db.scalar(select(Organization).where(Organization.slug == get_settings().demo_reference_org_slug))
    if org is None:
        org = db.scalar(select(Organization).where(Organization.is_demo.is_(True)).order_by(Organization.created_at))
    if org is None:
        raise NotFound("Demo workspace is not seeded yet")
    return org


@router.get("/stats")
def demo_stats(db: Session = Depends(_raw_session)) -> dict:
    """Landing-page live stats, computed from the seeded demo workspace (labelled as demo data)."""
    try:
        org = _reference_org(db)
    except NotFound:
        return {
            "simulated": True,
            "seeded": False,
            "tests_executed": 0,
            "findings": 0,
            "controls": 0,
            "evidence_coverage": 0.0,
        }
    from aegis_api.models import Control, TestResult

    tests = db.scalar(select(func.count(TestResult.id)).where(TestResult.organization_id == org.id)) or 0
    findings = db.scalar(select(func.count(Finding.id)).where(Finding.organization_id == org.id)) or 0
    controls = db.scalar(select(func.count(Control.id)).where(Control.organization_id == org.id)) or 0
    findings_with_ev = (
        db.scalar(
            select(func.count(func.distinct(Finding.id))).select_from(Finding).where(Finding.organization_id == org.id)
        )
        or 0
    )
    evidence = db.scalar(select(func.count(Evidence.id)).where(Evidence.organization_id == org.id)) or 0
    return {
        "simulated": True,
        "seeded": True,
        "organization": org.name,
        "tests_executed": int(tests),
        "findings": int(findings),
        "controls": int(controls),
        "evidence_artifacts": int(evidence),
        "evidence_coverage": round(100 * findings_with_ev / findings, 1) if findings else 0.0,
    }


@router.get("/snapshot")
def demo_snapshot(db: Session = Depends(_raw_session)) -> dict:
    """A compact snapshot of the flagship demo (systems, top findings) for the landing live-audit demo."""
    org = _reference_org(db)
    systems = db.scalars(
        select(AISystem).where(AISystem.organization_id == org.id, AISystem.deleted_at.is_(None))
    ).all()
    audits = db.scalars(
        select(Audit)
        .where(Audit.organization_id == org.id, Audit.status.in_(["completed", "partially_completed"]))
        .order_by(Audit.created_at.desc())
        .limit(3)
    ).all()
    findings = db.scalars(
        select(Finding).where(Finding.organization_id == org.id).order_by(Finding.risk_score.desc()).limit(6)
    ).all()
    return {
        "simulated": True,
        "systems": [
            {"id": str(s.id), "name": s.name, "type": s.system_type, "risk_tier": s.risk_tier} for s in systems
        ],
        "audits": [
            {
                "id": str(a.id),
                "name": a.name,
                "dimensions": a.summary.get("dimensions", {}),
                "findings": a.findings_count,
                "high_risk": a.summary.get("high_risk", 0),
                "evidence": a.evidence_count,
            }
            for a in audits
        ],
        "findings": [
            {
                "id": str(f.id),
                "number": f.number,
                "title": f.title,
                "category": f.category,
                "severity": f.severity,
                "risk_level": f.risk_level,
            }
            for f in findings
        ],
    }
