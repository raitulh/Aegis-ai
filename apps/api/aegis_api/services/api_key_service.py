"""API key issuance, rotation and verification (hashed at rest, plaintext shown once)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import ApiKey
from aegis_api.models.enums import Role
from aegis_api.security.rbac import ALL_SCOPES
from aegis_api.security.tokens import keyed_hash, new_api_key


@dataclass
class IssuedKey:
    api_key: ApiKey
    plaintext: str


def create_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    name: str,
    role: str = Role.ANALYST,
    scopes: list[str] | None = None,
    created_by_id: uuid.UUID | None = None,
    expires_in_days: int | None = None,
    test: bool = False,
) -> IssuedKey:
    scopes = [s for s in (scopes or ["read"]) if s in ALL_SCOPES] or ["read"]
    plaintext, prefix = new_api_key(test=test)
    record = ApiKey(
        organization_id=organization_id,
        name=name,
        prefix=prefix,
        key_hash=keyed_hash(plaintext),
        scopes=scopes,
        role=role,
        created_by_id=created_by_id,
        expires_at=(utcnow() + timedelta(days=expires_in_days)) if expires_in_days else None,
    )
    session.add(record)
    session.flush()
    return IssuedKey(api_key=record, plaintext=plaintext)


def get_api_key(session: Session, key_id: uuid.UUID, organization_id: uuid.UUID) -> ApiKey | None:
    """Retrieve an API key by ID within the tenant's organization."""
    record = session.get(ApiKey, key_id)
    if record is None or record.organization_id != organization_id:
        return None
    return record


def list_api_keys(session: Session, organization_id: uuid.UUID, include_revoked: bool = True) -> list[ApiKey]:
    """List all API keys for an organization."""
    stmt = select(ApiKey).where(ApiKey.organization_id == organization_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    return list(session.scalars(stmt.order_by(ApiKey.created_at.desc())).all())


def update_api_key(
    session: Session,
    key_id: uuid.UUID,
    organization_id: uuid.UUID,
    *,
    name: str | None = None,
    scopes: list[str] | None = None,
) -> ApiKey | None:
    """Update metadata or scopes of an existing active API key."""
    record = get_api_key(session, key_id, organization_id)
    if record is None or record.revoked_at is not None:
        return None
    if name is not None:
        record.name = name.strip()
    if scopes is not None:
        valid_scopes = [s for s in scopes if s in ALL_SCOPES]
        record.scopes = valid_scopes or ["read"]
    session.flush()
    return record


def rotate_api_key(
    session: Session,
    key_id: uuid.UUID,
    organization_id: uuid.UUID,
    created_by_id: uuid.UUID | None = None,
) -> IssuedKey | None:
    """Rotate an existing API key: revokes the old key and issues a replacement with the same permissions."""
    old_key = get_api_key(session, key_id, organization_id)
    if old_key is None or old_key.revoked_at is not None:
        return None

    # Revoke old key
    old_key.revoked_at = utcnow()

    # Calculate remaining validity if the old key had an expiration
    remaining_days = None
    if old_key.expires_at:
        remaining_delta = old_key.expires_at - utcnow()
        if remaining_delta.total_seconds() > 0:
            remaining_days = max(1, remaining_delta.days)

    is_test = old_key.prefix.startswith("test_")
    new_issued = create_api_key(
        session,
        organization_id=organization_id,
        name=f"{old_key.name} (rotated)",
        role=old_key.role,
        scopes=list(old_key.scopes),
        created_by_id=created_by_id or old_key.created_by_id,
        expires_in_days=remaining_days,
        test=is_test,
    )
    return new_issued


def verify_api_key(session: Session, plaintext: str) -> ApiKey | None:
    if not plaintext.startswith(("aeg_live_", "aeg_test_")):
        return None
    record = session.scalar(select(ApiKey).where(ApiKey.key_hash == keyed_hash(plaintext)))
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at is not None and record.expires_at < utcnow():
        return None
    record.last_used_at = utcnow()
    return record


def revoke_api_key(session: Session, key_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
    record = session.get(ApiKey, key_id)
    if record is None or record.organization_id != organization_id or record.revoked_at is not None:
        return False
    record.revoked_at = utcnow()
    return True


def touch_last_used(session: Session, key_id: uuid.UUID, when: datetime | None = None) -> None:
    record = session.get(ApiKey, key_id)
    if record:
        record.last_used_at = when or utcnow()
