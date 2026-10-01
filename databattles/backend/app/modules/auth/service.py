"""Authentication (who you are). Authorization lives in app.core.permissions."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import SESSION_COOKIE, Actor
from app.core.errors import AppError, Conflict, Forbidden, NotFound, RateLimited, ValidationFailed
from app.core.ids import is_valid_handle, random_suffix, slugify
from app.core.rate_limit import client_ip, enforce
from app.core.security import (
    hash_identifier,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.core.time import utcnow
from app.email.sender import queue_email
from app.integrations.oauth import OAuthProfile
from app.models.community import Notification
from app.models.enums import UserStatus
from app.models.github import GitHubAccount
from app.models.org import Organization, OrgMembership
from app.models.user import AuthEvent, AuthSession, AuthToken, OAuthIdentity, PlatformRoleAssignment, User
from app.modules.auth.schemas import LoginIn, MembershipOut, MeOut, SignupIn
from app.modules.search.indexer import index_user

VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(minutes=30)
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_THRESHOLD = 8
GENERIC_LOGIN_ERROR = "Invalid email or password."


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _password_or_raise(password: str, email: str | None, field: str = "password") -> None:
    problems = password_problems(password, email)
    if problems:
        raise ValidationFailed(details={"fields": {field: " ".join(problems)}})


def unique_handle(db: Session, base: str) -> str:
    candidate = slugify(base, 24).replace("-", "_").strip("_") or "user"
    if len(candidate) < 3:
        candidate = (candidate + "_user")[:24]
    if not is_valid_handle(candidate):
        candidate = "user_" + random_suffix(6)
    handle = candidate
    while db.scalar(select(User.id).where(User.handle == handle)):
        handle = f"{candidate[:23]}_{random_suffix(4)}"
    return handle


def record_auth_event(db: Session, event: str, request: Request | None, *, user_id: uuid.UUID | None = None,
                      email: str | None = None, suspicious: bool = False) -> None:
    db.add(AuthEvent(
        event=event, user_id=user_id,
        email_hash=hash_identifier(normalize_email(email)) if email else None,
        ip_hash=hash_identifier(client_ip(request)) if request else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] if request else None,
        suspicious=suspicious,
    ))


def _issue_token(db: Session, user: User, purpose: str, ttl: timedelta, payload: dict | None = None) -> str:
    token = new_token(32)
    db.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=hash_token(token), payload=payload or {},
                     expires_at=utcnow() + ttl))
    return token


def consume_token(db: Session, token: str, purpose: str) -> AuthToken:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token), AuthToken.purpose == purpose)
                    .with_for_update())
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        raise AppError("This link is invalid or has expired.", code="invalid_token", status_code=400)
    row.used_at = utcnow()
    return row


def create_session(db: Session, user: User, request: Request | None) -> str:
    token = new_token(32)
    db.add(AuthSession(
        user_id=user.id, token_hash=hash_token(token), expires_at=utcnow() + timedelta(days=settings.SESSION_TTL_DAYS),
        ip_hash=hash_identifier(client_ip(request)) if request else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] if request else None,
    ))
    user.last_login_at = utcnow()
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.SESSION_TTL_DAYS * 86400, httponly=True,
                        secure=settings.COOKIE_SECURE, samesite="lax", path="/", domain=settings.COOKIE_DOMAIN)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", domain=settings.COOKIE_DOMAIN)


def build_me(db: Session, user: User) -> MeOut:
    roles = list(db.scalars(select(PlatformRoleAssignment.role).where(PlatformRoleAssignment.user_id == user.id)))
    rows = db.execute(
        select(OrgMembership, Organization).join(Organization, Organization.id == OrgMembership.org_id)
        .where(OrgMembership.user_id == user.id, OrgMembership.status.in_(["active", "pending"]))
        .order_by(Organization.name)
    ).all()
    memberships = [MembershipOut(org_id=o.id, org_slug=o.slug, org_name=o.name, org_type=o.type, role=m.role,
                                 status=m.status, verification_method=m.verification_method, verified_at=m.verified_at)
                   for m, o in rows]
    gh = db.scalar(select(GitHubAccount.login).where(GitHubAccount.user_id == user.id))
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    from app.models.user import DEFAULT_PRIVACY

    return MeOut(
        id=user.id, email=user.email, email_verified=user.email_verified_at is not None, handle=user.handle,
        display_name=user.display_name, avatar_url=user.avatar_url, headline=user.headline, timezone=user.timezone,
        platform_roles=sorted(roles), memberships=memberships,
        onboarding_completed=user.onboarding_completed_at is not None,
        open_to_opportunities=user.open_to_opportunities, privacy={**DEFAULT_PRIVACY, **(user.privacy or {})},
        github_login=gh, has_password=user.password_hash is not None, unread_notifications=unread, is_demo=user.is_demo, created_at=user.created_at,
    )


# ----------------------------------------------------------------------------- flows


def signup(db: Session, data: SignupIn, request: Request) -> None:
    """Always returns the same response, whether or not the email is registered (no account enumeration)."""
    enforce("signup", client_ip(request), limit=10, window_seconds=3600)
    email = normalize_email(data.email)
    _password_or_raise(data.password, email)
    display_name = data.display_name.strip()
    if not display_name:
        raise ValidationFailed(details={"fields": {"display_name": "Enter your name."}})
    if data.handle:
        handle = data.handle.strip().lower()
        if not is_valid_handle(handle):
            raise ValidationFailed(details={"fields": {"handle": "Use 3–30 lowercase letters, numbers, - or _."}})
        if db.scalar(select(User.id).where(User.handle == handle)):
            raise Conflict("That handle is taken.", code="handle_taken", details={"fields": {"handle": "That handle is taken."}})
    else:
        handle = unique_handle(db, display_name)

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        if existing.status != UserStatus.deleted:
            queue_email(db, existing.email, "account_exists",
                        {"name": existing.display_name, "link": f"{settings.WEB_BASE_URL}/login"},
                        dedupe_key=f"account_exists:{existing.id}:{utcnow():%Y%m%d%H}")
        record_auth_event(db, "signup_existing_email", request, email=email)
        db.commit()
        return

    user = User(email=email, password_hash=hash_password(data.password), handle=handle, display_name=display_name)
    db.add(user)
    db.flush()
    token = _issue_token(db, user, "verify_email", VERIFY_TTL)
    queue_email(db, email, "verify_email", {"name": display_name,
                                            "link": f"{settings.WEB_BASE_URL}/verify-email?token={token}"})
    record_auth_event(db, "signup", request, user_id=user.id, email=email)
    index_user(db, user)
    db.commit()


def _recent_failures(db: Session, email: str) -> int:
    return db.scalar(select(func.count()).select_from(AuthEvent).where(
        AuthEvent.email_hash == hash_identifier(email), AuthEvent.event == "login_failure",
        AuthEvent.created_at > utcnow() - LOCKOUT_WINDOW)) or 0


def login(db: Session, data: LoginIn, request: Request) -> tuple[User, str]:
    enforce("login_ip", client_ip(request), limit=30, window_seconds=60)
    email = normalize_email(data.email)
    if _recent_failures(db, email) >= LOCKOUT_THRESHOLD:
        record_auth_event(db, "login_locked", request, email=email, suspicious=True)
        db.commit()
        raise RateLimited("Too many failed attempts. Try again in 15 minutes or reset your password.",
                          code="login_locked")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.status == UserStatus.deleted or not verify_password(data.password, user.password_hash if user else None):
        failures = _recent_failures(db, email) + 1
        record_auth_event(db, "login_failure", request, user_id=user.id if user else None, email=email,
                          suspicious=failures >= LOCKOUT_THRESHOLD // 2)
        db.commit()
        raise AppError(GENERIC_LOGIN_ERROR, code="invalid_credentials", status_code=401)
    if user.status in (UserStatus.suspended, UserStatus.banned):
        record_auth_event(db, "login_blocked_suspended", request, user_id=user.id, email=email)
        db.commit()
        raise Forbidden("Your account is suspended. Contact support if you believe this is a mistake.",
                        code="account_suspended")
    if user.email_verified_at is None:
        raise Forbidden("Please verify your email address. We can resend the link.", code="email_not_verified")
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
    token = create_session(db, user, request)
    record_auth_event(db, "login_success", request, user_id=user.id, email=email)
    db.commit()
    return user, token


def verify_email(db: Session, token: str, request: Request) -> tuple[User, str]:
    row = consume_token(db, token, "verify_email")
    user = db.get(User, row.user_id)
    if user is None or user.status != UserStatus.active:
        raise AppError("This link is invalid or has expired.", code="invalid_token", status_code=400)
    new_email = row.payload.get("new_email")
    if new_email:
        if db.scalar(select(User.id).where(User.email == new_email, User.id != user.id)):
            raise AppError("This link is invalid or has expired.", code="invalid_token", status_code=400)
        user.email = new_email
    user.email_verified_at = utcnow()
    session = create_session(db, user, request)
    record_auth_event(db, "email_verified", request, user_id=user.id)
    db.commit()
    return user, session


def resend_verification(db: Session, email: str, request: Request) -> None:
    enforce("resend_verify", client_ip(request), limit=5, window_seconds=3600)
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user and user.email_verified_at is None and user.status == UserStatus.active:
        token = _issue_token(db, user, "verify_email", VERIFY_TTL)
        queue_email(db, user.email, "verify_email", {"name": user.display_name,
                                                     "link": f"{settings.WEB_BASE_URL}/verify-email?token={token}"},
                    dedupe_key=f"verify:{user.id}:{utcnow():%Y%m%d%H%M}")
    db.commit()


def forgot_password(db: Session, email: str, request: Request) -> None:
    enforce("forgot_ip", client_ip(request), limit=5, window_seconds=3600)
    enforce("forgot_email", normalize_email(email), limit=3, window_seconds=3600)
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user and user.status == UserStatus.active:
        token = _issue_token(db, user, "reset_password", RESET_TTL)
        queue_email(db, user.email, "password_reset", {"name": user.display_name,
                                                       "link": f"{settings.WEB_BASE_URL}/reset-password?token={token}"})
        record_auth_event(db, "password_reset_requested", request, user_id=user.id, email=user.email)
    db.commit()


def reset_password(db: Session, token: str, password: str, request: Request) -> None:
    row = consume_token(db, token, "reset_password")
    user = db.get(User, row.user_id)
    if user is None or user.status != UserStatus.active:
        raise AppError("This link is invalid or has expired.", code="invalid_token", status_code=400)
    _password_or_raise(password, user.email)
    user.password_hash = hash_password(password)
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()  # possession of the inbox is proven
    _revoke_all(db, user.id)
    queue_email(db, user.email, "password_changed", {"name": user.display_name,
                                                     "link": f"{settings.WEB_BASE_URL}/forgot-password"})
    record_auth_event(db, "password_reset", request, user_id=user.id)
    db.commit()


def _revoke_all(db: Session, user_id: uuid.UUID, except_session: uuid.UUID | None = None) -> None:
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_session:
        stmt = stmt.where(AuthSession.id != except_session)
    db.execute(stmt.values(revoked_at=utcnow()))


def change_password(db: Session, actor: Actor, current: str, new: str, request: Request) -> None:
    user = actor.user
    assert user is not None
    if not verify_password(current, user.password_hash):
        raise AppError("Your current password is incorrect.", code="reauth_failed", status_code=403)
    _password_or_raise(new, user.email, "new_password")
    user.password_hash = hash_password(new)
    _revoke_all(db, user.id, except_session=actor.session_id)
    queue_email(db, user.email, "password_changed", {"name": user.display_name,
                                                     "link": f"{settings.WEB_BASE_URL}/forgot-password"})
    record_auth_event(db, "password_changed", request, user_id=user.id)
    db.commit()


def change_email(db: Session, actor: Actor, current: str, new_email: str, request: Request) -> None:
    user = actor.user
    assert user is not None
    if not verify_password(current, user.password_hash):
        raise AppError("Your current password is incorrect.", code="reauth_failed", status_code=403)
    new_email = normalize_email(new_email)
    taken = db.scalar(select(User).where(User.email == new_email))
    if taken is not None and taken.id != user.id:
        queue_email(db, new_email, "account_exists", {"name": taken.display_name, "link": f"{settings.WEB_BASE_URL}/login"},
                    dedupe_key=f"account_exists:{taken.id}:{utcnow():%Y%m%d%H}")
    elif taken is None:
        token = _issue_token(db, user, "verify_email", VERIFY_TTL, {"new_email": new_email})
        queue_email(db, new_email, "verify_email", {"name": user.display_name,
                                                    "link": f"{settings.WEB_BASE_URL}/verify-email?token={token}"})
    record_auth_event(db, "email_change_requested", request, user_id=user.id)
    db.commit()


def list_sessions(db: Session, actor: Actor) -> list[AuthSession]:
    return list(db.scalars(select(AuthSession).where(
        AuthSession.user_id == actor.id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()
    ).order_by(AuthSession.last_seen_at.desc())))


def revoke_session(db: Session, actor: Actor, session_id: uuid.UUID) -> None:
    session = db.get(AuthSession, session_id)
    if session is None or session.user_id != actor.id:
        raise NotFound()
    session.revoked_at = utcnow()
    record_auth_event(db, "session_revoked", None, user_id=actor.id)
    db.commit()


def revoke_other_sessions(db: Session, actor: Actor) -> None:
    assert actor.id
    _revoke_all(db, actor.id, except_session=actor.session_id)
    record_auth_event(db, "sessions_revoked_others", None, user_id=actor.id)
    db.commit()


def logout(db: Session, actor: Actor) -> None:
    if actor.session_id:
        db.execute(update(AuthSession).where(AuthSession.id == actor.session_id).values(revoked_at=utcnow()))
        db.commit()


def oauth_login(db: Session, profile: OAuthProfile, request: Request) -> tuple[User, str]:
    """Sign in with an external identity. Links to an existing account only via a provider-verified email."""
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == profile.provider,
                                                     OAuthIdentity.provider_user_id == profile.provider_user_id))
    user: User | None = db.get(User, identity.user_id) if identity else None
    if user is None:
        if not profile.email or not profile.email_verified:
            raise AppError("Your provider account has no verified email address.", code="oauth_email_unverified")
        email = normalize_email(profile.email)
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=None, handle=unique_handle(db, profile.login or profile.name or email.split("@")[0]),
                        display_name=(profile.name or email.split("@")[0])[:80], avatar_url=profile.avatar_url,
                        email_verified_at=utcnow())
            db.add(user)
            db.flush()
            index_user(db, user)
        elif user.email_verified_at is None:
            user.email_verified_at = utcnow()
        db.add(OAuthIdentity(user_id=user.id, provider=profile.provider, provider_user_id=profile.provider_user_id,
                             email=profile.email))
    if user.status != UserStatus.active:
        raise Forbidden("Your account is suspended.", code="account_suspended")
    token = create_session(db, user, request)
    record_auth_event(db, f"login_oauth_{profile.provider}", request, user_id=user.id)
    db.commit()
    return user, token


def delete_account(db: Session, actor: Actor, password: str | None, request: Request) -> None:
    """Anonymizes the account. Competition results and certificates are retained as historical records
    attributed to 'Deleted user' (see docs/SECURITY.md — data retention)."""
    user = actor.user
    assert user is not None
    if user.password_hash and not verify_password(password or "", user.password_hash):
        raise AppError("Your current password is incorrect.", code="reauth_failed", status_code=403)
    anon = f"deleted-{uuid.uuid4().hex[:12]}"
    user.email = f"{anon}@deleted.invalid"
    user.handle = anon[:30]
    user.display_name = "Deleted user"
    user.password_hash = None
    user.headline = user.bio_md = user.bio_html = user.avatar_url = user.website_url = None
    user.skills, user.interests = [], []
    user.status = UserStatus.deleted
    user.deleted_at = utcnow()
    _revoke_all(db, user.id)
    db.execute(update(OAuthIdentity).where(OAuthIdentity.user_id == user.id).values(provider_user_id=anon))
    gh = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    if gh:
        db.delete(gh)
    record_audit(db, user.id, "account.delete", target_type="user", target_id=user.id)
    from app.modules.search.indexer import remove

    remove(db, "user", user.id)
    db.commit()
