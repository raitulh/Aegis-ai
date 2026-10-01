"""PostgreSQL full-text search over the denormalized `search_documents` index.

The index only contains public content plus organization-scoped rows, which are filtered by the
viewer's memberships at query time. Highlights use sentinel markers ([[ ]]) that the client converts
to <mark> after escaping, so no server-generated HTML is trusted.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.deps import Actor
from app.core.pagination import PageParams
from app.models.system import SearchDocument

ENTITY_TYPES = ("competition", "dataset", "project", "course", "organization", "user", "thread")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _visibility(actor: Actor) -> Any:
    clause = SearchDocument.visibility == "public"
    orgs = actor.member_org_ids() if actor.is_authenticated else []
    if orgs:
        clause = or_(clause, (SearchDocument.visibility == "org") & SearchDocument.org_id.in_(orgs))
    return clause


def _prefix_query(q: str) -> Any | None:
    words = _WORD_RE.findall(q)[:6]
    if not words:
        return None
    return func.to_tsquery("english", " & ".join(f"{w.lower()}:*" for w in words))


def search(actor: Actor, q: str, params: PageParams, *, entity_type: str | None) -> dict[str, Any]:
    db = actor.db
    q = q.strip()[:200]
    if len(q) < 2:
        return {"items": [], "total": 0, "page": params.page, "page_size": params.page_size, "has_next": False, "facets": {}, "query": q}
    for tsq in (func.websearch_to_tsquery("english", q), _prefix_query(q)):
        if tsq is None:
            continue
        base = select(SearchDocument).where(SearchDocument.tsv.op("@@")(tsq), _visibility(actor))
        facets = dict(db.execute(select(SearchDocument.entity_type, func.count()).where(SearchDocument.tsv.op("@@")(tsq), _visibility(actor))
                                 .group_by(SearchDocument.entity_type)).all())
        if not facets:
            continue
        stmt = base.where(SearchDocument.entity_type == entity_type) if entity_type else base
        total = facets.get(entity_type, 0) if entity_type else sum(facets.values())
        rank = func.ts_rank_cd(SearchDocument.tsv, tsq)
        headline = func.ts_headline("english", func.left(SearchDocument.body, 3000), tsq,
                                    literal("StartSel=[[, StopSel=]], MaxWords=30, MinWords=12, MaxFragments=2, FragmentDelimiter= … "))
        rows = db.execute(select(SearchDocument, rank.label("rank"), headline.label("snippet"))
                          .where(SearchDocument.id.in_(stmt.with_only_columns(SearchDocument.id).scalar_subquery()))
                          .order_by(rank.desc(), SearchDocument.updated_at.desc())
                          .limit(params.page_size).offset(params.offset)).all()
        return {
            "items": [{"type": d.entity_type, "id": d.entity_id, "title": d.title, "subtitle": d.subtitle, "url": d.url,
                       "snippet": snippet or "", "tags": list(d.tags or []), "score": round(float(r), 4)} for d, r, snippet in rows],
            "total": total, "page": params.page, "page_size": params.page_size, "has_next": params.offset + len(rows) < total,
            "facets": facets, "query": q,
        }
    return {"items": [], "total": 0, "page": params.page, "page_size": params.page_size, "has_next": False, "facets": {}, "query": q}


def suggest(actor: Actor, q: str, limit: int = 8) -> list[dict[str, Any]]:
    db: Session = actor.db
    tsq = _prefix_query(q.strip()[:100])
    if tsq is None:
        return []
    rows = db.scalars(select(SearchDocument).where(SearchDocument.tsv.op("@@")(tsq), _visibility(actor))
                      .order_by(func.ts_rank_cd(SearchDocument.tsv, tsq).desc()).limit(limit)).all()
    return [{"type": d.entity_type, "title": d.title, "subtitle": d.subtitle, "url": d.url} for d in rows]
