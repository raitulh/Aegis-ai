"""Token authentication for API/SDK clients: JWT access tokens + rotating refresh tokens.

Rotation rules:
* each refresh returns a *new* refresh token (same family) and marks the presented one used;
* presenting a used or revoked refresh token is treated as theft (replay): the whole family is revoked and the
  event is audited, which also invalidates every outstanding access token of that family;
* logout / revoke revokes the family; password reset revokes every family of the user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Unauthorized
from aegis_api.models import Membership, RefreshToken, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.security.jwt_tokens import issue_access_token
from aegis_api.security.tokens import keyed_hash, random_token
from aegis_api.services import audit_log

REFRESH_PREFIX = "aegr_"


@dataclass
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    family_id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID


def _new_refresh(
    session: Session,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    family_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    user_agent: str | None,
) -> tuple[str, RefreshToken]:
    plaintext = REFRESH_PREFIX + random_token(40)
    record = RefreshToken(
        user_id=user_id,
        organization_id=organization_id,
        family_id=family_id,
        token_hash=keyed_hash(plaintext),
        parent_id=parent_id,
        expires_at=utcnow() + timedelta(days=get_settings().refresh_token_ttl_days),
        user_agent=(user_agent or "")[:256] or None,
    )
    session.add(record)
    session.flush()
    return plaintext, record


def issue_pair(session: Session, *, user: User, organization_id: uuid.UUID, user_agent: str | None = None) -> TokenPair:
    family_id = uuid.uuid4()
    refresh, record = _new_refresh(
        session,
        user_id=user.id,
        organization_id=organization_id,
        family_id=family_id,
        parent_id=None,
        user_agent=user_agent,
    )
    access, access_exp = issue_access_token(user.id, organization_id, family_id)
    return TokenPair(access, access_exp, refresh, record.expires_at, family_id, organization_id, user.id)


def _revoke_family(session: Session, family_id: uuid.UUID, reason: str) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason=reason)
    )


def refresh(session: Session, presented: str, *, user_agent: str | None = None) -> TokenPair:
    if not presented.startswith(REFRESH_PREFIX):
        raise Unauthorized("Invalid refresh token", code="invalid_token")
    record = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == keyed_hash(presented)).with_for_update()
    )
    if record is None:
        raise Unauthorized("Invalid refresh token", code="invalid_token")
    if record.used_at is not None or record.revoked_at is not None:
        # Replay of a rotated or revoked token → assume theft; kill the whole family.
        _revoke_family(session, record.family_id, "reuse_detected")
        audit_log.record(
            session,
            organization_id=record.organization_id,
            action="token.reuse_detected",
            resource_type="refresh_token_family",
            resource_id=record.family_id,
            actor_label=f"user:{record.user_id}",
        )
        session.commit()
        raise Unauthorized("Refresh token reuse detected; all sessions in this family were revoked", code="token_reuse")
    if record.expires_at < utcnow():
        raise Unauthorized("Refresh token expired", code="token_expired")
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == record.user_id,
            Membership.organization_id == record.organization_id,
            Membership.status != MembershipStatus.SUSPENDED,
        )
    )
    if membership is None:
        _revoke_family(session, record.family_id, "membership_revoked")
        session.commit()
        raise Unauthorized("Workspace access has been revoked", code="access_revoked")
    record.used_at = utcnow()
    plaintext, new_record = _new_refresh(
        session,
        user_id=record.user_id,
        organization_id=record.organization_id,
        family_id=record.family_id,
        parent_id=record.id,
        user_agent=user_agent,
    )
    access, access_exp = issue_access_token(record.user_id, record.organization_id, record.family_id)
    return TokenPair(
        access, access_exp, plaintext, new_record.expires_at, record.family_id, record.organization_id, record.user_id
    )


def revoke(session: Session, presented: str, *, reason: str = "logout") -> bool:
    record = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == keyed_hash(presented)))
    if record is None:
        return False
    _revoke_family(session, record.family_id, reason)
    audit_log.record(
        session,
        organization_id=record.organization_id,
        action="token.revoked",
        resource_type="refresh_token_family",
        resource_id=record.family_id,
        actor_label=f"user:{record.user_id}",
    )
    return True


def revoke_all_for_user(session: Session, user_id: uuid.UUID, reason: str) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason=reason)
    )


def family_active(session: Session, family_id: uuid.UUID) -> bool:
    """An access token is honoured only while its family has at least one un-revoked token."""
    return (
        session.scalar(
            select(RefreshToken.id)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .limit(1)
        )
        is not None
    )
