"""Document ingestion: artifact/source bytes → parsed, chunked, scanned, embedded and graph-linked documents.

Pipeline (the pure steps live in :mod:`engines.lab.ingestion`)::

    prepare (authorize, idempotency, versioning)  →  read bytes  →  detect → parse → chunk → entities
    → prompt-injection scan (document + every chunk)  →  persist document + chunks  →  embeddings → graph

* Idempotent: re-ingesting the same bytes (same checksum) for the same source or artifact returns the existing
  document; new bytes for the same source create the next ``version``.
* Documents are untrusted data. A document whose injection risk reaches the quarantine threshold is stored as
  ``quarantined``: its chunks are kept for reviewers but are neither embedded nor searchable, and it adds
  nothing to the knowledge graph.
* The worker path (:func:`ingest_artifact_version_job` / :func:`ingest_source_job`) holds no transaction while
  reading object storage, parsing or embedding; :func:`ingest_artifact_version` does everything with the
  caller's session (convenient for synchronous callers — local embedders only run inline).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import NotFound, PayloadTooLarge, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_by_id
from aegis_api.lab.knowledge import embeddings
from aegis_api.lab.knowledge.embeddings import EmbeddingItem
from aegis_api.lab.models import Project, ResearchSource, SourceChunk, SourceDocument
from aegis_api.schemas.common import Page, PageParams
from engines.lab.ingestion import (
    DETECT_HEAD_BYTES,
    Chunk,
    ExtractedEntities,
    ParsedDocument,
    UnsupportedDocumentError,
    chunk,
    detect_type,
    extract_entities,
    parse,
)
from engines.lab.ingestion.chunker import DEFAULT_OVERLAP_TOKENS, DEFAULT_TARGET_TOKENS
from engines.lab.ingestion.entities import ENTITY_ENGINE_VERSION
from engines.lab.ingestion.parsers import INGESTION_PARSER_VERSION
from engines.lab.prompt_security import QUARANTINE_THRESHOLD, InjectionScan, scan_for_injection

log = structlog.get_logger("aegis.lab.knowledge.ingestion")

MAX_TEXT_CHARS = 1_000_000
MAX_CHUNKS = 5000
MAX_GRAPH_ENTITIES = 25
MAX_FINDINGS = 50
INDEXED = "indexed"
QUARANTINED = "quarantined"
FAILED = "failed"


@dataclass(frozen=True)
class PreparedIngestion:
    """Everything the pipeline needs after authorization (no ORM objects: safe to carry across transactions)."""

    organization_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    checksum: str
    size_bytes: int
    source_id: uuid.UUID | None = None
    artifact_version_id: uuid.UUID | None = None
    storage_key: str | None = None
    source_type: str | None = None


@dataclass
class PipelineResult:
    detected_type: str
    parsed: ParsedDocument | None
    chunks: list[Chunk] = field(default_factory=list)
    chunk_scans: list[InjectionScan] = field(default_factory=list)
    entities: ExtractedEntities | None = None
    risk_score: float = 0.0
    findings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def quarantined(self) -> bool:
        return self.error is None and self.risk_score >= QUARANTINE_THRESHOLD

    @property
    def status(self) -> str:
        if self.error is not None:
            return FAILED
        return QUARANTINED if self.quarantined else INDEXED


# --- pure pipeline --------------------------------------------------------------------------------
def run_pipeline(data: bytes, *, filename: str, mime_type: str) -> PipelineResult:
    """Detect → parse → chunk → entities → injection scan (pure CPU work, no IO)."""
    kind = detect_type(filename, mime_type, data[: DETECT_HEAD_BYTES + 1])
    if kind == "unsupported":
        return PipelineResult(detected_type=kind, parsed=None, error="Unsupported document type")
    try:
        parsed = parse(kind, data, max_chars=MAX_TEXT_CHARS, max_bytes=get_settings().max_lab_upload_bytes)
    except UnsupportedDocumentError as exc:
        return PipelineResult(detected_type=kind, parsed=None, error=str(exc)[:500])
    if not parsed.text.strip():
        return PipelineResult(detected_type=kind, parsed=parsed, error="No text could be extracted")
    pieces = chunk(parsed.text, sections=parsed.sections)[:MAX_CHUNKS]
    doc_scan = scan_for_injection(parsed.text)
    chunk_scans = [scan_for_injection(piece.text) for piece in pieces]
    risk = max([doc_scan.risk_score, *(s.risk_score for s in chunk_scans)], default=0.0)
    findings: list[dict[str, Any]] = [
        {**finding.as_dict(), "scope": "document"} for finding in doc_scan.findings[:MAX_FINDINGS]
    ]
    for piece, scan in zip(pieces, chunk_scans, strict=True):
        if scan.quarantine and len(findings) < MAX_FINDINGS:
            findings.append({"scope": f"chunk:{piece.seq}", "risk_score": scan.risk_score, "rules": scan.rules[:10]})
    return PipelineResult(
        detected_type=kind,
        parsed=parsed,
        chunks=pieces,
        chunk_scans=chunk_scans,
        entities=extract_entities(parsed.text),
        risk_score=risk,
        findings=findings,
    )


# --- preparation ----------------------------------------------------------------------------------
def _load_source(
    db: Session, actor: Actor, source_id: uuid.UUID | str | None, project: Project
) -> ResearchSource | None:
    if source_id is None:
        return None
    source = get_owned(db, ResearchSource, source_id, actor, label="Source")
    if source.project_id != project.id:
        raise ValidationFailed("The source belongs to a different project")
    return source


def find_existing(
    db: Session,
    project_id: uuid.UUID,
    checksum: str,
    *,
    source_id: uuid.UUID | None,
    artifact_version_id: uuid.UUID | None,
) -> SourceDocument | None:
    """The document an identical re-ingestion must return (idempotency)."""
    if source_id is not None:
        latest = db.scalar(
            select(SourceDocument)
            .where(SourceDocument.source_id == source_id)
            .order_by(SourceDocument.version.desc())
            .limit(1)
        )
        return latest if latest is not None and latest.checksum == checksum else None
    stmt = select(SourceDocument).where(
        SourceDocument.project_id == project_id,
        SourceDocument.source_id.is_(None),
        SourceDocument.checksum == checksum,
    )
    if artifact_version_id is not None:
        by_version = db.scalar(stmt.where(SourceDocument.artifact_version_id == artifact_version_id).limit(1))
        if by_version is not None:
            return by_version
    return db.scalar(stmt.order_by(SourceDocument.created_at).limit(1))


def prepare_artifact_ingestion(
    db: Session,
    actor: Actor,
    artifact_version_id: uuid.UUID | str,
    *,
    source_id: uuid.UUID | str | None = None,
    title: str | None = None,
) -> PreparedIngestion | SourceDocument:
    """Authorize (``memory:write`` + ``artifact:download``) and return the existing document when this exact
    content was already ingested, else what the pipeline needs."""
    from aegis_api.lab.data.artifacts import ArtifactQuarantined, get_artifact_version
    from aegis_api.lab.models import Artifact

    version = get_artifact_version(db, actor, artifact_version_id, permission="artifact:download")
    project = load_project(db, actor, version.project_id, "memory:write")
    if version.scan_status == "infected":
        raise ArtifactQuarantined()
    limit = get_settings().max_lab_upload_bytes
    if version.size_bytes > limit:
        raise PayloadTooLarge(f"Artifact version is {version.size_bytes} bytes, above the {limit} byte ingestion limit")
    source = _load_source(db, actor, source_id, project)
    existing = find_existing(
        db,
        project.id,
        version.checksum,
        source_id=source.id if source else None,
        artifact_version_id=version.id,
    )
    if existing is not None:
        return existing
    artifact = db.get(Artifact, version.artifact_id)
    name = title or (source.title if source else None) or (artifact.name if artifact else None)
    return PreparedIngestion(
        organization_id=actor.organization_id,
        project_id=project.id,
        workspace_id=project.workspace_id,
        title=" ".join((name or version.original_filename or "Document").split())[:1000],
        filename=version.original_filename or "document",
        mime_type=version.mime_type,
        checksum=version.checksum,
        size_bytes=version.size_bytes,
        source_id=source.id if source else None,
        artifact_version_id=version.id,
        storage_key=version.storage_key,
        source_type=source.source_type if source else None,
    )


def read_prepared_bytes(prepared: PreparedIngestion) -> bytes:
    """Read the artifact bytes (outside any transaction) and verify size and checksum."""
    from aegis_api.lab.data.artifacts import ArtifactIntegrityError
    from aegis_api.lab.storage import get_storage

    if prepared.storage_key is None:
        raise ValidationFailed("Nothing to read for this document")
    data = get_storage().get_bytes(prepared.storage_key, prepared.size_bytes)
    if len(data) != prepared.size_bytes or hashlib.sha256(data).hexdigest() != prepared.checksum:
        log.error("ingestion_integrity_mismatch", artifact_version_id=str(prepared.artifact_version_id))
        raise ArtifactIntegrityError("Artifact bytes failed the integrity check")
    return data


def source_markdown(source: ResearchSource) -> str | None:
    """A metadata document for a source (title, authors, venue, identifiers, abstract); ``None`` without an
    abstract (nothing worth indexing)."""
    if not source.abstract:
        return None
    lines = [f"# {source.title}", ""]
    if source.authors:
        lines.append(f"Authors: {', '.join(str(a) for a in list(source.authors)[:30])}")
    if source.publication_date:
        lines.append(f"Published: {source.publication_date.isoformat()}")
    if source.publisher:
        lines.append(f"Venue: {source.publisher}")
    if source.doi:
        lines.append(f"DOI: {source.doi}")
    if source.url:
        lines.append(f"URL: {source.url}")
    lines.extend(["", "## Abstract", "", source.abstract.strip(), ""])
    return "\n".join(lines)


def prepare_source_ingestion(
    db: Session, actor: Actor, source_id: uuid.UUID | str
) -> tuple[PreparedIngestion | SourceDocument | None, bytes | None]:
    """(prepared, bytes) for a source's metadata document, the existing document, or ``(None, None)`` when
    the source has nothing to index."""
    source = get_owned(db, ResearchSource, source_id, actor, label="Source")
    project = load_project(db, actor, source.project_id, "memory:write")
    text = source_markdown(source)
    if text is None:
        return None, None
    data = text.encode("utf-8")
    checksum = hashlib.sha256(data).hexdigest()
    existing = find_existing(db, project.id, checksum, source_id=source.id, artifact_version_id=None)
    if existing is not None:
        return existing, None
    return (
        PreparedIngestion(
            organization_id=actor.organization_id,
            project_id=project.id,
            workspace_id=project.workspace_id,
            title=source.title[:1000],
            filename=f"source-{source.id}.md",
            mime_type="text/markdown",
            checksum=checksum,
            size_bytes=len(data),
            source_id=source.id,
            source_type=source.source_type,
        ),
        data,
    )


# --- persistence ----------------------------------------------------------------------------------
def _document_node_type(prepared: PreparedIngestion, result: PipelineResult) -> str:
    if prepared.source_type in ("paper", "preprint") or result.detected_type == "pdf":
        return "Paper"
    if result.detected_type in ("csv", "tsv", "json", "jsonl"):
        return "Dataset"
    return "Paper"


def _link_graph(
    db: Session, actor: Actor, document: SourceDocument, prepared: PreparedIngestion, result: PipelineResult
) -> int:
    """Document node + entity nodes (uses) + cited papers (derived_from). Returns the number of edges."""
    from aegis_api.lab.knowledge.graph import get_graph

    graph = get_graph(db, actor, required=False)
    if graph is None or result.entities is None:
        return 0
    entities = result.entities
    doc_type = _document_node_type(prepared, result)
    doc_key = f"source:{prepared.source_id}" if prepared.source_id else f"document:{document.checksum}"
    if prepared.source_id is not None:
        from aegis_api.lab.research.sources import graph_key

        source = db.get(ResearchSource, prepared.source_id)
        if source is not None:
            doc_key = graph_key(source)
    doc_node = graph.upsert_node(
        doc_type,
        doc_key,
        document.title,
        ref_type="research_source" if prepared.source_id else "source_document",
        ref_id=prepared.source_id or document.id,
        project_id=document.project_id,
        properties={"document_id": str(document.id), "detected_type": document.detected_type},
    )
    provenance = {
        "source_document_id": str(document.id),
        "extraction": ENTITY_ENGINE_VERSION,
        "method": "deterministic entity extraction",
    }
    edges = 0
    for node_type, names in (("Method", entities.methods), ("Dataset", entities.datasets), ("Model", entities.models)):
        for name in names[:MAX_GRAPH_ENTITIES]:
            node = graph.upsert_node(
                node_type, f"entity:{name.casefold()}", name, project_id=document.project_id, properties={"name": name}
            )
            if node.id != doc_node.id:
                graph.link(doc_node, node, "uses", confidence=0.6, provenance=provenance)
                edges += 1
    for doi in entities.dois[:MAX_GRAPH_ENTITIES]:
        node = graph.upsert_node(
            "Paper", f"doi:{doi.lower()}", doi, project_id=document.project_id, properties={"doi": doi}
        )
        if node.id != doc_node.id:
            graph.link(doc_node, node, "derived_from", confidence=0.5, provenance=provenance)
            edges += 1
    return edges


def persist_document(
    db: Session,
    actor: Actor,
    prepared: PreparedIngestion,
    result: PipelineResult,
    *,
    vectors: Sequence[Sequence[float]] | None = None,
    embedding_model: str | None = None,
) -> SourceDocument:
    """Store the document + chunks (+ embeddings, graph) in the caller's transaction. Idempotent."""
    advisory_xact_lock(db, f"ingest:{prepared.project_id}:{prepared.source_id or prepared.checksum}")
    existing = find_existing(
        db,
        prepared.project_id,
        prepared.checksum,
        source_id=prepared.source_id,
        artifact_version_id=prepared.artifact_version_id,
    )
    if existing is not None:
        return existing
    version = 1
    if prepared.source_id is not None:
        current = db.scalar(
            select(func.max(SourceDocument.version)).where(SourceDocument.source_id == prepared.source_id)
        )
        version = int(current or 0) + 1
    parsed = result.parsed
    parse_metadata: dict[str, Any] = {
        "parser_version": INGESTION_PARSER_VERSION,
        "detected_type": result.detected_type,
        "chunker": {"target_tokens": DEFAULT_TARGET_TOKENS, "overlap_tokens": DEFAULT_OVERLAP_TOKENS},
        "injection_risk": round(result.risk_score, 3),
        "artifact_checksum": prepared.checksum,
        "ingested_by": actor.as_dict(),
    }
    if parsed is not None:
        parse_metadata.update(
            {
                "warnings": parsed.warnings[:50],
                "truncated": parsed.truncated,
                "metadata": parsed.metadata,
                "sections": len(parsed.sections),
                "parsed_title": parsed.title,
            }
        )
    document = SourceDocument(
        id=uuid.uuid4(),
        organization_id=prepared.organization_id,
        workspace_id=prepared.workspace_id,
        project_id=prepared.project_id,
        source_id=prepared.source_id,
        artifact_version_id=prepared.artifact_version_id,
        title=prepared.title or (parsed.title if parsed else None) or prepared.filename,
        mime_type=prepared.mime_type[:120],
        detected_type=result.detected_type,
        version=version,
        checksum=prepared.checksum,
        size_bytes=prepared.size_bytes,
        storage_key=prepared.storage_key,
        status=result.status,
        parse_metadata=parse_metadata,
        entities=result.entities.model_dump(mode="json") if result.entities else {},
        injection_findings=result.findings,
        chunk_count=len(result.chunks),
        error=result.error,
        created_by_id=actor.user_id,
    )
    db.add(document)
    db.flush()
    chunk_rows: list[SourceChunk] = []
    for piece, scan in zip(result.chunks, result.chunk_scans, strict=True):
        row = SourceChunk(
            id=uuid.uuid4(),
            organization_id=prepared.organization_id,
            document_id=document.id,
            project_id=prepared.project_id,
            seq=piece.seq,
            text=piece.text,
            token_count=piece.token_count,
            char_start=piece.char_start,
            char_end=piece.char_end,
            chunk_metadata={"heading": piece.heading, "injection_risk": scan.risk_score},
            content_hash=hashlib.sha256(piece.text.encode("utf-8")).hexdigest(),
        )
        chunk_rows.append(row)
    db.add_all(chunk_rows)
    db.flush()
    if document.status == INDEXED and chunk_rows:
        items = [EmbeddingItem(owner_id=row.id, text=row.text) for row in chunk_rows]
        if vectors is not None and embedding_model is not None:
            embeddings.store_vectors(
                db,
                organization_id=prepared.organization_id,
                owner_type="source_chunk",
                project_id=prepared.project_id,
                model=embedding_model,
                items=items,
                vectors=vectors,
            )
        else:
            embeddings.index_texts(db, actor, "source_chunk", items, prepared.project_id)
        edges = _link_graph(db, actor, document, prepared, result)
        document.parse_metadata = {**document.parse_metadata, "graph_edges": edges}
    if prepared.source_id is not None:
        source = db.get(ResearchSource, prepared.source_id)
        if source is not None:
            source.status = (
                "quarantined"
                if document.status == QUARANTINED
                else ("failed" if document.status == FAILED else "ingested")
            )
            if prepared.artifact_version_id is not None:
                source.content_artifact_id = prepared.artifact_version_id
    db.flush()
    log.info(
        "document_ingested",
        document_id=str(document.id),
        status=document.status,
        chunks=document.chunk_count,
        version=document.version,
    )
    return document


