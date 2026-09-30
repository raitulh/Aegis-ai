"""Scientific memory: short-term, mission, project and organization memories with provenance.

Rules:
* Everything stored carries provenance (source, source_ref, actor) and a confidence that is *not* a measurement.
* Mission/short-term memory is working memory and may be written by agents directly.
* Durable memory (project/organization scope) from agents/workflows, or anything that looks like a prompt
  injection, is only *proposed*; the policy engine requires a human review before it becomes active. Memory
  is never auto-promoted from arbitrary agent output.
* Retrieval is hybrid (PostgreSQL full-text + pgvector cosine) fused with reciprocal-rank fusion, recency and
  confidence, and only ever returns ACTIVE, unexpired memories the caller may see.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import InvalidState, ValidationFailed
from aegis_api.models.lab import Memory, MemoryLink
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.access import get_scoped
from aegis_api.services.lab.common import Actor, sha256_bytes
from engines.lab.enums import MemoryCategory, MemoryScope, MemoryStatus
from engines.lab.search import fuse
from engines.lab.security.prompt_injection import detect, sanitize

DURABLE_SCOPES = frozenset({MemoryScope.PROJECT, MemoryScope.ORGANIZATION})
MAX_CONTENT_CHARS = 20_000


def propose(
    db: Session,
    *,
    organization_id: uuid.UUID,
    actor: Actor,
    scope: str,
    category: str,
    content: str,
    source: str,
    source_ref: dict[str, Any] | None = None,
    title: str | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    confidence: float = 0.5,
    provenance: dict[str, Any] | None = None,
    sensitivity: str = "normal",
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
    expires_at: datetime | None = None,
) -> Memory:
    scope = MemoryScope(scope).value
    category = MemoryCategory(category).value
    if scope in (MemoryScope.PROJECT, MemoryScope.MISSION) and project_id is None:
        raise ValidationFailed("project and mission memories need a project")
    if scope == MemoryScope.MISSION and mission_id is None:
        raise ValidationFailed("mission memories need a mission")
    clean, _ = sanitize(content, max_chars=MAX_CONTENT_CHARS)
    if not clean.strip():
        raise ValidationFailed("memory content is empty")
    injection = detect(clean)
    digest = sha256_bytes(f"{scope}|{project_id}|{mission_id}|{clean}".encode())
    existing = db.scalar(
        select(Memory).where(
            Memory.organization_id == organization_id,
            Memory.content_hash == digest,
            Memory.status.in_([MemoryStatus.ACTIVE, MemoryStatus.PROPOSED]),
        )
    )
    if existing is not None:
        return existing
    status = MemoryStatus.ACTIVE
    requires_review = False
    decision: dict[str, Any] | None = None
    if injection.score >= 0.8:
        status, requires_review = MemoryStatus.QUARANTINED, True
    elif scope in DURABLE_SCOPES or injection.suspicious:
        result = lab_policy.evaluate(
            db,
            organization_id=organization_id,
            action="memory.promote",
            facts=lab_policy.base_facts(
                db,
                organization_id,
                actor,
                extra={"memory": {"scope": scope, "category": category, "injection_score": injection.score}},
            ),
            actor=actor,
            resource_type="memory",
        )
        decision = result.to_dict()
        if result.denied:
            status, requires_review = MemoryStatus.REJECTED, False
        elif result.needs_approval:
            status, requires_review = MemoryStatus.PROPOSED, True
    memory = Memory(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        owner_user_id=actor.user_id,
        scope=scope,
        category=category,
        source=source[:32],
        source_ref=source_ref or {},
        title=(title or "")[:300] or None,
        content=clean,
        content_hash=digest,
        confidence=max(0.0, min(1.0, float(confidence))),
        provenance={**(provenance or {}), "actor": actor.fact(), "policy": decision},
        version=1,
        status=status,
        requires_review=requires_review,
        sensitivity=sensitivity if sensitivity in ("normal", "sensitive") else "normal",
        injection_score=injection.score,
        embedding=embedding,
        embedding_model=embedding_model if embedding is not None else None,
        expires_at=expires_at,
    )
    db.add(memory)
    db.flush()
    return memory


def review(
    db: Session, principal: Principal, memory_id: uuid.UUID | str, *, approve: bool, reason: str | None = None
) -> Memory:
    principal.require_human("memory review")
    principal.require("memory:review")
    memory = get_scoped(db, principal, Memory, memory_id, label="Memory")
    if memory.status not in (MemoryStatus.PROPOSED, MemoryStatus.QUARANTINED):
        raise InvalidState(f"Memory is {memory.status}; only proposed/quarantined memories are reviewed")
    before = memory.status
    memory.status = MemoryStatus.ACTIVE if approve else MemoryStatus.REJECTED
    memory.requires_review = False
    memory.reviewed_by_id = principal.user_id
    memory.reviewed_at = utcnow()
    audit_log.record(
        db,
        organization_id=memory.organization_id,
        action="lab.memory.reviewed",
        resource_type="memory",
        resource_id=memory.id,
        principal=principal,
        before={"status": before},
        after={"status": memory.status, "reason": reason},
    )
    return memory


def supersede(
    db: Session,
    principal: Principal,
    memory_id: uuid.UUID | str,
    *,
    content: str,
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
) -> Memory:
    principal.require("memory:write")
    old = get_scoped(db, principal, Memory, memory_id, label="Memory")
    if old.status != MemoryStatus.ACTIVE:
        raise InvalidState("Only active memories can be superseded")
    new = propose(
        db,
        organization_id=old.organization_id,
        actor=Actor.of(principal),
        scope=old.scope,
        category=old.category,
        content=content,
        source="user_revision",
        source_ref={"supersedes": str(old.id)},
        title=old.title,
        project_id=old.project_id,
        mission_id=old.mission_id,
        confidence=old.confidence,
        provenance={"supersedes": str(old.id)},
        embedding=embedding,
        embedding_model=embedding_model,
    )
    new.version = old.version + 1
    new.supersedes_id = old.id
    if new.status == MemoryStatus.ACTIVE:
        old.status = MemoryStatus.SUPERSEDED
    return new


def link(db: Session, memory: Memory, *, target_type: str, target_id: str | uuid.UUID, relation: str = "about") -> None:
    exists = db.scalar(
        select(MemoryLink.id).where(
            MemoryLink.memory_id == memory.id,
            MemoryLink.target_type == target_type,
            MemoryLink.target_id == str(target_id),
            MemoryLink.relation == relation,
        )
    )
    if exists is None:
        db.add(
            MemoryLink(
                organization_id=memory.organization_id,
                memory_id=memory.id,
                target_type=target_type[:32],
                target_id=str(target_id),
                relation=relation[:32],
            )
        )


def _visible(
    stmt: Select[Any],
    organization_id: uuid.UUID,
    *,
    project_ids: list[uuid.UUID] | None,
    project_id: uuid.UUID | None,
    mission_id: uuid.UUID | None,
    scopes: list[str] | None,
    categories: list[str] | None,
    include_all_statuses: bool = False,
) -> Select[Any]:
    stmt = stmt.where(Memory.organization_id == organization_id)
    if not include_all_statuses:
        stmt = stmt.where(
            Memory.status == MemoryStatus.ACTIVE, or_(Memory.expires_at.is_(None), Memory.expires_at > func.now())
        )
    if project_ids is not None:
        stmt = stmt.where(or_(Memory.project_id.is_(None), Memory.project_id.in_(project_ids)))
    if project_id is not None:
        stmt = stmt.where(or_(Memory.project_id.is_(None), Memory.project_id == project_id))
    if mission_id is not None:
        # Working memory of *other* missions is never mixed into this mission's context.
        stmt = stmt.where(or_(Memory.scope != MemoryScope.MISSION, Memory.mission_id == mission_id))
    elif not scopes:
        stmt = stmt.where(Memory.scope != MemoryScope.MISSION)
    if scopes:
        stmt = stmt.where(Memory.scope.in_(scopes))
    if categories:
        stmt = stmt.where(Memory.category.in_(categories))
    return stmt


def search(
    db: Session,
    organization_id: uuid.UUID,
    query: str,
    *,
    query_vector: list[float] | None = None,
    embedding_model: str | None = None,
    project_ids: list[uuid.UUID] | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    scopes: list[str] | None = None,
    categories: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    k = max(1, min(limit, 50)) * 3

    def visible(stmt: Select[Any]) -> Select[Any]:
        return _visible(
            stmt,
            organization_id,
            project_ids=project_ids,
            project_id=project_id,
            mission_id=mission_id,
            scopes=scopes,
            categories=categories,
        )

    tsv = func.to_tsvector(literal_column("'english'"), func.coalesce(Memory.title, "") + " " + Memory.content)
    tsq = func.plainto_tsquery(literal_column("'english'"), query)
    keyword = [
        str(r[0])
        for r in db.execute(
            visible(select(Memory.id)).where(tsv.op("@@")(tsq)).order_by(func.ts_rank(tsv, tsq).desc()).limit(k)
        ).all()
    ]
    vector: list[str] = []
    if query_vector is not None and embedding_model:
        vector = [
            str(r[0])
            for r in db.execute(
                visible(select(Memory.id))
                .where(Memory.embedding.is_not(None), Memory.embedding_model == embedding_model)
                .order_by(Memory.embedding.cosine_distance(query_vector))
                .limit(k)
            ).all()
        ]
    ids = list(dict.fromkeys(keyword + vector))
    if not ids:
        return []
    rows = {str(m.id): m for m in db.scalars(select(Memory).where(Memory.id.in_([uuid.UUID(i) for i in ids]))).all()}
    fused = fuse(
        {"keyword": keyword, "vector": vector},
        created_at={i: rows[i].created_at for i in rows},
        confidence={i: rows[i].confidence for i in rows},
        limit=limit,
    )
    out = []
    for item in fused:
        m = rows.get(item.id)
        if m is None:
            continue
        out.append({**memory_dict(m), "score": round(item.score, 6), "ranks": item.ranks})
    return out


def list_memories(
    db: Session,
    organization_id: uuid.UUID,
    *,
    project_ids: list[uuid.UUID] | None,
    status: str | None = None,
    scope: str | None = None,
    project_id: uuid.UUID | None = None,
) -> Select[Memory]:
    stmt = select(Memory).where(Memory.organization_id == organization_id)
    if project_ids is not None:
        stmt = stmt.where(or_(Memory.project_id.is_(None), Memory.project_id.in_(project_ids)))
    if status:
        stmt = stmt.where(Memory.status == status)
    if scope:
        stmt = stmt.where(Memory.scope == scope)
    if project_id:
        stmt = stmt.where(Memory.project_id == project_id)
    return stmt.order_by(Memory.created_at.desc())


def purge_expired(db: Session, *, limit: int = 500) -> int:
    """Short-term memories past their expiry are removed (they carry no evidentiary value)."""
    result = db.execute(
        text(
            "DELETE FROM lab.memories WHERE id IN (SELECT id FROM lab.memories WHERE scope = 'short_term' "
            "AND expires_at IS NOT NULL AND expires_at < now() LIMIT :n)"
        ),
        {"n": limit},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def memory_dict(m: Memory) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "scope": m.scope,
        "category": m.category,
        "title": m.title,
        "content": m.content,
        "confidence": m.confidence,
        "source": m.source,
        "source_ref": m.source_ref,
        "status": m.status,
        "project_id": str(m.project_id) if m.project_id else None,
        "mission_id": str(m.mission_id) if m.mission_id else None,
        "version": m.version,
        "injection_score": m.injection_score,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
