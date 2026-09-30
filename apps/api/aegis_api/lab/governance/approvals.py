"""Human approval gates with separation of duties.

* Anyone (humans, agents, workflows) may *request* an approval; requests are idempotent per
  organization + action + subject while PENDING.
* Only signed-in humans may *decide*, and only with the approval's ``required_permission`` inside the
  approval's project. API keys, service accounts, agents and workflows are refused.
* Separation of duties: the human who requested an approval cannot decide it (unless the organization
  explicitly sets ``settings.allow_self_approval = true``).
* Decisions signal the waiting workflow (when one is attached); workflows also poll approvals, so a
  missing/unavailable workflow engine never loses a decision.
* Expired requests become EXPIRED (on decision attempts and by the ``governance.expire_approvals`` sweep).
"""

from __future__ import annotations

import importlib
import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import (
    ORG_SUPERUSER_ROLES,
    effective_permissions,
    get_owned,
    load_project,
    visible_project_ids,
)
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.governance.durable import jsonable, on_rollback
from aegis_api.lab.governance.schemas import ApprovalOut
from aegis_api.lab.models import Approval, Mission, Project
from aegis_api.lab.observability import metrics
from aegis_api.models import Organization
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.rbac import ALL_LAB_PERMISSIONS
from engines.lab.policy import Decision, is_valid_action_name
from engines.lab.states import ApprovalStatus, RiskLevel, assert_transition

log = structlog.get_logger("aegis.lab.governance")

DEFAULT_EXPIRY_HOURS = 72
MAX_EXPIRY_HOURS = 24 * 30
MAX_PAYLOAD_BYTES = 64 * 1024
DEFAULT_SIGNAL = "approval"
# Credentials that act directly on behalf of the requesting human (separation of duties applies).
_HUMAN_CREDENTIAL_KINDS = frozenset({"user", "api_key"})


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def _uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"Invalid {label} id") from exc


