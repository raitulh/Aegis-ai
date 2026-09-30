"""Scientific sources: normalization, de-duplicated upsert, deterministic trust metadata, graph + search index.

``trust_metadata`` is produced only by :func:`engines.lab.source_quality.assess_source` from bibliographic
metadata (type, peer review, DOI, venue, citations, domain, retraction) and stores the inputs it was computed
from, so every score can be reproduced and audited. A language model never influences it.

Sources are unique per project by DOI and by canonical URL (partial unique indexes); upserts serialize per
project with a transaction-scoped advisory lock, so concurrent searches cannot create duplicates.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate, sort_clause
from aegis_api.lab.llm.schemas import Citation
from aegis_api.lab.models import Project, ResearchSource, ResearchTask
from aegis_api.lab.research.literature import (
    PaperRecord,
    arxiv_id_from_url,
    canonical_url,
    citation_string,
    normalize_doi,
)
from aegis_api.lab.research.schemas import SourceCreate
from aegis_api.schemas.common import Page, PageParams
from engines.lab.ingestion import sanitize_url
from engines.lab.source_quality import SourceMetadata, assess_source

log = structlog.get_logger("aegis.lab.research.sources")

DiscoveredBy = Literal["user", "agent", "deep_research", "search"]
SOURCE_TYPES = ("paper", "preprint", "web_page", "dataset", "upload", "book", "other")
PAPER_TYPES = ("paper", "preprint")
MAX_BATCH = 200
SOURCE_FTS = literal_column(
    "to_tsvector('english', coalesce(research_sources.title,'') || ' ' || coalesce(research_sources.abstract,''))"
)
TS_CONFIG = literal_column("'english'::regconfig")


class SourceInput(BaseModel):
    """Normalized input for :func:`upsert_sources` (from search records, citations or manual entry)."""

    source_type: str = "paper"
    title: str
    url: str | None = None
    doi: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    publication_date: date | None = None
    publication_year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    citation: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    cited_by_count: int | None = None
    is_retracted: bool = False
    is_peer_reviewed: bool | None = None
    work_type: str | None = None

    @classmethod
    def from_paper(cls, record: PaperRecord) -> SourceInput:
        return cls(
            source_type=record.source_type,
            title=record.title,
            url=record.url,
            doi=record.doi,
            authors=list(record.authors),
            abstract=record.abstract,
            publication_date=record.publication_date,
            publication_year=record.publication_year,
            venue=record.venue,
            publisher=record.publisher,
            citation=citation_string(record),
            external_ids=dict(record.external_ids),
            cited_by_count=record.cited_by_count,
            is_retracted=record.is_retracted,
            is_peer_reviewed=record.is_peer_reviewed,
            work_type=record.type,
        )

    @classmethod
    def from_citation(cls, citation: Citation) -> SourceInput:
        """A web citation (e.g. from Deep Research); DOI/arXiv links are recognized as papers."""
        url = citation.url
        doi = normalize_doi(url) if "doi.org/" in url.lower() else None
        arxiv = arxiv_id_from_url(url)
        source_type = "paper" if doi else "preprint" if arxiv else "web_page"
        title = " ".join((citation.title or "").split()) or url
        return cls(
            source_type=source_type,
            title=title[:1000],
            url=url,
            doi=doi,
            external_ids={"arxiv": arxiv} if arxiv else {},
            work_type="preprint" if arxiv else ("journal-article" if doi else "web page"),
        )

    @classmethod
    def from_create(cls, data: SourceCreate) -> SourceInput:
        return cls(
            source_type=data.source_type,
            title=data.title,
            url=data.url,
            doi=data.doi,
            authors=list(data.authors),
            abstract=data.abstract,
            publication_date=data.publication_date,
            publication_year=data.publication_date.year if data.publication_date else None,
            venue=data.venue,
            publisher=data.publisher,
            citation=data.citation,
            external_ids=dict(data.external_ids),
            is_retracted=data.is_retracted,
            is_peer_reviewed=data.is_peer_reviewed,
            work_type=data.work_type,
        )


def _clean_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = " ".join("".join(ch for ch in value if ch.isprintable() or ch in "\n\t").split())
    return cleaned[:limit] or None


def normalize_input(item: SourceInput | PaperRecord) -> SourceInput:
    """Validate and normalize one input (URL sanitized, DOI normalized, lengths bounded)."""
    data = SourceInput.from_paper(item) if isinstance(item, PaperRecord) else item
    if data.source_type not in SOURCE_TYPES:
        raise ValidationFailed(f"source_type must be one of {', '.join(SOURCE_TYPES)}")
    title = _clean_text(data.title, 1000)
    if not title:
        raise ValidationFailed("A source title is required")
    url: str | None = None
    if data.url:
        url = sanitize_url(data.url.strip())
        if url is None:
            raise ValidationFailed("url must be an http(s) URL")
    doi = normalize_doi(data.doi) if data.doi else None
    if data.doi and doi is None:
        raise ValidationFailed("doi is not a valid DOI (expected 10.xxxx/…)")
    return data.model_copy(
        update={
            "title": title,
            "url": url,
            "doi": doi,
            "authors": [a for a in (_clean_text(x, 200) for x in data.authors[:100]) if a],
            "abstract": _clean_text(data.abstract, 20_000),
            "venue": _clean_text(data.venue, 300),
            "publisher": _clean_text(data.publisher, 300),
            "citation": _clean_text(data.citation, 2000),
            "external_ids": {str(k)[:40]: str(v)[:200] for k, v in list(data.external_ids.items())[:10] if v},
            "cited_by_count": data.cited_by_count if data.cited_by_count and data.cited_by_count > 0 else None,
        }
    )


def source_canonical_url(data: SourceInput) -> str | None:
    if data.doi:
        return f"https://doi.org/{data.doi}"
    return canonical_url(data.url) if data.url else None


def trust_inputs(data: SourceInput) -> dict[str, Any]:
    year = data.publication_year or (data.publication_date.year if data.publication_date else None)
    return {
        "source_type": data.source_type,
        "url": data.url,
        "doi": data.doi,
        "venue": data.venue,
        "publisher": data.publisher,
        "type": data.work_type,
        "is_peer_reviewed": data.is_peer_reviewed,
        "is_retracted": data.is_retracted,
        "cited_by_count": data.cited_by_count,
        "publication_year": year,
    }


def assess(data: SourceInput) -> dict[str, Any]:
    """Deterministic trust metadata (with the inputs it was computed from)."""
    inputs = trust_inputs(data)
    trust = assess_source(SourceMetadata(**inputs), current_year=utcnow().year)
    return {**trust.model_dump(mode="json"), "inputs": inputs}


def metadata_checksum(data: SourceInput) -> str:
    canonical = json.dumps(
        {
            "title": data.title,
            "authors": data.authors,
            "doi": data.doi,
            "canonical_url": source_canonical_url(data),
            "publication_date": data.publication_date.isoformat() if data.publication_date else None,
            "venue": data.venue,
            "publisher": data.publisher,
            "abstract": data.abstract,
            "external_ids": dict(sorted(data.external_ids.items())),
            "is_retracted": data.is_retracted,
            "type": data.work_type,
            "source_type": data.source_type,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_existing(db: Session, project_id: uuid.UUID, doi: str | None, canonical: str | None) -> ResearchSource | None:
    if doi:
        found = db.scalar(
            select(ResearchSource).where(ResearchSource.project_id == project_id, ResearchSource.doi == doi)
        )
        if found is not None:
            return found
    if canonical:
        return db.scalar(
            select(ResearchSource).where(
                ResearchSource.project_id == project_id, ResearchSource.canonical_url == canonical
            )
        )
    return None


def _refresh(db: Session, source: ResearchSource, data: SourceInput, canonical: str | None) -> bool:
    """Fill gaps and refresh volatile metadata (citations, retraction) of an existing source, then re-assess
    trust. Existing values win over new ones except for monotonic facts. Returns whether anything changed."""
    previous: dict[str, Any] = (source.trust_metadata or {}).get("inputs") or {}
    counts = [c for c in (previous.get("cited_by_count"), data.cited_by_count) if isinstance(c, int) and c > 0]
    peer_reviewed = previous.get("is_peer_reviewed")
    published = source.source_type == "paper" or data.source_type == "paper"
    merged = SourceInput(
        source_type="paper" if published and source.source_type in PAPER_TYPES else source.source_type,
        title=source.title,
        url=source.url or data.url,
        doi=source.doi or data.doi,
        authors=list(source.authors or []) or data.authors,
        abstract=source.abstract or data.abstract,
        publication_date=source.publication_date or data.publication_date,
        publication_year=previous.get("publication_year") or data.publication_year,
        venue=previous.get("venue") or data.venue,
        publisher=previous.get("publisher") or data.publisher,
        citation=source.citation or data.citation,
        external_ids={**data.external_ids, **(source.external_ids or {})},
        cited_by_count=max(counts) if counts else None,
        is_retracted=bool(previous.get("is_retracted")) or data.is_retracted,
        is_peer_reviewed=peer_reviewed if peer_reviewed is not None else data.is_peer_reviewed,
        work_type=previous.get("type") or data.work_type,
    )
    checksum = metadata_checksum(merged)
    if checksum == source.checksum:
        return False
    source.source_type = merged.source_type
    source.url = merged.url
    if merged.doi and not _doi_taken(db, source, merged.doi):
        source.doi = merged.doi
    source.canonical_url = source.canonical_url or canonical
    source.authors = merged.authors
    source.abstract = merged.abstract
    source.publication_date = merged.publication_date
    source.publisher = merged.venue or merged.publisher
    source.citation = merged.citation
    source.external_ids = merged.external_ids
    source.trust_metadata = assess(merged)
    source.checksum = checksum
    return True


def _doi_taken(db: Session, source: ResearchSource, doi: str) -> bool:
    if source.doi == doi:
        return False
    other = db.scalar(
        select(ResearchSource.id).where(
            ResearchSource.project_id == source.project_id, ResearchSource.doi == doi, ResearchSource.id != source.id
        )
    )
    return other is not None


def graph_key(source: ResearchSource) -> str:
    if source.doi:
        return f"doi:{source.doi}"
    arxiv = (source.external_ids or {}).get("arxiv")
    if arxiv:
        return f"arxiv:{arxiv}"
    if source.canonical_url:
        return f"url:{source.canonical_url}"
    return f"source:{source.id}"


def _graph_nodes(db: Session, actor: Actor, sources: Sequence[ResearchSource]) -> None:
    from aegis_api.lab.knowledge.graph import get_graph

    graph = get_graph(db, actor, required=False)
    if graph is None:
        return
    for source in sources:
        node_type = (
            "Paper" if source.source_type in PAPER_TYPES else "Dataset" if source.source_type == "dataset" else None
        )
        if node_type is None:
            continue
        year = source.publication_date.year if source.publication_date else None
        graph.upsert_node(
            node_type,
            graph_key(source),
            source.title,
            ref_type="research_source",
            ref_id=source.id,
            project_id=source.project_id,
            properties={
                "source_type": source.source_type,
                "doi": source.doi,
                "year": year,
                "venue": source.publisher,
                "authors": list(source.authors or [])[:20],
                "trust_score": (source.trust_metadata or {}).get("score"),
                "trust_tier": (source.trust_metadata or {}).get("tier"),
            },
        )


def _index(db: Session, actor: Actor, sources: Sequence[ResearchSource]) -> None:
    from aegis_api.lab.knowledge.embeddings import EmbeddingItem, index_texts

    by_project: dict[uuid.UUID, list[EmbeddingItem]] = {}
    for source in sources:
        text = f"{source.title}\n{source.abstract or ''}".strip()
        by_project.setdefault(source.project_id, []).append(EmbeddingItem(owner_id=source.id, text=text))
    for project_id, items in by_project.items():
        index_texts(db, actor, "research_source", items, project_id)


def upsert_sources(
    db: Session,
    actor: Actor,
    project_id: uuid.UUID | str,
    records: Sequence[SourceInput | PaperRecord],
    *,
    research_task_id: uuid.UUID | str | None = None,
    discovered_by: DiscoveredBy = "search",
) -> list[ResearchSource]:
    """Create or refresh sources (de-duplicated by DOI, then canonical URL). Returns one row per input, in
    input order (duplicates in the batch map to the same row). Emits ``SOURCE_ADDED`` for new sources."""
    if len(records) > MAX_BATCH:
        raise ValidationFailed(f"At most {MAX_BATCH} sources can be upserted at once")
    project = load_project(db, actor, project_id, "research:run")
    task_uuid: uuid.UUID | None = None
    if research_task_id is not None:
        task = get_owned(db, ResearchTask, research_task_id, actor, label="Research task")
        if task.project_id != project.id:
            raise ValidationFailed("The research task belongs to a different project")
        task_uuid = task.id
    normalized = [normalize_input(r) for r in records]
    advisory_xact_lock(db, f"research-sources:{project.id}")
    now = utcnow()
    results: list[ResearchSource] = []
    created: list[ResearchSource] = []
    touched: list[ResearchSource] = []
    for data in normalized:
        canonical = source_canonical_url(data)
        existing = _find_existing(db, project.id, data.doi, canonical)
        if existing is not None:
            if _refresh(db, existing, data, canonical):
                touched.append(existing)
            results.append(existing)
            continue
        source = ResearchSource(
            id=uuid.uuid4(),
            organization_id=actor.organization_id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            research_task_id=task_uuid,
            source_type=data.source_type,
            url=data.url,
            canonical_url=canonical,
            doi=data.doi,
            external_ids=data.external_ids,
            title=data.title,
            publisher=data.venue or data.publisher,
            authors=data.authors,
            publication_date=data.publication_date,
            retrieved_at=now if discovered_by != "user" else None,
            citation=data.citation,
            abstract=data.abstract,
            checksum=metadata_checksum(data),
            trust_metadata=assess(data),
            status="discovered",
            discovered_by=discovered_by,
            agent_run_id=actor.agent_run_id,
            created_by_id=actor.user_id,
        )
        db.add(source)
        db.flush()
        results.append(source)
        created.append(source)
    db.flush()
    for source in created:
        emit(
            db,
            organization_id=actor.organization_id,
            type=EventType.SOURCE_ADDED,
            payload={
                "source_id": str(source.id),
                "source_type": source.source_type,
                "title": source.title[:300],
                "doi": source.doi,
                "trust_tier": source.trust_metadata.get("tier"),
                "research_task_id": str(task_uuid) if task_uuid else None,
                "discovered_by": discovered_by,
            },
            project_id=project.id,
            workspace_id=project.workspace_id,
            mission_id=_task_mission(db, task_uuid),
            subject_type="research_source",
            subject_id=source.id,
            actor=actor,
        )
    changed = created + touched
    if changed:
        _graph_nodes(db, actor, changed)
        _index(db, actor, changed)
    return results


def _task_mission(db: Session, task_id: uuid.UUID | None) -> uuid.UUID | None:
    if task_id is None:
        return None
    task = db.get(ResearchTask, task_id)
    return task.mission_id if task is not None else None


# --- reads ----------------------------------------------------------------------------------------
def get_source(db: Session, actor: Actor, source_id: uuid.UUID | str) -> ResearchSource:
    source = get_owned(db, ResearchSource, source_id, actor, label="Source")
    load_project(db, actor, source.project_id, "research:read")
    return source


def get_paper(db: Session, actor: Actor, source_id: uuid.UUID | str) -> ResearchSource:
    source = get_source(db, actor, source_id)
    if source.source_type not in PAPER_TYPES:
        raise NotFound("Paper not found")
    return source


def _scoped(db: Session, actor: Actor, project_id: uuid.UUID | str | None) -> Any:
    stmt = select(ResearchSource).where(ResearchSource.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "research:read")
        return stmt.where(ResearchSource.project_id == project.id)
    actor.require("research:read")
    visible = visible_project_ids(db, actor)
    return stmt.where(ResearchSource.project_id.in_(visible)) if visible is not None else stmt


def list_sources(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | str | None = None,
    source_type: str | None = None,
    research_task_id: uuid.UUID | str | None = None,
    q: str | None = None,
    papers_only: bool = False,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str | None = None,
    mapper: Any = None,
) -> Page[Any]:
    stmt = _scoped(db, actor, project_id)
    if papers_only:
        if source_type is not None and source_type not in PAPER_TYPES:
            raise ValidationFailed("source_type must be paper or preprint")
        stmt = stmt.where(ResearchSource.source_type.in_(PAPER_TYPES))
    if source_type is not None:
        if source_type not in SOURCE_TYPES:
            raise ValidationFailed(f"source_type must be one of {', '.join(SOURCE_TYPES)}")
        stmt = stmt.where(ResearchSource.source_type == source_type)
    if research_task_id is not None:
        try:
            stmt = stmt.where(ResearchSource.research_task_id == uuid.UUID(str(research_task_id)))
        except ValueError as exc:
            raise ValidationFailed("research_task_id must be a UUID") from exc
    if year_from is not None:
        stmt = stmt.where(ResearchSource.publication_date >= date(year_from, 1, 1))
    if year_to is not None:
        stmt = stmt.where(ResearchSource.publication_date <= date(year_to, 12, 31))
    order = sort_clause(ResearchSource, sort, ("created_at", "publication_date", "title"))
    if q:
        query = func.websearch_to_tsquery(TS_CONFIG, q)
        stmt = stmt.where(SOURCE_FTS.op("@@")(query))
        if sort is None:
            order = func.ts_rank_cd(SOURCE_FTS, query).desc()
    stmt = stmt.order_by(order, ResearchSource.id.desc())
    return paginate(db, stmt, params, mapper or (lambda s: s))


def sources_for_task(db: Session, task: ResearchTask) -> list[ResearchSource]:
    return list(
        db.scalars(
            select(ResearchSource)
            .where(ResearchSource.research_task_id == task.id)
            .order_by(ResearchSource.created_at, ResearchSource.id)
        )
    )


def top_sources(
    db: Session,
    project: Project,
    *,
    mission_id: uuid.UUID | None = None,
    limit: int = 12,
) -> list[ResearchSource]:
    """Highest-trust sources of a project (or of a mission's research tasks), newest first on ties."""
    trust = ResearchSource.trust_metadata["score"].as_float()
    stmt = select(ResearchSource).where(ResearchSource.project_id == project.id)
    if mission_id is not None:
        task_ids = select(ResearchTask.id).where(ResearchTask.mission_id == mission_id)
        stmt = stmt.where(ResearchSource.research_task_id.in_(task_ids))
    stmt = stmt.order_by(trust.desc().nulls_last(), ResearchSource.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))
