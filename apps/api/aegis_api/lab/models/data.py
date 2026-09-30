"""Datasets with immutable versions, and artifacts with immutable versions (bytes live in object storage)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import ProjectScoped, project_scope_fk, user_fk


class Dataset(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    __tablename__ = "datasets"
    __table_args__ = (project_scope_fk(), UniqueConstraint("project_id", "name", name="uq_datasets_project_name"))

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(1000))
    tags: Mapped[list[str]] = mapped_column(default=list)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DatasetVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable dataset version. Experiments must reference a version, never a mutable dataset."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
        Index("ix_dataset_versions_checksum", "organization_id", "checksum"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))  # sha256 of the stored bytes
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    format: Mapped[str] = mapped_column(String(24))  # csv|jsonl|json|parquet|npz|binary
    schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    dataset_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source: Mapped[str | None] = mapped_column(String(1000))
    license: Mapped[str | None] = mapped_column(String(120))
    transformations: Mapped[list[Any]] = mapped_column(default=list)
    # Named splits → {"storage_key", "checksum", "rows", "visibility": "experiment"|"evaluator_only"}.
    # "evaluator_only" splits (held-out labels) are never mounted into experiment sandboxes.
    splits: Mapped[dict[str, Any]] = mapped_column(default=dict)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class Artifact(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A logical artifact (log, model, checkpoint, CSV, JSON, plot, notebook, code, report, manifest…)."""

    __tablename__ = "artifacts"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_artifacts_org_kind", "organization_id", "kind"),
        Index("ix_artifacts_run", "experiment_run_id"),
        Index("ix_artifacts_mission", "mission_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="SET NULL"), nullable=True
    )
    compute_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    # evidence: required for reproducibility (never purged) | standard | ephemeral
    retention_class: Mapped[str] = mapped_column(String(16), default="standard")
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ArtifactVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable artifact bytes reference (only ``scan_status`` may change, via trigger allowance)."""

    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_version"),
        Index("ix_artifact_versions_checksum", "organization_id", "checksum"),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(120))
    original_filename: Mapped[str | None] = mapped_column(String(300))
    scan_status: Mapped[str] = mapped_column(String(16), default="not_scanned")  # not_scanned|clean|infected|error
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