# --- entry points ---------------------------------------------------------------------------------
def ingest_artifact_version(
    db: Session,
    actor: Actor,
    artifact_version_id: uuid.UUID | str,
    *,
    source_id: uuid.UUID | str | None = None,
    title: str | None = None,
) -> SourceDocument:
    """Ingest an artifact version with the caller's session (all steps inline)."""
    prepared = prepare_artifact_ingestion(db, actor, artifact_version_id, source_id=source_id, title=title)
    if isinstance(prepared, SourceDocument):
        return prepared
    data = read_prepared_bytes(prepared)
    result = run_pipeline(data, filename=prepared.filename, mime_type=prepared.mime_type)
    return persist_document(db, actor, prepared, result)


def _vectors_for(prepared: PreparedIngestion, result: PipelineResult) -> tuple[list[list[float]] | None, str | None]:
    """Embeddings computed outside any transaction (worker path)."""
    if result.status != INDEXED or not result.chunks:
        return None, None
    embedder = embeddings.resolve_embedder(prepared.organization_id)
    return embeddings.compute_vectors(embedder, [c.text for c in result.chunks]), embedder.name


def ingest_artifact_version_job(
    actor: Actor,
    artifact_version_id: uuid.UUID | str,
    *,
    project_id: uuid.UUID | str | None = None,
    source_id: uuid.UUID | str | None = None,
    title: str | None = None,
) -> SourceDocument | None:
    """Worker path: short transactions around object-storage reads, parsing and embedding.

    Returns the document detached from its session (attributes loaded)."""
    with tenant_uow(actor) as db:
        prepared = prepare_artifact_ingestion(db, actor, artifact_version_id, source_id=source_id, title=title)
        if project_id is not None and str(prepared.project_id) != str(project_id):
            raise ValidationFailed("The artifact version belongs to a different project")
        if isinstance(prepared, SourceDocument):
            db.expunge(prepared)
            return prepared
    data = read_prepared_bytes(prepared)
    result = run_pipeline(data, filename=prepared.filename, mime_type=prepared.mime_type)
    vectors, model = _vectors_for(prepared, result)
    with tenant_uow(actor) as db:
        document = persist_document(db, actor, prepared, result, vectors=vectors, embedding_model=model)
        db.expunge(document)
        return document


