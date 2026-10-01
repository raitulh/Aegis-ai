"""Cross-cutting infrastructure tables: audit trail, jobs, feature flags, search, errors."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk
from app.models.enums import JobStatus


class AuditLog(UUIDPk, Base):
    """Append-only (a database trigger rejects UPDATE/DELETE). Never store secrets in metadata."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_created", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    org_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    competition_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    reason: Mapped[str | None] = mapped_column(String(1000))
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(32))


class Job(UUIDPk, Base):
    """Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED). No Redis required for the MVP."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
    )

    kind: Mapped[str] = mapped_column(String(48), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_at: Mapped[datetime | None]
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    description: Mapped[str] = mapped_column(String(300), default="")
    is_public: Mapped[bool] = mapped_column(default=True)  # exposed to the web client
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class AppErrorLog(UUIDPk, Base):
    """Unhandled errors for the admin dashboard. No request bodies or secrets."""

    __tablename__ = "app_error_logs"

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(String(8))
    path: Mapped[str | None] = mapped_column(String(300))
    error_type: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(16), default="api")  # api|worker


class SearchDocument(UUIDPk, Base):
    """Denormalized search index over public/authorized content (PostgreSQL full-text search)."""

    __tablename__ = "search_documents"
    __table_args__ = (
        Index("uq_search_entity", "entity_type", "entity_id", unique=True),
        Index("ix_search_tsv", "tsv", postgresql_using="gin"),
    )

    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[uuid.UUID] = mapped_column()
    title: Mapped[str] = mapped_column(String(300))
    subtitle: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(300))
    visibility: Mapped[str] = mapped_column(String(16), default="public")  # public|org
    org_id: Mapped[uuid.UUID | None] = mapped_column()
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    tsv = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(subtitle, '')), 'B') || "
            "setweight(to_tsvector('english', coalesce(body, '')), 'C')",
            persisted=True,
        ),
    )


class SeedMarker(Base):
    """Tracks demo seed runs so demo data can be removed cleanly."""

    __tablename__ = "seed_markers"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)
