"""API contract for datasets, dataset versions, artifacts and artifact versions.

Storage keys, internal paths and object-store URLs are never part of a read model; bytes are reached only
through the authorized download endpoints (signed, short-lived URLs or streaming).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_api.schemas.common import ORMModel

SplitVisibility = Literal["experiment", "evaluator_only"]
RetentionClass = Literal["evidence", "standard", "ephemeral"]
DownloadMode = Literal["redirect", "stream", "url"]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    license: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=25)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        cleaned = []
        for tag in value:
            tag = tag.strip()
            if not tag or len(tag) > 50:
                raise ValueError("tags must be 1-50 characters")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned


class DatasetOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    name: str
    description: str | None = None
    license: str | None = None
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    current_version_id: str | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SplitSpec(BaseModel):
    """Declared split: which file (multi-file upload) or which split-column value, and who may read it."""

    model_config = ConfigDict(extra="forbid")

    visibility: SplitVisibility = "experiment"
    file: str | None = Field(default=None, max_length=300, description="Uploaded filename for this split")


class SplitOut(BaseModel):
    visibility: SplitVisibility
    checksum: str
    size_bytes: int
    rows: int | None = None
    filename: str | None = None
    format: str | None = None


class DatasetVersionOut(ORMModel):
    id: str
    organization_id: str
    dataset_id: str
    project_id: str
    version: int
    checksum: str
    size_bytes: int
    row_count: int | None = None
    format: str
    data_schema: dict[str, Any] = Field(default_factory=dict, validation_alias="schema", serialization_alias="schema")
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="dataset_metadata", serialization_alias="metadata"
    )
    source: str | None = None
    license: str | None = None
    transformations: list[Any] = Field(default_factory=list)
    splits: dict[str, SplitOut] = Field(default_factory=dict)
    parent_version_id: str | None = None
    created_by_id: str | None = None
    created_at: datetime

    @field_validator("splits", mode="before")
    @classmethod
    def _public_splits(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        return {
            name: {k: v for k, v in (spec or {}).items() if k != "storage_key"}
            for name, spec in value.items()
            if isinstance(spec, dict)
        }


class DatasetLineageOut(BaseModel):
    version_id: str
    depth: int
    items: list[DatasetVersionOut] = Field(description="The version followed by its ancestors (nearest first)")
    truncated: bool = False


class ArtifactVersionOut(ORMModel):
    id: str
    organization_id: str
    artifact_id: str
    project_id: str
    version: int
    size_bytes: int
    checksum: str
    mime_type: str
    original_filename: str | None = None
    scan_status: str
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="artifact_metadata", serialization_alias="metadata"
    )
    created_by_id: str | None = None
    created_at: datetime


class ArtifactOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    mission_id: str | None = None
    experiment_run_id: str | None = None
    compute_job_id: str | None = None
    kind: str
    name: str
    description: str | None = None
    current_version_id: str | None = None
    retention_class: str
    legal_hold: bool = False
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    current_version: ArtifactVersionOut | None = None


class SignedUrlOut(BaseModel):
    url: str
    method: Literal["GET"] = "GET"
    expires_at: datetime
    filename: str | None = None
    size_bytes: int
    checksum: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class ErrorEnvelope(BaseModel):
    """The standard error envelope (documentation model for ``responses=``)."""

    error: ErrorDetail
