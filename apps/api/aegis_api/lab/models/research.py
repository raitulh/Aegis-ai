"""Research tasks (incl. Gemini Deep Research), scientific sources, research events, ingested documents."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Computed,
    Date,
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

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import ProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import ResearchTaskStatus


class ResearchTask(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A literature/web/deep-research task. Deep Research runs in the provider's background; the
    provider interaction id is persisted so polling/streaming can resume after any restart."""

    __tablename__ = "research_tasks"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_research_tasks_org_status", "organization_id", "status"),
        Index("ix_research_tasks_provider_interaction", "provider_interaction_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(24))  # deep_research | literature_search | web_research
    title: Mapped[str] = mapped_column(String(300))
    query: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    plan: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    plan_status: Mapped[str] = mapped_column(String(24), default="NOT_REQUIRED")  # PENDING_REVIEW|APPROVED|...
    status: Mapped[str] = mapped_column(String(16), default=ResearchTaskStatus.CREATED)
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_agent: Mapped[str | None] = mapped_column(String(160))
    provider_interaction_id: Mapped[str | None] = mapped_column(String(255))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    report: Mapped[str | None] = mapped_column(Text)
    report_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = money_column()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class ResearchSource(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A scientific source (paper, web page, dataset…). ``trust_metadata`` is computed by deterministic
    rules (venue, peer review, retraction flags, domain) — never because a model said so."""

    __tablename__ = "research_sources"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_research_sources_org_type", "organization_id", "source_type"),
        Index(
            "uq_research_sources_project_url",
            "project_id",
            "canonical_url",
            unique=True,
            postgresql_where="canonical_url IS NOT NULL",
        ),
        Index("uq_research_sources_project_doi", "project_id", "doi", unique=True, postgresql_where="doi IS NOT NULL"),
    )

    research_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(24))  # paper|preprint|web_page|dataset|upload|book|other
    url: Mapped[str | None] = mapped_column(String(2000))
    canonical_url: Mapped[str | None] = mapped_column(String(2000))
    doi: Mapped[str | None] = mapped_column(String(255))
    external_ids: Mapped[dict[str, Any]] = mapped_column(default=dict)
    title: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str | None] = mapped_column(String(300))
    authors: Mapped[list[Any]] = mapped_column(default=list)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    citation: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    trust_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered|ingested|failed|quarantined
    content_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    discovered_by: Mapped[str] = mapped_column(String(24), default="user")  # user|agent|deep_research|search
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class ResearchEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """Provider-side progress of a research task (thought summaries, status changes, citations)."""

    __tablename__ = "research_events"
    __table_args__ = (UniqueConstraint("research_task_id", "seq", name="uq_research_events_task_seq"),)

    research_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_tasks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    provider_event_id: Mapped[str | None] = mapped_column(String(255))


class SourceDocument(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A parsed, versioned document produced by the ingestion pipeline (PDF, CSV, JSON, Markdown,
    notebook, HTML, text). Re-ingesting changed content of the same source creates a new version."""

    __tablename__ = "source_documents"
    __table_args__ = (
        project_scope_fk(),
        UniqueConstraint("source_id", "version", name="uq_source_documents_source_version"),
        Index("ix_source_documents_project_checksum", "project_id", "checksum"),
    )

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    mime_type: Mapped[str] = mapped_column(String(120))
    detected_type: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer, default=1)
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded|parsed|indexed|failed|quarantined
    parse_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    entities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    injection_findings: Mapped[list[Any]] = mapped_column(default=list)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class SourceChunk(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("document_id", "seq", name="uq_source_chunks_document_seq"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))
