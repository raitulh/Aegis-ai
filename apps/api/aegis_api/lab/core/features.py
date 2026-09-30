"""Feature flags: settings defaults overridden per organization by ``feature_flags`` rows."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import get_current_principal, get_db
from aegis_api.lab.core.errors import FeatureDisabled
from aegis_api.models import FeatureFlag
from aegis_api.security.context import Principal

LAB_FEATURES = (
    "evolution",
    "deep_research",
    "mcp",
    "gpu_execution",
    "enterprise_sso",
    "verification",
    "graph_memory",
    "billing",
)


def feature_enabled(db: Session, organization_id: uuid.UUID | None, key: str) -> bool:
    """Org override > global override (organization_id NULL) > settings default."""
    rows = db.execute(
        select(FeatureFlag.organization_id, FeatureFlag.enabled).where(
            FeatureFlag.key == key,
            or_(FeatureFlag.organization_id == organization_id, FeatureFlag.organization_id.is_(None)),
        )
    ).all()
    org_value = next((enabled for org, enabled in rows if org is not None), None)
    if org_value is not None:
        return bool(org_value)
    global_value = next((enabled for org, enabled in rows if org is None), None)
    if global_value is not None:
        return bool(global_value)
    return bool(get_settings().feature_defaults().get(key, False))


def ensure_feature(db: Session, organization_id: uuid.UUID, key: str) -> None:
    if not feature_enabled(db, organization_id, key):
        raise FeatureDisabled(f"The '{key}' feature is disabled for this organization")


def require_feature(key: str) -> Callable[..., None]:
    def _dep(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)) -> None:
        ensure_feature(db, principal.organization_id, key)

    return _dep


def all_features(db: Session, organization_id: uuid.UUID) -> dict[str, bool]:
    return {key: feature_enabled(db, organization_id, key) for key in get_settings().feature_defaults()}
