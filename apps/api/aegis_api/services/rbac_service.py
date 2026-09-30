"""Permission resolution for system roles and organization-defined custom roles, plus catalogue seeding."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.models import Permission, RoleDefinition, RolePermission
from aegis_api.security.rbac import (
    ADMIN_ASSURANCE,
    LAB_PERMISSIONS,
    ROLE_PERMISSIONS,
    ROLE_RANK,
    is_system_role,
    permissions_for_role,
)


def resolve_permissions(session: Session, organization_id: uuid.UUID, role: str) -> frozenset[str]:
    if is_system_role(role):
        return permissions_for_role(role)
    record = session.scalar(
        select(RoleDefinition).where(RoleDefinition.organization_id == organization_id, RoleDefinition.key == role)
    )
    if record is None:
        return frozenset()
    keys = session.scalars(select(RolePermission.permission_key).where(RolePermission.role_id == record.id)).all()
    return frozenset(keys)


def seed_catalogue(session: Session) -> None:
    """Idempotently sync the global permission catalogue and immutable system role definitions."""
    catalogue: dict[str, tuple[str, str]] = dict(LAB_PERMISSIONS)
    for key in sorted(ADMIN_ASSURANCE | {"org:delete", "billing:manage"}):
        catalogue.setdefault(key, (key.split(":")[0], "Assurance platform permission"))
    existing = {p.key: p for p in session.scalars(select(Permission)).all()}
    for key, (category, description) in catalogue.items():
        if key not in existing:
            session.add(Permission(key=key, category=category, description=description))
    session.flush()
    for role, perms in ROLE_PERMISSIONS.items():
        record = session.scalar(
            select(RoleDefinition).where(RoleDefinition.organization_id.is_(None), RoleDefinition.key == role)
        )
        if record is None:
            record = RoleDefinition(
                organization_id=None,
                key=role,
                name=role.replace("_", " ").title(),
                is_system=True,
                rank=ROLE_RANK.get(role, 0),
            )
            session.add(record)
            session.flush()
        current = set(
            session.scalars(select(RolePermission.permission_key).where(RolePermission.role_id == record.id)).all()
        )
        for key in sorted(set(perms) - current):
            if key in catalogue:
                session.add(RolePermission(role_id=record.id, permission_key=key))
    session.flush()
