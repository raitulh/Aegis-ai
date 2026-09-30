"""Audit logging for lab actions (append-only ``audit_logs``; UPDATE/DELETE blocked by trigger)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from aegis_api.lab.core.actor import Actor
from aegis_api.models import AuditLog


class AuditAction:
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    TOKEN_ISSUED = "TOKEN_ISSUED"  # noqa: S105 - audit action name, not a secret
    TOKEN_REFRESHED = "TOKEN_REFRESHED"  # noqa: S105
    TOKEN_REVOKED = "TOKEN_REVOKED"  # noqa: S105
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"  # noqa: S105
    API_KEY_ROTATED = "API_KEY_ROTATED"
    SERVICE_ACCOUNT_CREATED = "SERVICE_ACCOUNT_CREATED"
    SERVICE_ACCOUNT_DISABLED = "SERVICE_ACCOUNT_DISABLED"
    WORKSPACE_CREATED = "WORKSPACE_CREATED"
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_MEMBER_CHANGED = "PROJECT_MEMBER_CHANGED"
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_UPDATED = "MISSION_UPDATED"
    MISSION_PLAN_APPROVED = "MISSION_PLAN_APPROVED"
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_PAUSED = "MISSION_PAUSED"
    MISSION_CANCELLED = "MISSION_CANCELLED"
    AUTONOMY_CHANGED = "AUTONOMY_CHANGED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_CONFIGURED = "AGENT_CONFIGURED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_DENIED = "TOOL_DENIED"
    MCP_CALLED = "MCP_CALLED"
    MCP_SERVER_REGISTERED = "MCP_SERVER_REGISTERED"
    MCP_SERVER_APPROVED = "MCP_SERVER_APPROVED"
    FILE_UPLOADED = "FILE_UPLOADED"
    ARTIFACT_DOWNLOADED = "ARTIFACT_DOWNLOADED"
    ARTIFACT_DELETED = "ARTIFACT_DELETED"
    DATASET_CREATED = "DATASET_CREATED"
    DATASET_DOWNLOADED = "DATASET_DOWNLOADED"
    MALWARE_DETECTED = "MALWARE_DETECTED"
    DATASET_VERSION_CREATED = "DATASET_VERSION_CREATED"
    EXPERIMENT_CREATED = "EXPERIMENT_CREATED"
    EXPERIMENT_EXECUTED = "EXPERIMENT_EXECUTED"
    EXPERIMENT_CANCELLED = "EXPERIMENT_CANCELLED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    MEMORY_REVIEWED = "MEMORY_REVIEWED"
    STRATEGY_CREATED = "STRATEGY_CREATED"
    STRATEGY_PROMOTED = "STRATEGY_PROMOTED"
    STRATEGY_ROLLED_BACK = "STRATEGY_ROLLED_BACK"
    EVOLUTION_STARTED = "EVOLUTION_STARTED"
    POLICY_CHANGED = "POLICY_CHANGED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_CANCELLED = "APPROVAL_CANCELLED"
    POLICY_DENIED = "POLICY_DENIED"
    SUBSCRIPTION_CHANGED = "SUBSCRIPTION_CHANGED"
    INVOICE_CREATED = "INVOICE_CREATED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    DISCOVERY_CREATED = "DISCOVERY_CREATED"
    DISCOVERY_APPROVED = "DISCOVERY_APPROVED"
    DISCOVERY_PUBLISHED = "DISCOVERY_PUBLISHED"
    DISCOVERY_REJECTED = "DISCOVERY_REJECTED"
    MODEL_CONFIG_CHANGED = "MODEL_CONFIG_CHANGED"
    FEATURE_FLAG_CHANGED = "FEATURE_FLAG_CHANGED"
    QUOTA_CHANGED = "QUOTA_CHANGED"
    WEBHOOK_CHANGED = "WEBHOOK_CHANGED"
    ADMIN_ACTION = "ADMIN_ACTION"


def audit(
    db: Session,
    actor: Actor,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    organization_id: uuid.UUID | None = None,
) -> AuditLog:
    """Record a sensitive action. Never include secrets, tokens or full confidential documents."""
    entry = AuditLog(
        organization_id=organization_id or actor.organization_id,
        user_id=actor.user_id,
        actor_type=actor.kind if actor.kind != "workflow" else "system",
        actor_label=actor.label[:320],
        action=action,
        resource_type=resource_type[:48],
        resource_id=str(resource_id)[:64] if resource_id is not None else None,
        request_id=actor.request_id,
        before=_clean(before),
        after=_clean(after),
    )
    db.add(entry)
    return entry


def _clean(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    from aegis_api.lab.core.events import _jsonable

    return _jsonable(value)
