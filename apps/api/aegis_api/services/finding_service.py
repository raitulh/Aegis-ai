"""Finding creation, deduplication, status transitions and notifications."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import (
    Audit,
    Evidence,
    EvidenceLink,
    Finding,
    FindingEvent,
    Membership,
    Notification,
    Organization,
    TestResult,
)
from aegis_api.models.enums import CATEGORY_DIMENSION, OPEN_FINDING_STATUSES, FindingStatus, Severity
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from engines.risk.scoring import assess_finding_risk

POLICY_IMPORTANCE = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.55,
    Severity.LOW: 0.35,
    Severity.INFO: 0.2,
}


def _next_number(session: Session, org: Organization) -> int:
    org.finding_seq = (org.finding_seq or 1000) + 1
    return org.finding_seq


def create_from_group(
    session: Session, audit: Audit, org: Organization, group: Any, evidence_ids: list[uuid.UUID], extra: dict[str, Any]
) -> Finding:
    system = audit.system
    rep = group.representative
    severity = group.severity or Severity.MEDIUM
    ev_conf = ConfidenceMap.level(session, evidence_ids)
    risk = assess_finding_risk(
        severity=severity,
        confidence=group.confidence,
        occurrences=group.occurrences,
        sample_size=group.sample_size,
        environment=system.environment,
        risk_tier=system.risk_tier,
        policy_importance=POLICY_IMPORTANCE.get(severity, 0.5),
        evidence_confidence=ev_conf,
    )
    fingerprint = group.fingerprint()
    existing = session.scalar(
        select(Finding).where(
            Finding.organization_id == org.id,
            Finding.fingerprint == fingerprint,
            Finding.status.in_(list(OPEN_FINDING_STATUSES)),
        )
    )
    control = _resolve_control(session, audit, group.control_ref)
    details = {
        "evaluator_summary": rep.outcome.summary,
        "observed": rep.outcome.observed,
        "notes": rep.outcome.notes,
        "group": group.group,
        **extra,
    }
    if existing:
        existing.occurrences = group.occurrences
        existing.sample_size = group.sample_size
        existing.confidence = group.confidence
        existing.severity = severity
        existing.risk_level = risk.level
        existing.risk_score = risk.score
        existing.risk_reasons = risk.reasons
        existing.risk_factors = [f.__dict__ | {"contribution": f.contribution} for f in risk.factors]
        existing.audit_id = audit.id
        existing.details = details
        existing.updated_at = utcnow()
        _link_evidence(session, org.id, existing.id, evidence_ids)
        _tag_results(session, audit.id, group, existing.id)
        session.add(
            FindingEvent(
                organization_id=org.id,
                finding_id=existing.id,
                type="reobserved",
                note=f"Reobserved in audit {audit.id}",
                data={"audit_id": str(audit.id)},
            )
        )
        return existing

    finding = Finding(
        organization_id=org.id,
        number=_next_number(session, org),
        title=group.title(),
        category=group.category,
        dimension=CATEGORY_DIMENSION.get(group.category, "governance"),
        severity=severity,
        status=FindingStatus.OPEN,
        description=rep.outcome.summary or group.title(),
        system_id=system.id,
        system_version=system.version,
        model_version=f"{system.model_name} {system.model_version or ''}".strip() or None,
        audit_id=audit.id,
        control_id=control.id if control else None,
        control_ref=group.control_ref,
        policy_id=control.policy_id if control else None,
        test_type=group.test_type,
        evaluator_key=rep.evaluator_key,
        evaluator_version=rep.evaluator_version,
        confidence=group.confidence,
        impact=_impact(group, risk.level),
        risk_level=risk.level,
        risk_score=risk.score,
        risk_reasons=risk.reasons,
        risk_factors=[
            {
                "key": f.key,
                "label": f.label,
                "value": round(f.value, 3),
                "weight": f.weight,
                "contribution": f.contribution,
                "detail": f.detail,
            }
            for f in risk.factors
        ],
        occurrences=group.occurrences,
        sample_size=group.sample_size,
        fingerprint=fingerprint,
        details=details,
        evidence_unavailable_reason=None if evidence_ids else "No evidence artifact was produced for this result type.",
        is_demo=audit.is_demo,
    )
    session.add(finding)
    session.flush()
    _link_evidence(session, org.id, finding.id, evidence_ids)
    _tag_results(session, audit.id, group, finding.id)
    session.add(
        FindingEvent(
            organization_id=org.id,
            finding_id=finding.id,
            type="created",
            to_status=FindingStatus.OPEN,
            note="Finding created by audit",
        )
    )
    return finding


def _impact(group: Any, risk_level: str) -> str:
    return f"{risk_level.title()} risk in the {group.dimension} dimension, observed in {group.occurrences} of {group.sample_size} test(s)."


def _resolve_control(session: Session, audit: Audit, control_ref: str | None):
    if not control_ref:
        return None
    from aegis_api.models import Control

    return session.scalar(
        select(Control)
        .where(Control.organization_id == audit.organization_id, Control.control_id == control_ref)
        .order_by(Control.created_at.desc())
    )


def _link_evidence(session: Session, org_id: uuid.UUID, finding_id: uuid.UUID, evidence_ids: list[uuid.UUID]) -> None:
    for ev_id in dict.fromkeys(evidence_ids):
        exists = session.scalar(
            select(EvidenceLink.id).where(
                EvidenceLink.evidence_id == ev_id,
                EvidenceLink.target_type == "finding",
                EvidenceLink.target_id == finding_id,
            )
        )
        if not exists:
            session.add(
                EvidenceLink(
                    organization_id=org_id,
                    evidence_id=ev_id,
                    target_type="finding",
                    target_id=finding_id,
                    relation="supports",
                )
            )


def _tag_results(session: Session, audit_id: uuid.UUID, group: Any, finding_id: uuid.UUID) -> None:
    result_ids = [getattr(r, "result_id", None) for r in group.rows if r.outcome.failed]
    for rid in result_ids:
        if rid:
            result = session.get(TestResult, rid)
            if result:
                result.finding_id = finding_id


class ConfidenceMap:
    @staticmethod
    def level(session: Session, evidence_ids: list[uuid.UUID]) -> float:
        if not evidence_ids:
            return 0.6
        levels = session.scalars(select(Evidence.confidence_level).where(Evidence.id.in_(evidence_ids))).all()
        mapping = {"high": 0.9, "medium": 0.7, "low": 0.5}
        vals = [mapping.get(str(x), 0.7) for x in levels]
        return round(sum(vals) / len(vals), 3) if vals else 0.7


def transition(
    session: Session,
    finding: Finding,
    *,
    status: str | None,
    principal: Principal,
    note: str | None = None,
    assignee_id: uuid.UUID | None = None,
    due_date: Any = None,
    request_id: str | None = None,
) -> Finding:
    before = {"status": finding.status, "assignee_id": str(finding.assignee_id) if finding.assignee_id else None}
    if status and status != finding.status:
        session.add(
            FindingEvent(
                organization_id=finding.organization_id,
                finding_id=finding.id,
                type="status_changed",
                actor_id=principal.fk_user_id,
                actor_label=principal.actor_label,
                from_status=finding.status,
                to_status=status,
                note=note,
            )
        )
        finding.status = status
        if status == FindingStatus.RESOLVED:
            finding.resolved_at = utcnow()
    if assignee_id is not None:
        finding.assignee_id = assignee_id
    if due_date is not None:
        finding.due_date = due_date
    finding.updated_at = utcnow()
    audit_log.record(
        session,
        organization_id=finding.organization_id,
        action="finding.updated",
        resource_type="finding",
        resource_id=finding.id,
        principal=principal,
        request_id=request_id,
        before=before,
        after={"status": finding.status},
    )
    return finding


def notify_audit_complete(session: Session, audit: Audit, findings: list[Finding]) -> None:
    members = session.scalars(
        select(Membership.user_id).where(
            Membership.organization_id == audit.organization_id, Membership.status == "active"
        )
    ).all()
    critical = [f for f in findings if f.risk_level in ("high", "critical")]
    for user_id in members:
        session.add(
            Notification(
                organization_id=audit.organization_id,
                user_id=user_id,
                type="audit.completed",
                title=f"Audit '{audit.name}' completed",
                body=f"{len(findings)} finding(s), {len(critical)} high-risk.",
                link=f"/dashboard/audits/{audit.id}",
                severity="high" if critical else "info",
            )
        )
    for finding in critical:
        for user_id in members:
            session.add(
                Notification(
                    organization_id=audit.organization_id,
                    user_id=user_id,
                    type="finding.critical",
                    title=f"{finding.risk_level.title()} risk: {finding.title}",
                    body=finding.impact,
                    link=f"/dashboard/findings/{finding.id}",
                    severity=finding.risk_level,
                )
            )
    from aegis_api.services import webhook_service

    webhook_service.enqueue_event(
        session,
        audit.organization_id,
        "audit.completed",
        {"audit_id": str(audit.id), "findings": len(findings), "status": audit.status},
    )
    for finding in findings:
        webhook_service.enqueue_event(
            session,
            audit.organization_id,
            "finding.created",
            {
                "finding_id": str(finding.id),
                "number": finding.number,
                "severity": finding.severity,
                "risk_level": finding.risk_level,
            },
        )
        if finding.risk_level in ("high", "critical"):
            webhook_service.enqueue_event(
                session,
                audit.organization_id,
                "critical_risk.detected",
                {"finding_id": str(finding.id), "risk_level": finding.risk_level},
            )
