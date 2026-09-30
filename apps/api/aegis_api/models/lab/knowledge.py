"""Research tasks (incl. Gemini Deep Research), sources, documents, scientific memory and the knowledge graph."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import (
    Base,
    CreatedMixin,
    IdMixin,
    OptimisticLockMixin,
    OrgMixin,
    TimestampMixin,
    lab_args,
    lab_fk,
)
from aegis_api.models.systems import EMBEDDING_DIM


class ResearchTask(IdMixin, TimestampMixin, OptimisticLockMixin, OrgMixin, Base):
    __tablename__ = "research_tasks"
    __table_args__ = lab_args(Index("ix_lab_research_tasks_org_status", "organization_id", "status"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(24), default="deep_research")  # deep_research | literature_pipeline
    status: Mapped[str] = mapped_column(String(16), default="created")
    require_plan_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    plan: Mapped[dict[str, Any]] = mapped_column(default=dict)
    plan_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_agent: Mapped[str | None] = mapped_column(String(120))
    provider_interaction_id: Mapped[str | None] = mapped_column(String(255))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    last_event_id: Mapped[str | None] = mapped_column(String(255))
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[str | None] = mapped_column(Text)
    report_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    citations_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ResearchEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "research_events"
    __table_args__ = lab_args(UniqueConstraint("research_task_id", "seq", name="uq_lab_research_events_task_seq"))

    research_task_id: Mapped[uuid.UUID] = mapped_column(lab_fk("research_tasks"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    provider_event_id: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Document(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "documents"
    __table_args__ = lab_args(Index("ix_lab_documents_project_created", "project_id", "created_at"))

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    doc_type: Mapped[str] = mapped_column(String(24))
    source_kind: Mapped[str] = mapped_column(String(24), default="upload")  # upload | url | research | artifact
    source_url: Mapped[str | None] = mapped_column(String(2000))
    filename: Mapped[str | None] = mapped_column(String(255))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="processing")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class DocumentVersion(IdMixin, TimestampMixin, OrgMixin, Base):
    """Stable, content-addressed document version (same bytes → same version)."""

    __tablename__ = "document_versions"
    __table_args__ = lab_args(
        UniqueConstraint("document_id", "version", name="uq_lab_document_versions_doc_version"),
        UniqueConstraint("document_id", "sha256", name="uq_lab_document_versions_doc_sha"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(lab_fk("documents"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(500))
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    injection_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    scan_status: Mapped[str] = mapped_column(String(24), default="not_scanned")
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | indexed | failed
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DocumentChunk(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = lab_args(
        UniqueConstraint("document_version_id", "chunk_index", name="uq_lab_document_chunks_version_idx")
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(lab_fk("document_versions"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120))


class ResearchSource(IdMixin, CreatedMixin, OrgMixin, Base):
    """A cited source with provenance. Authority is never inferred from a model's opinion."""

    __tablename__ = "research_sources"
    __table_args__ = lab_args(
        Index("ix_lab_research_sources_project", "project_id", "created_at"),
        UniqueConstraint("project_id", "checksum", name="uq_lab_research_sources_project_checksum"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    research_task_id: Mapped[uuid.UUID | None] = mapped_column(
        lab_fk("research_tasks", "SET NULL"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32))  # paper | preprint | web | dataset_documentation | ...
    url: Mapped[str | None] = mapped_column(String(2000))
    title: Mapped[str | None] = mapped_column(String(1000))
    publisher: Mapped[str | None] = mapped_column(String(300))
    authors: Mapped[list[str]] = mapped_column(default=list)
    publication_date: Mapped[str | None] = mapped_column(String(32))
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    citation: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    arxiv_id: Mapped[str | None] = mapped_column(String(32))
    snippet: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    trust_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    injection_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    document_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("documents", "SET NULL"), nullable=True)


class Memory(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "memories"
    __table_args__ = lab_args(
        Index("ix_lab_memories_scope", "organization_id", "scope", "category", "status"),
        Index("ix_lab_memories_project", "project_id", "created_at"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("missions"), nullable=True, index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(24))  # user | agent | experiment | failure | research | system
    source_ref: Mapped[dict[str, Any]] = mapped_column(default=dict)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(lab_fk("memories", "SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")  # normal | sensitive
    injection_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MemoryLink(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "memory_links"
    __table_args__ = lab_args(
        UniqueConstraint("memory_id", "target_type", "target_id", "relation", name="uq_lab_memory_links_quad"),
        Index("ix_lab_memory_links_target", "target_type", "target_id"),
    )

    memory_id: Mapped[uuid.UUID] = mapped_column(lab_fk("memories"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32), default="about")


class GraphNode(IdMixin, TimestampMixin, OrgMixin, Base):
    """Knowledge-graph node (PostgreSQL implementation of the GraphStore abstraction)."""

    __tablename__ = "graph_nodes"
    __table_args__ = lab_args(
        UniqueConstraint("organization_id", "node_type", "key", name="uq_lab_graph_nodes_type_key"),
        Index("ix_lab_graph_nodes_ref", "ref_type", "ref_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    node_type: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(String(500))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    properties: Mapped[dict[str, Any]] = mapped_column(default=dict)


class GraphEdge(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "graph_edges"
    __table_args__ = lab_args(
        UniqueConstraint("source_id", "target_id", "relation", name="uq_lab_graph_edges_triple"),
        Index("ix_lab_graph_edges_target", "target_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(lab_fk("graph_nodes"), index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(lab_fk("graph_nodes"))
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_by: Mapped[str] = mapped_column(String(24), default="system")  # system | agent | user
    evidence_ids: Mapped[list[str]] = mapped_column(default=list)
    properties: Mapped[dict[str, Any]] = mapped_column(default=dict)
