"""Knowledge ingestion and retrieval.

Pipeline (run by the ArtifactProcessingWorkflow, never inside a request transaction):
    store raw bytes → scan → detect type → parse → prompt-injection scan → metadata/entity extraction
    → chunk → embed → persist chunks → knowledge-graph nodes/edges

All ingested content is treated as untrusted data: it is sanitized, scored for injection, and only ever
reaches a model inside a fenced RESEARCH DATA block. Documents with a high injection score are quarantined
from retrieval until reviewed.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Select, func, literal_column, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.errors import PayloadTooLarge, ValidationFailed
from aegis_api.infrastructure.scanning import looks_executable, scan_bytes
from aegis_api.infrastructure.storage import get_storage, object_key
from aegis_api.models import Project
from aegis_api.models.lab import Document, DocumentChunk, DocumentVersion, ResearchSource
from aegis_api.security.context import Principal
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services.lab import graph, usage
from aegis_api.services.lab.common import sha256_bytes
from aegis_api.services.lab.model_gateway import CallContext, get_gateway
from engines.lab.knowledge.chunking import chunk_text
from engines.lab.knowledge.extraction import extract_metadata
from engines.lab.knowledge.parsing import ParseError, detect_type, parse
from engines.lab.search import fuse
from engines.lab.security.prompt_injection import detect, sanitize

log = structlog.get_logger("aegis.lab.knowledge")

QUARANTINE_SCORE = 0.8
EMBED_BATCH = 64
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "html": "text/html",
    "notebook": "application/x-ipynb+json",
    "text": "text/plain",
}


def embed_texts(organization_id: uuid.UUID, texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed outside any DB transaction (may call an external embedding API)."""
    if not texts:
        return [], ""
    vectors: list[list[float]] = []
    model = ""
    for i in range(0, len(texts), EMBED_BATCH):
        batch, model = get_gateway().embed(CallContext(organization_id=organization_id), texts[i : i + EMBED_BATCH])
        vectors.extend(batch)
    return vectors, model


# --- ingestion ---------------------------------------------------------------------------------------------


def store_document(
    db: Session,
    principal: Principal,
    project: Project,
    *,
    filename: str,
    data: bytes,
    declared_type: str | None = None,
    title: str | None = None,
    source_kind: str = "upload",
    source_url: str | None = None,
) -> DocumentVersion:
    """Persist raw bytes + a pending version. Call with no open transaction around the storage write."""
    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLarge(f"Document exceeds {settings.max_upload_bytes} bytes")
    if not data:
        raise ValidationFailed("Document is empty")
    if looks_executable(data):
        raise ValidationFailed("Executable content is not accepted as a document")
    try:
        doc_type = detect_type(filename, data, declared_type)
    except ParseError as exc:
        raise ValidationFailed(str(exc)) from exc
    digest = sha256_bytes(data)
    key = object_key(principal.organization_id, "documents", str(project.id), f"{digest}")
    stored = get_storage().put_bytes(key, data, content_type=CONTENT_TYPES.get(doc_type, "application/octet-stream"))
    document = Document(
        organization_id=principal.organization_id,
        project_id=project.id,
        title=(title or filename or "document")[:300],
        doc_type=doc_type,
        source_kind=source_kind,
        source_url=source_url,
        filename=filename[:300] if filename else None,
        status="processing",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        organization_id=principal.organization_id,
        document_id=document.id,
        version=1,
        sha256=digest,
        size_bytes=stored.size,
        content_type=CONTENT_TYPES.get(doc_type),
        storage_key=key,
        doc_metadata={"filename": filename},
        entities=[],
        injection_report={},
        scan_status="pending",
        status="pending",
        chunk_count=0,
        retrieved_at=datetime.now(UTC) if source_kind == "url" else None,
    )
    db.add(version)
    db.flush()
    document.current_version_id = version.id
    usage.record_storage(
        db,
        organization_id=principal.organization_id,
        object_kind="document",
        object_id=version.id,
        size_bytes=stored.size,
        project_id=project.id,
    )
    return version


