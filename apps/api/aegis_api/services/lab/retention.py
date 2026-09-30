"""Data retention (per organization, configured in ``organization_quotas.retention`` as days per class).

Classes and behaviour:
* ``artifacts`` — standard-class artifact content older than N days is purged from object storage (row kept,
  ``purged_at`` set). Evidence-class artifacts (code bundles, run outputs referenced by evidence, reports) are
  never purged by retention.
* ``raw_outputs`` — ephemeral/log artifacts older than N days are purged (defaults to 30 days for ephemeral).
* ``agent_logs`` — inter-agent message envelopes older than N days are deleted.
* ``research_events`` — provider progress events older than N days are deleted (append-only table; the purge
  runs with the explicit retention override that the immutability trigger honours).
* ``llm_metadata`` / ``audit_logs`` — usage ledgers and the audit log are retained (billing/audit integrity);
  they are only removed with the organization.
Expired idempotency keys and short-term memories are always cleaned up; expired refresh tokens (identity layer,
not tenant-scoped) are removed by ``purge_expired_refresh_tokens``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import IdempotencyKey, RefreshToken
from aegis_api.models.lab import AgentMessageRecord, Artifact, ArtifactVersion
from aegis_api.services import quota_service
from aegis_api.services.lab import artifacts as artifact_service
from aegis_api.services.lab import memory as memory_service

DEFAULT_DAYS = {"artifacts": 365, "raw_outputs": 90, "agent_logs": 180, "research_events": 365}
EPHEMERAL_DAYS = 30
BATCH = 200


def policy(db: Session, organization_id: uuid.UUID) -> dict[str, int]:
    configured = quota_service.get_quota(db, organization_id).retention or {}
    out = dict(DEFAULT_DAYS)
    for key, value in configured.items():
        if key in out and value is not None:
            out[key] = max(1, int(value))
    return out


def apply(db: Session, organization_id: uuid.UUID) -> dict[str, Any]:
    days = policy(db, organization_id)
    now = utcnow()
    purged = 0
    candidates = db.execute(
        select(ArtifactVersion, Artifact)
        .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
        .where(
            ArtifactVersion.organization_id == organization_id,
            ArtifactVersion.purged_at.is_(None),
            Artifact.retention_class != "evidence",
        )
        .limit(BATCH * 5)
    ).all()
    for version, artifact in candidates:
        if artifact.retention_class == "ephemeral" or artifact.kind in ("logs",):
            limit = (
                min(days["raw_outputs"], EPHEMERAL_DAYS)
                if artifact.retention_class == "ephemeral"
                else days["raw_outputs"]
            )
        else:
            limit = days["artifacts"]
        if version.created_at < now - timedelta(days=limit):
            artifact_service.purge_version(db, version)
            purged += 1
            if purged >= BATCH:
                break
    messages = db.execute(
        delete(AgentMessageRecord).where(
            AgentMessageRecord.organization_id == organization_id,
            AgentMessageRecord.created_at < now - timedelta(days=days["agent_logs"]),
        )
    )
    db.execute(text("SET LOCAL aegis.allow_evidence_delete = 'on'"))
    research = db.execute(
        text("DELETE FROM lab.research_events WHERE organization_id = :org AND created_at < :cutoff"),
        {"org": organization_id, "cutoff": now - timedelta(days=days["research_events"])},
    )
    db.execute(text("SET LOCAL aegis.allow_evidence_delete = 'off'"))
    idem = db.execute(
        delete(IdempotencyKey).where(IdempotencyKey.organization_id == organization_id, IdempotencyKey.expires_at < now)
    )
    short_term = memory_service.purge_expired(db)
    return {
        "artifacts_purged": purged,
        "agent_messages_deleted": int(getattr(messages, "rowcount", 0) or 0),
        "research_events_deleted": int(getattr(research, "rowcount", 0) or 0),
        "idempotency_keys_deleted": int(getattr(idem, "rowcount", 0) or 0),
        "short_term_memories_deleted": short_term,
        "policy_days": days,
    }


def purge_expired_refresh_tokens(admin_db: Session) -> int:
    """Identity-layer maintenance (admin session): refresh tokens expired for more than a week."""
    result = admin_db.execute(delete(RefreshToken).where(RefreshToken.expires_at < utcnow() - timedelta(days=7)))
    return int(getattr(result, "rowcount", 0) or 0)
