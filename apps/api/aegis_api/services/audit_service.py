"""Audit lifecycle: create, enqueue, cancel, compare."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.jobs.jobs import run_audit_job
from aegis_api.models import AISystem, Audit, Finding, PolicyVersion
from aegis_api.models.enums import TERMINAL_AUDIT_STATUSES, AuditStatus
from aegis_api.security.context import Principal
from aegis_api.services import audit_log


def create_audit(
    session: Session, principal: Principal, data: Any, *, start: bool = True, kind: str = "audit"
) -> Audit:
    system = session.get(AISystem, uuid.UUID(data.system_id))
    if system is None or system.organization_id != principal.organization_id or system.deleted_at is not None:
        raise NotFound("AI system not found")
    policy_version_ids: list[str] = []
    for pv_id in data.policy_version_ids:
        pv = session.get(PolicyVersion, uuid.UUID(pv_id))
        if pv is None or pv.organization_id != principal.organization_id:
            raise ValidationFailed(f"Policy version {pv_id} not found")
        policy_version_ids.append(str(pv.id))
    categories = [c.value if hasattr(c, "value") else c for c in data.categories]
    config = data.config.model_dump() if hasattr(data.config, "model_dump") else dict(data.config)
    audit = Audit(
        organization_id=principal.organization_id,
        system_id=system.id,
        name=data.name or f"{system.name} — {data.intensity} audit",
        kind=kind,
        status=AuditStatus.DRAFT,
        intensity=data.intensity.value if hasattr(data.intensity, "value") else data.intensity,
        categories=categories,
        policy_version_ids=policy_version_ids,
        config=config,
        is_demo=system.is_demo,
        created_by_id=principal.user_id,
    )
    session.add(audit)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="audit.created",
        resource_type="audit",
        resource_id=audit.id,
        principal=principal,
        after={"system_id": str(system.id), "categories": categories},
    )
    if start:
        enqueue(session, audit, principal)
    return audit


def enqueue(session: Session, audit: Audit, principal: Principal | None = None) -> None:
    if audit.status in TERMINAL_AUDIT_STATUSES:
        raise Conflict("Audit has already completed")
    audit.status = AuditStatus.QUEUED
    audit.progress = 0
    session.flush()
    # Commit happens on the request boundary; schedule the job to run after commit.
    session.info.setdefault("after_commit", []).append((run_audit_job, audit.organization_id, str(audit.id)))
    if principal:
        audit_log.record(
            session,
            organization_id=audit.organization_id,
            action="audit.launched",
            resource_type="audit",
            resource_id=audit.id,
            principal=principal,
        )


def cancel_audit(session: Session, audit: Audit, principal: Principal) -> Audit:
    if audit.status in TERMINAL_AUDIT_STATUSES:
        raise Conflict("Audit is already finished")
    audit.cancel_requested = True
    audit.status = AuditStatus.CANCELLED
    audit.completed_at = utcnow()
    audit_log.record(
        session,
        organization_id=audit.organization_id,
        action="audit.cancelled",
        resource_type="audit",
        resource_id=audit.id,
        principal=principal,
    )
    return audit


def get_audit(session: Session, audit_id: uuid.UUID, organization_id: uuid.UUID) -> Audit:
    audit = session.get(Audit, audit_id)
    if audit is None or audit.organization_id != organization_id:
        raise NotFound("Audit not found")
    return audit


def compare_audits(session: Session, a: Audit, b: Audit) -> dict[str, Any]:
    fa = {f.fingerprint: f for f in session.scalars(select(Finding).where(Finding.audit_id == a.id)).all()}
    fb = {f.fingerprint: f for f in session.scalars(select(Finding).where(Finding.audit_id == b.id)).all()}
    new = [_f(fb[k]) for k in fb.keys() - fa.keys()]
    resolved = [_f(fa[k]) for k in fa.keys() - fb.keys()]
    regressions = []
    unchanged = 0
    from engines.common.types import SEVERITY_RANK

    for k in fa.keys() & fb.keys():
        if SEVERITY_RANK.get(fb[k].severity, 0) > SEVERITY_RANK.get(fa[k].severity, 0):
            regressions.append({**_f(fb[k]), "was": fa[k].severity})
        else:
            unchanged += 1
    return {
        "audit_a": str(a.id),
        "audit_b": str(b.id),
        "new_findings": new,
        "resolved_findings": resolved,
        "regressions": regressions,
        "unchanged": unchanged,
        "risk_delta": {"a": a.summary.get("dimensions", {}), "b": b.summary.get("dimensions", {})},
    }


def _f(finding: Finding) -> dict[str, Any]:
    return {
        "id": str(finding.id),
        "number": finding.number,
        "title": finding.title,
        "severity": finding.severity,
        "category": finding.category,
        "risk_level": finding.risk_level,
    }


def audit_matrix(audit: Audit) -> list[dict[str, Any]]:
    return audit.summary.get("test_matrix", [])
