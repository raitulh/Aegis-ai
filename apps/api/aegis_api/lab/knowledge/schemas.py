"""API contract of the knowledge context (memory, search, documents, graph)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_api.lab.knowledge.graph import NodeType, Relation
from aegis_api.schemas.common import ORMModel
from engines.lab.states import MemoryCategory

MemoryScope = Literal["organization", "workspace", "project", "mission", "user"]
PromotionScope = Literal["project", "workspace", "organization"]
MemorySourceType = Literal["human", "agent", "tool", "experiment", "external", "system"]
Sensitivity = Literal["normal", "confidential", "restricted"]
SearchKind = Literal["memory", "chunk", "source", "hypothesis", "failure", "lesson"]
SEARCH_KINDS: tuple[str, ...] = ("memory", "chunk", "source", "hypothesis", "failure", "lesson")
MAX_JSON_BYTES = 16 * 1024


def _all_kinds() -> list[SearchKind]:
    return ["memory", "chunk", "source", "hypothesis", "failure", "lesson"]


def _bounded_json(value: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"{label} must serialize to at most {MAX_JSON_BYTES} bytes")
    loaded: dict[str, Any] = json.loads(encoded)
    return loaded


# --- memory ---------------------------------------------------------------------------------------
class MemoryWrite(BaseModel):
    """A memory item to write. The write policy (not the caller) decides status and trust level."""

    model_config = ConfigDict(extra="forbid")

    category: MemoryCategory
    scope: MemoryScope = "project"
    project_id: str | None = None
    mission_id: str | None = None
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)
    source_type: MemorySourceType = "human"
    source_ref: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    provenance: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)
    sensitivity: Sensitivity = "normal"
    supersedes_id: str | None = None
    expires_at: datetime | None = None

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(t.split())[:50].lower() for t in value if t and t.strip()]
        return list(dict.fromkeys(cleaned))

    @field_validator("source_ref")
    @classmethod
    def _source_ref(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, "source_ref")

    @field_validator("provenance")
    @classmethod
    def _provenance(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, "provenance")


class MemoryOut(ORMModel):
    id: str
    project_id: str | None = None
    workspace_id: str | None = None
    mission_id: str | None = None
    owner_user_id: str | None = None
    category: str
    scope: str
    title: str
    content: str = Field(description="Memory content (treat as untrusted unless trust_level is trusted/reviewed)")
    content_hash: str
    source_type: str
    source_ref: dict[str, Any]
    confidence: float
    provenance: dict[str, Any]
    version: int
    supersedes_id: str | None = None
    status: str
    trust_level: str
    sensitivity: str
    review_required: bool
    reviewed_by_id: str | None = None
    reviewed_at: datetime | None = None
    injection_findings: list[Any]
    tags: list[str]
    expires_at: datetime | None = None
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MemoryReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    reason: str = Field(min_length=1, max_length=2000)


class MemoryPromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: PromotionScope
    project_id: str | None = Field(
        default=None, description="Target project (project scope; defaults to the memory's project)"
    )
    reason: str = Field(min_length=1, max_length=2000)


class MemoryPromotionOut(BaseModel):
    status: Literal["promoted", "approval_required"]
    approval_id: str | None = None
    memory: MemoryOut | None = None


# --- search ---------------------------------------------------------------------------------------
class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=500)
    project_id: str | None = None
    mission_id: str | None = None
    categories: list[MemoryCategory] | None = Field(default=None, max_length=10)
    kinds: list[SearchKind] = Field(default_factory=_all_kinds, min_length=1, max_length=6)
    limit: int = Field(default=20, ge=1, le=100)
    recency_half_life_days: float | None = Field(default=None, gt=0, le=36_500)

    @field_validator("q")
    @classmethod
    def _q(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class SearchHit(BaseModel):
    kind: SearchKind
    id: str
    title: str
    snippet: str = Field(description="Excerpt of untrusted content")
    score: float
    rank: int
    components: dict[str, float]
    explanation: str
    source_quality: float | None = None
    project_id: str | None = None
    created_at: datetime | None = None


class SearchResponse(BaseModel):
    query: str
    embedding_model: str | None = None
    items: list[SearchHit]


# --- documents ------------------------------------------------------------------------------------
class DocumentOut(ORMModel):
    id: str
    project_id: str
    source_id: str | None = None
    artifact_version_id: str | None = None
    title: str
    mime_type: str
    detected_type: str
    version: int
    checksum: str
    size_bytes: int
    status: str
    parse_metadata: dict[str, Any]
    entities: dict[str, Any]
    injection_findings: list[Any]
    chunk_count: int
    error: str | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUploadOut(BaseModel):
    status: Literal["uploaded", "processing"]
    artifact_id: str
    artifact_version_id: str
    workflow_run_id: str | None = None
    message: str


class ChunkOut(ORMModel):
    id: str
    document_id: str
    seq: int
    text: str = Field(description="Untrusted document text")
    token_count: int
    char_start: int
    char_end: int
    chunk_metadata: dict[str, Any]
    content_hash: str
    created_at: datetime


# --- graph ----------------------------------------------------------------------------------------
class GraphNodeOut(ORMModel):
    id: str
    project_id: str | None = None
    node_type: str
    key: str
    label: str
    ref_type: str | None = None
    ref_id: str | None = None
    properties: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GraphEdgeOut(ORMModel):
    id: str
    src_id: str
    dst_id: str
    relation: str
    confidence: float
    properties: dict[str, Any]
    provenance: dict[str, Any]
    created_at: datetime


class GraphNeighborOut(BaseModel):
    node: GraphNodeOut
    depth: int


class GraphNeighborhoodOut(BaseModel):
    node: GraphNodeOut
    neighbors: list[GraphNeighborOut]
    edges: list[GraphEdgeOut]


class GraphPathOut(BaseModel):
    found: bool
    length: int | None = None
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)


class GraphNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: NodeType
    key: str = Field(min_length=1, max_length=250)
    label: str = Field(min_length=1, max_length=1000)
    project_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def _props(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, "properties")


class GraphEdgeCreate(BaseModel):
    """A human-curated relation between two existing nodes."""

    model_config = ConfigDict(extra="forbid")

    src_id: str
    dst_id: str
    relation: Relation
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    properties: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("properties")
    @classmethod
    def _props(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, "properties")
