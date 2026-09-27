"""Encrypted secret storage and provider credential resolution."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound
from aegis_api.models import Secret
from aegis_api.security.crypto import decrypt_str, encrypt_str, last4


def create_secret(
    session: Session,
    *,
    organization_id: uuid.UUID,
    name: str,
    value: str,
    kind: str = "api_key",
    created_by_id: uuid.UUID | None = None,
) -> Secret:
    existing = session.scalar(select(Secret).where(Secret.organization_id == organization_id, Secret.name == name))
    if existing:
        existing.ciphertext = encrypt_str(value)
        existing.last4 = last4(value)
        existing.kind = kind
        return existing
    secret = Secret(
        organization_id=organization_id,
        name=name,
        kind=kind,
        ciphertext=encrypt_str(value),
        last4=last4(value),
        created_by_id=created_by_id,
    )
    session.add(secret)
    session.flush()
    return secret


def reveal_secret(session: Session, secret_id: uuid.UUID, organization_id: uuid.UUID) -> str:
    secret = session.get(Secret, secret_id)
    if secret is None or secret.organization_id != organization_id:
        raise NotFound("Secret not found")
    return decrypt_str(secret.ciphertext)


def resolve_optional(session: Session, secret_id: uuid.UUID | None, organization_id: uuid.UUID) -> str | None:
    if secret_id is None:
        return None
    try:
        return reveal_secret(session, secret_id, organization_id)
    except (NotFound, ValueError):
        return None
