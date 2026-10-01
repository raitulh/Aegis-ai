"""Authentication, session and current-user endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import SESSION_COOKIE, _raw_session, get_current_principal
from aegis_api.errors import Unauthorized
from aegis_api.ratelimit import guest_rate_limit, login_throttle, public_rate_limit
from aegis_api.schemas.auth import (
    InvitationAccept,
    LoginRequest,
    OrganizationOut,
    SessionOut,
    SignupRequest,
    SupabaseExchangeRequest,
    UserOut,
)
from aegis_api.schemas.common import Message
from aegis_api.security.context import Principal
from aegis_api.security.rbac import permissions_for_role
from aegis_api.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def _audit_auth_event(db: Session, result: auth_service.SessionResult, action: str) -> None:
    from aegis_api.services import audit_log

    audit_log.record(
        db,
        organization_id=result.organization.id,
        action=action,
        resource_type="user",
        resource_id=result.user.id,
        actor_type="user",
        actor_label=result.user.email,
        user_id=result.user.id,
    )


def _audit_logout(db: Session, user_id: uuid.UUID, request: Request) -> None:
    from aegis_api.models import User
    from aegis_api.services import audit_log

    user = db.get(User, user_id)
    if user is None or user.default_organization_id is None:
        return
    audit_log.record(
        db,
        organization_id=user.default_organization_id,
        action="auth.logout",
        resource_type="user",
        resource_id=user.id,
        actor_type="user",
        actor_label=user.email,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
    )


def _session_out(result: auth_service.SessionResult) -> SessionOut:
    return SessionOut(
        user=UserOut.model_validate(result.user),
        organization=OrganizationOut.model_validate(result.organization),
        role=result.membership.role,
        permissions=sorted(permissions_for_role(result.membership.role)),
        is_guest=result.user.is_guest,
        expires_at=result.expires_at,
    )


@router.post("/signup", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
def signup(body: SignupRequest, response: Response, db: Session = Depends(_raw_session)) -> SessionOut:
    result = auth_service.signup(
        db, email=body.email, password=body.password, full_name=body.full_name, org_name=body.organization_name
    )
    _audit_auth_event(db, result, "auth.signup")
    db.commit()
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    return _session_out(result)


@router.post("/login", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
def login(body: LoginRequest, response: Response, db: Session = Depends(_raw_session)) -> SessionOut:
    throttle = login_throttle()
    throttle.check(body.email)
    try:
        result = auth_service.login(db, email=body.email, password=body.password)
    except Unauthorized:
        throttle.record_failure(body.email)
        # Security log (no password, email pseudonymised): visible to operators, not tied to a tenant.
        import hashlib

        import structlog

        structlog.get_logger("aegis.auth").warning(
            "login_failed", account=hashlib.sha256(body.email.strip().lower().encode()).hexdigest()[:16]
        )
        raise
    throttle.reset(body.email)
    _audit_auth_event(db, result, "auth.login")
    db.commit()
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    return _session_out(result)


@router.post("/guest", response_model=SessionOut, dependencies=[Depends(public_rate_limit), Depends(guest_rate_limit)])
def guest(response: Response, db: Session = Depends(_raw_session)) -> SessionOut:
    """Provision a temporary demo sandbox — no signup required (used by the /demo experience)."""
    from aegis_api.services import demo_service

    result = auth_service.create_guest_sandbox(db)
    demo_service.seed_workspace(db, result.organization, minimal=True)
    db.commit()
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    return _session_out(result)


@router.post("/supabase", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
def supabase_exchange(body: SupabaseExchangeRequest, db: Session = Depends(_raw_session)) -> SessionOut:
    user = auth_service.authenticate_supabase(db, body.access_token)
    membership = auth_service.membership_for(db, user, None)
    db.commit()
    if membership is None:
        raise Unauthorized("This account has no active workspace")
    from aegis_api.models import Organization

    org = db.get(Organization, membership.organization_id)
    return SessionOut(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org),
        role=membership.role,
        permissions=sorted(permissions_for_role(membership.role)),
        is_guest=user.is_guest,
    )


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: Session = Depends(_raw_session)) -> Message:
    token = request.cookies.get(SESSION_COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer ") and auth[7:].startswith("aegs_"):
        token = auth[7:]
    if token:
        user_id = auth_service.revoke_session(db, token)
        if user_id is not None:
            _audit_logout(db, user_id, request)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Message(message="Signed out")


@router.get("/session", response_model=SessionOut)
def current_session(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(_raw_session)
) -> SessionOut:
    from aegis_api.models import Organization, User

    user = db.get(User, principal.user_id)
    org = db.get(Organization, principal.organization_id)
    return SessionOut(
        user=UserOut.model_validate(user)
        if user
        else UserOut(
            id=str(principal.user_id),
            email=principal.email or "",
            full_name=principal.display_name,
            is_guest=principal.is_guest,
            auth_provider=principal.auth_method,
        ),
        organization=OrganizationOut.model_validate(org),
        role=principal.role,
        permissions=sorted(principal.permissions),
        is_guest=principal.is_guest,
    )


@router.post("/invitations/accept", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
def accept_invitation(
    body: InvitationAccept, request: Request, response: Response, db: Session = Depends(_raw_session)
) -> SessionOut:
    """Accept a workspace invitation. Existing accounts must be signed in as the invited email; new users
    supply a name and password and an account is created."""
    from aegis_api.deps import _extract_credentials

    _, session_token, _ = _extract_credentials(request)
    current = auth_service.resolve_session(db, session_token) if session_token else None
    result = auth_service.accept_invitation(
        db, token=body.token, current_user=current, password=body.password, full_name=body.full_name
    )
    db.commit()
    if result.session_token:
        _set_cookie(response, result.session_token)
    return _session_out(result)
