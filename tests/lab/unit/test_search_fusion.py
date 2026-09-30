"""Hybrid-search fusion: RRF, score normalization, recency decay, source quality and explanations."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from engines.lab.search import (
    FusionWeights,
    combine,
    fuse_many,
    normalize_scores,
    rank_order,
    recency_decay,
    reciprocal_rank_fusion,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# Reciprocal rank fusion
# ---------------------------------------------------------------------------------------------
def test_rrf_scores_match_formula():
    fused = dict(reciprocal_rank_fusion({"kw": ["a", "b", "c"], "sem": ["b", "a"]}, k=60))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63)


def test_rrf_ordering_and_tie_breaks():
    fused = reciprocal_rank_fusion({"kw": ["a", "b", "c"], "sem": ["b", "a"]}, k=60)
    # a and b tie on score and sources and best rank -> id order
    assert [hit for hit, _ in fused] == ["a", "b", "c"]


def test_rrf_tie_break_prefers_more_sources_then_best_rank():
    fused = reciprocal_rank_fusion({"s1": ["x", "y"], "s2": ["z"]}, k=1)
    # x: 1/2, z: 1/2 (tie) -> both one source, best rank 1 -> id order; y: 1/3
    assert [hit for hit, _ in fused] == ["x", "z", "y"]


def test_rrf_weights():
    fused = reciprocal_rank_fusion({"kw": ["a"], "sem": ["b"]}, k=60, weights={"sem": 2.0})
    assert [hit for hit, _ in fused] == ["b", "a"]
    assert dict(fused)["b"] == pytest.approx(2 / 61)


def test_rrf_duplicates_count_once():
    fused = dict(reciprocal_rank_fusion({"kw": ["a", "a", "b"]}, k=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62)


def test_rrf_is_independent_of_source_order():
    a = reciprocal_rank_fusion({"kw": ["a", "b"], "sem": ["c", "a"]})
    b = reciprocal_rank_fusion({"sem": ["c", "a"], "kw": ["a", "b"]})
    assert a == b


def test_rrf_validation():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"kw": ["a"]}, k=0)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"kw": ["a"]}, weights={"kw": -1.0})
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"kw": ["a"]}, weights={"kw": math.nan})


def test_rrf_empty():
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"kw": []}) == []


def test_fuse_many():
    assert fuse_many({"a": iter(["x", "y"]), "b": ["y"], "c": ["y", "z"]}) == ["y", "x", "z"]


# ---------------------------------------------------------------------------------------------
# Normalization and ordering
# ---------------------------------------------------------------------------------------------
def test_normalize_scores_min_max():
    assert normalize_scores({"a": 2.0, "b": 4.0, "c": 3.0}) == {"a": 0.0, "b": 1.0, "c": 0.5}


def test_normalize_scores_equal_and_non_finite():
    assert normalize_scores({"a": 5.0, "b": 5.0}) == {"a": 1.0, "b": 1.0}
    assert normalize_scores({"a": math.nan, "b": math.inf, "c": 1.0}) == {"c": 1.0}
    assert normalize_scores({}) == {}


def test_rank_order_keeps_best_score_and_breaks_ties_by_id():
    assert rank_order([("b", 1.0), ("a", 1.0), ("c", 2.0), ("b", 3.0)]) == ["b", "c", "a"]


# ---------------------------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------------------------
def test_recency_decay_half_life():
    assert recency_decay(NOW, NOW) == 1.0
    assert recency_decay(NOW - timedelta(days=365), NOW, half_life_days=365) == pytest.approx(0.5)
    assert recency_decay(NOW - timedelta(days=730), NOW, half_life_days=365) == pytest.approx(0.25)
    assert recency_decay(NOW - timedelta(days=30), NOW, half_life_days=30) == pytest.approx(0.5)


def test_recency_decay_is_monotonic():
    values = [recency_decay(NOW - timedelta(days=d), NOW) for d in (0, 10, 100, 1000)]
    assert values == sorted(values, reverse=True)


def test_recency_future_unknown_naive_and_dates():
    assert recency_decay(NOW + timedelta(days=10), NOW) == 1.0
    assert recency_decay(None, NOW) == 0.5
    assert recency_decay(None, NOW, default=0.3) == 0.3
    naive = datetime(2025, 9, 30, 12, 0)
    assert recency_decay(naive, NOW, half_life_days=365) == pytest.approx(0.5)
    assert recency_decay(date(2025, 9, 30), date(2026, 9, 30), half_life_days=365) == pytest.approx(0.5)


def test_recency_rejects_bad_half_life():
    with pytest.raises(ValueError):
        recency_decay(NOW, NOW, half_life_days=0)


# ---------------------------------------------------------------------------------------------
# combine
# ---------------------------------------------------------------------------------------------
def test_combine_hit_in_both_lists_ranks_first():
    hits = combine([("a", 12.0), ("b", 7.0)], [("b", 0.91), ("c", 0.85)])
    assert [h.id for h in hits] == ["b", "a", "c"]
    top = hits[0]
    assert top.rank == 1
    assert (top.keyword_rank, top.semantic_rank) == (2, 1)
    assert set(top.components) == {"rrf", "keyword", "semantic"}
    assert top.components["semantic"] == 1.0
    assert "keyword #2" in top.explanation and "semantic #1" in top.explanation


def test_combine_scores_are_in_unit_interval_and_sorted():
    hits = combine({"a": 1.0, "b": 0.5, "c": 0.1}, {"c": 0.9, "d": 0.2}, recency={"a": 1.0}, source_quality={"d": 1.0})
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_recency_changes_ordering_when_weighted():
    keyword = [("old", 1.0), ("new", 1.0)]
    weights = FusionWeights(rrf=0.0, keyword=0.0, semantic=0.0, recency=1.0, source_quality=0.0)
    hits = combine(keyword, None, recency={"old": 0.1, "new": 0.9}, weights=weights)
    assert [h.id for h in hits] == ["new", "old"]


def test_source_quality_changes_ordering():
    hits = combine(
        [("low", 1.0), ("high", 0.99)],
        None,
        source_quality={"low": 0.0, "high": 1.0},
        weights={"rrf": 0.1, "keyword": 0.1, "semantic": 0.0, "source_quality": 1.0},
    )
    assert hits[0].id == "high"
    assert hits[0].components["source_quality"] == 1.0


def test_missing_maps_are_inactive_and_missing_ids_neutral():
    hits = combine([("a", 1.0), ("b", 0.5)], None, recency={"a": 0.9})
    assert "recency" in hits[0].components and "source_quality" not in hits[0].components
    by_id = {h.id: h for h in hits}
    assert by_id["b"].components["recency"] == 0.5


def test_non_finite_and_out_of_range_components_are_sanitized():
    hits = combine([("a", 1.0), ("b", math.nan)], [("a", math.inf)], source_quality={"a": 7.0})
    assert [h.id for h in hits] == ["a"]
    assert hits[0].components["source_quality"] == 1.0


def test_combine_limit_and_empty():
    assert combine(None, None) == []
    hits = combine([("a", 3.0), ("b", 2.0), ("c", 1.0)], None, limit=2)
    assert [h.id for h in hits] == ["a", "b"]
    assert combine([("a", 1.0)], None, limit=0) == []


def test_combine_is_deterministic_with_ties_by_id():
    hits = combine([("z", 1.0), ("m", 1.0), ("a", 1.0)], [("z", 1.0), ("m", 1.0), ("a", 1.0)])
    # rank lists are score-desc/id-asc, so a is first everywhere
    assert [h.id for h in hits] == ["a", "m", "z"]
    assert combine({"x": 1.0, "y": 1.0}, None) == combine({"y": 1.0, "x": 1.0}, None)


def test_combine_rejects_all_zero_weights():
    with pytest.raises(ValueError):
        combine([("a", 1.0)], None, weights={"rrf": 0.0, "keyword": 0.0, "semantic": 0.0})


def test_fusion_weights_reject_unknown_keys_and_negatives():
    with pytest.raises(ValueError):
        FusionWeights.model_validate({"popularity": 1.0})
    with pytest.raises(ValueError):
        FusionWeights(rrf=-1.0)


def test_rrf_component_is_normalized_to_one_for_top_of_both_lists():
    hits = combine([("a", 1.0)], [("a", 1.0)])
    assert hits[0].components["rrf"] == 1.0
    assert hits[0].score == 1.0
