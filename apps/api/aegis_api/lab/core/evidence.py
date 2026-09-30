"""Anchoring lab evidence in the immutable, hash-chained ``evidence`` table.

Lab evidence (experiment results, artifact checksums, evaluation outputs, reproduction verdicts, source
citations) is appended to the same append-only store the assurance platform uses. Lab records form one
hash chain per organization (``audit_id``/``system_id`` NULL, ``kind`` prefixed ``lab.``). Appends are
serialized per organization with a transaction-scoped advisory lock so the chain never forks.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.models import Evidence
from engines.evidence.hashing import chain_hash, content_hash, verify_chain

LAB_KIND_PREFIX = "lab."


def append_evidence(
    db: Session,
    *,
    organization_id: uuid.UUID,
    kind: str,
    title: str,
    content: dict[str, Any],
    source_uri: str | None = None,
    storage_key: str | None = None,
    confidence_level: str = "high",
    confidence_reasons: list[str] | None = None,
) -> Evidence:
    """Append one immutable evidence record to the organization's lab hash chain."""
    kind = kind if kind.startswith(LAB_KIND_PREFIX) else f"{LAB_KIND_PREFIX}{kind}"
    advisory_xact_lock(db, f"lab-evidence-chain:{organization_id}")
    head = db.execute(
        select(Evidence.seq, Evidence.chain_hash)
        .where(
            Evidence.organization_id == organization_id,
            Evidence.audit_id.is_(None),
            Evidence.system_id.is_(None),
            Evidence.kind.like(f"{LAB_KIND_PREFIX}%"),
        )
        .order_by(Evidence.seq.desc())
        .limit(1)
    ).first()
    prev_hash = head.chain_hash if head else None
    seq = (head.seq + 1) if head else 1
    c_hash = content_hash({"kind": kind, "title": title, "content": content, "source_uri": source_uri})
    record = Evidence(
        organization_id=organization_id,
        seq=seq,
        kind=kind[:32],
        title=title[:300],
        content=content,
        content_hash=c_hash,
        prev_hash=prev_hash,
        chain_hash=chain_hash(prev_hash, c_hash),
        confidence_level=confidence_level,
        confidence_reasons=confidence_reasons or [],
        source_uri=source_uri,
        storage_key=storage_key,
    )
    db.add(record)
    db.flush()
    return record


def verify_lab_chain(db: Session, organization_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(
        select(Evidence.content_hash, Evidence.chain_hash, Evidence.prev_hash)
        .where(
            Evidence.organization_id == organization_id,
            Evidence.audit_id.is_(None),
            Evidence.system_id.is_(None),
            Evidence.kind.like(f"{LAB_KIND_PREFIX}%"),
        )
        .order_by(Evidence.seq)
    ).all()
    return verify_chain([{"content_hash": r[0], "chain_hash": r[1], "prev_hash": r[2]} for r in rows])


def lab_evidence_count(db: Session, organization_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count(Evidence.id)).where(
                Evidence.organization_id == organization_id, Evidence.kind.like(f"{LAB_KIND_PREFIX}%")
            )
        )
        or 0
    )
