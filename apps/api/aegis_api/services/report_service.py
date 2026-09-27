"""Structured audit report generation (JSON is canonical; PDF/CSV are rendered on export)."""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import Audit, Control, ControlAssessment, Evidence, Finding, Report
from engines.evaluation.aggregation import ResultRow
from engines.evidence.hashing import content_hash

DISCLAIMER = (
    "This report presents automated assessment results. Automated evaluations are assessments, not proof "
    "of perfect safety. Fairness tests reveal tested behavioural patterns, not a complete sociotechnical "
    "fairness determination. Compliance/framework mappings are informational reference mappings and do not "
    "constitute legal advice or certification."
)


def build_report_content(
    session: Session,
    audit: Audit,
    rows: list[ResultRow],
    findings: list[Finding],
    matrix: list[dict[str, Any]],
    scores: dict[str, float],
) -> dict[str, Any]:
    system = audit.system
    evidence = session.scalars(select(Evidence).where(Evidence.audit_id == audit.id).order_by(Evidence.seq)).all()
    assessments = session.scalars(select(ControlAssessment).where(ControlAssessment.audit_id == audit.id)).all()
    control_map = (
        {
            c.id: c
            for c in session.scalars(select(Control).where(Control.id.in_([a.control_id for a in assessments]))).all()
        }
        if assessments
        else {}
    )
    return {
        "generated_at": utcnow().isoformat(),
        "disclaimer": DISCLAIMER,
        "executive_summary": {
            "posture": audit.summary.get("posture"),
            "dimensions": scores,
            "total_findings": len(findings),
            "high_risk": sum(1 for f in findings if f.risk_level in ("high", "critical")),
            "headline": _headline(audit, findings, scores),
        },
        "system_description": {
            "name": system.name,
            "type": system.system_type,
            "environment": system.environment,
            "risk_tier": system.risk_tier,
            "model": system.model_name,
            "model_version": system.model_version,
            "version": system.version,
            "business_purpose": system.business_purpose,
            "owner": system.owner_name,
        },
        "scope": {
            "categories": audit.categories,
            "intensity": audit.intensity,
            "policy_versions": audit.policy_version_ids,
        },
        "test_configuration": audit.config,
        "methodology": {
            "principle": "Deterministic where possible, model-assisted where useful, evidence-backed everywhere.",
            "evaluators": audit.manifest.get("evaluator_versions", {}),
            "tests_executed": len(rows),
        },
        "findings_summary": {
            "by_severity": _counts(findings, "severity"),
            "by_dimension": _counts(findings, "dimension"),
            "by_status": _counts(findings, "status"),
        },
        "detailed_findings": [_finding_dict(f) for f in sorted(findings, key=lambda f: f.risk_score, reverse=True)],
        "test_matrix": matrix,
        "policy_coverage": [
            {
                "control": control_map[a.control_id].control_id if a.control_id in control_map else str(a.control_id),
                "status": a.status,
                "tests_run": a.tests_run,
                "failures": a.failures,
            }
            for a in assessments
        ],
        "evidence_index": [
            {"seq": e.seq, "kind": e.kind, "title": e.title, "hash": e.content_hash, "confidence": e.confidence_level}
            for e in evidence
        ],
        "evidence_integrity": {"head_hash": audit.evidence_head_hash, "count": len(evidence)},
        "limitations": [
            "Results reflect the tests executed and the sources available at audit time.",
            "Counterfactual results describe controlled behavioural differences, not real-world discrimination.",
            "Absence of a finding is not proof of safety.",
            DISCLAIMER,
        ],
        "framework_mappings_note": "See the Frameworks view for control-to-framework reference mappings.",
        "audit_metadata": {
            "audit_id": str(audit.id),
            "status": audit.status,
            "started_at": audit.started_at.isoformat() if audit.started_at else None,
            "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
            "manifest": audit.manifest,
            "cost": audit.cost,
            "missing_categories": audit.missing_categories,
        },
    }


def _headline(audit: Audit, findings: list[Finding], scores: dict[str, float]) -> str:
    high = sum(1 for f in findings if f.risk_level in ("high", "critical"))
    weakest = min(scores.items(), key=lambda kv: kv[1])[0] if scores else "unknown"
    return f"{len(findings)} finding(s) identified ({high} high-risk). Weakest dimension: {weakest}."


def _counts(findings: list[Finding], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[getattr(f, attr)] = out.get(getattr(f, attr), 0) + 1
    return out


def _finding_dict(f: Finding) -> dict[str, Any]:
    return {
        "number": f.number,
        "title": f.title,
        "category": f.category,
        "severity": f.severity,
        "status": f.status,
        "risk_level": f.risk_level,
        "risk_score": round(f.risk_score, 1),
        "risk_reasons": f.risk_reasons,
        "control_ref": f.control_ref,
        "confidence": f.confidence,
        "occurrences": f.occurrences,
        "sample_size": f.sample_size,
        "description": f.description,
        "impact": f.impact,
    }


def generate_report(
    session: Session,
    audit: Audit,
    rows: list[ResultRow],
    findings: list[Finding],
    matrix: list[dict[str, Any]],
    scores: dict[str, float],
) -> Report:
    content = build_report_content(session, audit, rows, findings, matrix, scores)
    report = Report(
        organization_id=audit.organization_id,
        audit_id=audit.id,
        title=f"AI Assurance Report — {audit.system.name}",
        status="ready",
        content=content,
        content_hash=content_hash(content),
    )
    session.add(report)
    session.flush()
    return report


def findings_to_csv(findings: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = [
        "number",
        "title",
        "category",
        "severity",
        "risk_level",
        "risk_score",
        "status",
        "control_ref",
        "confidence",
        "occurrences",
        "sample_size",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for f in findings:
        writer.writerow(f)
    return buf.getvalue()
