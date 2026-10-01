"""Audit trail helper. Audit rows are written in the same transaction as the change they describe."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact, request_id_var
from app.models.system import AuditLog

ip_hash_var: ContextVar[str | None] = ContextVar("ip_hash", default=None)


def record_audit(
    db: Session,
    actor_id: uuid.UUID | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object | None = None,
    reason: str | None = None,
    org_id: uuid.UUID | None = None,
    competition_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        reason=reason,
        org_id=org_id,
        competition_id=competition_id,
        meta=redact(meta or {}),  # type: ignore[arg-type]
        request_id=request_id_var.get(),
        ip_hash=ip_hash_var.get(),
    )
    db.add(entry)
    return entry
