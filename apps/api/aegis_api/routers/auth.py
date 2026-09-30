"""Authentication, session and current-user endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import SESSION_COOKIE, _raw_session, get_current_principal
from aegis_api.errors import Unauthorized
from aegis_api.ratelimit import public_rate_limit
from aegis_api.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    OrganizationOut,
    RefreshRequest,
    ResetPasswordRequest,
    SessionOut,
    SignupRequest,
    SupabaseExchangeRequest,
    TokenRequest,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
)
from aegis_api.schemas.common import Message
from aegis_api.security.context import Principal
from aegis_api.security.rbac import permissions_for_role
from aegis_api.services import account_service, audit_log, auth_service, sso_service, token_service

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
    audit_log.record(
        db,
        organization_id=result.organization.id,
        action="auth.login",
        resource_type="user",
        resource_id=result.user.id,
        actor_label=result.user.email,
        actor_type="user",
    )
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
        user = auth_service.resolve_session(db, token)
        auth_service.revoke_session(db, token)
        if user is not None and user.default_organization_id:
            audit_log.record(
                db,
                organization_id=user.default_organization_id,
                action="auth.logout",
                resource_type="user",
                resource_id=user.id,
                actor_label=user.email,
                actor_type="user",
            )
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


# --- token authentication (API / SDK clients) ------------------------------------------------------


def _token_response(pair: token_service.TokenPair) -> TokenResponse:
    from aegis_api.db.base import utcnow

    now = utcnow()
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=int((pair.access_expires_at - now).total_seconds()),
        refresh_token=pair.refresh_token,
        refresh_expires_in=int((pair.refresh_expires_at - now).total_seconds()),
        organization_id=str(pair.organization_id),
    )


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Password grant: issue an access + refresh token pair",
    description="Returns a short-lived JWT access token and a rotating refresh token bound to one workspace.",
    dependencies=[Depends(public_rate_limit)],
)
def issue_token(body: TokenRequest, request: Request, db: Session = Depends(_raw_session)) -> TokenResponse:
    result = auth_service.login(db, email=body.email, password=body.password)
    org_id = result.organization.id
    if body.organization_id:
        try:
            requested = uuid.UUID(body.organization_id)
        except ValueError as exc:
            raise Unauthorized("Unknown workspace") from exc
        membership = auth_service.membership_for(db, result.user, requested)
        if membership is None:
            raise Unauthorized("You do not have access to this workspace")
        org_id = membership.organization_id
    pair = token_service.issue_pair(
        db, user=result.user, organization_id=org_id, user_agent=request.headers.get("user-agent")
    )
    audit_log.record(
        db,
        organization_id=org_id,
        action="auth.token_issued",
        resource_type="refresh_token_family",
        resource_id=pair.family_id,
        actor_label=result.user.email,
        actor_type="user",
    )
    db.commit()
    return _token_response(pair)


@router.post(
    "/token/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token",
    description="Exchanges a refresh token for a new pair. Re-using a rotated token revokes the whole token family.",
    dependencies=[Depends(public_rate_limit)],
)
def refresh_token(body: RefreshRequest, request: Request, db: Session = Depends(_raw_session)) -> TokenResponse:
    pair = token_service.refresh(db, body.refresh_token, user_agent=request.headers.get("user-agent"))
    db.commit()
    return _token_response(pair)


@router.post("/token/revoke", response_model=Message, summary="Revoke a refresh token family (logout)")
def revoke_token(body: RefreshRequest, db: Session = Depends(_raw_session)) -> Message:
    token_service.revoke(db, body.refresh_token)
    db.commit()
    return Message(message="Token revoked")


# --- password reset & email verification ----------------------------------------------------------


@router.post(
    "/password/forgot",
    response_model=Message,
    status_code=202,
    summary="Request a password reset email",
    dependencies=[Depends(public_rate_limit)],
)
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(_raw_session)) -> Message:
    account_service.request_password_reset(db, body.email)
    db.commit()
    return Message(message="If an account exists for this email, a reset link has been sent")


@router.post(
    "/password/reset",
    response_model=Message,
    summary="Complete a password reset (revokes all sessions and tokens)",
    dependencies=[Depends(public_rate_limit)],
)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(_raw_session)) -> Message:
    user = account_service.reset_password(db, body.token, body.new_password)
    if user.default_organization_id:
        audit_log.record(
            db,
            organization_id=user.default_organization_id,
            action="auth.password_reset",
            resource_type="user",
            resource_id=user.id,
            actor_label=user.email,
            actor_type="user",
        )
    db.commit()
    return Message(message="Password updated. Please sign in again.")


@router.post(
    "/email/verify/request", response_model=Message, status_code=202, summary="Send an email verification link"
)
def request_email_verification(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(_raw_session)
) -> Message:
    from aegis_api.models import User

    user = db.get(User, principal.user_id)
    if user is not None and principal.actor_type == "user":
        account_service.request_email_verification(db, user)
        db.commit()
    return Message(message="Verification email sent if the address is not yet verified")


@router.post(
    "/email/verify",
    response_model=Message,
    summary="Confirm an email address",
    dependencies=[Depends(public_rate_limit)],
)
def verify_email(body: VerifyEmailRequest, db: Session = Depends(_raw_session)) -> Message:
    account_service.verify_email(db, body.token)
    db.commit()
    return Message(message="Email verified")


# --- enterprise SSO (OIDC) --------------------------------------------------------------------------
SSO_STATE_COOKIE = "aegis_sso_state"


@router.get(
    "/sso/{connection_id}/authorize",
    summary="Start an OIDC single sign-on flow",
    response_class=RedirectResponse,
    status_code=307,
    dependencies=[Depends(public_rate_limit)],
)
def sso_authorize(connection_id: uuid.UUID, db: Session = Depends(_raw_session)) -> RedirectResponse:
    url, sealed = sso_service.authorization_url(db, connection_id)
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(
        SSO_STATE_COOKIE,
        sealed,
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
        max_age=600,
        path="/",
    )
    return response


@router.get(
    "/sso/callback",
    summary="OIDC redirect URI",
    response_class=RedirectResponse,
    status_code=303,
    dependencies=[Depends(public_rate_limit)],
)
def sso_callback(
    request: Request,
    code: str = Query(..., max_length=2048),
    state: str = Query(..., max_length=256),
    db: Session = Depends(_raw_session),
) -> RedirectResponse:
    user, membership = sso_service.complete(
        db, code=code, state=state, sealed_state=request.cookies.get(SSO_STATE_COOKIE)
    )
    token, _expires = auth_service._issue_session(db, user)
    audit_log.record(
        db,
        organization_id=membership.organization_id,
        action="auth.sso_login",
        resource_type="user",
        resource_id=user.id,
        actor_label=user.email,
        actor_type="user",
    )
    db.commit()
    response = RedirectResponse(get_settings().web_base_url.rstrip("/") + "/dashboard", status_code=303)
    _set_cookie(response, token)
    response.delete_cookie(SSO_STATE_COOKIE, path="/")
    return response
