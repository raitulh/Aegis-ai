"""Shared column helpers for lab models.

Project-scoped tables carry ``(organization_id, workspace_id, project_id)`` and a *composite* foreign key
to ``projects(id, workspace_id, organization_id)``. PostgreSQL referential checks bypass Row Level
Security, so the composite key is what guarantees at the database level that a child row can never be
attached to another tenant's (or another workspace's) project — defence in depth behind RLS and the
service-layer ownership checks.

Project foreign keys use ``NO ACTION`` (not ``CASCADE``): a project that still owns research records
cannot be hard-deleted (archive it instead), while deleting a whole organization still cascades because
the children are removed by the same statement.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Numeric, Uuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

MONEY = Numeric(18, 6)


def money_column(**kwargs: Any) -> Mapped[Decimal]:
    return mapped_column(MONEY, default=Decimal("0"), server_default="0", nullable=False, **kwargs)


def project_scope_fk() -> ForeignKeyConstraint:
    """Composite FK (project_id, workspace_id, organization_id) → projects."""
    return ForeignKeyConstraint(
        ["project_id", "workspace_id", "organization_id"],
        ["projects.id", "projects.workspace_id", "projects.organization_id"],
    )


class ProjectScoped:
    """Adds workspace_id + project_id columns. Tables using it must add ``project_scope_fk()`` to
    ``__table_args__``."""

    @declared_attr
    def workspace_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(Uuid, nullable=False, index=True)

    @declared_attr
    def project_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(Uuid, nullable=False, index=True)


class OptionalProjectScoped:
    """Workspace/project scope that may be NULL (organization-level records)."""

    @declared_attr
    def workspace_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(Uuid, nullable=True, index=True)

    @declared_attr
    def project_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(Uuid, nullable=True, index=True)


def mission_fk(nullable: bool = True, ondelete: str | None = None) -> Mapped[uuid.UUID | None]:
    return mapped_column(Uuid, ForeignKey("missions.id", ondelete=ondelete), nullable=nullable, index=True)


def user_fk(nullable: bool = True) -> Mapped[uuid.UUID | None]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=nullable)
