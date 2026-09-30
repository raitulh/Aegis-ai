"""Hybrid-search rank fusion: reciprocal rank fusion, score normalization, recency decay and source quality.

The knowledge context runs a keyword query (Postgres full-text) and a semantic query (pgvector) and hands
both hit lists here. :func:`combine` fuses them into one ranked list and explains every score by its
components, so ranking is transparent and reproducible. All functions are deterministic: sources are
iterated in sorted order, non-finite scores are ignored and ties are broken by stable keys (never by
input order or hash randomization).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field

SEARCH_FUSION_VERSION = "search-fusion-1.0.0"
DEFAULT_RRF_K = 60
_TIE_DECIMALS = 12

Hits = Sequence[tuple[str, float]] | Mapping[str, float]


def _finite(value: float) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _as_pairs(hits: Hits | None) -> list[tuple[str, float]]:
    """Normalize hits to ``(id, score)`` pairs, dropping non-finite scores and keeping the best score per id."""
    if not hits:
        return []
    items = hits.items() if isinstance(hits, Mapping) else hits
    best: dict[str, float] = {}
    for hit_id, score in items:
        if not _finite(score):
            continue
        key = str(hit_id)
        if key not in best or score > best[key]:
            best[key] = float(score)
    return list(best.items())


def rank_order(hits: Hits | None) -> list[str]:
    """Ids ordered by score (desc) with ties broken by id (asc)."""
    return [hit_id for hit_id, _ in sorted(_as_pairs(hits), key=lambda item: (-item[1], item[0]))]


def normalize_scores(scores: Mapping[str, float] | Sequence[tuple[str, float]]) -> dict[str, float]:
    """Min–max normalize finite scores to ``[0, 1]``; if all scores are equal every id gets ``1.0``."""
    pairs = _as_pairs(scores)
    if not pairs:
        return {}
    values = [score for _, score in pairs]
    low, high = min(values), max(values)
    if high == low:
        return {hit_id: 1.0 for hit_id, _ in pairs}
    span = high - low
    return {hit_id: (score - low) / span for hit_id, score in pairs}


def reciprocal_rank_fusion(
    rank_lists: Mapping[str, Sequence[str]],
    k: int = DEFAULT_RRF_K,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: ``score(d) = Σ_s w_s / (k + rank_s(d))`` with 1-based ranks.

    Duplicate ids within one list count once (at their best rank). Sources missing from ``weights``
    weigh 1.0. Ties: higher score, then more sources, then better best-rank, then id.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    weights = weights or {}
    for source, weight in weights.items():
        if not _finite(weight) or weight < 0:
            raise ValueError(f"weight for source '{source}' must be a finite non-negative number")
    scores: dict[str, float] = {}
    sources: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    for source in sorted(rank_lists):
        weight = float(weights.get(source, 1.0))
        seen: set[str] = set()
        rank = 0
        for raw_id in rank_lists[source]:
            hit_id = str(raw_id)
            if hit_id in seen:
                continue
            seen.add(hit_id)
            rank += 1
            scores[hit_id] = scores.get(hit_id, 0.0) + weight / (k + rank)
            sources[hit_id] = sources.get(hit_id, 0) + 1
            best_rank[hit_id] = min(best_rank.get(hit_id, rank), rank)
    ordered = sorted(
        scores,
        key=lambda hit_id: (-round(scores[hit_id], _TIE_DECIMALS), -sources[hit_id], best_rank[hit_id], hit_id),
    )
    return [(hit_id, scores[hit_id]) for hit_id in ordered]


def _to_utc(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def recency_decay(
    published_at: datetime | date | None,
    now: datetime | date,
    half_life_days: float = 365.0,
    *,
    default: float = 0.5,
) -> float:
    """Exponential recency weight ``0.5 ** (age_days / half_life_days)`` in ``(0, 1]``.

    Future dates count as age 0 (weight 1.0). Unknown dates get ``default``. Naive datetimes are UTC.
    """
    if half_life_days <= 0 or not _finite(half_life_days):
        raise ValueError("half_life_days must be a positive finite number")
    if published_at is None:
        return default
    age_days = (_to_utc(now) - _to_utc(published_at)).total_seconds() / 86_400.0
    return float(0.5 ** (max(0.0, age_days) / half_life_days))


class FusionWeights(BaseModel):
    """Relative weight of each component of the fused score (normalized over the active components)."""

    model_config = ConfigDict(extra="forbid")

    rrf: float = Field(default=0.5, ge=0.0)
    keyword: float = Field(default=0.15, ge=0.0)
    semantic: float = Field(default=0.15, ge=0.0)
    recency: float = Field(default=0.1, ge=0.0)
    source_quality: float = Field(default=0.1, ge=0.0)
    rrf_k: int = Field(default=DEFAULT_RRF_K, gt=0)
    keyword_source_weight: float = Field(default=1.0, ge=0.0)
    semantic_source_weight: float = Field(default=1.0, ge=0.0)
    neutral: float = Field(default=0.5, ge=0.0, le=1.0, description="Value for a missing recency/quality score.")


class RankedHit(BaseModel):
    id: str
    rank: int
    score: float
    components: dict[str, float]
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    explanation: str


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def combine(
    keyword_hits: Hits | None,
    semantic_hits: Hits | None,
    *,
    recency: Mapping[str, float] | None = None,
    source_quality: Mapping[str, float] | None = None,
    weights: FusionWeights | Mapping[str, float] | None = None,
    limit: int | None = None,
) -> list[RankedHit]:
    """Fuse keyword and semantic hits into one explained ranking.

    Components (each in ``[0, 1]``): ``rrf`` (RRF over both rank lists, divided by its maximum possible
    value), ``keyword`` and ``semantic`` (min–max normalized raw scores; 0 when absent), ``recency`` and
    ``source_quality`` (as given, clamped; ``weights.neutral`` for ids missing from a supplied map). A
    component whose map is not supplied at all is inactive and its weight is dropped. The final score is
    the weight-normalized sum of active components.
    """
    fw = weights if isinstance(weights, FusionWeights) else FusionWeights.model_validate(dict(weights or {}))
    keyword_pairs, semantic_pairs = _as_pairs(keyword_hits), _as_pairs(semantic_hits)
    keyword_order, semantic_order = rank_order(keyword_pairs), rank_order(semantic_pairs)
    keyword_norm, semantic_norm = normalize_scores(keyword_pairs), normalize_scores(semantic_pairs)
    rank_lists = {"keyword": keyword_order, "semantic": semantic_order}
    source_weights = {"keyword": fw.keyword_source_weight, "semantic": fw.semantic_source_weight}
    fused = dict(reciprocal_rank_fusion(rank_lists, k=fw.rrf_k, weights=source_weights))
    rrf_max = sum(weight for source, weight in source_weights.items() if rank_lists[source]) / (fw.rrf_k + 1)
    keyword_rank = {hit_id: index + 1 for index, hit_id in enumerate(keyword_order)}
    semantic_rank = {hit_id: index + 1 for index, hit_id in enumerate(semantic_order)}

    active: dict[str, float] = {"rrf": fw.rrf, "keyword": fw.keyword, "semantic": fw.semantic}
    if recency is not None:
        active["recency"] = fw.recency
    if source_quality is not None:
        active["source_quality"] = fw.source_quality
    total_weight = sum(active.values())
    if total_weight <= 0:
        raise ValueError("at least one active component must have a positive weight")

    hits: list[tuple[str, float, dict[str, float]]] = []
    for hit_id in fused:
        components: dict[str, float] = {
            "rrf": _clamp01(fused[hit_id] / rrf_max) if rrf_max > 0 else 0.0,
            "keyword": keyword_norm.get(hit_id, 0.0),
            "semantic": semantic_norm.get(hit_id, 0.0),
        }
        if recency is not None:
            value = recency.get(hit_id)
            components["recency"] = _clamp01(value) if value is not None and _finite(value) else fw.neutral
        if source_quality is not None:
            value = source_quality.get(hit_id)
            components["source_quality"] = _clamp01(value) if value is not None and _finite(value) else fw.neutral
        score = sum(active[name] * components[name] for name in active) / total_weight
        hits.append((hit_id, score, {name: round(value, 6) for name, value in components.items()}))

    hits.sort(key=lambda item: (-round(item[1], _TIE_DECIMALS), -round(item[2]["rrf"], _TIE_DECIMALS), item[0]))
    if limit is not None:
        hits = hits[: max(0, limit)]
    ranked: list[RankedHit] = []
    for position, (hit_id, score, components) in enumerate(hits, start=1):
        kw, sem = keyword_rank.get(hit_id), semantic_rank.get(hit_id)
        ranked.append(
            RankedHit(
                id=hit_id,
                rank=position,
                score=round(score, 6),
                components=components,
                keyword_rank=kw,
                semantic_rank=sem,
                explanation=_explain(components, kw, sem),
            )
        )
    return ranked


def _explain(components: Mapping[str, float], keyword_rank: int | None, semantic_rank: int | None) -> str:
    ranks = ", ".join(
        f"{name} #{rank}" if rank is not None else f"not in {name}"
        for name, rank in (("keyword", keyword_rank), ("semantic", semantic_rank))
    )
    parts = [f"rrf={components['rrf']:.3f} ({ranks})"]
    parts.extend(
        f"{name}={components[name]:.3f}"
        for name in ("keyword", "semantic", "recency", "source_quality")
        if name in components
    )
    return "; ".join(parts)


def fuse_many(rank_lists: Mapping[str, Iterable[str]], k: int = DEFAULT_RRF_K) -> list[str]:
    """Convenience: ids ordered by unweighted RRF over any number of rank lists."""
    return [hit_id for hit_id, _ in reciprocal_rank_fusion({s: list(ids) for s, ids in rank_lists.items()}, k=k)]
