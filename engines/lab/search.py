"""Hybrid retrieval scoring: reciprocal rank fusion of keyword + semantic rankings, recency decay and source
quality weighting. Permission filtering happens *before* ranking, in the database query (RLS + scope filters),
so nothing the principal cannot access is ever scored or returned."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_SOURCE_QUALITY: dict[str, float] = {
    "verified_result": 1.0,
    "peer_reviewed": 0.9,
    "internal_experiment": 0.85,
    "preprint": 0.7,
    "dataset_documentation": 0.7,
    "user_upload": 0.6,
    "web": 0.5,
    "agent_generated": 0.4,
    "unknown": 0.5,
}


@dataclass(frozen=True)
class ScoredItem:
    id: str
    score: float
    rrf: float
    recency: float
    quality: float
    ranks: dict[str, int]


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]], *, k: int = 60, weights: Mapping[str, float] | None = None
) -> dict[str, tuple[float, dict[str, int]]]:
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ranking in rankings.items():
        w = (weights or {}).get(name, 1.0)
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + position)
            ranks.setdefault(item, {})[name] = position
    return {item: (score, ranks[item]) for item, score in scores.items()}


def recency_weight(created_at: datetime | None, *, half_life_days: float = 180.0, now: datetime | None = None) -> float:
    if created_at is None:
        return 0.5
    now = now or datetime.now(UTC)
    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / half_life_days)


def fuse(
    rankings: Mapping[str, Sequence[str]],
    *,
    created_at: Mapping[str, datetime | None] | None = None,
    source_kind: Mapping[str, str] | None = None,
    confidence: Mapping[str, float] | None = None,
    recency_importance: float = 0.2,
    quality_importance: float = 0.3,
    quality_table: Mapping[str, float] | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> list[ScoredItem]:
    table = quality_table or DEFAULT_SOURCE_QUALITY
    fused = reciprocal_rank_fusion(rankings)
    if not fused:
        return []
    max_rrf = max(v[0] for v in fused.values())
    out: list[ScoredItem] = []
    for item, (rrf, ranks) in fused.items():
        rec = recency_weight((created_at or {}).get(item), now=now)
        quality = table.get((source_kind or {}).get(item, "unknown"), table["unknown"])
        conf = (confidence or {}).get(item)
        if conf is not None and not math.isnan(conf):
            quality = quality * (0.5 + 0.5 * max(0.0, min(conf, 1.0)))
        base = rrf / max_rrf
        score = (
            base * (1 - recency_importance - quality_importance)
            + rec * recency_importance
            + quality * quality_importance
        )
        out.append(ScoredItem(item, round(score, 6), rrf, round(rec, 4), round(quality, 4), ranks))
    out.sort(key=lambda s: (-s.score, s.id))
    return out[:limit]
