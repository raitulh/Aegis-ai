"""Verification criteria profiles and claim-status decisions."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from engines.lab.states import CLAIM_TRANSITIONS, ClaimStatus
from engines.lab.verification import (
    ALL_CRITERIA,
    CRITERIA_PROFILES,
    MODEL_JUDGMENT_WEIGHT,
    CheckResults,
    CriteriaProfile,
    decide,
    evaluate_criteria,
    get_profile,
)

FULL: dict[str, Any] = {
    "reproductions": [{"id": "rep-1", "verdict": "reproduced"}, {"id": "rep-2", "verdict": "reproduced"}],
    "statistics": {
        "p_value": 0.001,
        "p_value_adjusted": 0.003,
        "ci_low": 0.02,
        "ci_high": 0.08,
        "claimed_direction": "increase",
    },
    "baseline_runs_completed": True,
    "baseline_evaluator_passed": True,
    "evaluators": [{"key": "classification", "passed": True, "independent": True}],
    "provenance": {"complete": True},
    "verifier": {
        "generator_agent_version_ids": ["av-1"],
        "generator_models": ["model-a"],
        "verifier_agent_version_ids": ["av-9"],
        "verifier_models": ["model-b"],
    },
    "supporting_evidence_count": 4,
    "contradicting_evidence_count": 0,
}

MAKE_MISSING = {
    "replicated": lambda c: c.update(reproductions=[]),
    "statistically_supported": lambda c: c.update(statistics=None),
    "baseline_validated": lambda c: c.update(baseline_runs_completed=None),
    "evaluator_passed": lambda c: c.update(evaluators=[]),
    "provenance_complete": lambda c: c.update(provenance=None),
    "independent_verifier": lambda c: c.update(verifier=None),
    "no_contradicting_evidence": lambda c: c.update(contradicting_evidence_count=None),
}


def checks(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(FULL)
    data.update(changes)
    return data


def test_profiles_are_well_formed():
    assert set(CRITERIA_PROFILES) == {"default", "ml_benchmark", "simulation", "computational_science"}
    assert set(CRITERIA_PROFILES["ml_benchmark"].required) == set(ALL_CRITERIA)
    assert CRITERIA_PROFILES["simulation"].min_reproductions == 2 and CRITERIA_PROFILES["simulation"].alpha == 0.01
    for profile in CRITERIA_PROFILES.values():
        assert profile.required and len(profile.key) <= 48  # scientific_claims.criteria_profile column
    with pytest.raises(ValueError):
        CriteriaProfile(key="empty", description="x", required=())
    with pytest.raises(KeyError):
        get_profile("nope")


@pytest.mark.parametrize("profile", sorted(CRITERIA_PROFILES))
def test_all_criteria_satisfied_is_verified(profile):
    result = decide(profile, checks())
    assert result.status == ClaimStatus.VERIFIED
    assert result.confidence == pytest.approx(1.0)
    assert result.failed == [] and result.missing == []
    assert result.profile == profile


@pytest.mark.parametrize(
    ("profile", "criterion"), [(p, c.value) for p in sorted(CRITERIA_PROFILES) for c in ALL_CRITERIA]
)
def test_never_verified_with_a_missing_required_criterion(profile, criterion):
    data = checks()
    MAKE_MISSING[criterion](data)
    result = decide(profile, data)
    required = {c.value for c in CRITERIA_PROFILES[profile].required}
    assert criterion in result.missing
    if criterion in required:
        assert result.status == ClaimStatus.PARTIALLY_VERIFIED
        assert result.confidence <= 0.8
        assert criterion in result.rationale
    else:
        assert result.status == ClaimStatus.VERIFIED


def test_nothing_checked_is_candidate_even_with_model_support():
    result = decide("default", {})
    assert result.status == ClaimStatus.CANDIDATE and len(result.missing) == len(ALL_CRITERIA)
    judged = decide("default", {"model_judgment": {"supports": True, "confidence": 1.0, "rationale": "looks right"}})
    assert judged.status == ClaimStatus.CANDIDATE
    assert judged.confidence == pytest.approx(MODEL_JUDGMENT_WEIGHT)
    assert "advisory" in judged.rationale


def test_model_judgment_only_nudges_confidence():
    partial = checks(provenance=None)
    base = decide("default", partial)
    up = decide("default", {**partial, "model_judgment": {"supports": True, "confidence": 0.9}})
    down = decide("default", {**partial, "model_judgment": {"supports": False, "confidence": 0.9}})
    assert base.status == up.status == down.status == ClaimStatus.PARTIALLY_VERIFIED
    assert up.confidence <= 0.8  # never above the status cap
    # confidence = clamp(support × penalty ± weight × judgment confidence, 0, cap)
    assert down.confidence == pytest.approx(
        base.confidence_components["support"] - MODEL_JUDGMENT_WEIGHT * 0.9, abs=1e-6
    )
    assert down.confidence < base.confidence
    verified_disputed = decide("default", {**checks(), "model_judgment": {"supports": False, "confidence": 1.0}})
    assert verified_disputed.status == ClaimStatus.VERIFIED  # a model cannot reject deterministic evidence either


def test_failed_reproduction_rejects():
    result = decide("default", checks(reproductions=[{"verdict": "not_reproduced"}]))
    assert result.status == ClaimStatus.REJECTED and result.confidence <= 0.1
    assert "replicated" in result.failed


def test_significant_reversed_effect_rejects():
    result = decide("default", checks(statistics={"p_value_adjusted": 0.001, "ci_low": -0.09, "ci_high": -0.03}))
    assert result.status == ClaimStatus.REJECTED and "opposite direction" in result.rationale
    minimize = decide(
        "default",
        checks(statistics={"p_value": 0.001, "ci_low": -5.0, "ci_high": -1.0, "claimed_direction": "minimize"}),
    )
    assert minimize.status == ClaimStatus.VERIFIED  # "minimize" claims a decrease: a negative CI supports it


def test_contradicting_evidence_contests():
    result = decide("default", checks(contradicting_evidence_count=2))
    assert result.status == ClaimStatus.CONTESTED and "alongside supporting evidence" in result.rationale
    mixed = decide("default", checks(reproductions=[{"verdict": "reproduced"}, {"verdict": "not_reproduced"}]))
    assert mixed.status == ClaimStatus.CONTESTED
    evaluator_disagrees = decide(
        "default",
        checks(evaluators=[{"key": "classification", "passed": True}, {"key": "regression", "passed": False}]),
    )
    assert evaluator_disagrees.status == ClaimStatus.CONTESTED
    alone = decide("default", {"contradicting_evidence_count": 1})
    assert alone.status == ClaimStatus.CONTESTED and "without supporting evidence" in alone.rationale


def test_statistical_support_requires_significance_and_ci_direction():
    crossing = evaluate_criteria(
        "default", checks(statistics={"p_value_adjusted": 0.01, "ci_low": -0.01, "ci_high": 0.05})
    )
    assert crossing["statistically_supported"].state == "failed"
    not_sig = evaluate_criteria(
        "default", checks(statistics={"p_value_adjusted": 0.2, "ci_low": 0.01, "ci_high": 0.05})
    )
    assert not_sig["statistically_supported"].state == "failed"
    no_ci = evaluate_criteria("default", checks(statistics={"p_value_adjusted": 0.01}))
    assert no_ci["statistically_supported"].state == "missing"
    strict = evaluate_criteria(
        "simulation", checks(statistics={"p_value_adjusted": 0.03, "ci_low": 0.01, "ci_high": 0.05})
    )
    assert strict["statistically_supported"].state == "failed"  # simulation alpha is 0.01
    stricter_evidence = evaluate_criteria(
        "default", checks(statistics={"p_value_adjusted": 0.03, "alpha": 0.01, "ci_low": 0.01, "ci_high": 0.05})
    )
    assert stricter_evidence["statistically_supported"].state == "failed"


def test_replication_thresholds_and_partial_reproductions():
    one = checks(reproductions=[{"verdict": "reproduced"}])
    assert evaluate_criteria("default", one)["replicated"].state == "satisfied"
    assert evaluate_criteria("simulation", one)["replicated"].state == "missing"  # needs two
    partial = checks(reproductions=[{"verdict": "partially_reproduced"}])
    assert evaluate_criteria("default", partial)["replicated"].state == "failed"
    self_repro = checks(reproductions=[{"verdict": "reproduced", "independent": False}])
    assert evaluate_criteria("default", self_repro)["replicated"].state == "missing"


def test_independent_verifier_and_evaluator_rules():
    same_model = checks(verifier={**FULL["verifier"], "verifier_models": ["model-a"]})
    assert evaluate_criteria("ml_benchmark", same_model)["independent_verifier"].state == "failed"
    assert decide("ml_benchmark", same_model).status == ClaimStatus.PARTIALLY_VERIFIED
    human = checks(
        verifier={"verifier_is_human": True, "verifier_actor_ids": ["user-2"], "generator_actor_ids": ["user-1"]}
    )
    assert evaluate_criteria("ml_benchmark", human)["independent_verifier"].state == "satisfied"
    dependent = checks(evaluators=[{"key": "metric", "passed": True, "independent": False}])
    result = evaluate_criteria("default", dependent)["evaluator_passed"]
    assert result.state == "missing" and "non-independent" in result.detail
    baseline_failed = evaluate_criteria("ml_benchmark", checks(baseline_evaluator_passed=False))
    assert baseline_failed["baseline_validated"].state == "failed"
    lineage = evaluate_criteria("default", checks(provenance={"complete": False, "missing": ["DatasetVersion"]}))
    assert (
        lineage["provenance_complete"].state == "failed" and "DatasetVersion" in lineage["provenance_complete"].detail
    )


def test_confidence_formula_is_transparent():
    data = checks()
    MAKE_MISSING["independent_verifier"](data)  # optional under "default"
    MAKE_MISSING["provenance_complete"](data)  # required under "default"
    result = decide("default", data)
    # required: 4 of 5 satisfied; optional (baseline_validated, independent_verifier): 1 of 2
    expected = (4 + 0.5 * 1) / (5 + 0.5 * 2)
    assert result.confidence_components["support"] == pytest.approx(expected)
    assert result.confidence == pytest.approx(expected)
    failed = checks(provenance={"complete": False})
    penalised = decide("default", failed)
    assert penalised.confidence_components["failure_penalty"] == 0.5


def test_statuses_are_claim_machine_states_and_inputs_can_be_dicts():
    seen = set()
    for data in (
        {},
        checks(),
        checks(provenance=None),
        checks(contradicting_evidence_count=3),
        checks(reproductions=[{"verdict": "not_reproduced"}]),
    ):
        result = decide("default", data)
        assert result.status in CLAIM_TRANSITIONS
        seen.add(result.status)
    assert seen == {
        ClaimStatus.CANDIDATE,
        ClaimStatus.VERIFIED,
        ClaimStatus.PARTIALLY_VERIFIED,
        ClaimStatus.CONTESTED,
        ClaimStatus.REJECTED,
    }
    assert decide("default", CheckResults.model_validate(checks())) == decide("default", checks())
    with pytest.raises(ValueError):
        decide("default", {"statistics": {"p_value": 2.0}})
