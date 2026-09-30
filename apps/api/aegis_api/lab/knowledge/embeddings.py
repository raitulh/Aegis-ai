"""Vector embeddings for memories, source chunks, sources and other owned text (pgvector).

The embedder comes from :func:`aegis_api.services.model_gateway.build_embedder` — the deterministic local
``HashEmbedder`` by default (lexical feature hashing, no external calls) or a provider embedding model. Its
dimension must equal ``settings.embedding_dim`` (the column's fixed dimension); a mismatch fails loudly.

Vectors are only ever compared with vectors of the *same* embedding model (``embeddings.model``), and one
row exists per (owner_type, owner_id, model) — re-indexing is an upsert.

Transactions: local embedders run inline. A provider embedder makes a network call, so callers that hold a
transaction (request handlers, services) get the embedding computed *after commit* in its own short unit of
work (:func:`index_texts`); worker code can compute vectors outside any transaction with
:func:`compute_vectors` and store them with :func:`store_vectors`.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import ServiceUnavailable
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.events import after_commit
from aegis_api.lab.models import Embedding
from aegis_api.services.model_gateway import build_embedder
from engines.providers.embeddings import Embedder, HashEmbedder

log = structlog.get_logger("aegis.lab.knowledge.embeddings")

BATCH_SIZE = 64
MAX_EMBED_CHARS = 8000
OWNER_TYPES = frozenset({"memory", "source_chunk", "research_source", "hypothesis", "failure", "lesson", "graph_node"})


class EmbeddingUnavailable(ServiceUnavailable):
    """The configured embedding model is unavailable or misconfigured."""

    code = "embedding_unavailable"


@dataclass(frozen=True)
class EmbeddingItem:
    owner_id: uuid.UUID
    text: str

    @property
    def content_hash(self) -> str:
        return text_hash(self.text)


@dataclass(frozen=True)
class QueryVector:
    model: str
    vector: list[float]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_text(text: str) -> str:
    """The text that is embedded (whitespace-collapsed and bounded)."""
    return " ".join((text or "").split())[:MAX_EMBED_CHARS]


def get_embedder(db: Session, organization_id: uuid.UUID) -> Embedder:
    """The organization's embedder, checked against the fixed vector dimension."""
    embedder = build_embedder(db, organization_id)
    expected = get_settings().embedding_dim
    if embedder.dim != expected:
        raise EmbeddingUnavailable(
            f"Embedding model {embedder.name} produces {embedder.dim}-d vectors; EMBEDDING_DIM is {expected}"
        )
    return embedder


def is_local(embedder: Embedder) -> bool:
    """Local embedders make no network calls and may run inside a transaction."""
    return isinstance(embedder, HashEmbedder)


def compute_vectors(embedder: Embedder, texts: Sequence[str]) -> list[list[float]]:
    """Embed ``texts`` in batches; every vector must have the configured dimension and finite values."""
    expected = get_settings().embedding_dim
    vectors: list[list[float]] = []
    prepared = [prepare_text(t) for t in texts]
    for start in range(0, len(prepared), BATCH_SIZE):
        try:
            batch = embedder.embed(prepared[start : start + BATCH_SIZE])
        except Exception as exc:
            raise EmbeddingUnavailable(f"Embedding model {embedder.name} failed: {type(exc).__name__}") from exc
        for vector in batch:
            if len(vector) != expected or not all(math.isfinite(v) for v in vector):
                raise EmbeddingUnavailable(f"Embedding model {embedder.name} returned an invalid vector")
        vectors.extend([float(v) for v in vector] for vector in batch)
    return vectors


def store_vectors(
    db: Session,
    *,
    organization_id: uuid.UUID,
    owner_type: str,
    project_id: uuid.UUID | None,
    model: str,
    items: Sequence[EmbeddingItem],
    vectors: Sequence[Sequence[float]],
) -> int:
    """Upsert one row per item (unique owner/model)."""
    if owner_type not in OWNER_TYPES:
        raise ValueError(f"Unknown embedding owner type '{owner_type}'")
    if len(items) != len(vectors):
        raise ValueError("items and vectors must have the same length")
    if not items:
        return 0
    dim = get_settings().embedding_dim
    rows = [
        {
            "id": uuid.uuid4(),
            "organization_id": organization_id,
            "project_id": project_id,
            "owner_type": owner_type,
            "owner_id": item.owner_id,
            "model": model[:120],
            "dim": dim,
            "embedding": list(vector),
            "content_hash": item.content_hash,
        }
        for item, vector in zip(items, vectors, strict=True)
    ]
    stmt = insert(Embedding).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_embeddings_owner_model",
        set_={
            "embedding": stmt.excluded.embedding,
            "content_hash": stmt.excluded.content_hash,
            "project_id": stmt.excluded.project_id,
            "dim": stmt.excluded.dim,
            "created_at": func.now(),
        },
    )
    db.execute(stmt)
    return len(rows)


def index_texts(
    db: Session,
    actor: Actor,
    owner_type: str,
    items: Sequence[EmbeddingItem],
    project_id: uuid.UUID | None,
) -> int:
    """Index ``items`` for ``owner_type``. Local embedders write now (same transaction); provider embedders
    are computed after commit in their own short transaction (never holding this one across the call).
    Returns the number of rows written now (0 when deferred)."""
    items = [i for i in items if i.text and i.text.strip()]
    if not items:
        return 0
    embedder = get_embedder(db, actor.organization_id)
    if is_local(embedder):
        vectors = compute_vectors(embedder, [i.text for i in items])
        return store_vectors(
            db,
            organization_id=actor.organization_id,
            owner_type=owner_type,
            project_id=project_id,
            model=embedder.name,
            items=items,
            vectors=vectors,
        )
    organization_id = actor.organization_id
    pending = list(items)

    def deferred() -> None:
        try:
            vectors = compute_vectors(embedder, [i.text for i in pending])
            with tenant_uow(organization_id) as session:
                store_vectors(
                    session,
                    organization_id=organization_id,
                    owner_type=owner_type,
                    project_id=project_id,
                    model=embedder.name,
                    items=pending,
                    vectors=vectors,
                )
        except Exception:
            log.warning("embedding_index_failed", owner_type=owner_type, items=len(pending), exc_info=True)

    after_commit(db, deferred)
    return 0


def index_text(
    db: Session, actor: Actor, owner_type: str, owner_id: uuid.UUID, text: str, project_id: uuid.UUID | None
) -> int:
    return index_texts(db, actor, owner_type, [EmbeddingItem(owner_id=owner_id, text=text)], project_id)


def resolve_embedder(organization_id: uuid.UUID) -> Embedder:
    """The embedder, resolved in its own short transaction (for worker code and search)."""
    with tenant_uow(organization_id) as session:
        return get_embedder(session, organization_id)


def query_vector(embedder: Embedder, text: str) -> QueryVector:
    """Embed a search query (call outside any transaction for provider embedders)."""
    return QueryVector(model=embedder.name, vector=compute_vectors(embedder, [text])[0])