def ingest_source_job(actor: Actor, source_id: uuid.UUID | str) -> SourceDocument | None:
    """Worker path for a source: its content artifact when present, else its metadata document."""
    with tenant_uow(actor) as db:
        source = get_owned(db, ResearchSource, source_id, actor, label="Source")
        content_version = source.content_artifact_id
        if content_version is None:
            prepared, data = prepare_source_ingestion(db, actor, source.id)
            if isinstance(prepared, SourceDocument):
                db.expunge(prepared)
                return prepared
        else:
            prepared, data = None, None
    if content_version is not None:
        return ingest_artifact_version_job(actor, content_version, source_id=source_id)
    if prepared is None or data is None:
        return None
    result = run_pipeline(data, filename=prepared.filename, mime_type=prepared.mime_type)
    vectors, model = _vectors_for(prepared, result)
    with tenant_uow(actor) as db:
        document = persist_document(db, actor, prepared, result, vectors=vectors, embedding_model=model)
        db.expunge(document)
        return document


# --- reads ----------------------------------------------------------------------------------------
def get_document(db: Session, actor: Actor, document_id: uuid.UUID | str) -> SourceDocument:
    document = get_owned(db, SourceDocument, document_id, actor, label="Document")
    load_project(db, actor, document.project_id, "memory:read")
    return document


