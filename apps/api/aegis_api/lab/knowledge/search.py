"""Hybrid knowledge search: PostgreSQL full-text + pgvector similarity, fused and explained.

For every requested kind the actor may read, two candidate lists are retrieved:

* **keyword** — ``websearch_to_tsquery`` against the same ``to_tsvector`` expressions as the GIN indexes of
  migration 0004 (``memories.tsv`` and ``source_chunks.tsv`` are generated columns), ranked by ``ts_rank_cd``;
* **semantic** — cosine distance between the query embedding and stored embeddings of the *same* model.

Both lists are fused by :func:`engines.lab.search.combine` (reciprocal rank fusion + normalized scores,
optional recency decay and deterministic source quality), so every score is explained by its components.

Permission filtering happens in SQL before ranking: tenant (RLS + explicit predicate), visible projects,
per-kind read permissions, memory status/sensitivity/ownership rules, quarantined documents excluded. A hit
is never returned unless the actor can read it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnClause, ColumnElement

from aegis_api.db.base import utcnow
from aegis_api.lab.core.access import effective_permissions, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.knowledge import embeddings
from aegis_api.lab.knowledge.embeddings import QueryVector
from aegis_api.lab.knowledge.memory import visibility_clause
from aegis_api.lab.knowledge.schemas import SearchHit, SearchKind, SearchQuery
from aegis_api.lab.models import (
    Embedding,
    Failure,
    Hypothesis,
    Lesson,
    Memory,
    Project,
    ResearchSource,
    ResearchTask,
    SourceChunk,
    SourceDocument,
)
from engines.lab.search import FusionWeights, combine, recency_decay

TS_CONFIG: ColumnClause[Any] = literal_column("'english'::regconfig")
FTS: dict[str, ColumnClause[Any]] = {
    "source": literal_column(
        "to_tsvector('english', coalesce(research_sources.title,'') || ' ' || coalesce(research_sources.abstract,''))"
    ),
    "hypothesis": literal_column(
        "to_tsvector('english', coalesce(hypotheses.statement,'') || ' ' || coalesce(hypotheses.rationale,''))"
    ),
    "failure": literal_column(
        "to_tsvector('english', coalesce(failures.title,'') || ' ' || coalesce(failures.root_cause,''))"
    ),
    "lesson": literal_column("to_tsvector('english', coalesce(lessons.statement,''))"),
}
KIND_PERMISSIONS: dict[str, str] = {
    "memory": "memory:read",
    "chunk": "memory:read",
    "source": "research:read",
    "hypothesis": "hypothesis:read",
    "failure": "failure:read",
    "lesson": "failure:read",
}
OWNER_TYPES: dict[str, str] = {
    "memory": "memory",
    "chunk": "source_chunk",
    "source": "research_source",
    "hypothesis": "hypothesis",
    "failure": "failure",
    "lesson": "lesson",
}
CANDIDATE_FACTOR = 3
MIN_CANDIDATES = 20
MIN_SEMANTIC_SIMILARITY = 0.05
SNIPPET_CHARS = 280
_TOKEN = re.compile(r"[\w-]{3,}", re.UNICODE)


@dataclass
class _Row:
    kind: SearchKind
    id: uuid.UUID
    title: str
    text: str
    project_id: uuid.UUID | None
    created_at: datetime | None
    published: date | datetime | None
    quality: float | None


class _Context:
    """Per-search permission context (projects visible, permissions per kind)."""

    def __init__(self, db: Session, actor: Actor, query: SearchQuery) -> None:
        self.db = db
        self.actor = actor
        self.query = query
        self.project: Project | None = None
        if query.project_id is not None:
            self.project = load_project(db, actor, query.project_id)
            self.visible: list[uuid.UUID] | None = [self.project.id]
            perms = effective_permissions(db, actor, self.project)
        else:
            self.visible = visible_project_ids(db, actor)
            perms = actor.permissions
        self.kinds = [k for k in dict.fromkeys(query.kinds) if KIND_PERMISSIONS[k] in perms]
        if query.mission_id is not None:
            self.kinds = [k for k in self.kinds if k != "lesson"]
        self.mission_id = uuid.UUID(str(query.mission_id)) if query.mission_id else None

    def project_clause(self, column: Any, *, nullable: bool) -> ColumnElement[bool]:
        if self.visible is None:
            return column.is_not(None) if not nullable else literal_column("true")
        clause = column.in_(self.visible)
        if nullable and self.project is None:
            return clause | column.is_(None)
        return clause


def _memory_filter(ctx: _Context) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [
        visibility_clause(ctx.db, ctx.actor, statuses=["ACTIVE"], visible_projects=ctx.visible)
    ]
    if ctx.project is not None:
        clauses.append(Memory.project_id == ctx.project.id)
    if ctx.mission_id is not None:
        clauses.append(Memory.mission_id == ctx.mission_id)
    if ctx.query.categories:
        clauses.append(Memory.category.in_([str(c) for c in ctx.query.categories]))
    return clauses


def _filters(ctx: _Context, kind: str) -> tuple[Any, list[ColumnElement[bool]]]:
    """(model, predicates) restricting ``kind`` to rows the actor may read."""
    org = ctx.actor.organization_id
    if kind == "memory":
        return Memory, _memory_filter(ctx)
    if kind == "chunk":
        clauses = [
            SourceChunk.organization_id == org,
            ctx.project_clause(SourceChunk.project_id, nullable=False),
            SourceChunk.document_id.in_(
                select(SourceDocument.id).where(
                    SourceDocument.status == "indexed", SourceDocument.organization_id == org
                )
            ),
        ]
        if ctx.mission_id is not None:
            clauses.append(
                SourceChunk.document_id.in_(
                    select(SourceDocument.id)
                    .join(ResearchSource, ResearchSource.id == SourceDocument.source_id)
                    .join(ResearchTask, ResearchTask.id == ResearchSource.research_task_id)
                    .where(ResearchTask.mission_id == ctx.mission_id)
                )
            )
        return SourceChunk, clauses
    if kind == "source":
        clauses = [ResearchSource.organization_id == org, ctx.project_clause(ResearchSource.project_id, nullable=False)]
        if ctx.mission_id is not None:
            clauses.append(
                ResearchSource.research_task_id.in_(
                    select(ResearchTask.id).where(ResearchTask.mission_id == ctx.mission_id)
                )
            )
        return ResearchSource, clauses
    if kind == "hypothesis":
        clauses = [Hypothesis.organization_id == org, ctx.project_clause(Hypothesis.project_id, nullable=False)]
        if ctx.mission_id is not None:
            clauses.append(Hypothesis.mission_id == ctx.mission_id)
        return Hypothesis, clauses
    if kind == "failure":
        clauses = [Failure.organization_id == org, ctx.project_clause(Failure.project_id, nullable=True)]
        if ctx.mission_id is not None:
            clauses.append(Failure.mission_id == ctx.mission_id)
        return Failure, clauses
    return Lesson, [Lesson.organization_id == org, ctx.project_clause(Lesson.project_id, nullable=True)]


def _tsvector(kind: str) -> Any:
    if kind == "memory":
        return Memory.tsv
    if kind == "chunk":
        return SourceChunk.tsv
    return FTS[kind]


def _keyword(ctx: _Context, kind: str, limit: int) -> dict[str, float]:
    model, clauses = _filters(ctx, kind)
    vector = _tsvector(kind)
    tsquery = func.websearch_to_tsquery(TS_CONFIG, ctx.query.q)
    rank = func.ts_rank_cd(vector, tsquery)
    stmt = select(model.id, rank).where(vector.op("@@")(tsquery), *clauses).order_by(rank.desc(), model.id).limit(limit)
    return {f"{kind}:{row[0]}": float(row[1]) for row in ctx.db.execute(stmt).all()}


def _semantic(ctx: _Context, kind: str, vector: QueryVector, limit: int) -> dict[str, float]:
    model, clauses = _filters(ctx, kind)
    distance = Embedding.embedding.cosine_distance(vector.vector)
    stmt = (
        select(Embedding.owner_id, distance)
        .join(model, model.id == Embedding.owner_id)
        .where(
            Embedding.organization_id == ctx.actor.organization_id,
            Embedding.owner_type == OWNER_TYPES[kind],
            Embedding.model == vector.model,
            *clauses,
        )
        .order_by(distance, Embedding.owner_id)
        .limit(limit)
    )
    hits: dict[str, float] = {}
    for owner_id, dist in ctx.db.execute(stmt).all():
        similarity = 1.0 - float(dist)
        if similarity >= MIN_SEMANTIC_SIMILARITY:
            hits[f"{kind}:{owner_id}"] = similarity
    return hits


def _load(ctx: _Context, keys: list[str]) -> dict[str, _Row]:
    by_kind: dict[str, list[uuid.UUID]] = {}
    for key in keys:
        kind, raw = key.split(":", 1)
        by_kind.setdefault(kind, []).append(uuid.UUID(raw))
    rows: dict[str, _Row] = {}
    db = ctx.db
    for kind, ids in by_kind.items():
        if kind == "memory":
            for m in db.scalars(select(Memory).where(Memory.id.in_(ids))):
                trust = {"trusted": 1.0, "reviewed": 0.8}.get(m.trust_level, 0.4)
                rows[f"memory:{m.id}"] = _Row(
                    "memory", m.id, m.title, m.content, m.project_id, m.created_at, m.updated_at, trust
                )
        elif kind == "chunk":
            result = db.execute(
                select(SourceChunk, SourceDocument.title, ResearchSource.trust_metadata)
                .join(SourceDocument, SourceDocument.id == SourceChunk.document_id)
                .outerjoin(ResearchSource, ResearchSource.id == SourceDocument.source_id)
                .where(SourceChunk.id.in_(ids))
            ).all()
            for c, title, trust_meta in result:
                score = (trust_meta or {}).get("score") if trust_meta else None
                rows[f"chunk:{c.id}"] = _Row(
                    "chunk", c.id, title, c.text, c.project_id, c.created_at, c.created_at, _as_float(score)
                )
        elif kind == "source":
            for s in db.scalars(select(ResearchSource).where(ResearchSource.id.in_(ids))):
                rows[f"source:{s.id}"] = _Row(
                    "source",
                    s.id,
                    s.title,
                    s.abstract or s.citation or "",
                    s.project_id,
                    s.created_at,
                    s.publication_date or s.created_at,
                    _as_float((s.trust_metadata or {}).get("score")),
                )
        elif kind == "hypothesis":
            for h in db.scalars(select(Hypothesis).where(Hypothesis.id.in_(ids))):
                text = f"{h.statement}\n{h.rationale or ''}"
                rows[f"hypothesis:{h.id}"] = _Row(
                    "hypothesis", h.id, h.statement[:300], text, h.project_id, h.created_at, h.created_at, None
                )
        elif kind == "failure":
            for f in db.scalars(select(Failure).where(Failure.id.in_(ids))):
                rows[f"failure:{f.id}"] = _Row(
                    "failure", f.id, f.title, f.root_cause or f.title, f.project_id, f.created_at, f.detected_at, None
                )
        elif kind == "lesson":
            for lesson in db.scalars(select(Lesson).where(Lesson.id.in_(ids))):
                rows[f"lesson:{lesson.id}"] = _Row(
                    "lesson",
                    lesson.id,
                    lesson.statement[:300],
                    lesson.statement,
                    lesson.project_id,
                    lesson.created_at,
                    lesson.created_at,
                    None,
                )
    return rows


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def snippet(text: str, query: str, limit: int = SNIPPET_CHARS) -> str:
    """A whitespace-normalized excerpt around the first query term (untrusted text, bounded)."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    lowered = flat.casefold()
    positions = [lowered.find(t.casefold()) for t in _TOKEN.findall(query)]
    hits = [p for p in positions if p >= 0]
    start = max(0, min(hits) - limit // 4) if hits else 0
    excerpt = flat[start : start + limit]
    return ("…" if start > 0 else "") + excerpt + ("…" if start + limit < len(flat) else "")


def prepare_query_vector(actor: Actor, text: str) -> QueryVector | None:
    """Embed the query outside any request transaction (``None`` when no embedder is usable)."""
    try:
        embedder = embeddings.resolve_embedder(actor.organization_id)
        return embeddings.query_vector(embedder, text)
    except embeddings.EmbeddingUnavailable:
        return None


def search(
    db: Session,
    actor: Actor,
    query: SearchQuery,
    *,
    query_vector: QueryVector | None = None,
    weights: FusionWeights | Mapping[str, float] | None = None,
) -> list[SearchHit]:
    """Hybrid search over the kinds in ``query`` the actor may read (see the module docstring).

    ``query_vector`` may be precomputed (e.g. outside the request transaction); otherwise a local embedder
    embeds the query inline and a provider embedder is skipped (keyword-only) to avoid a network call inside
    the caller's transaction.
    """
    ctx = _Context(db, actor, query)
    if not ctx.kinds:
        return []
    vector = query_vector
    if vector is None:
        embedder = embeddings.get_embedder(db, actor.organization_id)
        if embeddings.is_local(embedder):
            vector = embeddings.query_vector(embedder, query.q)
    candidates = max(MIN_CANDIDATES, query.limit * CANDIDATE_FACTOR)
    keyword: dict[str, float] = {}
    semantic: dict[str, float] = {}
    for kind in ctx.kinds:
        keyword.update(_keyword(ctx, kind, candidates))
        if vector is not None:
            semantic.update(_semantic(ctx, kind, vector, candidates))
    if not keyword and not semantic:
        return []
    rows = _load(ctx, sorted(set(keyword) | set(semantic)))
    keyword = {k: v for k, v in keyword.items() if k in rows}
    semantic = {k: v for k, v in semantic.items() if k in rows}
    recency: dict[str, float] | None = None
    if query.recency_half_life_days is not None:
        now = utcnow()
        recency = {k: recency_decay(r.published, now, query.recency_half_life_days) for k, r in rows.items()}
    quality = {k: r.quality for k, r in rows.items() if r.quality is not None}
    ranked = combine(
        keyword,
        semantic,
        recency=recency,
        source_quality=quality or None,
        weights=weights,
        limit=query.limit,
    )
    hits: list[SearchHit] = []
    for item in ranked:
        row = rows[item.id]
        hits.append(
            SearchHit(
                kind=row.kind,
                id=str(row.id),
                title=" ".join(row.title.split())[:300],
                snippet=snippet(row.text, query.q),
                score=item.score,
                rank=item.rank,
                components=item.components,
                explanation=item.explanation,
                source_quality=row.quality if row.kind in ("source", "chunk") else None,
                project_id=str(row.project_id) if row.project_id else None,
                created_at=row.created_at,
            )
        )
    return hits
