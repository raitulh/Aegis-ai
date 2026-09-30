"""Token-based authentication: password grant → (JWT access token, rotating refresh token).

Refresh tokens are opaque (``aegr_`` + 48 random bytes), stored only as a keyed hash, and organised in
*families* (one per sign-in). Every refresh rotates the token; presenting a token that was already
rotated or revoked is treated as theft and revokes the whole family (OAuth 2.0 Security BCP §4.14).
A family expires ``JWT_REFRESH_TTL_DAYS`` after sign-in (rotation does not extend it). Access tokens
carry the family id (``sid``), so revoking a family also invalidates its outstanding access tokens.

These functions run on the identity-layer owner session (``refresh_tokens`` has no RLS, and no tenant
context exists before authentication) and never commit — the router owns the transaction.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Forbidden, Unauthorized
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.models import OrganizationSettings, RefreshToken
from aegis_api.models import Membership, Organization, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.security.jwt_tokens import IssuedAccessToken, issue_access_token
from aegis_api.security.passwords import verify_password
from aegis_api.security.rbac import permissions_for_role
from aegis_api.security.tokens import keyed_hash
from aegis_api.services import auth_service

REFRESH_PREFIX = "aegr_"
MAX_REFRESH_TOKEN_LENGTH = 256
EXPIRED_RETENTION = timedelta(days=7)  # keep briefly for forensics, then purge


class RefreshTokenReuse(Unauthorized):
    """A rotated or revoked refresh token was presented; the whole sign-in has been revoked."""

    code = "refresh_token_reused"


@dataclass
class TokenPair:
    access: IssuedAccessToken
    refresh_token: str
    refresh_expires_at: datetime
    user: User
    membership: Membership
    family_id: uuid.UUID


@dataclass(frozen=True)
class ClientInfo:
    user_agent: str | None = None
    ip_address: str | None = None
    request_id: str | None = None


def _trim(value: str | None, length: int) -> str | None:
    return value[:length] if value else None


def new_refresh_token() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(48)


def _user_actor(user: User, membership: Membership, request_id: str | None) -> Actor:
    return Actor(
        kind="user",
        organization_id=membership.organization_id,
        permissions=permissions_for_role(membership.role),
        label=user.email,
        user_id=user.id,
        role=membership.role,
        request_id=request_id,
        auth_method="jwt",
        is_platform_admin=user.is_platform_admin,
    )


def _invalid_refresh() -> Unauthorized:
    return Unauthorized("Invalid refresh token", code="invalid_refresh_token")


def active_membership(db: Session, user: User, organization_id: uuid.UUID) -> Membership:
    """The user's ACTIVE membership in an organization that is not suspended (platform admins excepted)."""
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == organization_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise Forbidden("You do not have access to this organization")
    org = db.get(Organization, organization_id)
    if org is None:
        raise Forbidden("You do not have access to this organization")
    if (org.settings or {}).get("suspended") and not user.is_platform_admin:
        raise Forbidden("This organization has been suspended", code="organization_suspended")
    return membership


def _sso_enforced(db: Session, organization_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(OrganizationSettings.sso_enforced).where(OrganizationSettings.organization_id == organization_id)
        )
    )


def issue_token_pair(
    db: Session,
    *,
    user: User,
    membership: Membership,
    client: ClientInfo,
    family_id: uuid.UUID | None = None,
    family_expires_at: datetime | None = None,
) -> tuple[TokenPair, RefreshToken]:
    settings = get_settings()
    family = family_id or uuid.uuid4()
    expires = family_expires_at or (utcnow() + timedelta(days=settings.jwt_refresh_ttl_days))
    plaintext = new_refresh_token()
    record = RefreshToken(
        user_id=user.id,
        organization_id=membership.organization_id,
        family_id=family,
        token_hash=keyed_hash(plaintext),
        expires_at=expires,
        user_agent=_trim(client.user_agent, 256),
        ip_address=_trim(client.ip_address, 64),
    )
    db.add(record)
    db.flush()
    access = issue_access_token(
        user_id=user.id, organization_id=membership.organization_id, role=membership.role, session_id=family
    )
    pair = TokenPair(
        access=access,
        refresh_token=plaintext,
        refresh_expires_at=expires,
        user=user,
        membership=membership,
        family_id=family,
    )
    return pair, record


