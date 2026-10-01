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

LAST_USED_RESOLUTION_SECONDS = 60


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


def verify_api_key(session: Session, plaintext: str) -> ApiKey | None:
    if not plaintext.startswith(("aeg_live_", "aeg_test_")):
        return None
    record = session.scalar(select(ApiKey).where(ApiKey.key_hash == keyed_hash(plaintext)))
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at is not None and record.expires_at < utcnow():
        return None
    # Throttled bookkeeping: a hot key (e.g. runtime ingestion) must not serialise every request on one row.
    now = utcnow()
    if record.last_used_at is None or (now - record.last_used_at).total_seconds() > LAST_USED_RESOLUTION_SECONDS:
        record.last_used_at = now
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
