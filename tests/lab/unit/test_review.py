"""Scientific review checks, including the overclaiming scanner."""

from __future__ import annotations

from typing import Any

import pytest

from engines.lab.comparison import compare_samples
from engines.lab.design_validator import validate_design
from engines.lab.experiment_spec import StatisticalPlan
from engines.lab.review import CHECKS, ReviewInput, review, scan_overclaiming
from tests.lab.unit.test_design_validator import CONTEXT
from tests.lab.unit.test_experiment_spec import clean_spec, clean_spec_dict

IMPROVED = compare_samples(
    [0.80, 0.81, 0.79, 0.80, 0.82],
    [0.86, 0.87, 0.85, 0.88, 0.86],
    metric="accuracy",
    direction="maximize",
    plan=StatisticalPlan(n_seeds=5),
)


def clean_input(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "hypotheses": [
            {
                "id": "hyp-1",
                "statement": "Distillation improves accuracy at equal latency.",
                "novelty_rationale": "Prior work distils only encoder layers; we distil attention maps as well (refs 2, 5).",
                "status": "SUPPORTED",
            }
        ],
        "literature_refs": 6,
        "specs": [{"id": "exp-1", "spec": clean_spec()}],
        "validation_reports": [{"id": "exp-1", "report": validate_design(clean_spec_dict(), CONTEXT)}],
        "comparisons": [{"id": "cmp-1", "comparison": IMPROVED}],
        "claims": [
            {
                "id": "claim-1",
                "text": "The distilled model improved accuracy by 6 points (95% CI 0.04-0.08) over the baseline.",
                "status": "VERIFIED",
                "supporting_evidence_count": 3,
                "evidence_ids": ["ev-1", "ev-2", "ev-3"],
            }
        ],
        "reproductions": [{"id": "rep-1", "verdict": "reproduced", "experiment_id": "exp-1"}],
        "report_text": "Across five seeds the distilled model improved accuracy; the effect was reproduced once.",
    }
    data.update(changes)
    return data


def test_clean_mission_passes_every_check():
    result = review(clean_input())
    assert result.verdict == "pass", result.findings
    assert set(result.checks) == set(CHECKS) and len(CHECKS) == 10
    assert all(status == "pass" for status in result.checks.values())
    assert result.score == 1.0


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("These results prove that the method works.", "proof language"),
        ("The improvement is proven on all datasets.", "proof language"),
        ("Our approach guarantees convergence.", "guarantee language"),
        ("Accuracy is guaranteed above 90%.", "guarantee language"),
        ("This definitively establishes the effect.", "certainty language"),
        ("The model always outperforms the baseline.", "universal claim"),
        ("The optimiser never fails to converge.", "universal claim"),
        ("We achieve state-of-the-art results.", "state-of-the-art claim"),
        ("A new SOTA on the benchmark.", "state-of-the-art claim"),
        ("This is a breakthrough in efficiency.", "hype language"),
        ("A revolutionary training scheme.", "hype language"),
        ("The detector reaches 100% recall.", "absolute percentage"),
    ],
)
def test_overclaiming_patterns_are_detected(text, label):
    hits = scan_overclaiming(text)
    assert [h["label"] for h in hits] == [label]
    assert hits[0]["excerpt"] and hits[0]["offset"] >= 0


@pytest.mark.parametrize(
    "text",
    [
        "This does not prove causation.",
        "We cannot guarantee generalisation to other domains.",
        "The result is not definitively established.",
        "The method is not 100% reliable.",
        "The job never exceeded its memory limit.",
        "Results were improved in 4 of 5 seeds.",
        "We provide evidence consistent with the hypothesis.",
    ],
)
def test_hedged_or_neutral_language_is_not_flagged(text):
    assert scan_overclaiming(text) == []


def test_overclaiming_in_claims_fails_and_in_report_is_a_concern():
    in_claim = review(
        clean_input(claims=[{**clean_input()["claims"][0], "text": "This proves the method is always better."}])
    )
    assert in_claim.checks["overclaiming"] == "fail" and in_claim.verdict == "fail"
    in_report = review(clean_input(report_text="A breakthrough result."))
    assert in_report.checks["overclaiming"] == "concern" and in_report.verdict == "concerns"
    assert in_report.findings[0].refs[0]["source"] == "report"


