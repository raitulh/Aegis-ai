"""Promotion gate matrix and the bootstrap helpers behind it."""

from __future__ import annotations

import pytest

from engines.lab.evolution.promotion import PromotionGate, PromotionPolicy
from engines.lab.evolution.stats import bootstrap_mean_ci, bootstrap_mean_difference

INCUMBENT = {
    "scientific_performance": [0.60, 0.62, 0.61, 0.59, 0.60],
    "safety": [1.0] * 5,
    "reproducibility": [0.90, 0.92, 0.91, 0.90, 0.91],
}
CANDIDATE = {
    "scientific_performance": [0.70, 0.72, 0.71, 0.69, 0.70],
    "safety": [1.0] * 5,
    "reproducibility": [0.91, 0.92, 0.90, 0.91, 0.92],
}
IMPROVED_BENCH = [{"suite": "literature_bench", "score": 0.81, "baseline_score": 0.74, "improved": True}]


def gate(**policy: object) -> PromotionGate:
    return PromotionGate(PromotionPolicy(bootstrap_resamples=1000, **policy))  # type: ignore[arg-type]


def test_clear_improvement_with_benchmark_evidence_is_eligible() -> None:
    decision = gate().evaluate(INCUMBENT, CANDIDATE, IMPROVED_BENCH)
    assert decision.eligible, decision.reasons
    assert decision.requires_human_approval
    assert "eligibility is not promotion" in decision.reasons[0]
    primary = decision.statistics["primary"]["improvement"]
    assert primary["estimate"] == pytest.approx(0.1, abs=1e-9)
    assert 0 < primary["ci"][0] < 0.1 < primary["ci"][1]
    assert decision.failed_checks() == []


def test_insufficient_seeds_is_ineligible() -> None:
    few = {k: v[:2] for k, v in CANDIDATE.items()}
    decision = gate().evaluate(INCUMBENT, few, IMPROVED_BENCH)
    assert not decision.eligible and "sample_size" in decision.failed_checks()


def test_confidence_interval_crossing_zero_is_ineligible() -> None:
    noisy = {**CANDIDATE, "scientific_performance": [0.40, 0.85, 0.55, 0.75, 0.52]}
    decision = gate().evaluate(INCUMBENT, noisy, IMPROVED_BENCH)
    assert not decision.eligible
    assert decision.failed_checks() == ["primary_improvement"]
    assert "includes or lies below 0" in decision.reasons[0]


def test_improvement_below_minimum_is_ineligible() -> None:
    decision = gate(min_improvement=0.2).evaluate(INCUMBENT, CANDIDATE, IMPROVED_BENCH)
    assert decision.failed_checks() == ["primary_improvement"]
    relative = gate(min_improvement=0.1, improvement_mode="relative").evaluate(INCUMBENT, CANDIDATE, IMPROVED_BENCH)
    assert relative.eligible  # +0.1 on 0.604 is a ~16.6% relative gain
    assert relative.statistics["primary"]["relative_improvement"]["estimate"] == pytest.approx(0.1 / 0.604, rel=1e-6)


def test_safety_regression_or_violation_is_ineligible() -> None:
    unsafe = {**CANDIDATE, "safety": [1.0, 1.0, 0.95, 1.0, 1.0]}
    decision = gate().evaluate(INCUMBENT, unsafe, IMPROVED_BENCH)
    assert not decision.eligible
    assert {"feasibility", "no_regression"} <= set(decision.failed_checks())


def test_missing_safety_samples_cannot_be_verified() -> None:
    blind = {k: v for k, v in CANDIDATE.items() if k != "safety"}
    decision = gate().evaluate(INCUMBENT, blind, IMPROVED_BENCH)
    assert {"sample_size", "feasibility", "no_regression"} <= set(decision.failed_checks())


def test_reproducibility_regression_respects_tolerance() -> None:
    worse = {**CANDIDATE, "reproducibility": [0.88, 0.89, 0.88, 0.89, 0.88]}
    assert gate().evaluate(INCUMBENT, worse, IMPROVED_BENCH).failed_checks() == ["no_regression"]
    tolerant = gate(max_regression={"reproducibility": 0.05}).evaluate(INCUMBENT, worse, IMPROVED_BENCH)
    assert tolerant.eligible


def test_benchmark_evidence_is_required_and_must_show_improvement() -> None:
    assert gate().evaluate(INCUMBENT, CANDIDATE, []).failed_checks() == ["benchmark_evidence"]
    flat = [{"suite": "literature_bench", "score": 0.74, "baseline_score": 0.74}]
    assert gate().evaluate(INCUMBENT, CANDIDATE, flat).failed_checks() == ["benchmark_evidence"]
    not_significant = [{"suite": "literature_bench", "score": 0.76, "baseline_score": 0.74, "improved": False}]
    assert not gate().evaluate(INCUMBENT, CANDIDATE, not_significant).eligible
    regressed = [*IMPROVED_BENCH, {"suite": "hypothesis_bench", "score": 0.5, "baseline_score": 0.7}]
    decision = gate().evaluate(INCUMBENT, CANDIDATE, regressed)
    assert not decision.eligible and "hypothesis_bench" in decision.reasons[0]
    assert not gate().evaluate(INCUMBENT, CANDIDATE, [{"suite": "x"}]).eligible  # malformed evidence
    assert gate(require_benchmark=False).evaluate(INCUMBENT, CANDIDATE, None).eligible


def test_minimised_primary_objective_is_direction_aware() -> None:
    policy = {"primary_objective": "cost", "no_regression_objectives": ("safety",)}
    incumbent = {"cost": [2.0, 2.1, 1.9, 2.0], "safety": [1.0] * 4}
    cheaper = {"cost": [1.0, 1.1, 0.9, 1.0], "safety": [1.0] * 4}
    assert gate(**policy).evaluate(incumbent, cheaper, IMPROVED_BENCH).eligible
    assert not gate(**policy).evaluate(cheaper, incumbent, IMPROVED_BENCH).eligible


def test_paired_bootstrap_and_determinism() -> None:
    a = gate(paired=True).evaluate(INCUMBENT, CANDIDATE, IMPROVED_BENCH)
    b = gate(paired=True).evaluate(INCUMBENT, CANDIDATE, IMPROVED_BENCH)
    assert a == b and a.statistics["primary"]["improvement"]["method"] == "paired"


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        PromotionPolicy(primary_objective="unknown")
    with pytest.raises(ValueError):
        PromotionPolicy(max_regression={"safety": -0.1})
    assert PromotionGate({"min_seeds": 5}).policy.min_seeds == 5


def test_bootstrap_helpers() -> None:
    result = bootstrap_mean_difference([2.0, 2.2, 1.8, 2.1], [1.0, 1.1, 0.9, 1.0], seed=1)
    assert result.estimate == pytest.approx(1.025) and result.excludes_zero()
    assert result.lower < 1.025 < result.upper
    same = bootstrap_mean_difference([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert same.lower == same.upper == 0.0 and not same.excludes_zero()
    with pytest.raises(ValueError):
        bootstrap_mean_difference([1.0], [0.0, 0.0], relative=True)
    with pytest.raises(ValueError):
        bootstrap_mean_difference([1.0, 2.0], [1.0], paired=True)
    with pytest.raises(ValueError):
        bootstrap_mean_difference([float("nan")], [1.0])
    mean, lo, hi = bootstrap_mean_ci([0.1, 0.2, 0.3, 0.4], seed=2)
    assert mean == pytest.approx(0.25) and lo <= mean <= hi
    assert bootstrap_mean_ci([0.5]) == (0.5, 0.5, 0.5)
