"""Organization quotas (agents, concurrent experiments, spend, storage, research jobs, autonomy ceiling)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import QuotaExceeded
from aegis_api.models import OrganizationQuota
from engines.lab.enums import AutonomyLevel

RETENTION_CLASSES = ("agent_logs", "research_events", "artifacts", "raw_outputs", "audit_logs", "llm_metadata")


def get_quota(session: Session, organization_id: uuid.UUID) -> OrganizationQuota:
    quota = session.scalar(select(OrganizationQuota).where(OrganizationQuota.organization_id == organization_id))
    if quota is None:
        quota = OrganizationQuota(organization_id=organization_id, retention={})
        session.add(quota)
        session.flush()
    return quota


def autonomy_ceiling(session: Session, organization_id: uuid.UUID) -> str:
    """The effective ceiling is the lower of the organization quota and the platform maximum."""
    org_level = AutonomyLevel(get_quota(session, organization_id).max_autonomy_level)
    platform = AutonomyLevel(get_settings().lab_platform_max_autonomy)
    return (org_level if org_level.rank <= platform.rank else platform).value


def check_count(value: int, limit: int | None, what: str) -> None:
    if limit is not None and value >= limit:
        raise QuotaExceeded(f"Organization quota reached: {what} (limit {limit})", code="quota_exceeded")
