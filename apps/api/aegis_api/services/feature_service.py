"""Feature flags: organization override → global override → configured default."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import FeatureDisabled
from aegis_api.models import FeatureFlag


def flags(session: Session, organization_id: uuid.UUID | None) -> dict[str, bool]:
    values = dict(get_settings().feature_defaults())
    rows = session.scalars(
        select(FeatureFlag).where(
            or_(FeatureFlag.organization_id.is_(None), FeatureFlag.organization_id == organization_id)
        )
    ).all()
    for row in sorted(rows, key=lambda r: r.organization_id is not None):  # global first, org wins
        values[row.key] = row.enabled
    return values


def is_enabled(session: Session, organization_id: uuid.UUID | None, key: str) -> bool:
    return bool(flags(session, organization_id).get(key, False))


def require(session: Session, organization_id: uuid.UUID | None, key: str) -> None:
    if not is_enabled(session, organization_id, key):
        raise FeatureDisabled(f"The '{key}' feature is disabled for this workspace", code="feature_disabled")


def set_flag(session: Session, organization_id: uuid.UUID | None, key: str, enabled: bool) -> FeatureFlag:
    row = session.scalar(
        select(FeatureFlag).where(
            FeatureFlag.key == key,
            FeatureFlag.organization_id.is_(None)
            if organization_id is None
            else FeatureFlag.organization_id == organization_id,
        )
    )
    if row is None:
        row = FeatureFlag(organization_id=organization_id, key=key, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    session.flush()
    return row
