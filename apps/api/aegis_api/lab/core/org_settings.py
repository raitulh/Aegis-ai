"""Typed per-organization lab settings (autonomy ceiling, quotas, retention, egress, execution policy)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.lab.models import OrganizationSettings
from engines.lab.states import AutonomyLevel

DEFAULT_QUOTAS: dict[str, Any] = {
    "max_agents": 100,
    "max_concurrent_experiments": 4,
    "max_llm_spend_usd": 500.0,  # per calendar month
    "max_compute_spend_usd": 500.0,  # per calendar month
    "max_storage_bytes": 50 * 1024**3,
    "max_research_jobs": 50,  # concurrent (non-terminal) research tasks
    "max_missions": 100,  # non-archived missions
}
DEFAULT_RETENTION: dict[str, Any] = {
    "agent_logs_days": 180,
    "research_events_days": 365,
    "artifacts_days": None,
    "raw_outputs_days": 90,
    "audit_logs_days": None,
    "llm_metadata_days": 365,
}


def get_org_settings(db: Session, organization_id: uuid.UUID) -> OrganizationSettings:
    """Return the organization's settings row, creating it with safe defaults on first use."""
    row = db.scalar(select(OrganizationSettings).where(OrganizationSettings.organization_id == organization_id))
    if row is not None:
        return row
    db.execute(
        insert(OrganizationSettings)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            max_autonomy_level=AutonomyLevel.L3_AUTOMATED_EXECUTION,
            default_autonomy_level=AutonomyLevel.L1_RESEARCH_AUTOMATION,
            quotas=DEFAULT_QUOTAS,
            retention=DEFAULT_RETENTION,
            egress_allowlist=[],
            execution_policy={},
            data_processing={"allow_external_llm": False},
            sso_enforced=False,
            lock_version=1,
        )
        .on_conflict_do_nothing(constraint="uq_organization_settings_org")
    )
    row = db.scalar(select(OrganizationSettings).where(OrganizationSettings.organization_id == organization_id))
    assert row is not None
    return row


def quota(db: Session, organization_id: uuid.UUID, key: str) -> Any:
    settings = get_org_settings(db, organization_id)
    return (settings.quotas or {}).get(key, DEFAULT_QUOTAS.get(key))


def allows_external_llm(db: Session, organization_id: uuid.UUID) -> bool:
    """Explicit consent to send organization data to hosted model providers (default: no)."""
    return bool((get_org_settings(db, organization_id).data_processing or {}).get("allow_external_llm", False))
