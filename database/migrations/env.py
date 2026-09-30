"""Alembic environment. Migrations always run with the owner/admin connection (DATABASE_ADMIN_URL)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

import aegis_api.models  # noqa: F401  (register tables)
from aegis_api.config import get_settings
from aegis_api.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().admin_database_url


MANAGED_SCHEMAS = {None, "public", "lab"}


def include_name(name, type_, parent_names):  # type: ignore[no-untyped-def]
    # Only the application schemas are managed by autogenerate (the ``aegis`` schema holds RLS helpers).
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # Indexes created with raw SQL (HNSW vector, full-text GIN) are managed manually in migrations.
    return not (type_ == "index" and name and (name.startswith("ix_fts_") or name.startswith("ix_vec_")))


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
