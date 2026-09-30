"""Hybrid scientific memory: memory items, links, embeddings (pgvector) and the knowledge graph."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.config import get_settings
from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, project_scope_fk, user_fk
from engines.lab.states import MemoryStatus

EMBEDDING_DIM = get_settings().embedding_dim


class Memory(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    """A scientific memory item with provenance, confidence and a review-gated write policy.

    Memory is potentially attacker-controlled: agent- or tool-originated items above mission scope are
    written as ``PROPOSED`` and must be reviewed before they influence other missions; content with
    prompt-injection indicators is ``QUARANTINED``.
    """

    __tablename__ = "memories"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_memories_org_category_status", "organization_id", "category", "status"),
        Index("ix_memories_mission", "mission_id"),
        Index("ix_memories_tsv", "tsv", postgresql_using="gin"),
        Index("ix_memories_content_hash", "organization_id", "content_hash"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))  # organization|workspace|project|mission|user
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(16))  # human|agent|tool|experiment|external|system
    source_ref: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=MemoryStatus.PROPOSED)
    trust_level: Mapped[str] = mapped_column(String(16), default="untrusted")  # trusted|reviewed|untrusted
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")  # normal|confidential|restricted
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = user_fk()
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    injection_findings: Mapped[list[Any]] = mapped_column(default=list)
    tags: Mapped[list[str]] = mapped_column(default=list)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))", persisted=True),
    )


class MemoryLink(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint("memory_id", "target_type", "target_id", "relation", name="uq_memory_links_edge"),
        Index("ix_memory_links_target", "target_type", "target_id"),
    )

    memory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class Embedding(IdMixin, CreatedMixin, OrgMixin, Base):
    """Vector embedding of any owned text (memories, source chunks, hypotheses, failures…)."""

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model", name="uq_embeddings_owner_model"),
        Index("ix_embeddings_org_owner_type", "organization_id", "owner_type"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    model: Mapped[str] = mapped_column(String(120))
    dim: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    content_hash: Mapped[str] = mapped_column(String(64))


class GraphNode(IdMixin, TimestampMixin, OrgMixin, Base):
    """Knowledge-graph node (Paper, Author, Claim, Method, Dataset, Model, Hypothesis, Experiment,
    Result, Failure, Strategy, Discovery). Backed by PostgreSQL; the GraphService abstraction allows a
    dedicated graph database later."""

    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("organization_id", "node_type", "key", name="uq_graph_nodes_type_key"),
        Index("ix_graph_nodes_ref", "ref_type", "ref_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    node_type: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(String(1000))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    properties: Mapped[dict[str, Any]] = mapped_column(default=dict)


class GraphEdge(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "relation", name="uq_graph_edges_triple"),
        Index("ix_graph_edges_dst_relation", "dst_id", "relation"),
    )

    src_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    dst_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    properties: Mapped[dict[str, Any]] = mapped_column(default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