def password_grant(
    db: Session, *, email: str, password: str, organization_id: uuid.UUID | None, client: ClientInfo
) -> TokenPair:
    """Authenticate with email + password (same rules as ``/auth/login``) and issue a token pair."""
    if not get_settings().local_auth_enabled:
        raise Unauthorized("Password authentication is disabled on this deployment", code="local_auth_disabled")
    normalized = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == normalized))
    # verify_password runs a dummy hash when the user has no password, keeping timing uniform.
    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or user.auth_provider != "local" or not password_ok:
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if organization_id is None:
        primary = auth_service.membership_for(db, user, None)
        if primary is None:
            raise Unauthorized("This account has no active workspace")
        organization_id = primary.organization_id
    membership = active_membership(db, user, organization_id)
    if _sso_enforced(db, organization_id) and not user.is_platform_admin:
        raise Forbidden("This organization requires single sign-on", code="sso_required")
    user.last_active_at = utcnow()
    _purge_expired(db, user.id)
    pair, record = issue_token_pair(db, user=user, membership=membership, client=client)
    actor = _user_actor(user, membership, client.request_id)
    audit(db, actor, AuditAction.LOGIN, "user", user.id, after={"method": "password", "grant": "token"})
    audit(
        db,
        actor,
        AuditAction.TOKEN_ISSUED,
        "refresh_token",
        record.id,
        after={"family_id": record.family_id, "access_jti": pair.access.jti, "expires_at": record.expires_at},
    )
    return pair


def _purge_expired(db: Session, user_id: uuid.UUID) -> None:
    """Drop the user's refresh tokens that expired long ago (their families can no longer be used)."""
    db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.expires_at < utcnow() - EXPIRED_RETENTION
        )
    )


def _revoke_family(db: Session, family_id: uuid.UUID, reason: str) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def revoke_all_for_user(db: Session, user_id: uuid.UUID, reason: str) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def family_is_active(db: Session, family_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(RefreshToken.id)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _lookup(db: Session, token: str) -> RefreshToken | None:
    if not token.startswith(REFRESH_PREFIX) or len(token) > MAX_REFRESH_TOKEN_LENGTH:
        return None
    return db.scalar(select(RefreshToken).where(RefreshToken.token_hash == keyed_hash(token)).with_for_update())


def refresh(db: Session, *, refresh_token: str, client: ClientInfo) -> TokenPair:
    """Rotate a refresh token. Reuse of a rotated/revoked token revokes the family and raises
    :class:`RefreshTokenReuse` — the caller must COMMIT before re-raising so the revocation sticks."""
    record = _lookup(db, refresh_token)
    if record is None:
        raise _invalid_refresh()
    now = utcnow()
    if record.rotated_at is not None or record.revoked_at is not None:
        revoked = _revoke_family(db, record.family_id, "reuse_detected")
        audit(
            db,
            Actor.system(record.organization_id, label="identity:refresh-token-guard"),
            AuditAction.TOKEN_REUSE_DETECTED,
            "refresh_token",
            record.id,
            after={
                "family_id": record.family_id,
                "user_id": record.user_id,
                "tokens_revoked": revoked,
                "ip_address": client.ip_address,
            },
        )
        raise RefreshTokenReuse("Refresh token reuse detected; this sign-in has been revoked")
    if record.expires_at <= now:
        raise Unauthorized("Refresh token expired", code="refresh_token_expired")
    user = db.get(User, record.user_id)
    if user is None:
        raise _invalid_refresh()
    membership = active_membership(db, user, record.organization_id)
    pair, new_record = issue_token_pair(
        db,
        user=user,
        membership=membership,
        client=client,
        family_id=record.family_id,
        family_expires_at=record.expires_at,
    )
    record.rotated_at = now
    record.replaced_by_id = new_record.id
    user.last_active_at = now
    audit(
        db,
        _user_actor(user, membership, client.request_id),
        AuditAction.TOKEN_REFRESHED,
        "refresh_token",
        new_record.id,
        after={"family_id": record.family_id, "rotated_from": record.id, "access_jti": pair.access.jti},
    )
    db.flush()
    return pair


def revoke(db: Session, *, refresh_token: str, client: ClientInfo) -> None:
    """Revoke the sign-in (whole family) a refresh token belongs to. Unknown tokens are ignored (no oracle)."""
    record = _lookup(db, refresh_token)
    if record is None:
        return
    revoked = _revoke_family(db, record.family_id, "revoked")
    if not revoked:
        return
    user = db.get(User, record.user_id)
    actor = (
        Actor(
            kind="user",
            organization_id=record.organization_id,
            permissions=frozenset(),
            label=user.email,
            user_id=user.id,
            request_id=client.request_id,
            auth_method="jwt",
        )
        if user is not None
        else Actor.system(record.organization_id, label="identity:token-revoke")
    )
    audit(
        db,
        actor,
        AuditAction.TOKEN_REVOKED,
        "refresh_token",
        record.id,
        after={"family_id": record.family_id, "tokens_revoked": revoked},
    )
