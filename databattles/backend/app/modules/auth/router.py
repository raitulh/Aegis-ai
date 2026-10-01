from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Actor, get_actor, require_user
from app.core.errors import AppError, NotFound
from app.core.rate_limit import limit_by_ip
from app.core.schemas import Message
from app.core.security import make_signed_payload, new_token, read_signed_payload
from app.integrations.oauth import enabled_providers, get_provider
from app.models.community import EmailOutbox
from app.modules.auth import service
from app.modules.auth.schemas import (
    ChangeEmailIn,
    ChangePasswordIn,
    EmailIn,
    LoginIn,
    LoginOut,
    MailOut,
    MeOut,
    ResetPasswordIn,
    SessionOut,
    SignupIn,
    TokenIn,
)

router = APIRouter(prefix="/auth", tags=["auth"])

OAUTH_COOKIE = "db_oauth"
_SIGNUP_MESSAGE = "Check your inbox — we sent a link to verify your email address."


def safe_next(path: str | None) -> str:
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or "\n" in path:
        return "/dashboard"
    return path[:300]


@router.get("/csrf", response_model=Message, summary="Ensure the CSRF cookie is set")
def csrf() -> Message:
    return Message(message="ok")


@router.post("/signup", response_model=Message, status_code=202)
def signup(data: SignupIn, request: Request, db: Session = Depends(get_db)) -> Message:
    service.signup(db, data, request)
    return Message(message=_SIGNUP_MESSAGE)


@router.post("/login", response_model=LoginOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> LoginOut:
    user, token = service.login(db, data, request)
    service.set_session_cookie(response, token)
    return LoginOut(user=service.build_me(db, user), session_token=token if data.issue_bearer else None)


@router.post("/logout", response_model=Message)
def logout(response: Response, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Message:
    if actor.is_authenticated:
        service.logout(db, actor)
    service.clear_session_cookie(response)
    return Message(message="Signed out.")


@router.get("/me", response_model=MeOut | None)
def me(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> MeOut | None:
    if not actor.is_authenticated or actor.user is None:
        return None
    return service.build_me(db, actor.user)


@router.post("/verify-email", response_model=LoginOut)
def verify_email(data: TokenIn, request: Request, response: Response, db: Session = Depends(get_db)) -> LoginOut:
    user, token = service.verify_email(db, data.token, request)
    service.set_session_cookie(response, token)
    return LoginOut(user=service.build_me(db, user))


@router.post("/resend-verification", response_model=Message, status_code=202)
def resend_verification(data: EmailIn, request: Request, db: Session = Depends(get_db)) -> Message:
    service.resend_verification(db, data.email, request)
    return Message(message="If that account needs verification, a new link is on its way.")


@router.post("/forgot-password", response_model=Message, status_code=202)
def forgot_password(data: EmailIn, request: Request, db: Session = Depends(get_db)) -> Message:
    service.forgot_password(db, data.email, request)
    return Message(message="If an account exists for that email, a reset link is on its way.")


@router.post("/reset-password", response_model=Message)
def reset_password(data: ResetPasswordIn, request: Request, db: Session = Depends(get_db)) -> Message:
    service.reset_password(db, data.token, data.password, request)
    return Message(message="Password updated. Sign in with your new password.")


@router.post("/change-password", response_model=Message)
def change_password(data: ChangePasswordIn, request: Request, actor: Actor = Depends(require_user),
                    db: Session = Depends(get_db)) -> Message:
    service.change_password(db, actor, data.current_password, data.new_password, request)
    return Message(message="Password changed. Other devices were signed out.")


@router.post("/change-email", response_model=Message, status_code=202)
def change_email(data: ChangeEmailIn, request: Request, actor: Actor = Depends(require_user),
                 db: Session = Depends(get_db)) -> Message:
    service.change_email(db, actor, data.current_password, data.new_email, request)
    return Message(message="Check the new inbox to confirm the change.")


@router.get("/sessions", response_model=list[SessionOut])
def sessions(actor: Actor = Depends(require_user), db: Session = Depends(get_db)) -> list[SessionOut]:
    return [SessionOut(id=s.id, created_at=s.created_at, last_seen_at=s.last_seen_at, expires_at=s.expires_at,
                       user_agent=s.user_agent, current=s.id == actor.session_id) for s in service.list_sessions(db, actor)]


@router.delete("/sessions/{session_id}", response_model=Message)
def revoke_session(session_id: uuid.UUID, actor: Actor = Depends(require_user), db: Session = Depends(get_db)) -> Message:
    service.revoke_session(db, actor, session_id)
    return Message(message="Session revoked.")


@router.post("/sessions/revoke-others", response_model=Message)
def revoke_others(actor: Actor = Depends(require_user), db: Session = Depends(get_db)) -> Message:
    service.revoke_other_sessions(db, actor)
    return Message(message="Signed out of all other devices.")


@router.get("/providers")
def providers() -> dict[str, list[str]]:
    return {"providers": enabled_providers()}


@router.get("/oauth/{provider}/start", dependencies=[Depends(limit_by_ip("oauth_start", 20, 60))])
def oauth_start(provider: str, next: str | None = Query(default=None)) -> RedirectResponse:
    impl = get_provider(provider)
    if impl is None:
        raise NotFound("This sign-in provider is not enabled.")
    state = new_token(24)
    redirect_uri = f"{settings.WEB_BASE_URL}/api/v1/auth/oauth/{provider}/callback"
    resp = RedirectResponse(impl.authorize_url(state, redirect_uri, "login"), status_code=302)
    cookie = make_signed_payload({"s": state, "n": safe_next(next), "p": provider}, "oauth", 600)
    resp.set_cookie(OAUTH_COOKIE, cookie, max_age=600, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
                    path="/api/v1/auth/oauth")
    return resp


@router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request, code: str | None = Query(None, max_length=512),
                   state: str | None = Query(None, max_length=128), error: str | None = Query(None, max_length=64),
                   db: Session = Depends(get_db)) -> RedirectResponse:
    if error or not code or not state:  # the user declined on the provider's consent screen
        resp = RedirectResponse(f"{settings.WEB_BASE_URL}/login?error=oauth_denied", status_code=302)
        resp.delete_cookie(OAUTH_COOKIE, path="/api/v1/auth/oauth")
        return resp
    impl = get_provider(provider)
    stored = read_signed_payload(request.cookies.get(OAUTH_COOKIE, ""), "oauth")
    if impl is None or stored is None or stored.get("s") != state or stored.get("p") != provider:
        return RedirectResponse(f"{settings.WEB_BASE_URL}/login?error=oauth_state", status_code=302)
    redirect_uri = f"{settings.WEB_BASE_URL}/api/v1/auth/oauth/{provider}/callback"
    try:
        profile = impl.exchange(code, redirect_uri)
        _, token = service.oauth_login(db, profile, request)
    except AppError as exc:
        return RedirectResponse(f"{settings.WEB_BASE_URL}/login?error={exc.code}", status_code=302)
    resp = RedirectResponse(f"{settings.WEB_BASE_URL}{safe_next(stored.get('n'))}", status_code=302)
    service.set_session_cookie(resp, token)
    resp.delete_cookie(OAUTH_COOKIE, path="/api/v1/auth/oauth")
    return resp


@router.get("/dev/mailbox", response_model=list[MailOut], include_in_schema=False)
def dev_mailbox(db: Session = Depends(get_db)) -> list[MailOut]:
    """Development-only view of outgoing email (verification links etc.). Disabled in production."""
    if settings.is_production:
        raise NotFound()
    rows = db.scalars(select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(50)).all()
    return [MailOut.model_validate(r) for r in rows]
