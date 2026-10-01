"""Finding creation, deduplication, status transitions and notifications."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.models import (
    Audit,
    Evidence,
    EvidenceLink,
    Finding,
    FindingEvent,
    FindingOccurrence,
    Membership,
    Notification,
    Organization,
    TestResult,
)
from aegis_api.models.enums import CATEGORY_DIMENSION, FindingStatus, Severity
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


def allocate_finding_number(organization_id: uuid.UUID) -> int:
    """Atomically allocate the next human-readable finding number for a workspace.

    Runs in its own short transaction so concurrent audits never hold (or race on) the organization row
    for the duration of an audit. A rolled-back audit leaves a gap in the sequence, which is acceptable:
    numbers are identifiers, not counts."""
    with session_scope(org_id=organization_id) as s:
        value: int = s.execute(
            text(
                "UPDATE organizations SET finding_seq = coalesce(finding_seq, 1000) + 1 WHERE id = :id RETURNING finding_seq"
            ),
            {"id": organization_id},
        ).scalar_one()
    return int(value)


def _next_number(session: Session, org: Organization) -> int:
    return allocate_finding_number(org.id)


# Statuses from which a re-detected issue is treated as a regression and reopened.
REOPENABLE_STATUSES = {FindingStatus.RESOLVED, "fixed"}


def find_existing(
    session: Session, organization_id: uuid.UUID, system_id: uuid.UUID, fingerprint: str
) -> Finding | None:
    """Deduplication is scoped to (organization, system, fingerprint): the same issue on two systems is two
    findings. The most recent matching finding wins."""
    return session.scalar(
        select(Finding)
        .where(
            Finding.organization_id == organization_id,
            Finding.system_id == system_id,
            Finding.fingerprint == fingerprint,
        )
        .order_by(Finding.created_at.desc())
        .limit(1)
    )


def record_occurrence(
    session: Session,
    finding: Finding,
    *,
    source_type: str,
    source_id: uuid.UUID,
    audit_id: uuid.UUID | None,
    occurrences: int,
    sample_size: int,
    system_version: str | None,
) -> None:
    exists = session.scalar(
        select(FindingOccurrence.id).where(
            FindingOccurrence.finding_id == finding.id,
            FindingOccurrence.source_type == source_type,
            FindingOccurrence.source_id == source_id,
        )
    )
    if exists:
        return
    session.add(
        FindingOccurrence(
            organization_id=finding.organization_id,
            finding_id=finding.id,
            system_id=finding.system_id,
            audit_id=audit_id,
            source_type=source_type,
            source_id=source_id,
            severity=finding.severity,
            risk_level=finding.risk_level,
            occurrences=occurrences,
            sample_size=sample_size,
            system_version=system_version,
            observed_at=utcnow(),
        )
    )


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
    existing = find_existing(session, org.id, system.id, fingerprint)
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
        existing.last_audit_id = audit.id
        existing.last_seen_at = utcnow()
        existing.details = details
        existing.updated_at = utcnow()
        _link_evidence(session, org.id, existing.id, evidence_ids)
        _tag_results(session, audit.id, group, existing.id)
        if existing.status in REOPENABLE_STATUSES:
            session.add(
                FindingEvent(
                    organization_id=org.id,
                    finding_id=existing.id,
                    type="regressed",
                    from_status=existing.status,
                    to_status=FindingStatus.OPEN,
                    note=f"Issue detected again by audit {audit.id} after it was {existing.status}",
                    data={"audit_id": str(audit.id)},
                )
            )
            existing.status = FindingStatus.OPEN
            existing.resolved_at = None
            existing.details = {**details, "regressed": True}
        else:
            session.add(
                FindingEvent(
                    organization_id=org.id,
                    finding_id=existing.id,
                    type="reobserved",
                    note=f"Reobserved in audit {audit.id}",
                    data={"audit_id": str(audit.id)},
                )
            )
        record_occurrence(
            session,
            existing,
            source_type="audit",
            source_id=audit.id,
            audit_id=audit.id,
            occurrences=group.occurrences,
            sample_size=group.sample_size,
            system_version=system.version,
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
        last_audit_id=audit.id,
        last_seen_at=utcnow(),
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
        sla_due_at=sla_due(severity),
    )
    session.add(finding)
    session.flush()
    _link_evidence(session, org.id, finding.id, evidence_ids)
    _tag_results(session, audit.id, group, finding.id)
    record_occurrence(
        session,
        finding,
        source_type="audit",
        source_id=audit.id,
        audit_id=audit.id,
        occurrences=group.occurrences,
        sample_size=group.sample_size,
        system_version=system.version,
    )
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


# --- lifecycle ---------------------------------------------------------------------------------------
# open → triaged → in_remediation → fixed → retesting → resolved, with accepted_risk / false_positive as
# explicit, audited exits. Every change is validated here; arbitrary status mutation is impossible.
TRANSITIONS: dict[str, set[str]] = {
    "open": {"triaged", "acknowledged", "in_remediation", "resolved", "accepted_risk", "false_positive"},
    "acknowledged": {"triaged", "in_remediation", "resolved", "accepted_risk", "false_positive", "open"},
    "triaged": {"in_remediation", "resolved", "accepted_risk", "false_positive", "open"},
    "in_remediation": {"fixed", "triaged", "resolved", "accepted_risk", "open"},
    "fixed": {"retesting", "resolved", "in_remediation", "open"},
    "retesting": {"resolved", "in_remediation", "open"},
    "resolved": {"open"},
    "accepted_risk": {"open"},
    "false_positive": {"open"},
}
SLA_DAYS = {"critical": 7, "high": 30, "medium": 90, "low": 180, "info": 365}
MAX_RISK_ACCEPTANCE_DAYS = 365
PRIORITIES = {"p1", "p2", "p3", "p4"}


def sla_due(severity: str, created: Any = None) -> Any:
    from datetime import timedelta

    return (created or utcnow()) + timedelta(days=SLA_DAYS.get(severity, 90))


def allowed_transitions(status: str) -> list[str]:
    return sorted(TRANSITIONS.get(status, set()))


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
    tags: list[str] | None = None,
    priority: str | None = None,
    risk_acceptance: dict[str, Any] | None = None,
) -> Finding:
    from datetime import UTC, datetime, timedelta

    from aegis_api.errors import Conflict, ValidationFailed

    before = {
        "status": finding.status,
        "assignee_id": str(finding.assignee_id) if finding.assignee_id else None,
        "priority": finding.priority,
        "tags": list(finding.tags or []),
    }
    if status and status != finding.status:
        if status not in TRANSITIONS.get(finding.status, set()):
            raise Conflict(
                f"Cannot move a finding from '{finding.status}' to '{status}'",
                code="invalid_transition",
                details={"from": finding.status, "allowed": allowed_transitions(finding.status)},
            )
        if status == FindingStatus.ACCEPTED_RISK:
            principal.require("findings:accept_risk")
            ra = risk_acceptance or {}
            reason = (ra.get("reason") or note or "").strip()
            expires = ra.get("expires_at")
            if len(reason) < 10:
                raise ValidationFailed("Risk acceptance requires a justification of at least 10 characters")
            if expires is None:
                raise ValidationFailed("Risk acceptance requires an expiry date")
            if isinstance(expires, str):
                expires = datetime.fromisoformat(expires)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= utcnow() or expires > utcnow() + timedelta(days=MAX_RISK_ACCEPTANCE_DAYS):
                raise ValidationFailed(f"Risk acceptance must expire within {MAX_RISK_ACCEPTANCE_DAYS} days")
            finding.risk_acceptance = {
                "reason": reason,
                "owner_id": str(ra.get("owner_id") or principal.user_id),
                "approved_by": principal.actor_label,
                "approved_at": utcnow().isoformat(),
                "expires_at": expires.isoformat(),
            }
            finding.risk_accepted_until = expires
        elif finding.status == FindingStatus.ACCEPTED_RISK:
            finding.risk_accepted_until = None
        session.add(
            FindingEvent(
                organization_id=finding.organization_id,
                finding_id=finding.id,
                type="status_changed",
                actor_id=principal.user_id if principal.auth_method != "api_key" else None,
                actor_label=principal.actor_label,
                from_status=finding.status,
                to_status=status,
                note=note,
                data={"risk_acceptance": finding.risk_acceptance} if status == FindingStatus.ACCEPTED_RISK else {},
            )
        )
        finding.status = status
        if status == FindingStatus.RESOLVED:
            finding.resolved_at = utcnow()
        elif status == "open":
            finding.resolved_at = None
    if assignee_id is not None:
        from aegis_api.models import Membership

        member = session.scalar(
            select(Membership.id).where(
                Membership.organization_id == finding.organization_id,
                Membership.user_id == assignee_id,
                Membership.status == "active",
            )
        )
        if member is None:
            raise ValidationFailed("Assignee must be an active member of this workspace")
        if finding.assignee_id != assignee_id:
            session.add(
                FindingEvent(
                    organization_id=finding.organization_id,
                    finding_id=finding.id,
                    type="assigned",
                    actor_label=principal.actor_label,
                    note=note,
                    data={"assignee_id": str(assignee_id)},
                )
            )
        finding.assignee_id = assignee_id
    if due_date is not None:
        finding.due_date = due_date
    if tags is not None:
        clean = sorted({t.strip().lower()[:40] for t in tags if t and t.strip()})[:20]
        finding.tags = clean
    if priority is not None:
        if priority not in PRIORITIES:
            raise ValidationFailed("priority must be one of p1, p2, p3, p4")
        finding.priority = priority
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
        after={"status": finding.status, "priority": finding.priority, "tags": list(finding.tags or [])},
    )
    if status == FindingStatus.RESOLVED:
        from aegis_api.services import webhook_service

        webhook_service.enqueue_event(
            session,
            finding.organization_id,
            "finding.resolved",
            {"finding_id": str(finding.id), "number": finding.number},
        )
    return finding


def add_comment(session: Session, principal: Principal, finding: Finding, body: str) -> Any:
    from aegis_api.errors import ValidationFailed
    from aegis_api.models import FindingComment

    text_body = (body or "").strip()
    if not text_body or len(text_body) > 10_000:
        raise ValidationFailed("Comment must be between 1 and 10,000 characters")
    comment = FindingComment(
        organization_id=finding.organization_id,
        finding_id=finding.id,
        author_id=principal.user_id if principal.auth_method != "api_key" else None,
        author_label=principal.actor_label,
        body=text_body,
    )
    session.add(comment)
    session.add(
        FindingEvent(
            organization_id=finding.organization_id,
            finding_id=finding.id,
            type="commented",
            actor_label=principal.actor_label,
            note=text_body[:200],
        )
    )
    session.flush()
    return comment


def bulk_update(
    session: Session, principal: Principal, ids: list[uuid.UUID], changes: dict[str, Any]
) -> dict[str, Any]:
    """Apply the same change to many findings. Each item is validated independently; failures are reported
    per item and never partially applied to that item."""
    from aegis_api.errors import AppError

    updated: list[str] = []
    failed: list[dict[str, str]] = []
    for finding_id in ids[:500]:
        finding = session.get(Finding, finding_id)
        if finding is None or finding.organization_id != principal.organization_id:
            failed.append({"id": str(finding_id), "error": "not_found"})
            continue
        try:
            with session.begin_nested():
                transition(session, finding, principal=principal, request_id=principal.request_id, **changes)
            updated.append(str(finding_id))
        except AppError as exc:
            failed.append({"id": str(finding_id), "error": exc.code, "message": exc.message})
    return {"updated": updated, "failed": failed}


def expire_risk_acceptances() -> int:
    """Maintenance: re-open findings whose risk acceptance has expired (history is preserved)."""
    from aegis_api.db.session import admin_session_scope

    count = 0
    with admin_session_scope() as s:
        expired = s.scalars(
            select(Finding)
            .where(Finding.status == FindingStatus.ACCEPTED_RISK, Finding.risk_accepted_until < utcnow())
            .limit(500)
        ).all()
        for finding in expired:
            s.add(
                FindingEvent(
                    organization_id=finding.organization_id,
                    finding_id=finding.id,
                    type="risk_acceptance_expired",
                    from_status=finding.status,
                    to_status="open",
                    note="Risk acceptance expired; finding re-opened",
                    data={"risk_acceptance": finding.risk_acceptance},
                )
            )
            finding.status = FindingStatus.OPEN
            finding.risk_accepted_until = None
            finding.updated_at = utcnow()
            count += 1
    return count


def notify_audit_complete(
    session: Session, audit: Any, findings: list[Finding], *, new_findings: list[Finding] | None = None
) -> None:
    """In-app notifications and webhook events for a completed audit.

    ``finding.created`` fires only for findings first detected by this audit; re-observed findings are
    summarised in the audit notification instead of re-announced."""
    new_findings = findings if new_findings is None else new_findings
    new_ids = {f.id for f in new_findings}
    members = session.scalars(
        select(Membership.user_id).where(
            Membership.organization_id == audit.organization_id, Membership.status == "active"
        )
    ).all()
    critical = [f for f in findings if f.risk_level in ("high", "critical")]
    critical_new = [f for f in critical if f.id in new_ids or (f.details or {}).get("regressed")]
    for user_id in members:
        session.add(
            Notification(
                organization_id=audit.organization_id,
                user_id=user_id,
                type="audit.completed",
                title=f"Audit '{audit.name}' completed",
                body=f"{len(findings)} finding(s) ({len(new_findings)} new), {len(critical)} high-risk.",
                link=f"/dashboard/audits/{audit.id}",
                severity="high" if critical else "info",
            )
        )
    for finding in critical_new:
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
        {
            "audit_id": str(audit.id),
            "system_id": str(audit.system_id),
            "findings": len(findings),
            "new_findings": len(new_findings),
            "status": audit.status,
        },
    )
    for finding in new_findings:
        webhook_service.enqueue_event(
            session,
            audit.organization_id,
            "finding.created",
            {
                "finding_id": str(finding.id),
                "audit_id": str(audit.id),
                "number": finding.number,
                "severity": finding.severity,
                "risk_level": finding.risk_level,
            },
        )
    for finding in critical_new:
        webhook_service.enqueue_event(
            session,
            audit.organization_id,
            "critical_risk.detected",
            {"finding_id": str(finding.id), "risk_level": finding.risk_level},
        )
