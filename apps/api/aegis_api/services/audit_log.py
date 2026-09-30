"""Append-only audit logging helper."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from aegis_api.models import AuditLog
from aegis_api.security.context import Principal


def record(
    session: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    principal: Principal | None = None,
    request_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor_label: str | None = None,
    actor_type: str = "system",
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        user_id=principal.fk_user_id if principal else None,
        actor_type=principal.actor_type if principal else actor_type,
        actor_label=principal.actor_label if principal else actor_label,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        request_id=request_id or (principal.request_id if principal else None),
        before=before,
        after=after,
    )
    session.add(entry)
    return entry