def _money(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except ArithmeticError as exc:
        raise ValidationFailed("estimated_cost_usd must be a number") from exc
    if not amount.is_finite() or amount < 0:
        raise ValidationFailed("estimated_cost_usd must be a non-negative number")
    return amount.quantize(Decimal("0.000001"))


def _decision_dict(decision: Decision | Mapping[str, Any] | None) -> dict[str, Any]:
    if decision is None:
        return {}
    if isinstance(decision, Decision):
        return jsonable(decision.to_dict())
    return jsonable(dict(decision))


def _action_label(action: str) -> str:
    return action if is_valid_action_name(action) else "other"


def approval_out(approval: Approval) -> ApprovalOut:
    return ApprovalOut.model_validate(approval)


def _event_payload(approval: Approval, **extra: Any) -> dict[str, Any]:
    return {
        "approval_id": str(approval.id),
        "action": approval.action,
        "subject_type": approval.subject_type,
        "subject_id": approval.subject_id,
        "title": approval.title,
        "status": approval.status,
        "risk_level": approval.risk_level,
        "estimated_cost_usd": approval.estimated_cost_usd,
        "required_permission": approval.required_permission,
        "expires_at": approval.expires_at,
        "workflow_run_id": approval.workflow_run_id,
        **extra,
    }


def _emit(db: Session, approval: Approval, type_: EventType, actor: Actor, **extra: Any) -> None:
    emit(
        db,
        organization_id=approval.organization_id,
        type=type_,
        payload=_event_payload(approval, **extra),
        mission_id=approval.mission_id,
        project_id=approval.project_id,
        workspace_id=approval.workspace_id,
        subject_type="approval",
        subject_id=approval.id,
        actor=actor,
    )


def _signal_workflow(db: Session, approval: Approval, *, approved: bool, reason: str | None) -> None:
    """Best-effort signal to the waiting workflow; workflows also poll approval status."""
    if approval.workflow_run_id is None:
        return
    try:
        launcher = importlib.import_module("aegis_api.lab.workflows.launcher")
    except ImportError:
        log.info("approval_signal_skipped", reason="workflow launcher unavailable", approval_id=str(approval.id))
        return
    signal = getattr(launcher, "signal_workflow", None)
    if signal is None:
        return
    payload = {"approval_id": str(approval.id), "approved": approved, "reason": reason, "status": approval.status}
    try:
        with db.begin_nested():
            signal(db, approval.workflow_run_id, approval.signal_name or DEFAULT_SIGNAL, payload)
    except Exception:
        log.warning("approval_signal_failed", approval_id=str(approval.id), exc_info=True)


# --- decision hooks ---------------------------------------------------------------------------------
# Contexts that own the subject of an approval (discoveries, strategy promotions, deep-research plans,
# failure recoveries…) react to human decisions through hooks registered at import time:
#     register_approval_hook("discovery.", on_approval_decided)
# Hooks run after the decision is recorded, in a savepoint, with the deciding (human) actor. A failing hook
# never undoes the decision; the owning workflow reconciles by polling the approval status.
ApprovalHook = Callable[[Session, Actor, Approval], None]
_HOOKS: list[tuple[str, ApprovalHook]] = []
HOOK_MODULES: tuple[str, ...] = (
    "aegis_api.lab.verification.discoveries",
    "aegis_api.lab.strategies.promotion",
    "aegis_api.lab.research.deep_research",
    "aegis_api.lab.failures.recovery",
    "aegis_api.lab.missions.service",
    "aegis_api.lab.tools.broker",
)
_hooks_loaded = False


def register_approval_hook(action_prefix: str, hook: ApprovalHook) -> None:
    """Register ``hook`` for approvals whose action starts with ``action_prefix`` (idempotent)."""
    if not any(prefix == action_prefix and fn is hook for prefix, fn in _HOOKS):
        _HOOKS.append((action_prefix, hook))


def _load_hook_modules() -> None:
    global _hooks_loaded
    if _hooks_loaded:
        return
    for module in HOOK_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    _hooks_loaded = True


def _run_hooks(db: Session, actor: Actor, approval: Approval) -> None:
    _load_hook_modules()
    for prefix, hook in list(_HOOKS):
        if not approval.action.startswith(prefix):
            continue
        try:
            with db.begin_nested():
                hook(db, actor, approval)
        except Exception:
            log.warning("approval_hook_failed", approval_id=str(approval.id), action=approval.action, exc_info=True)


def _load_locked(db: Session, actor: Actor, approval_id: uuid.UUID | str) -> Approval:
    approval = get_owned(db, Approval, approval_id, actor, label="Approval")
    db.flush()
    locked = db.execute(
        select(Approval).where(Approval.id == approval.id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    if locked.project_id is not None:
        load_project(db, actor, locked.project_id)  # NotFound for restricted projects the actor cannot see
    return locked


def _allow_self_approval(db: Session, organization_id: uuid.UUID) -> bool:
    org = db.get(Organization, organization_id)
    return bool(org is not None and (org.settings or {}).get("allow_self_approval") is True)


def _is_requester(approval: Approval, actor: Actor) -> bool:
    return (
        approval.requested_by_type in _HUMAN_CREDENTIAL_KINDS
        and approval.requested_by_id is not None
        and approval.requested_by_id == actor.user_id
    )


# ---------------------------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------------------------
def _announce_request(db: Session, actor: Actor, approval: Approval) -> None:
    _emit(db, approval, EventType.APPROVAL_REQUESTED, actor, requested_by=actor.as_dict())
    audit(
        db,
        actor,
        AuditAction.APPROVAL_REQUESTED,
        "approval",
        approval.id,
        after={
            "action": approval.action,
            "subject_type": approval.subject_type,
            "subject_id": approval.subject_id,
            "risk_level": approval.risk_level,
            "estimated_cost_usd": approval.estimated_cost_usd,
            "required_permission": approval.required_permission,
        },
    )


def _find_pending(
    db: Session, organization_id: uuid.UUID, action: str, subject_type: str, subject_id: str
) -> Approval | None:
    return db.scalar(
        select(Approval)
        .where(
            Approval.organization_id == organization_id,
            Approval.action == action,
            Approval.subject_type == subject_type,
            Approval.subject_id == subject_id,
            Approval.status == ApprovalStatus.PENDING,
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )


def request_approval(
    db: Session,
    actor: Actor,
    *,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID | str,
    title: str,
    payload: Mapping[str, Any] | None = None,
    risk_level: str = "MEDIUM",
    estimated_cost_usd: Decimal | float | int = 0,
    decision: Decision | Mapping[str, Any] | None = None,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    workflow_run_id: uuid.UUID | str | None = None,
    signal_name: str | None = None,
    required_permission: str = "approval:decide",
    expires_in_hours: int = DEFAULT_EXPIRY_HOURS,
) -> Approval:
    """Request a human approval (idempotent: an existing PENDING request for the same subject is returned).

    The request survives a rollback of the caller's transaction (callers typically raise
    ``ApprovalRequired(approval_id=...)`` right after this call).
    """
    if not is_valid_action_name(action):
        raise ValidationFailed("action must look like 'namespace.verb' (max 64 chars)")
    subject_type = (subject_type or "").strip()
    subject = str(subject_id).strip() if subject_id is not None else ""
    if not subject_type or len(subject_type) > 32:
        raise ValidationFailed("subject_type must be 1-32 characters")
    if not subject or len(subject) > 64:
        raise ValidationFailed("subject_id must be 1-64 characters")
    clean_title = " ".join((title or "").split())[:300]
    if not clean_title:
        raise ValidationFailed("title is required")
    risk = (risk_level or "").upper()
    if risk not in RiskLevel.__members__:
        raise ValidationFailed(f"risk_level must be one of {', '.join(RiskLevel.__members__)}")
    if required_permission not in ALL_LAB_PERMISSIONS:
        raise ValidationFailed(f"Unknown required_permission '{required_permission}'")
    if isinstance(expires_in_hours, bool) or not 1 <= int(expires_in_hours) <= MAX_EXPIRY_HOURS:
        raise ValidationFailed(f"expires_in_hours must be between 1 and {MAX_EXPIRY_HOURS}")
    if signal_name is not None and not 1 <= len(signal_name) <= 64:
        raise ValidationFailed("signal_name must be 1-64 characters")
    request_payload = jsonable(dict(payload or {}))
    if len(json.dumps(request_payload, default=str)) > MAX_PAYLOAD_BYTES:
        raise ValidationFailed(f"payload must serialize to at most {MAX_PAYLOAD_BYTES} bytes")
    cost = _money(estimated_cost_usd)

    mission: Mission | None = None
    if mission_id is not None:
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    pid = _uuid(project_id, "project")
    if mission is not None:
        if pid is not None and pid != mission.project_id:
            raise ValidationFailed("project_id does not match the mission's project")
        pid = mission.project_id
    project = get_owned(db, Project, pid, actor, label="Project") if pid is not None else None

    org_id = actor.organization_id
    advisory_xact_lock(db, f"approval:{org_id}:{action}:{subject_type}:{subject}")
    now = utcnow()
    existing = _find_pending(db, org_id, action, subject_type, subject)
    if existing is not None:
        if existing.expires_at is None or existing.expires_at > now:
            return existing
        _expire(db, existing, Actor.system(org_id, "system:approval-expiry"), now)

    approval = Approval(
        id=uuid.uuid4(),
        organization_id=org_id,
        workspace_id=project.workspace_id if project is not None else None,
        project_id=project.id if project is not None else None,
        mission_id=mission.id if mission is not None else None,
        action=action,
        subject_type=subject_type,
        subject_id=subject,
        title=clean_title,
        request_payload=request_payload,
        risk_level=risk,
        estimated_cost_usd=cost,
        requested_by_type=actor.kind,
        requested_by_id=actor.user_id,
        requested_by_agent_run_id=actor.agent_run_id,
        requested_by_label=actor.label[:320],
        policy_decision=_decision_dict(decision),
        required_permission=required_permission,
        status=ApprovalStatus.PENDING,
        expires_at=now + timedelta(hours=int(expires_in_hours)),
        workflow_run_id=_uuid(workflow_run_id, "workflow run"),
        signal_name=signal_name,
    )
    db.add(approval)
    db.flush()
    _announce_request(db, actor, approval)
    metrics.APPROVALS.labels(_action_label(action), ApprovalStatus.PENDING).inc()

    snapshot = {
        column.key: getattr(approval, column.key)
        for column in Approval.__table__.columns
        if column.key not in ("created_at", "updated_at", "lock_version")
    }

    def replay(session: Session) -> None:
        if session.get(Approval, snapshot["id"]) is not None:
            return
        advisory_xact_lock(session, f"approval:{org_id}:{action}:{subject_type}:{subject}")
        if _find_pending(session, org_id, action, subject_type, subject) is not None:
            return
        restored = Approval(**snapshot)
        session.add(restored)
        session.flush()
        _announce_request(session, actor, restored)

    on_rollback(db, org_id, replay, user_id=actor.user_id, name="approval_requested")
    return approval


# ---------------------------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------------------------
def _expire(db: Session, approval: Approval, actor: Actor, now: datetime) -> None:
    assert_transition("approval", approval.status, ApprovalStatus.EXPIRED)
    approval.status = ApprovalStatus.EXPIRED
    approval.decided_at = now
    approval.decision_reason = "Expired without a decision"
    db.flush()
    _emit(db, approval, EventType.APPROVAL_DECIDED, actor, approved=False)
    metrics.APPROVALS.labels(_action_label(approval.action), ApprovalStatus.EXPIRED).inc()
    _signal_workflow(db, approval, approved=False, reason="expired")


def _expire_by_id(organization_id: uuid.UUID, approval_id: uuid.UUID) -> Any:
    def replay(session: Session) -> None:
        row = session.execute(select(Approval).where(Approval.id == approval_id).with_for_update()).scalar_one_or_none()
        now = utcnow()
        if row is None or row.status != ApprovalStatus.PENDING or row.expires_at is None or row.expires_at > now:
            return
        _expire(session, row, Actor.system(organization_id, "system:approval-expiry"), now)

    return replay


def expire_due_approvals(db: Session, *, now: datetime | None = None, limit: int = 500) -> int:
    """Mark PENDING approvals past ``expires_at`` as EXPIRED (tenant-scoped; idempotent)."""
    moment = now or utcnow()
    rows = db.scalars(
        select(Approval)
        .where(
            Approval.status == ApprovalStatus.PENDING, Approval.expires_at.is_not(None), Approval.expires_at < moment
        )
        .order_by(Approval.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for approval in rows:
        _expire(db, approval, Actor.system(approval.organization_id, "system:approval-expiry"), moment)
    return len(rows)


# ---------------------------------------------------------------------------------------------
# Decide / cancel
# ---------------------------------------------------------------------------------------------
def decide_approval(db: Session, actor: Actor, approval_id: uuid.UUID | str, *, approve: bool, reason: str) -> Approval:
    """Approve or reject a PENDING request (humans only, with the required permission, never one's own)."""
    actor.require_human("deciding an approval")
    clean_reason = (reason or "").strip()
    if not 3 <= len(clean_reason) <= 2000:
        raise ValidationFailed("reason must be 3-2000 characters")
    approval = _load_locked(db, actor, approval_id)
    if approval.project_id is not None:
        project = load_project(db, actor, approval.project_id)
        granted = effective_permissions(db, actor, project)
    else:
        granted = actor.permissions
    required = approval.required_permission or "approval:decide"
    if required not in granted:
        raise Forbidden(f"Deciding this approval requires the '{required}' permission")

    target = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    assert_transition("approval", approval.status, target)
    now = utcnow()
    if approval.expires_at is not None and approval.expires_at <= now:
        _expire(db, approval, actor, now)
        on_rollback(db, actor.organization_id, _expire_by_id(actor.organization_id, approval.id), name="expire")
        raise Conflict(
            "This approval request has expired",
            code="approval_expired",
            details={"approval_id": str(approval.id), "expired_at": approval.expires_at.isoformat()},
        )
    if _is_requester(approval, actor) and not _allow_self_approval(db, actor.organization_id):
        raise Forbidden(
            "Separation of duties: you cannot decide an approval you requested",
            code="separation_of_duties",
        )

    approval.status = target
    approval.decided_by_id = actor.user_id
    approval.decision_reason = clean_reason
    approval.decided_at = now
    db.flush()
    _emit(db, approval, EventType.APPROVAL_DECIDED, actor, approved=approve, decided_by=actor.as_dict())
    audit(
        db,
        actor,
        AuditAction.APPROVAL_GRANTED if approve else AuditAction.APPROVAL_REJECTED,
        "approval",
        approval.id,
        before={"status": ApprovalStatus.PENDING},
        after={"status": target, "action": approval.action, "subject_id": approval.subject_id, "reason": clean_reason},
    )
    metrics.APPROVALS.labels(_action_label(approval.action), target).inc()
    _signal_workflow(db, approval, approved=approve, reason=clean_reason)
    _run_hooks(db, actor, approval)
    return approval


def cancel_approval(db: Session, actor: Actor, approval_id: uuid.UUID | str, *, reason: str | None = None) -> Approval:
    """Withdraw a PENDING request (the requester, the requesting agent/workflow, or an org owner/admin)."""
    approval = _load_locked(db, actor, approval_id)
    is_requester = (
        (_is_requester(approval, actor) and actor.kind in _HUMAN_CREDENTIAL_KINDS)
        or (actor.agent_run_id is not None and approval.requested_by_agent_run_id == actor.agent_run_id)
        or (actor.kind == "workflow" and approval.workflow_run_id == actor.workflow_run_id)
        or actor.kind == "system"
    )
    is_admin = actor.is_human and actor.role in ORG_SUPERUSER_ROLES
    if not (is_requester or is_admin):
        raise Forbidden("Only the requester or an organization admin can cancel this approval")
    assert_transition("approval", approval.status, ApprovalStatus.CANCELLED)
    clean_reason = (reason or "").strip()[:2000] or None
    approval.status = ApprovalStatus.CANCELLED
    approval.decided_at = utcnow()
    approval.decision_reason = clean_reason or "Cancelled"
    if actor.is_human:
        approval.decided_by_id = actor.user_id
    db.flush()
    _emit(db, approval, EventType.APPROVAL_DECIDED, actor, approved=False, cancelled=True)
    audit(
        db,
        actor,
        "APPROVAL_CANCELLED",
        "approval",
        approval.id,
        before={"status": ApprovalStatus.PENDING},
        after={"status": ApprovalStatus.CANCELLED, "reason": clean_reason},
    )
    metrics.APPROVALS.labels(_action_label(approval.action), ApprovalStatus.CANCELLED).inc()
    _signal_workflow(db, approval, approved=False, reason=clean_reason or "cancelled")
    return approval


# ---------------------------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------------------------
def is_approved(
    db: Session, organization_id: uuid.UUID, action: str, subject_type: str, subject_id: uuid.UUID | str
) -> bool:
    """True when an APPROVED decision exists for this organization + action + subject."""
    return (
        db.scalar(
            select(Approval.id)
            .where(
                Approval.organization_id == organization_id,
                Approval.action == action,
                Approval.subject_type == subject_type,
                Approval.subject_id == str(subject_id),
                Approval.status == ApprovalStatus.APPROVED,
            )
            .limit(1)
        )
        is not None
    )


def get_approval(db: Session, actor: Actor, approval_id: uuid.UUID | str) -> Approval:
    approval = get_owned(db, Approval, approval_id, actor, label="Approval")
    if approval.project_id is not None:
        try:
            load_project(db, actor, approval.project_id)
        except NotFound as exc:
            raise NotFound("Approval not found") from exc
    return approval


def list_approvals(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    status: str | None = None,
    mission_id: uuid.UUID | str | None = None,
    project_id: uuid.UUID | str | None = None,
    action: str | None = None,
) -> Page[ApprovalOut]:
    stmt = select(Approval).where(Approval.organization_id == actor.organization_id)
    if status:
        if status.upper() not in ApprovalStatus.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(ApprovalStatus.__members__)}")
        stmt = stmt.where(Approval.status == status.upper())
    if mission_id:
        stmt = stmt.where(Approval.mission_id == _uuid(mission_id, "mission"))
    if project_id:
        stmt = stmt.where(Approval.project_id == load_project(db, actor, project_id).id)
    if action:
        stmt = stmt.where(Approval.action == action)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where((Approval.project_id.is_(None)) | (Approval.project_id.in_(visible)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Approval.created_at.desc(), Approval.id.desc()).limit(params.page_size).offset(params.offset)
    ).all()
    return Page.build([approval_out(a) for a in rows], int(total), params)
