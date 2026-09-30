"""Baseline vs candidate comparison: direction-aware verdicts, tests, corrections, edge cases."""

from __future__ import annotations

import math

import pytest

from engines.lab.comparison import (
    COMPARISON_ENGINE_VERSION,
    MetricSamples,
    align_by_seed,
    compare_many,
    compare_samples,
)
from engines.lab.experiment_spec import StatisticalPlan

BASE = [0.80, 0.81, 0.79, 0.80, 0.82]
BETTER = [0.86, 0.87, 0.85, 0.88, 0.86]
LAT_BASE = [120.0, 118.0, 125.0, 121.0, 119.0]
LAT_FASTER = [100.0, 98.0, 103.0, 101.0, 99.0]
PLAN = StatisticalPlan(n_seeds=5)


def test_improved_for_maximize_metric():
    r = compare_samples(BASE, BETTER, metric="accuracy", direction="maximize", plan=PLAN)
    s = r.statistics
    assert r.verdict == "improved"
    assert r.engine_version == COMPARISON_ENGINE_VERSION == "comparison-1.0.0"
    assert s.test == "welch_t" and s.effect_size_kind == "hedges_g" and s.effect_size is not None and s.effect_size > 0
    assert s.delta == pytest.approx(0.06) and s.improvement == pytest.approx(0.06)
    assert s.relative_delta == pytest.approx(0.06 / 0.804)
    assert s.p_value is not None and s.p_value < 0.001 and s.p_value_adjusted == s.p_value and s.family_size == 1
    assert s.ci_low is not None and s.ci_low > 0 and r.ci_excludes_zero_in_improving_direction
    assert "Verdict: improved" in r.rationale and "Welch" in r.rationale


def test_direction_aware_minimize_metric():
    faster = compare_samples(LAT_BASE, LAT_FASTER, metric="latency_ms", direction="minimize", plan=PLAN)
    assert (
        faster.verdict == "improved" and faster.statistics.improvement is not None and faster.statistics.improvement > 0
    )
    assert faster.ci_excludes_zero_in_improving_direction  # ci_high < 0 for a minimize metric
    slower = compare_samples(LAT_FASTER, LAT_BASE, metric="latency_ms", direction="minimize", plan=PLAN)
    assert slower.verdict == "regressed"
    worse_acc = compare_samples(BETTER, BASE, metric="accuracy", direction="maximize", plan=PLAN)
    assert worse_acc.verdict == "regressed"


def test_no_significant_difference():
    a = [0.80, 0.83, 0.78, 0.81, 0.79]
    b = [0.81, 0.79, 0.82, 0.80, 0.80]
    r = compare_samples(a, b, metric="accuracy", direction="maximize", plan=PLAN)
    assert r.verdict == "no_significant_difference"
    assert r.statistics.p_value is not None and r.statistics.p_value > 0.05


def test_insufficient_data_by_n_and_plan():
    r = compare_samples([0.8], [0.9, 0.91], metric="acc", direction="maximize")
    assert r.verdict == "insufficient_data" and "at least" in r.rationale
    r = compare_samples(BASE[:3], BETTER[:3], metric="acc", direction="maximize", plan=StatisticalPlan(n_seeds=5))
    assert r.verdict == "insufficient_data"
    assert r.statistics.p_value is None and r.statistics.mean_candidate is not None  # descriptives still reported


def test_non_finite_values_are_excluded_and_reported():
    r = compare_samples([*BASE, math.nan], [*BETTER, math.inf], metric="acc", direction="maximize", plan=PLAN)
    assert r.statistics.n_excluded_baseline == 1 and r.statistics.n_excluded_candidate == 1
    assert r.statistics.n_baseline == 5 and r.verdict == "improved"
    assert any("non-finite" in w for w in r.warnings)
    diverged = compare_samples(BASE, [math.nan] * 5, metric="acc", direction="maximize", plan=PLAN)
    assert diverged.verdict == "insufficient_data"


def test_min_effect_blocks_negligible_improvements():
    base = [0.800, 0.801, 0.799, 0.800, 0.800, 0.801, 0.799, 0.800]
    cand = [0.8012, 0.8022, 0.8002, 0.8012, 0.8012, 0.8022, 0.8002, 0.8012]
    plan = StatisticalPlan(n_seeds=5)
    significant = compare_samples(base, cand, metric="acc", direction="maximize", plan=plan)
    assert significant.verdict == "improved"
    raw_floor = compare_samples(base, cand, metric="acc", direction="maximize", plan=plan, min_delta=0.01)
    assert raw_floor.verdict == "no_significant_difference"
    assert raw_floor.statistics.practically_significant is False
    assert "below the practical threshold" in raw_floor.rationale
    effect_floor = compare_samples(base, cand, metric="acc", direction="maximize", plan=plan, min_effect=5.0)
    assert effect_floor.verdict == "no_significant_difference"


