"""Lab evidence: immutable, hash-chained records scoped per mission (or per project for mission-less work).

Evidence rows are append-only (database triggers reject UPDATE/DELETE of content and hashes). Appends to a
chain are serialized with a transaction-scoped advisory lock on the chain scope, so ``seq``/``prev_hash`` are
consistent under concurrency; the unique ``(chain_scope, seq)`` index is the final backstop.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import Table, select, update
from sqlalchemy.orm import Session

from aegis_api.infrastructure.locks import advisory_xact_lock
from aegis_api.models import Evidence
from aegis_api.models.lab import Mission
from engines.evidence.hashing import chain_hash, content_hash, verify_chain

CONFIDENCE_LEVELS = ("low", "medium", "high")


def scope_for(*, mission_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None) -> str:
    if mission_id:
        return f"mission:{mission_id}"
    if project_id:
        return f"project:{project_id}"
    raise ValueError("evidence needs a mission or project scope")


def confidence_label(value: float | None) -> str:
    if value is None:
        return "low"
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"


def seal(
    db: Session,
    *,
    organization_id: uuid.UUID,
    kind: str,
    title: str,
    content: dict[str, Any],
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    confidence: float | str | None = None,
    reasons: list[str] | None = None,
    source_uri: str | None = None,
    storage_key: str | None = None,
) -> Evidence:
    """Append one evidence record to the scope's hash chain (within the caller's transaction)."""
    scope = scope_for(mission_id=mission_id, project_id=project_id)
    advisory_xact_lock(db, f"evidence:{scope}")
    last = db.execute(
        select(Evidence.seq, Evidence.chain_hash)
        .where(Evidence.chain_scope == scope)
        .order_by(Evidence.seq.desc())
        .limit(1)
    ).first()
    seq = (last.seq if last else 0) + 1
    prev = last.chain_hash if last else None
    body = {"kind": kind, "title": title, "content": content, "source_uri": source_uri, "storage_key": storage_key}
    c_hash = content_hash(body)
    ch = chain_hash(prev, c_hash)
    level = confidence if isinstance(confidence, str) else confidence_label(confidence)
    record = Evidence(
        organization_id=organization_id,
        chain_scope=scope,
        seq=seq,
        kind=kind[:32],
        title=title[:300],
        content=content,
        content_hash=c_hash,
        prev_hash=prev,
        chain_hash=ch,
        confidence_level=level if level in CONFIDENCE_LEVELS else "low",
        confidence_reasons=list(reasons or []),
        source_uri=source_uri,
        storage_key=storage_key,
    )
    db.add(record)
    db.flush()
    if mission_id:
        table = cast(Table, Mission.__table__)
        db.execute(update(table).where(table.c.id == mission_id).values(evidence_head_hash=ch, evidence_seq=seq))
    return record


def chain(db: Session, organization_id: uuid.UUID, scope: str) -> list[Evidence]:
    return list(
        db.scalars(
            select(Evidence)
            .where(Evidence.organization_id == organization_id, Evidence.chain_scope == scope)
            .order_by(Evidence.seq)
        ).all()
    )


def verify(db: Session, organization_id: uuid.UUID, scope: str) -> dict[str, Any]:
    """Recompute content and chain hashes for a scope (detects tampering even by a DB superuser)."""
    rows = chain(db, organization_id, scope)
    recomputed_ok: list[int] = []
    mismatched: list[int] = []
    for r in rows:
        body = {
            "kind": r.kind,
            "title": r.title,
            "content": r.content,
            "source_uri": r.source_uri,
            "storage_key": r.storage_key,
        }
        (recomputed_ok if content_hash(body) == r.content_hash else mismatched).append(r.seq)
    result = verify_chain(
        [{"content_hash": r.content_hash, "chain_hash": r.chain_hash, "prev_hash": r.prev_hash} for r in rows]
    )
    result["content_mismatch_seqs"] = mismatched
    result["valid"] = bool(result["valid"]) and not mismatched
    result["scope"] = scope
    return result


def records_for(db: Session, organization_id: uuid.UUID, ids: list[str]) -> list[Evidence]:
    uuids = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    if not uuids:
        return []
    return list(
        db.scalars(select(Evidence).where(Evidence.organization_id == organization_id, Evidence.id.in_(uuids))).all()
    )
