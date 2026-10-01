"""AI systems, providers, model registry, knowledge sources and change events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.config import get_settings
from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import (
    ConnectionStatus,
    DataClassification,
    Environment,
    RiskTier,
    SystemType,
)

EMBEDDING_DIM = get_settings().embedding_dim


class Provider(IdMixin, TimestampMixin, OrgMixin, Base):
    """A configured model provider connection (Ollama, Gemini, OpenAI, Anthropic, HTTP endpoint, demo)."""

    __tablename__ = "providers"

    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(String(500))
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(160))
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Explicit consent to send organization data to this (possibly external) provider.
    allow_data_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default=ConnectionStatus.UNKNOWN)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class AISystem(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "ai_systems"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_ai_systems_org_slug"),
        Index("ix_ai_systems_org_status", "organization_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    system_type: Mapped[str] = mapped_column(String(24), default=SystemType.LLM)
    environment: Mapped[str] = mapped_column(String(24), default=Environment.DEVELOPMENT)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(160))
    business_purpose: Mapped[str | None] = mapped_column(Text)
    risk_tier: Mapped[str] = mapped_column(String(16), default=RiskTier.LIMITED)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(80))
    endpoint_url: Mapped[str | None] = mapped_column(String(500))
    auth_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True
    )
    system_instructions_ref: Mapped[str | None] = mapped_column(String(500))
    system_instructions: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    data_classification: Mapped[str] = mapped_column(String(24), default=DataClassification.INTERNAL)
    version: Mapped[str] = mapped_column(String(40), default="v1.0")
    # guardrails, tools & permissions, demo profile, decision schema, retrieval configuration ...
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Runtime Guard mode: observe (record) | audit (record + findings) | enforce (block / require approval).
    runtime_mode: Mapped[str] = mapped_column(String(12), default="observe", server_default="observe")
    # Known-good audit used as the regression baseline for continuous assurance.
    baseline_audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audits.id", ondelete="SET NULL", use_alter=True), nullable=True
    )

    provider: Mapped[Provider | None] = relationship(lazy="joined", foreign_keys=[provider_id])


class SystemVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable snapshot of a system's configuration (used for reproducibility and change impact)."""

    __tablename__ = "system_versions"
    __table_args__ = (UniqueConstraint("system_id", "version", name="uq_system_versions_system_version"),)

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    changed_fields: Mapped[list[str]] = mapped_column(default=list)
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AIModel(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("organization_id", "provider_kind", "name", name="uq_models_org_name"),)

    provider_kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str | None] = mapped_column(String(160))
    family: Mapped[str | None] = mapped_column(String(80))
    context_window: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class ModelVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_versions_model_version"),)

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(80))
    digest: Mapped[str | None] = mapped_column(String(128))
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)


class KnowledgeDocument(IdMixin, TimestampMixin, OrgMixin, Base):
    """Reference corpus for groundedness / claim verification (e.g. a RAG system's knowledge base)."""

    __tablename__ = "knowledge_documents"

    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(24), default="internal")  # internal | web | upload
    retrieved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    version: Mapped[str | None] = mapped_column(String(40))


class KnowledgeChunk(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120))


class SystemEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """Timeline of observable changes: model version changes, prompt updates, retriever updates ..."""

    __tablename__ = "system_events"
    __table_args__ = (Index("ix_system_events_system_occurred", "system_id", "occurred_at"),)

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    occurred_at: Mapped[datetime]