def test_unsupported_claims_fail():
    result = review(
        clean_input(claims=[{"id": "claim-9", "text": "Accuracy improved.", "supporting_evidence_count": 0}])
    )
    assert result.checks["unsupported_claims"] == "fail"
    lone = review(
        clean_input(
            claims=[{"id": "c", "text": "Accuracy improved.", "status": "VERIFIED", "supporting_evidence_count": 1}]
        )
    )
    assert lone.checks["unsupported_claims"] == "concern"


def test_leakage_and_design_errors_fail():
    data = clean_spec_dict()
    data["datasets"].append({"dataset_version_id": "ds-1", "split": "train", "role": "test"})
    leaky = validate_design(data, CONTEXT)
    result = review(clean_input(validation_reports=[{"id": "exp-1", "report": leaky}]))
    assert result.checks["leakage"] == "fail"
    data = clean_spec_dict()
    data["seeds"] = []
    broken = validate_design(data, CONTEXT)
    result = review(clean_input(validation_reports=[{"id": "exp-1", "report": broken}]))
    assert result.checks["experiment_design"] == "fail" and result.checks["reproducibility"] == "fail"
    unvalidated = review(clean_input(validation_reports=[]))
    assert unvalidated.checks["experiment_design"] == "concern"


def test_baseline_quality():
    no_baseline = clean_spec(baseline={"kind": "none"})
    result = review(clean_input(specs=[{"id": "exp-2", "spec": no_baseline}]))
    assert result.checks["baseline_quality"] == "fail"
    unjustified = clean_spec(baseline={"kind": "reference_values", "reference_metrics": {"accuracy": 0.8}})
    assert review(clean_input(specs=[{"id": "exp-3", "spec": unjustified}])).checks["baseline_quality"] == "concern"


def test_reproducibility_outcomes():
    assert (
        review(clean_input(reproductions=[{"id": "r", "verdict": "not_reproduced"}])).checks["reproducibility"]
        == "fail"
    )
    assert (
        review(clean_input(reproductions=[{"id": "r", "verdict": "partially_reproduced"}])).checks["reproducibility"]
        == "concern"
    )
    assert review(clean_input(reproductions=[])).checks["reproducibility"] == "concern"


def test_statistical_validity():
    tiny = compare_samples([0.8], [0.9], metric="accuracy", direction="maximize")
    assert (
        review(clean_input(comparisons=[{"id": "c", "comparison": tiny}])).checks["statistical_validity"] == "concern"
    )
    claims_significance = clean_input(
        comparisons=[],
        claims=[{"id": "c", "text": "Accuracy is significantly higher (p < 0.01).", "supporting_evidence_count": 2}],
    )
    assert review(claims_significance).checks["statistical_validity"] == "fail"
    weak = clean_spec(statistical_plan={"alpha": 0.2, "n_seeds": 5})
    assert review(clean_input(specs=[{"id": "exp-1", "spec": weak}])).checks["statistical_validity"] == "concern"


def test_missing_controls_and_contradictions():
    no_controls = clean_spec(controls=[])
    assert review(clean_input(specs=[{"id": "exp-1", "spec": no_controls}])).checks["missing_controls"] == "concern"
    contradicted = clean_input(claims=[{**clean_input()["claims"][0], "contradicting_evidence_count": 2}])
    assert review(contradicted).checks["contradictory_evidence"] == "fail"  # verified yet contradicted
    regressed = compare_samples(
        [0.86, 0.87, 0.85, 0.88, 0.86],
        [0.80, 0.81, 0.79, 0.80, 0.82],
        metric="accuracy",
        direction="maximize",
        plan=StatisticalPlan(n_seeds=5),
    )
    both = clean_input(comparisons=[{"id": "a", "comparison": IMPROVED}, {"id": "b", "comparison": regressed}])
    assert review(both).checks["contradictory_evidence"] == "concern"


def test_novelty_reasoning_and_scoring():
    result = review(clean_input(hypotheses=[{"id": "h", "statement": "X helps."}], literature_refs=0))
    assert result.checks["novelty_reasoning"] == "concern"
    assert result.score == pytest.approx((9 * 1.0 + 0.5) / 10)
    assert ReviewInput.model_validate(clean_input()) and review(ReviewInput.model_validate(clean_input())) == review(
        clean_input()
    )
