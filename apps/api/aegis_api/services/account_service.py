"""Password reset and email verification (single-use, hashed, expiring tokens).

Responses never reveal whether an email exists (no account enumeration). Completing a password reset revokes
every session and refresh-token family of the user.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.models import AuthSession, AuthToken, User
from aegis_api.security.passwords import hash_password, password_problems
from aegis_api.security.tokens import keyed_hash, random_token
from aegis_api.services import email_service, token_service

RESET = "password_reset"
VERIFY = "email_verification"


def _issue(session: Session, user: User, purpose: str, ttl: timedelta) -> str:
    # Invalidate previous unused tokens of the same purpose.
    session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = random_token(32)
    session.add(
        AuthToken(
            user_id=user.id, email=user.email, purpose=purpose, token_hash=keyed_hash(token), expires_at=utcnow() + ttl
        )
    )
    return token


def _consume(session: Session, token: str, purpose: str) -> User:
    record = session.scalar(
        select(AuthToken)
        .where(AuthToken.token_hash == keyed_hash(token), AuthToken.purpose == purpose)
        .with_for_update()
    )
    if record is None or record.used_at is not None or record.expires_at < utcnow() or record.user_id is None:
        raise ValidationFailed("This link is invalid or has expired", code="invalid_token")
    record.used_at = utcnow()
    user = session.get(User, record.user_id)
    if user is None:
        raise ValidationFailed("This link is invalid or has expired", code="invalid_token")
    return user


def request_password_reset(session: Session, email: str) -> None:
    settings = get_settings()
    user = session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None or user.auth_provider != "local" or user.is_guest:
        return
    token = _issue(session, user, RESET, timedelta(minutes=settings.password_reset_ttl_minutes))
    email_service.send(
        email_service.Email(
            to=user.email,
            subject="Reset your Aegis password",
            text=(
                f"A password reset was requested for your account.\n\n"
                f"Reset link (valid {settings.password_reset_ttl_minutes} minutes):\n"
                f"{settings.web_base_url}/reset-password?token={token}\n\n"
                "If you did not request this, you can ignore this email."
            ),
        )
    )


def reset_password(session: Session, token: str, new_password: str) -> User:
    problems = password_problems(new_password)
    if problems:
        raise ValidationFailed("Password does not meet requirements: " + "; ".join(problems))
    user = _consume(session, token, RESET)
    user.password_hash = hash_password(new_password)
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    token_service.revoke_all_for_user(session, user.id, "password_reset")
    return user


def request_email_verification(session: Session, user: User) -> None:
    if user.email_verified or user.is_guest:
        return
    settings = get_settings()
    token = _issue(session, user, VERIFY, timedelta(hours=settings.email_verification_ttl_hours))
    email_service.send(
        email_service.Email(
            to=user.email,
            subject="Verify your email address",
            text=f"Confirm your email address:\n{settings.web_base_url}/verify-email?token={token}\n",
        )
    )


def verify_email(session: Session, token: str) -> User:
    user = _consume(session, token, VERIFY)
    user.email_verified = True
    return user
