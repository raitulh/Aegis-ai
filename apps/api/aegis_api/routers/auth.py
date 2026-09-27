"""Authentication, session and current-user endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import SESSION_COOKIE, _raw_session, get_current_principal
from aegis_api.errors import Unauthorized
from aegis_api.ratelimit import public_rate_limit
from aegis_api.schemas.auth import (
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
    db.commit()
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    return _session_out(result)


@router.post("/login", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
def login(body: LoginRequest, response: Response, db: Session = Depends(_raw_session)) -> SessionOut:
    result = auth_service.login(db, email=body.email, password=body.password)
    db.commit()
    assert result.session_token is not None
    _set_cookie(response, result.session_token)
    return _session_out(result)


@router.post("/guest", response_model=SessionOut, dependencies=[Depends(public_rate_limit)])
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
        auth_service.revoke_session(db, token)
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
