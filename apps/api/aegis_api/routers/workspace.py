"""Overview dashboard, global search, team, API keys, integrations, notifications, audit log."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.deps import get_db, require
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.models import (
    AISystem,
    Audit,
    AuditLog,
    Control,
    Finding,
    Integration,
    Membership,
    Notification,
    Policy,
    User,
)
from aegis_api.models.enums import OPEN_FINDING_STATUSES, IntegrationKind, Role
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.auth import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    InviteCreate,
    InviteCreated,
    MemberOut,
    RoleUpdate,
)
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.findings import IntegrationOut, NotificationOut, OverviewStats, SearchResponse, SearchResult
from aegis_api.security.context import Principal
from aegis_api.security.rbac import ASSIGNABLE_ROLES, can_assign_role
from aegis_api.services import api_key_service, audit_log, risk_service

router = APIRouter(prefix="/api/v1", tags=["Workspace"])


# --- overview ----------------------------------------------------------------------------------
@router.get("/overview", response_model=OverviewStats)
def overview(principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)) -> OverviewStats:
    org_id = principal.organization_id
    active_systems = (
        db.scalar(
            select(func.count(AISystem.id)).where(AISystem.organization_id == org_id, AISystem.deleted_at.is_(None))
        )
        or 0
    )
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    audits_today = (
        db.scalar(select(func.count(Audit.id)).where(Audit.organization_id == org_id, Audit.created_at >= today)) or 0
    )
    open_findings = (
        db.scalar(
            select(func.count(Finding.id)).where(
                Finding.organization_id == org_id, Finding.status.in_(list(OPEN_FINDING_STATUSES))
            )
        )
        or 0
    )
    critical = (
        db.scalar(
            select(func.count(Finding.id)).where(
                Finding.organization_id == org_id,
                Finding.status.in_(list(OPEN_FINDING_STATUSES)),
                Finding.risk_level.in_(["high", "critical"]),
            )
        )
        or 0
    )
    severity_rows = db.execute(
        select(Finding.severity, func.count(Finding.id))
        .where(Finding.organization_id == org_id, Finding.status.in_(list(OPEN_FINDING_STATUSES)))
        .group_by(Finding.severity)
    ).all()
    total_controls = (
        db.scalar(select(func.count(Control.id)).where(Control.organization_id == org_id, Control.status == "active"))
        or 0
    )
    from aegis_api.models import ControlAssessment

    tested_controls = (
        db.scalar(
            select(func.count(func.distinct(ControlAssessment.control_id))).where(
                ControlAssessment.organization_id == org_id, ControlAssessment.status != "not_tested"
            )
        )
        or 0
    )
    coverage = round(100 * tested_controls / total_controls, 1) if total_controls else 0.0
    current, previous = risk_service.current_dimensions(db, org_id)
    from engines.risk.scoring import posture_from_scores

    recent_audits = db.scalars(
        select(Audit).where(Audit.organization_id == org_id).order_by(Audit.created_at.desc()).limit(6)
    ).all()
    recent_incidents = db.scalars(
        select(Finding)
        .where(Finding.organization_id == org_id, Finding.risk_level.in_(["high", "critical"]))
        .order_by(Finding.created_at.desc())
        .limit(6)
    ).all()
    test_volume = sum(s.get("test_volume", 0) for s in risk_service.risk_trend(db, org_id, days=30))
    from aegis_api.models import Organization

    org = db.get(Organization, org_id)
    return OverviewStats(
        active_systems=int(active_systems),
        audits_today=int(audits_today),
        open_findings=int(open_findings),
        critical_risks=int(critical),
        policy_coverage=coverage,
        posture=posture_from_scores(current),
        dimensions=current,
        previous_dimensions=previous,
        risk_trend=risk_service.risk_trend(db, org_id, days=30),
        findings_by_severity={str(s): int(c) for s, c in severity_rows},
        recent_audits=[
            {
                "id": str(a.id),
                "name": a.name,
                "status": a.status,
                "system_id": str(a.system_id),
                "findings": a.findings_count,
                "created_at": a.created_at.isoformat(),
            }
            for a in recent_audits
        ],
        recent_incidents=[
            {
                "id": str(f.id),
                "number": f.number,
                "title": f.title,
                "risk_level": f.risk_level,
                "created_at": f.created_at.isoformat(),
            }
            for f in recent_incidents
        ],
        test_volume=int(test_volume),
        is_demo=bool(org and org.is_demo),
    )


# --- search ------------------------------------------------------------------------------------
@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    principal: Principal = Depends(require("org:read")),
    db: Session = Depends(get_db),
) -> SearchResponse:
    org_id = principal.organization_id
    like = f"%{q}%"
    groups: dict[str, list[SearchResult]] = {}

    def add(kind: str, rows, to) -> None:
        items = [to(r) for r in rows]
        if items:
            groups[kind] = items

    add(
        "systems",
        db.scalars(
            select(AISystem)
            .where(AISystem.organization_id == org_id, AISystem.deleted_at.is_(None), AISystem.name.ilike(like))
            .limit(5)
        ).all(),
        lambda s: SearchResult(
            type="system", id=str(s.id), title=s.name, subtitle=s.system_type, url=f"/dashboard/systems/{s.id}"
        ),
    )
    add(
        "findings",
        db.scalars(
            select(Finding)
            .where(Finding.organization_id == org_id, or_(Finding.title.ilike(like), Finding.description.ilike(like)))
            .limit(5)
        ).all(),
        lambda f: SearchResult(
            type="finding",
            id=str(f.id),
            title=f"#{f.number} {f.title}",
            subtitle=f.severity,
            url=f"/dashboard/findings/{f.id}",
        ),
    )
    add(
        "policies",
        db.scalars(select(Policy).where(Policy.organization_id == org_id, Policy.name.ilike(like)).limit(5)).all(),
        lambda p: SearchResult(
            type="policy", id=str(p.id), title=p.name, subtitle=p.key, url=f"/dashboard/policies/{p.id}"
        ),
    )
    add(
        "controls",
        db.scalars(
            select(Control)
            .where(Control.organization_id == org_id, or_(Control.control_id.ilike(like), Control.name.ilike(like)))
            .limit(5)
        ).all(),
        lambda c: SearchResult(
            type="control",
            id=str(c.id),
            title=f"{c.control_id}: {c.name}",
            subtitle=c.domain,
            url=f"/dashboard/policies?control={c.control_id}",
        ),
    )
    add(
        "audits",
        db.scalars(select(Audit).where(Audit.organization_id == org_id, Audit.name.ilike(like)).limit(5)).all(),
        lambda a: SearchResult(
            type="audit", id=str(a.id), title=a.name, subtitle=a.status, url=f"/dashboard/audits/{a.id}"
        ),
    )
    return SearchResponse(query=q, groups=groups, total=sum(len(v) for v in groups.values()))


# --- team --------------------------------------------------------------------------------------
@router.get("/team", response_model=list[MemberOut])
def list_team(principal: Principal = Depends(require("team:read")), db: Session = Depends(get_db)) -> list[MemberOut]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == principal.organization_id)
    ).all()
    return [
        MemberOut(
            membership_id=str(m.id),
            user_id=str(u.id),
            name=u.full_name,
            email=u.email,
            role=m.role,
            status=m.status,
            last_active_at=m.last_active_at or u.last_active_at,
        )
        for m, u in rows
    ]


@router.post("/team/invite", response_model=InviteCreated, status_code=201)
def invite(
    body: InviteCreate, principal: Principal = Depends(require("team:manage")), db: Session = Depends(get_db)
) -> InviteCreated:
    if body.role not in ASSIGNABLE_ROLES:
        raise ValidationFailed("That role cannot be assigned to a person")
    if not can_assign_role(principal.role, body.role):
        raise Forbidden("You cannot assign that role")
    from aegis_api.services import entitlements

    entitlements.check_quota(db, principal.organization_id, "seats")
    from aegis_api.config import get_settings
    from aegis_api.models import Invitation
    from aegis_api.security.tokens import keyed_hash, random_token
    from aegis_api.services import email_service

    email = body.email.strip().lower()
    existing_member = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == principal.organization_id, func.lower(User.email) == email)
    )
    if existing_member:
        raise ValidationFailed("That person is already a member of this workspace")
    # Supersede any pending invitation for the same address.
    for pending in db.scalars(
        select(Invitation).where(
            Invitation.organization_id == principal.organization_id,
            Invitation.email == email,
            Invitation.status == "pending",
        )
    ).all():
        pending.status = "revoked"
    token = random_token()
    expires = utcnow() + timedelta(days=7)
    invitation = Invitation(
        organization_id=principal.organization_id,
        email=email,
        role=body.role,
        token_hash=keyed_hash(token),
        invited_by_id=principal.user_id,
        expires_at=expires,
    )
    db.add(invitation)
    db.flush()
    invite_url = f"{get_settings().web_base_url.rstrip('/')}/invite?token={token}"
    sent = email_service.send(
        to=email,
        subject="You have been invited to an Aegis workspace",
        body=f"You were invited to join a workspace on Aegis as {body.role}.\n\nAccept: {invite_url}\n\n"
        "This link expires in 7 days.",
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="team.invited",
        resource_type="invitation",
        resource_id=invitation.id,
        principal=principal,
        after={"email": email, "role": body.role, "email_sent": sent},
    )
    return InviteCreated(
        message=f"Invitation created for {email}",
        invitation_id=str(invitation.id),
        invite_url=invite_url,
        email_sent=sent,
        expires_at=expires,
    )


@router.patch("/team/{membership_id}/role", response_model=MemberOut)
def change_role(
    membership_id: uuid.UUID,
    body: RoleUpdate,
    principal: Principal = Depends(require("team:manage")),
    db: Session = Depends(get_db),
) -> MemberOut:
    membership = db.get(Membership, membership_id)
    if membership is None or membership.organization_id != principal.organization_id:
        raise NotFound("Member not found")
    if body.role not in ASSIGNABLE_ROLES:
        raise ValidationFailed("Invalid role")
    # The actor must be allowed to both hold the *current* role's authority and grant the new one:
    # an admin can neither demote an owner nor promote anyone to owner.
    if not can_assign_role(principal.role, body.role) or not can_assign_role(principal.role, membership.role):
        raise Forbidden("You cannot change this member's role")
    if membership.role == Role.OWNER and body.role != Role.OWNER:
        owners = db.scalar(
            select(func.count(Membership.id)).where(
                Membership.organization_id == principal.organization_id,
                Membership.role == Role.OWNER,
                Membership.status == "active",
            )
        )
        if (owners or 0) <= 1:
            raise Conflict("A workspace must keep at least one owner")
    before = membership.role
    membership.role = body.role
    user = db.get(User, membership.user_id)
    if user is None:
        raise NotFound("Member not found")
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="team.role_changed",
        resource_type="membership",
        resource_id=membership.id,
        principal=principal,
        before={"role": before},
        after={"role": body.role, "user": user.email},
    )
    return MemberOut(
        membership_id=str(membership.id),
        user_id=str(user.id),
        name=user.full_name,
        email=user.email,
        role=membership.role,
        status=membership.status,
        last_active_at=membership.last_active_at,
    )


@router.delete("/team/{membership_id}", response_model=Message)
def remove_member(
    membership_id: uuid.UUID, principal: Principal = Depends(require("team:manage")), db: Session = Depends(get_db)
) -> Message:
    membership = db.get(Membership, membership_id)
    if membership is None or membership.organization_id != principal.organization_id:
        raise NotFound("Member not found")
    if membership.role == Role.OWNER:
        raise Forbidden("The workspace owner cannot be removed")
    if not can_assign_role(principal.role, membership.role):
        raise Forbidden("You cannot remove this member")
    from aegis_api.models import ApiKey

    # Keys a departing member created stop working with them.
    revoked = 0
    for key in db.scalars(
        select(ApiKey).where(
            ApiKey.organization_id == principal.organization_id,
            ApiKey.created_by_id == membership.user_id,
            ApiKey.revoked_at.is_(None),
        )
    ).all():
        key.revoked_at = utcnow()
        revoked += 1
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="team.member_removed",
        resource_type="membership",
        resource_id=membership.id,
        principal=principal,
        before={"user_id": str(membership.user_id), "role": membership.role},
        after={"api_keys_revoked": revoked},
    )
    db.delete(membership)
    return Message(message="Member removed")


# --- API keys ----------------------------------------------------------------------------------
@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    principal: Principal = Depends(require("api_keys:manage")), db: Session = Depends(get_db)
) -> list[ApiKeyOut]:
    from aegis_api.models import ApiKey

    keys = db.scalars(
        select(ApiKey).where(ApiKey.organization_id == principal.organization_id).order_by(ApiKey.created_at.desc())
    ).all()
    return [ApiKeyOut.model_validate(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    body: ApiKeyCreate, principal: Principal = Depends(require("api_keys:manage")), db: Session = Depends(get_db)
) -> ApiKeyCreated:
    if not can_assign_role(principal.role, body.role) and body.role != "viewer":
        from aegis_api.security.rbac import ROLE_RANK

        if ROLE_RANK.get(body.role, 99) > ROLE_RANK.get(principal.role, 0):
            raise Forbidden("API key role cannot exceed your own role")
    issued = api_key_service.create_api_key(
        db,
        organization_id=principal.organization_id,
        name=body.name,
        role=body.role,
        scopes=body.scopes,
        created_by_id=principal.user_id,
        expires_in_days=body.expires_in_days,
        test=body.test,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=issued.api_key.id,
        principal=principal,
    )
    return ApiKeyCreated(api_key=ApiKeyOut.model_validate(issued.api_key), plaintext=issued.plaintext)


@router.delete("/api-keys/{key_id}", response_model=Message)
def revoke_api_key(
    key_id: uuid.UUID, principal: Principal = Depends(require("api_keys:manage")), db: Session = Depends(get_db)
) -> Message:
    if not api_key_service.revoke_api_key(db, key_id, principal.organization_id):
        raise NotFound("API key not found")
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="api_key.revoked",
        resource_type="api_key",
        resource_id=key_id,
        principal=principal,
    )
    return Message(message="API key revoked")


# --- integrations ------------------------------------------------------------------------------
@router.get("/integrations", response_model=list[IntegrationOut])
def list_integrations(
    principal: Principal = Depends(require("integrations:read")), db: Session = Depends(get_db)
) -> list[IntegrationOut]:
    existing = {
        i.kind: i
        for i in db.scalars(select(Integration).where(Integration.organization_id == principal.organization_id)).all()
    }
    out = []
    for kind in IntegrationKind:
        integration = existing.get(kind)
        if integration:
            out.append(IntegrationOut.model_validate(integration))
        else:
            out.append(
                IntegrationOut(
                    id="",
                    kind=kind,
                    name=kind.replace("_", " ").title(),
                    status="not_configured",
                    config={},
                    last_checked_at=None,
                    last_error=None,
                    connected_at=None,
                )
            )
    return out


# --- notifications -----------------------------------------------------------------------------
@router.get("/notifications", response_model=Page[NotificationOut])
def list_notifications(
    params: PageParams = Depends(),
    unread: bool = Query(False),
    principal: Principal = Depends(require("org:read")),
    db: Session = Depends(get_db),
) -> Page[NotificationOut]:
    stmt = select(Notification).where(
        Notification.organization_id == principal.organization_id, Notification.user_id == principal.user_id
    )
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paginate(db, stmt.order_by(Notification.created_at.desc()), params, NotificationOut.model_validate)


@router.post("/notifications/{notification_id}/read", response_model=Message)
def mark_read(
    notification_id: uuid.UUID, principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)
) -> Message:
    n = db.get(Notification, notification_id)
    if n and n.user_id == principal.user_id:
        n.read_at = utcnow()
    return Message(message="Marked read")


@router.post("/notifications/read-all", response_model=Message)
def mark_all_read(principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)) -> Message:
    db.query(Notification).filter(Notification.user_id == principal.user_id, Notification.read_at.is_(None)).update(
        {Notification.read_at: utcnow()}
    )
    return Message(message="All marked read")


@router.get("/audit-log")
def audit_log_list(
    params: PageParams = Depends(),
    principal: Principal = Depends(require("audit_logs:read")),
    db: Session = Depends(get_db),
) -> Page:
    stmt = (
        select(AuditLog)
        .where(AuditLog.organization_id == principal.organization_id)
        .order_by(AuditLog.created_at.desc())
    )
    return paginate(
        db,
        stmt,
        params,
        lambda e: {
            "id": str(e.id),
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "actor": e.actor_label,
            "created_at": e.created_at.isoformat(),
            "request_id": e.request_id,
        },
    )


@router.get("/roles")
def roles(principal: Principal = Depends(require("team:read"))) -> list[dict]:
    """Role catalogue with the capabilities each role grants (server-enforced)."""
    from aegis_api.security.rbac import role_catalog

    return role_catalog()


@router.get("/jobs")
def jobs(
    params: PageParams = Depends(),
    status: str | None = Query(None, max_length=16),
    principal: Principal = Depends(require("jobs:read")),
    db: Session = Depends(get_db),
) -> Page:
    """Background-job ledger for this workspace (attempts, classified failures, dead letters)."""
    from aegis_api.models import JobRun

    stmt = select(JobRun).where(JobRun.organization_id == principal.organization_id)
    if status:
        stmt = stmt.where(JobRun.status == status)
    return paginate(
        db,
        stmt.order_by(JobRun.created_at.desc()),
        params,
        lambda j: {
            "id": str(j.id),
            "job": j.job.rsplit(":", 1)[-1],
            "status": j.status,
            "attempts": j.attempts,
            "max_attempts": j.max_attempts,
            "error_class": j.error_class,
            "error": j.error,
            "duration_ms": j.duration_ms,
            "created_at": j.created_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        },
    )