def test_paired_t_and_seed_alignment():
    base = {0: 0.80, 1: 0.75, 2: 0.83, 3: 0.78, 4: 0.81, 9: 0.5}
    cand = {0: 0.82, 1: 0.77, 2: 0.84, 3: 0.80, 4: 0.82, 7: 0.9}
    b, c, seeds = align_by_seed(base, cand)
    assert seeds == [0, 1, 2, 3, 4] and b[1] == 0.75 and c[1] == 0.77
    plan = StatisticalPlan(test="paired_t", n_seeds=5)
    r = compare_samples(b, c, metric="acc", direction="maximize", plan=plan)
    assert r.statistics.effect_size_kind == "cohens_dz" and r.verdict == "improved"
    unpaired = compare_samples(b, c, metric="acc", direction="maximize", plan=StatisticalPlan(n_seeds=5))
    assert unpaired.verdict == "no_significant_difference"  # pairing removes the between-seed variance
    mismatch = compare_samples([0.8, 0.8, 0.9], [0.9, 0.9], metric="acc", direction="maximize", plan=plan)
    assert mismatch.verdict == "insufficient_data" and "paired" in mismatch.rationale


def test_mann_whitney_uses_rank_effect_and_hodges_lehmann():
    plan = StatisticalPlan(test="mann_whitney", n_seeds=5)
    r = compare_samples(BASE, BETTER, metric="acc", direction="maximize", plan=plan)
    s = r.statistics
    assert s.effect_size_kind == "cliffs_delta" and s.effect_size == 1.0
    assert s.ci_estimand == "hodges_lehmann_shift" and s.ci_low is not None and s.ci_low > 0
    assert r.verdict == "improved"
    small = compare_samples(
        BASE[:3], BETTER[:3], metric="acc", direction="maximize", plan=StatisticalPlan(test="mann_whitney", n_seeds=3)
    )
    assert small.verdict == "no_significant_difference"  # exact p >= 0.1 with 3 vs 3
    assert small.statistics.ci_low is None and any("not achievable" in w for w in small.warnings)


def test_bootstrap_is_deterministic_from_plan_seed():
    plan = StatisticalPlan(test="bootstrap", n_seeds=5, bootstrap_seed=11, bootstrap_resamples=3000)
    a = compare_samples(BASE, BETTER, metric="acc", direction="maximize", plan=plan)
    b = compare_samples(BASE, BETTER, metric="acc", direction="maximize", plan=plan)
    assert a == b and a.verdict == "improved"
    assert a.statistics.bootstrap_seed == 11 and a.statistics.bootstrap_resamples == 3000


def test_zero_variance_arms_are_json_safe():
    r = compare_samples([0.8] * 5, [0.9] * 5, metric="acc", direction="maximize", plan=PLAN)
    assert r.verdict == "improved"
    assert r.statistics.statistic is None and r.statistics.effect_size is None  # ±inf reported as null
    assert r.statistics.p_value == 0.0
    r.model_dump_json()  # must not contain NaN/Infinity
    same = compare_samples([0.8] * 5, [0.8] * 5, metric="acc", direction="maximize", plan=PLAN)
    assert same.verdict == "no_significant_difference"


def test_compare_many_applies_holm_correction():
    samples = [
        MetricSamples(metric="accuracy", direction="maximize", baseline=BASE, candidate=BETTER),
        MetricSamples(metric="latency_ms", direction="minimize", baseline=LAT_BASE, candidate=LAT_FASTER),
        MetricSamples(
            metric="f1",
            direction="maximize",
            baseline=[0.7, 0.72, 0.71, 0.69, 0.7],
            candidate=[0.71, 0.7, 0.72, 0.7, 0.71],
        ),
        MetricSamples(metric="recall", direction="maximize", baseline=[0.5], candidate=[0.6]),
    ]
    results = compare_many(samples, PLAN)
    assert [r.metric for r in results] == ["accuracy", "latency_ms", "f1", "recall"]
    tested = [r for r in results if r.verdict != "insufficient_data"]
    assert all(r.statistics.family_size == 3 and r.statistics.correction == "holm" for r in tested)
    for r in tested:
        assert r.statistics.p_value_adjusted is not None and r.statistics.p_value is not None
        assert r.statistics.p_value_adjusted >= r.statistics.p_value
    assert results[3].verdict == "insufficient_data" and results[3].statistics.p_value_adjusted is None
    assert "holm-adjusted" in results[0].rationale


def test_correction_can_change_the_verdict():
    base = [1.00, 1.02, 0.98, 1.01, 0.99]
    cand = [1.03, 1.05, 1.00, 1.04, 1.02]  # p ≈ 0.03 on its own
    single = compare_samples(base, cand, metric="m0", direction="maximize", plan=PLAN)
    assert single.verdict == "improved"
    family = [
        MetricSamples(metric=f"m{i}", direction="maximize", baseline=base, candidate=cand if i == 0 else base)
        for i in range(5)
    ]
    holm = compare_many(family, PLAN)
    assert holm[0].verdict == "no_significant_difference"
    none = compare_many(family, StatisticalPlan(n_seeds=5, correction="none"))
    assert none[0].verdict == "improved"


def test_invalid_direction_rejected():
    with pytest.raises(ValueError, match="direction"):
        compare_samples(BASE, BETTER, metric="acc", direction="up")