def register_url(db: Session, principal: Principal, project: Project, *, url: str, title: str | None) -> Document:
    validate_outbound_url(url)  # SSRF check up-front; re-validated on every redirect hop when fetched
    document = Document(
        organization_id=principal.organization_id,
        project_id=project.id,
        title=(title or url)[:300],
        doc_type="html",
        source_kind="url",
        source_url=url[:2000],
        status="fetching",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(document)
    db.flush()
    return document


def fetch_url_document(organization_id: uuid.UUID, document_id: uuid.UUID) -> uuid.UUID | None:
    """Activity: fetch a registered URL (network, no transaction held) and store it as a new version."""
    from aegis_api.infrastructure.search import SearchError, fetch_url

    with session_scope(organization_id) as db:
        doc = db.get(Document, document_id)
        if doc is None or not doc.source_url:
            return None
        if doc.current_version_id:
            return doc.current_version_id
        url = doc.source_url
    try:
        fetched = fetch_url(url, max_bytes=get_settings().research_fetch_max_bytes)
    except (SearchError, ValueError) as exc:
        with session_scope(organization_id) as db:
            doc = db.get(Document, document_id)
            if doc is not None:
                doc.status = "failed"
        raise ValidationFailed(f"Could not fetch URL: {exc}") from exc
    try:
        doc_type = detect_type(fetched.final_url, fetched.data, fetched.content_type)
    except ParseError as exc:
        with session_scope(organization_id) as db:
            doc = db.get(Document, document_id)
            if doc is not None:
                doc.status = "failed"
        raise ValidationFailed(str(exc)) from exc
    key = object_key(organization_id, "documents", "url", fetched.sha256)
    stored = get_storage().put_bytes(
        key, fetched.data, content_type=CONTENT_TYPES.get(doc_type, "application/octet-stream")
    )
    with session_scope(organization_id) as db:
        doc = db.get(Document, document_id)
        assert doc is not None
        doc.doc_type = doc_type
        doc.status = "processing"
        version = DocumentVersion(
            organization_id=organization_id,
            document_id=doc.id,
            version=1,
            sha256=fetched.sha256,
            size_bytes=stored.size,
            content_type=fetched.content_type,
            storage_key=key,
            doc_metadata={
                "final_url": fetched.final_url,
                "truncated": fetched.truncated,
                "http_status": fetched.status,
            },
            entities=[],
            injection_report={},
            scan_status="pending",
            status="pending",
            chunk_count=0,
            retrieved_at=datetime.fromisoformat(fetched.retrieved_at),
        )
        db.add(version)
        db.flush()
        doc.current_version_id = version.id
        usage.record_storage(
            db,
            organization_id=organization_id,
            object_kind="document",
            object_id=version.id,
            size_bytes=stored.size,
            project_id=doc.project_id,
        )
        return version.id


def process_document_version(organization_id: uuid.UUID, version_id: uuid.UUID) -> dict[str, Any]:
    """Activity: scan → parse → injection check → chunk → embed → persist. Idempotent on replay."""
    with session_scope(organization_id) as db:
        version = db.get(DocumentVersion, version_id)
        if version is None:
            return {"status": "missing"}
        if version.status in ("ready", "quarantined", "failed"):
            return {"status": version.status, "chunks": version.chunk_count}
        doc = db.get(Document, version.document_id)
        assert doc is not None
        storage_key, doc_type, project_id, title = version.storage_key, doc.doc_type, doc.project_id, doc.title
    started = time.perf_counter()
    data = get_storage().get_bytes(storage_key, max_bytes=max(get_settings().max_upload_bytes, 64 * 1024 * 1024))
    scan = scan_bytes(data)
    if scan.status == "infected":
        _finish_version(organization_id, version_id, status="quarantined", scan_status="infected", error=scan.detail)
        return {"status": "quarantined", "reason": "malware"}
    try:
        parsed = parse(doc_type, data)
    except ParseError as exc:
        _finish_version(organization_id, version_id, status="failed", scan_status=scan.status, error=str(exc))
        return {"status": "failed", "error": str(exc)}
    text, truncated = sanitize(parsed.text, max_chars=2_000_000)
    injection = detect(text[:200_000])
    meta = extract_metadata(text, parsed.metadata)
    chunks = chunk_text(text)
    vectors, model = embed_texts(organization_id, [c.text for c in chunks]) if chunks else ([], "")
    quarantine = injection.score >= QUARANTINE_SCORE
    with session_scope(organization_id) as db:
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        if version.status in ("ready", "quarantined", "failed"):
            return {"status": version.status}
        padded: list[list[float] | None] = list(vectors) if vectors else [None] * len(chunks)
        for chunk, vec in zip(chunks, padded, strict=False):
            db.add(
                DocumentChunk(
                    organization_id=organization_id,
                    project_id=project_id,
                    document_version_id=version.id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    content_hash=chunk.content_hash,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding=vec,
                    embedding_model=(model or None) if vec is not None else None,
                )
            )
        version.doc_metadata = {
            **(version.doc_metadata or {}),
            **meta.to_dict(),
            "sections": parsed.sections[:100],
            "table_profile": parsed.table_profile,
            "text_truncated": truncated,
            "processing_ms": int((time.perf_counter() - started) * 1000),
        }
        version.entities = meta.entities[:200]
        version.injection_report = injection.to_dict()
        version.scan_status = scan.status
        version.chunk_count = len(chunks)
        version.status = "quarantined" if quarantine else "ready"
        doc = db.get(Document, version.document_id)
        assert doc is not None
        doc.status = version.status
        if meta.title and doc.title in ("", doc.filename, doc.source_url):
            doc.title = meta.title[:300]
        doc_node = graph.upsert_node(
            db,
            organization_id=organization_id,
            project_id=project_id,
            node_type="paper" if (meta.doi or meta.arxiv_id) else "document",
            key=f"document:{doc.id}",
            label=doc.title or title,
            ref_type="document",
            ref_id=doc.id,
            properties={"doi": meta.doi, "arxiv_id": meta.arxiv_id, "year": meta.year},
        )
        if not quarantine:
            for ent in meta.entities[:40]:
                name = str(ent.get("name") or "").strip()
                if not name:
                    continue
                node = graph.upsert_node(
                    db,
                    organization_id=organization_id,
                    project_id=project_id,
                    node_type="metric" if ent.get("type") == "Metric" else "concept",
                    key=f"{ent.get('type', 'concept')}:{name}",
                    label=name,
                )
                graph.add_edge(
                    db,
                    organization_id=organization_id,
                    source=doc_node,
                    target=node,
                    relation="mentions",
                    created_by="system",
                    confidence=0.6,
                )
        return {
            "status": version.status,
            "chunks": len(chunks),
            "injection_score": injection.score,
            "scan_status": scan.status,
            "embedding_model": model,
        }


def _finish_version(
    organization_id: uuid.UUID, version_id: uuid.UUID, *, status: str, scan_status: str, error: str | None
) -> None:
    with session_scope(organization_id) as db:
        version = db.get(DocumentVersion, version_id)
        if version is None:
            return
        version.status = status
        version.scan_status = scan_status
        version.error = (error or "")[:2000] or None
        doc = db.get(Document, version.document_id)
        if doc is not None:
            doc.status = status


# --- retrieval ---------------------------------------------------------------------------------------------


def search_chunks(
    db: Session,
    organization_id: uuid.UUID,
    query: str,
    *,
    project_ids: list[uuid.UUID] | None,
    project_id: uuid.UUID | None = None,
    query_vector: list[float] | None = None,
    embedding_model: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    k = max(1, min(limit, 50)) * 3

    def scoped(stmt: Select[Any]) -> Select[Any]:
        stmt = (
            stmt.join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .join(Document, Document.current_version_id == DocumentVersion.id)
            .where(DocumentChunk.organization_id == organization_id, DocumentVersion.status == "ready")
        )
        if project_ids is not None:
            stmt = stmt.where(DocumentChunk.project_id.in_(project_ids))
        if project_id is not None:
            stmt = stmt.where(DocumentChunk.project_id == project_id)
        return stmt

    tsv = func.to_tsvector(literal_column("'english'"), DocumentChunk.text)
    tsq = func.plainto_tsquery(literal_column("'english'"), query)
    keyword = [
        str(r[0])
        for r in db.execute(
            scoped(select(DocumentChunk.id)).where(tsv.op("@@")(tsq)).order_by(func.ts_rank(tsv, tsq).desc()).limit(k)
        ).all()
    ]
    vector: list[str] = []
    if query_vector is not None and embedding_model:
        vector = [
            str(r[0])
            for r in db.execute(
                scoped(select(DocumentChunk.id))
                .where(DocumentChunk.embedding.is_not(None), DocumentChunk.embedding_model == embedding_model)
                .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
                .limit(k)
            ).all()
        ]
    ids = list(dict.fromkeys(keyword + vector))
    if not ids:
        return []
    rows = {
        str(c.id): (c, d)
        for c, d in db.execute(
            select(DocumentChunk, Document)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentChunk.id.in_([uuid.UUID(i) for i in ids]))
        ).all()
    }
    fused = fuse(
        {"keyword": keyword, "vector": vector}, created_at={i: rows[i][0].created_at for i in rows}, limit=limit
    )
    return [
        {
            "chunk_id": item.id,
            "document_id": str(rows[item.id][1].id),
            "document_title": rows[item.id][1].title,
            "chunk_index": rows[item.id][0].chunk_index,
            "text": rows[item.id][0].text,
            "score": round(item.score, 6),
            "ranks": item.ranks,
        }
        for item in fused
        if item.id in rows
    ]


def record_sources(
    db: Session,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    hits: list[dict[str, Any]],
    research_task_id: uuid.UUID | None = None,
) -> list[ResearchSource]:
    """Persist search hits as cited sources (deduplicated per project by checksum)."""
    out: list[ResearchSource] = []
    for hit in hits:
        checksum = str(hit.get("checksum") or sha256_bytes(str(hit.get("url") or hit.get("title")).encode()))
        existing = db.scalar(
            select(ResearchSource).where(ResearchSource.project_id == project_id, ResearchSource.checksum == checksum)
        )
        if existing is not None:
            out.append(existing)
            continue
        snippet, _ = sanitize(str(hit.get("snippet") or ""), max_chars=4000)
        injection = detect(snippet)
        retrieved = hit.get("retrieved_at")
        source = ResearchSource(
            organization_id=organization_id,
            project_id=project_id,
            research_task_id=research_task_id,
            source_type=str(hit.get("source_type") or "web")[:24],
            url=(str(hit["url"])[:2000] if hit.get("url") else None),
            title=(str(hit.get("title") or "")[:500] or None),
            publisher=(str(hit.get("publisher") or "")[:300] or None),
            authors=[str(a)[:200] for a in (hit.get("authors") or [])][:50],
            publication_date=(str(hit.get("publication_date") or "")[:32] or None),
            retrieved_at=datetime.fromisoformat(retrieved) if isinstance(retrieved, str) else datetime.now(UTC),
            citation=(str(hit.get("citation") or "")[:1000] or None),
            doi=(str(hit.get("doi") or "")[:255] or None),
            arxiv_id=(str(hit.get("arxiv_id") or "")[:64] or None),
            snippet=snippet or None,
            checksum=checksum,
            trust_metadata={
                **(hit.get("trust_metadata") or {}),
                "provider": hit.get("provider"),
                "peer_reviewed": hit.get("source_type") == "journal_article",
            },
            injection_report=injection.to_dict(),
        )
        db.add(source)
        db.flush()
        out.append(source)
    return out


def search_sources(
    db: Session,
    organization_id: uuid.UUID,
    query: str | None,
    *,
    project_ids: list[uuid.UUID] | None,
    project_id: uuid.UUID | None = None,
    limit: int = 20,
) -> Select[ResearchSource]:
    stmt = select(ResearchSource).where(ResearchSource.organization_id == organization_id)
    if project_ids is not None:
        stmt = stmt.where(ResearchSource.project_id.in_(project_ids))
    if project_id is not None:
        stmt = stmt.where(ResearchSource.project_id == project_id)
    if query:
        tsv = func.to_tsvector(
            literal_column("'english'"),
            func.coalesce(ResearchSource.title, "") + " " + func.coalesce(ResearchSource.snippet, ""),
        )
        tsq = func.plainto_tsquery(literal_column("'english'"), query)
        stmt = stmt.where(or_(tsv.op("@@")(tsq), ResearchSource.title.ilike(f"%{query[:80]}%")))
    return stmt.order_by(ResearchSource.created_at.desc())


def source_dict(s: ResearchSource) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "source_type": s.source_type,
        "title": s.title,
        "url": s.url,
        "doi": s.doi,
        "arxiv_id": s.arxiv_id,
        "authors": s.authors,
        "publication_date": s.publication_date,
        "publisher": s.publisher,
        "retrieved_at": s.retrieved_at.isoformat() if s.retrieved_at else None,
        "snippet": s.snippet,
        "checksum": s.checksum,
        "trust_metadata": s.trust_metadata,
        "injection_score": (s.injection_report or {}).get("score", 0.0),
    }
