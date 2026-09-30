"""Declarative base, shared column types and mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, MetaData, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        list[str]: JSONB,
        list[dict[str, Any]]: JSONB,
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid,
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False, index=True
    )


class TimestampMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class OrgMixin:
    """Tenant-owned rows. Row Level Security policies are attached in the migration."""

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


class OptimisticLockMixin:
    """Optimistic concurrency control: every UPDATE checks and bumps ``lock_version``; a concurrent writer that
    loaded an older version gets ``StaleDataError`` (mapped to HTTP 409) instead of silently overwriting."""

    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        return {"version_id_col": cls.__table__.c.lock_version}  # type: ignore[attr-defined]


# PostgreSQL schema for the AI Scientist Evolution Lab bounded context (extraction boundary; avoids
# name collisions with the assurance domain's claims/policies/evaluators/tool_calls tables).
LAB_SCHEMA = "lab"


def lab_args(*items: Any) -> tuple[Any, ...]:
    """``__table_args__`` helper placing a table in the lab schema."""
    return (*items, {"schema": LAB_SCHEMA})


def lab_fk(table: str, ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey(f"{LAB_SCHEMA}.{table}.id", ondelete=ondelete)


def qualified_name(table: Any) -> str:
    return f"{table.schema}.{table.name}" if table.schema else table.name


# Tables that carry organization_id and receive tenant isolation RLS policies.
def tenant_tables() -> list[str]:
    return sorted(
        qualified_name(t) for t in Base.metadata.sorted_tables if "organization_id" in t.c and t.name != "memberships"
    )
