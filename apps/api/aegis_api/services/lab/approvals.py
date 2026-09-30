"""Human approval system.

Approvals are created by automation (workflows/agents) when policy or autonomy rules require a human, and are
decided only by interactive human users holding the approval's required permission. Decisions are audited,
emitted as events, and delivered to the waiting workflow as a signal. Requests expire; expiry also signals the
workflow so it never waits forever.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Forbidden, InvalidState, ValidationFailed
from aegis_api.models.lab import Approval
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import events
from aegis_api.services.lab.access import accessible_project_ids, get_scoped
from aegis_api.services.lab.common import Actor
from engines.lab.enums import ApprovalStatus, LabEventType
from engines.lab.policy.engine import PolicyResult
from engines.lab.state_machines import APPROVAL

DEFAULT_TTL = timedelta(days=7)

# Approval kind → the permission a human needs (in addition to approval:decide) to decide it.
KIND_PERMISSIONS: dict[str, str] = {
    "discovery_promotion": "discovery:approve",
    "publication": "discovery:publish",
    "strategy_promotion": "strategy:promote",
    "expensive_compute": "experiment:execute",
    "experiment_execution": "experiment:execute",
    "external_network": "experiment:execute",
    "secret_access": "experiment:execute",
    "plan_review": "mission:approve_plan",
    "memory_promotion": "memory:review",
    "budget_increase": "project:manage",
}


def signal_name_for(approval_id: uuid.UUID | str) -> str:
    return f"approval:{approval_id}"


def request(
    db: Session,
    *,
    organization_id: uuid.UUID,
    kind: str,
    resource_type: str,
    resource_id: str | uuid.UUID,
    title: str,
    requester: Actor,
    details: dict[str, Any] | None = None,
    policy_result: PolicyResult | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    workflow_run_id: uuid.UUID | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> Approval:
    """Create (or return the existing pending) approval for this resource/kind/workflow — idempotent, so a
    replayed workflow activity never duplicates requests."""
    existing = db.scalar(
        select(Approval).where(
            Approval.organization_id == organization_id,
            Approval.kind == kind,
            Approval.resource_type == resource_type,
            Approval.resource_id == str(resource_id),
            Approval.status == ApprovalStatus.PENDING,
            Approval.workflow_run_id == workflow_run_id
            if workflow_run_id is not None
            else Approval.workflow_run_id.is_(None),
        )
    )
    if existing is not None:
        return existing
    approval = Approval(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        kind=kind,
        status=ApprovalStatus.PENDING,
        resource_type=resource_type,
        resource_id=str(resource_id),
        title=title[:300],
        request=details or {},
        requester_type=requester.type,
        requester_id=requester.id[:64],
        requester_label=requester.label[:320],
        required_permission=KIND_PERMISSIONS.get(kind, "approval:decide"),
        policy=policy_result.to_dict() if policy_result else {},
        expires_at=utcnow() + ttl,
        workflow_run_id=workflow_run_id,
    )
    db.add(approval)
    db.flush()
    approval.signal_name = signal_name_for(approval.id) if workflow_run_id else None
    events.emit(
        db,
        organization_id=organization_id,
        mission_id=mission_id,
        project_id=project_id,
        event_type=LabEventType.APPROVAL_REQUESTED,
        message=f"Approval required: {approval.title}",
        data={
            "approval_id": str(approval.id),
            "kind": kind,
            "resource_type": resource_type,
            "resource_id": str(resource_id),
            "reasons": (policy_result.reasons if policy_result else []),
        },
        actor=requester,
    )
    audit_log.record(
        db,
        organization_id=organization_id,
        action="lab.approval.requested",
        resource_type="approval",
        resource_id=approval.id,
        actor_label=requester.label,
        actor_type=requester.type,
        after={"kind": kind, "resource_type": resource_type, "resource_id": str(resource_id)},
    )
    return approval


def list_approvals(
    db: Session,
    principal: Principal,
    *,
    status: str | None = None,
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Select[Approval]:
    stmt = select(Approval).where(Approval.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where((Approval.project_id.is_(None)) | (Approval.project_id.in_(visible)))
    if status:
        stmt = stmt.where(Approval.status == status)
    if mission_id:
        stmt = stmt.where(Approval.mission_id == mission_id)
    if project_id:
        stmt = stmt.where(Approval.project_id == project_id)
    return stmt.order_by(Approval.created_at.desc())


def decide(
    db: Session,
    principal: Principal,
    approval_id: uuid.UUID | str,
    *,
    decision: str,
    reason: str | None = None,
) -> Approval:
    principal.require_human("approval decision")
    principal.require("approval:decide")
    approval = get_scoped(db, principal, Approval, approval_id, label="Approval")
    if approval.required_permission and approval.required_permission != "approval:decide":
        principal.require(approval.required_permission)
    if decision not in ("approve", "reject"):
        raise ValidationFailed("decision must be 'approve' or 'reject'")
    if approval.status != ApprovalStatus.PENDING:
        raise InvalidState(f"Approval is already {approval.status}")
    if approval.expires_at is not None and approval.expires_at <= utcnow():
        _finish(db, approval, ApprovalStatus.EXPIRED, reason="expired before a decision was made", actor=None)
        raise InvalidState("Approval request has expired")
    if approval.requester_type == "user" and approval.requester_id == str(principal.user_id):
        raise Forbidden("Separation of duties: you cannot decide an approval you requested")
    target = ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
    approval.approver_id = principal.user_id
    _finish(db, approval, target, reason=reason, actor=Actor.of(principal))
    if approval.resource_type == "discovery":
        from aegis_api.services.lab import discoveries

        discoveries.on_approval_decided(db, principal, approval)
    audit_log.record(
        db,
        organization_id=approval.organization_id,
        action=f"lab.approval.{target}",
        resource_type="approval",
        resource_id=approval.id,
        principal=principal,
        after={"kind": approval.kind, "resource_id": approval.resource_id, "reason": reason},
    )
    return approval


def cancel(db: Session, approval: Approval, *, reason: str) -> Approval:
    if approval.status == ApprovalStatus.PENDING:
        _finish(db, approval, ApprovalStatus.CANCELLED, reason=reason, actor=None)
    return approval


def _finish(db: Session, approval: Approval, status: str, *, reason: str | None, actor: Actor | None) -> None:
    approval.status = APPROVAL.ensure(approval.status, status)
    approval.decision_reason = reason
    approval.decided_at = utcnow()
    events.emit(
        db,
        organization_id=approval.organization_id,
        mission_id=approval.mission_id,
        project_id=approval.project_id,
        event_type=LabEventType.APPROVAL_DECIDED,
        message=f"Approval {status}: {approval.title}",
        data={"approval_id": str(approval.id), "status": status, "kind": approval.kind},
        actor=actor,
    )
    if approval.workflow_run_id and approval.signal_name:
        from aegis_api.workflows import client as workflow_client

        workflow_client.signal(
            db,
            approval.workflow_run_id,
            approval.signal_name,
            {
                "approval_id": str(approval.id),
                "status": status,
                "approved": status == ApprovalStatus.APPROVED,
                "reason": reason,
                "approver_id": str(approval.approver_id) if approval.approver_id else None,
            },
        )


def expire_due(db: Session, *, limit: int = 200) -> int:
    rows = db.scalars(
        select(Approval)
        .where(Approval.status == ApprovalStatus.PENDING, Approval.expires_at <= utcnow())
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for approval in rows:
        _finish(db, approval, ApprovalStatus.EXPIRED, reason="expired", actor=None)
    return len(rows)


def status_of(db: Session, organization_id: uuid.UUID, approval_id: uuid.UUID | str) -> dict[str, Any]:
    approval = db.get(Approval, uuid.UUID(str(approval_id)))
    if approval is None or approval.organization_id != organization_id:
        return {"status": "missing", "approved": False}
    return {
        "approval_id": str(approval.id),
        "status": approval.status,
        "approved": approval.status == ApprovalStatus.APPROVED,
        "reason": approval.decision_reason,
        "approver_id": str(approval.approver_id) if approval.approver_id else None,
    }
