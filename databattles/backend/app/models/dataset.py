from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk
from app.models.enums import ContentStatus, ContentVisibility, VersionStatus

LICENSES: dict[str, str] = {
    "cc-by-4.0": "Creative Commons Attribution 4.0",
    "cc-by-sa-4.0": "Creative Commons Attribution-ShareAlike 4.0",
    "cc-by-nc-4.0": "Creative Commons Attribution-NonCommercial 4.0",
    "cc0-1.0": "CC0 1.0 (Public Domain Dedication)",
    "odbl-1.0": "Open Database License 1.0",
    "mit": "MIT License",
    "apache-2.0": "Apache License 2.0",
    "competition-use-only": "Competition use only (see terms)",
    "other": "Other (see description)",
}


class Dataset(UUIDPk, Timestamps, Base):
    __tablename__ = "datasets"
    __table_args__ = (Index("ix_datasets_tags", "tags", postgresql_using="gin"),)

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    subtitle: Mapped[str | None] = mapped_column(String(200))
    description_md: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    owner_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    license: Mapped[str] = mapped_column(String(32), default="cc-by-4.0")
    citation: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list, server_default="{}")
    visibility: Mapped[str] = mapped_column(String(16), default=ContentVisibility.public)
    requires_terms: Mapped[bool] = mapped_column(default=False)
    terms_md: Mapped[str | None] = mapped_column(Text)
    terms_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ContentStatus.active)
    takedown_reason: Mapped[str | None] = mapped_column(String(500))
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL", use_alter=True, name="fk_datasets_latest_version"))
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(default=False)


class DatasetVersion(UUIDPk, Base):
    """Versions are immutable once published; archived versions remain available to competitions using them."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=VersionStatus.draft)
    release_notes_md: Mapped[str] = mapped_column(Text, default="")
    release_notes_html: Mapped[str] = mapped_column(Text, default="")
    data_dictionary: Mapped[list[Any]] = mapped_column(default=list)  # [{column, type, description}]
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DatasetFile(UUIDPk, Base):
    __tablename__ = "dataset_files"
    __table_args__ = (UniqueConstraint("version_id", "filename"),)

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(24), default="data")  # data|sample_submission|notebook|documentation
    preview: Mapped[dict[str, Any] | None] = mapped_column()
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    scan_status: Mapped[str] = mapped_column(String(16), default="skipped")
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DatasetTermsAcceptance(Base):
    __tablename__ = "dataset_terms_acceptances"

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    terms_hash: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DatasetDownloadStat(Base):
    """Aggregate daily counts only — no per-user download trail."""

    __tablename__ = "dataset_download_stats"

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
