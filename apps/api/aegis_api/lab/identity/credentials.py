"""Password reset and email verification (single-use, hashed, short-lived tokens in ``auth_tokens``).

* Requesting a reset always succeeds from the caller's point of view (HTTP 202) whether or not the
  address exists — no user enumeration. Only local (password) accounts can be reset.
* Tokens are 256-bit random values stored as a keyed hash; a new request supersedes earlier tokens; a
  token is consumed atomically (``SELECT … FOR UPDATE``) and can never be used twice.
* Completing a reset revokes every server-side session and every refresh-token family of the user.
* Emails are sent after the transaction commits, in the background (``identity.email``).
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import RateLimited, ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.events import after_commit
from aegis_api.lab.identity import email as email_delivery
from aegis_api.lab.identity.tokens import revoke_all_for_user
from aegis_api.models import AuthSession, AuthToken, User
from aegis_api.ratelimit import get_limiter
from aegis_api.security.passwords import hash_password, password_problems
from aegis_api.security.tokens import keyed_hash, random_token
from aegis_api.services import auth_service

PASSWORD_RESET = "password_reset"  # noqa: S105 - token purpose label
EMAIL_VERIFY = "email_verify"
PASSWORD_RESET_TTL = timedelta(minutes=30)
EMAIL_VERIFY_TTL = timedelta(hours=24)
MAX_TOKEN_LENGTH = 512
RESET_THROTTLE_WINDOW_SECONDS = 3600


def _invalid_token() -> ValidationFailed:
    return ValidationFailed("This link is invalid or has expired", code="invalid_or_expired_token")


def _issue_single_use_token(db: Session, *, user: User, purpose: str, ttl: timedelta) -> str:
    now = utcnow()
    # A new request supersedes every outstanding token for the same purpose.
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = random_token(32)
    db.add(
        AuthToken(
            user_id=user.id, email=user.email, purpose=purpose, token_hash=keyed_hash(token), expires_at=now + ttl
        )
    )
    db.flush()
    return token


def _issue_password_reset_token(db: Session, user: User) -> str:
    """Create a password-reset token for ``user`` and return its plaintext (sent by email only)."""
    return _issue_single_use_token(db, user=user, purpose=PASSWORD_RESET, ttl=PASSWORD_RESET_TTL)


def _issue_email_verification_token(db: Session, user: User) -> str:
    return _issue_single_use_token(db, user=user, purpose=EMAIL_VERIFY, ttl=EMAIL_VERIFY_TTL)


def _consume(db: Session, token: str, purpose: str) -> tuple[AuthToken, User]:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise _invalid_token()
    record = db.scalar(
        select(AuthToken)
        .where(AuthToken.token_hash == keyed_hash(token), AuthToken.purpose == purpose)
        .with_for_update()
    )
    if record is None or record.used_at is not None or record.expires_at <= utcnow() or record.user_id is None:
        raise _invalid_token()
    user = db.get(User, record.user_id)
    # A token is bound to the address it was sent to: an email change invalidates it.
    if user is None or user.email.lower() != record.email.lower():
        raise _invalid_token()
    record.used_at = utcnow()
    return record, user


def _link(path: str, token: str) -> str:
    return f"{get_settings().web_base_url.rstrip('/')}{path}?token={quote(token, safe='')}"


def _user_actor(db: Session, user: User, request_id: str | None) -> Actor | None:
    membership = auth_service.membership_for(db, user, None)
    if membership is None:
        return None
    return Actor(
        kind="user",
        organization_id=membership.organization_id,
        permissions=frozenset(),
        label=user.email,
        user_id=user.id,
        role=membership.role,
        request_id=request_id,
        auth_method="session",
    )


# --- password reset ------------------------------------------------------------------------------
def _throttle_address(normalized_email: str) -> None:
    """Per-address limit (anti email-bombing), applied to known and unknown addresses alike (no oracle)."""
    try:
        get_limiter().check_custom(
            "password-reset",
            keyed_hash(normalized_email)[:32],
            get_settings().rate_limit_expensive_per_min,
            window=RESET_THROTTLE_WINDOW_SECONDS,
        )
    except RateLimited as exc:
        raise RateLimited(
            "Too many password reset requests for this address; try again later", retry_after=exc.retry_after
        ) from exc


def request_password_reset(db: Session, *, email: str) -> None:
    """Issue a reset token and email it (after commit). Silently does nothing for unknown addresses."""
    if not get_settings().local_auth_enabled:
        return
    normalized = email.strip().lower()
    _throttle_address(normalized)
    user = db.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None or user.auth_provider != "local" or user.is_guest:
        return
    token = _issue_password_reset_token(db, user)
    message = email_delivery.OutgoingEmail(
        to=user.email,
        subject="Reset your Aegis password",
        text=(
            "We received a request to reset the password for your Aegis account.\n\n"
            f"Reset it here (valid for {int(PASSWORD_RESET_TTL.total_seconds() // 60)} minutes, single use):\n"
            f"{_link('/reset-password', token)}\n\n"
            "If you did not request this, you can ignore this email; your password is unchanged."
        ),
    )
    after_commit(db, lambda: email_delivery.deliver(message, purpose=PASSWORD_RESET))


def confirm_password_reset(db: Session, *, token: str, new_password: str, request_id: str | None = None) -> User:
    """Set a new password with a valid reset token and revoke every session and refresh token."""
    problems = password_problems(new_password)
    if problems:
        raise ValidationFailed("Password does not meet requirements: " + "; ".join(problems))
    _, user = _consume(db, token, PASSWORD_RESET)
    if user.auth_provider != "local":
        raise _invalid_token()
    now = utcnow()
    user.password_hash = hash_password(new_password)
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == PASSWORD_RESET, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    sessions = db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    families = revoke_all_for_user(db, user.id, "password_reset")
    actor = _user_actor(db, user, request_id)
    if actor is not None:
        audit(
            db,
            actor,
            "PASSWORD_RESET",
            "user",
            user.id,
            after={"sessions_revoked": int(getattr(sessions, "rowcount", 0) or 0), "refresh_tokens_revoked": families},
        )
    db.flush()
    return user


# --- email verification ----------------------------------------------------------------------------
def request_email_verification(db: Session, *, user: User) -> bool:
    """Email a verification link to the user. Returns False when the address is already verified."""
    if user.email_verified or user.is_guest:
        return False
    token = _issue_email_verification_token(db, user)
    message = email_delivery.OutgoingEmail(
        to=user.email,
        subject="Verify your email address",
        text=(
            "Confirm the email address of your Aegis account:\n\n"
            f"{_link('/verify-email', token)}\n\n"
            f"The link is valid for {int(EMAIL_VERIFY_TTL.total_seconds() // 3600)} hours and can be used once."
        ),
    )
    after_commit(db, lambda: email_delivery.deliver(message, purpose=EMAIL_VERIFY))
    return True


def confirm_email_verification(db: Session, *, token: str, request_id: str | None = None) -> User:
    _, user = _consume(db, token, EMAIL_VERIFY)
    user.email_verified = True
    actor = _user_actor(db, user, request_id)
    if actor is not None:
        audit(db, actor, "EMAIL_VERIFIED", "user", user.id, after={"email_verified": True})
    db.flush()
    return user
