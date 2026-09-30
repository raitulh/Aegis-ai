"""API key issuance and verification (hashed at rest, plaintext shown once)."""

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
    service_account_id: uuid.UUID | None = None,
    rotated_from_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
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
        service_account_id=service_account_id,
        rotated_from_id=rotated_from_id,
        expires_at=expires_at or ((utcnow() + timedelta(days=expires_in_days)) if expires_in_days else None),
    )
    session.add(record)
    session.flush()
    return IssuedKey(api_key=record, plaintext=plaintext)


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


def rotate_api_key(
    session: Session, key_id: uuid.UUID, organization_id: uuid.UUID, *, rotated_by_id: uuid.UUID | None
) -> IssuedKey:
    """Issue a replacement key with identical role/scopes/owner and revoke the old one atomically."""
    record = session.get(ApiKey, key_id)
    if record is None or record.organization_id != organization_id or record.revoked_at is not None:
        from aegis_api.errors import NotFound

        raise NotFound("API key not found")
    remaining = None
    if record.expires_at is not None:
        remaining = record.expires_at - record.created_at
    issued = create_api_key(
        session,
        organization_id=organization_id,
        name=record.name,
        role=record.role,
        scopes=list(record.scopes),
        created_by_id=rotated_by_id or record.created_by_id,
        service_account_id=record.service_account_id,
        rotated_from_id=record.id,
        test=record.prefix.startswith("aeg_test_"),
        expires_at=(utcnow() + remaining) if remaining else None,
    )
    record.revoked_at = utcnow()
    return issued
