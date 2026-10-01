"""Platform infrastructure: background job ledger and idempotent request replay."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, IdMixin, TimestampMixin, utcnow


class JobRun(IdMixin, TimestampMixin, Base):
    """One logical background job. ``idempotency_key`` is deterministic for entity jobs (e.g. one key per
    audit), so a duplicate dispatch is recognised instead of executed twice."""

    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_runs_idempotency_key"),
        Index("ix_job_runs_status_next", "status", "next_attempt_at"),
        Index("ix_job_runs_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    job: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    args: Mapped[list[Any]] = mapped_column(default=list)
    # queued | running | retrying | succeeded | failed | dead
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    worker: Mapped[str | None] = mapped_column(String(160))
    error_class: Mapped[str | None] = mapped_column(String(24))  # transient | permanent | cancelled
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class IdempotencyKey(IdMixin, Base):
    """Stored response for an ``Idempotency-Key`` request so client retries replay instead of re-executing."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_idempotency_keys_org_key"),
        Index("ix_idempotency_keys_expires", "expires_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | completed
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