def list_documents(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | str | None = None,
    source_id: uuid.UUID | str | None = None,
    status: str | None = None,
    mapper: Any = None,
) -> Page[Any]:
    stmt = select(SourceDocument).where(SourceDocument.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "memory:read")
        stmt = stmt.where(SourceDocument.project_id == project.id)
    else:
        actor.require("memory:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(SourceDocument.project_id.in_(visible))
    if source_id is not None:
        try:
            stmt = stmt.where(SourceDocument.source_id == uuid.UUID(str(source_id)))
        except ValueError as exc:
            raise NotFound("Source not found") from exc
    if status is not None:
        if status not in ("uploaded", "parsed", INDEXED, FAILED, QUARANTINED):
            raise ValidationFailed("Unknown document status")
        stmt = stmt.where(SourceDocument.status == status)
    stmt = stmt.order_by(SourceDocument.created_at.desc(), SourceDocument.id.desc())
    return paginate(db, stmt, params, mapper or (lambda d: d))


def list_chunks(
    db: Session, actor: Actor, document_id: uuid.UUID | str, params: CursorParams, *, mapper: Any = None
) -> CursorPage[Any]:
    """Chunks in order. Chunks of a quarantined document are only shown to memory reviewers."""
    from aegis_api.errors import Forbidden
    from aegis_api.lab.knowledge.memory import can_review

    document = get_document(db, actor, document_id)
    if document.status == QUARANTINED and not can_review(db, actor, db.get(Project, document.project_id)):
        raise Forbidden("This document is quarantined; only memory reviewers can read its content")
    stmt = select(SourceChunk).where(SourceChunk.document_id == document.id)
    return paginate_by_id(db, stmt, params, id_col=SourceChunk.seq, mapper=mapper or (lambda c: c))
