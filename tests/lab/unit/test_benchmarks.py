"""Internal benchmark framework: the eight suites, scoring, hashing, comparison and fixture safety."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from engines.lab.benchmarks import (
    BENCHMARK_SUITES,
    BenchmarkSuite,
    UnknownBenchmarkSuite,
    compare,
    get_suite,
    list_suites,
    load_fixture,
    suite_manifest,
)
from engines.lab.benchmarks.base import BenchmarkCase
from engines.lab.benchmarks.scoring import macro_f1, ndcg_at_k, prf, recall_at_k, values_match
from engines.lab.benchmarks.suites.claim_verification import reference_claim_status
from engines.lab.benchmarks.suites.hypothesis import reference_hypothesis_checks
from engines.lab.benchmarks.suites.reproducibility import reference_reproduction_verdict
from engines.lab.benchmarks.suites.strategy_evolution import pareto_optimal_solutions, run_evolution
from engines.lab.evolution.promotion import PromotionGate, PromotionPolicy
from engines.lab.states import ClaimStatus, FailureType

EXPECTED_SUITES = {
    "literature_bench": "retrieval",
    "hypothesis_bench": "hypothesis_quality",
    "experiment_design_bench": "design_validation",
    "coding_experiment_bench": "coding",
    "failure_analysis_bench": "failure_classification",
    "strategy_evolution_bench": "evolution",
    "reproducibility_bench": "reproduction_decision",
    "claim_verification_bench": "claim_verification",
}
LABEL_SUITES = sorted(set(EXPECTED_SUITES) - {"strategy_evolution_bench"})
CRIPPLED = {"crossover_rate": 0.0, "mutation_scale": 0.005, "mutation_rate": 0.05}


def oracle_answer(key: str, case: BenchmarkCase) -> Any:
    e = case.expected
    if key == "literature_bench":
        return sorted(e["grades"], key=lambda d: -e["grades"][d])
    if key == "hypothesis_bench":
        return dict(e)
    if key == "experiment_design_bench":
        return list(e["issues"])
    if key == "coding_experiment_bench":
        return dict(e["outputs"])
    if key == "failure_analysis_bench":
        return e["failure_type"]
    if key == "reproducibility_bench":
        return {"verdict": e["verdict"]}
    if key == "claim_verification_bench":
        return e["status"]
    inp = case.input
    return {"solutions": pareto_optimal_solutions(inp["problem"], inp["dims"], 900)}


def oracle(suite: BenchmarkSuite) -> Callable[[Any], Any]:
    table = {json.dumps(c.input, sort_keys=True): c for c in suite.cases}
    return lambda inp: oracle_answer(suite.key, table[json.dumps(inp, sort_keys=True)])


# ---------------------------------------------------------------------------------------------
# Registry, metadata and hashing
# ---------------------------------------------------------------------------------------------
def test_all_eight_suites_are_registered_with_components() -> None:
    assert {s.key: s.component for s in list_suites()} == EXPECTED_SUITES
    assert [s.key for s in list_suites()] == sorted(EXPECTED_SUITES)
    with pytest.raises(UnknownBenchmarkSuite):
        get_suite("nope")


@pytest.mark.parametrize("key", sorted(EXPECTED_SUITES))
def test_suite_loads_with_at_least_eight_fixture_cases(key: str) -> None:
    suite = get_suite(key)
    assert suite.case_count >= 8
    assert len({c.id for c in suite.cases}) == suite.case_count
    meta, cases = load_fixture(f"{key}.json")
    assert meta["benchmark_fixture"] is True and "BENCHMARK FIXTURE" in meta["fixture_notice"]
    assert meta["suite"] == key and meta["version"] == suite.version
    assert len(cases) == suite.case_count


@pytest.mark.parametrize("key", sorted(EXPECTED_SUITES))
def test_content_hash_is_stable_and_sensitive_to_case_changes(key: str) -> None:
    suite = get_suite(key)
    rebuilt = type(suite)(
        key=suite.key,
        name=suite.name,
        version=suite.version,
        component=suite.component,
        description=suite.description,
        cases=load_fixture(f"{key}.json")[1],
        scorer=suite.scorer,
    )
    assert rebuilt.content_hash == suite.content_hash and len(suite.content_hash) == 64
    first = suite.cases[0]
    changed_cases = [first.model_copy(update={"tags": (*first.tags, "changed")}), *suite.cases[1:]]
    changed = BenchmarkSuite(
        key=suite.key,
        name=suite.name,
        version=suite.version,
        component=suite.component,
        description=suite.description,
        cases=changed_cases,
        scorer=suite.scorer,
    )
    assert changed.content_hash != suite.content_hash
    bumped = BenchmarkSuite(
        key=suite.key,
        name=suite.name,
        version="9.9.9",
        component=suite.component,
        description=suite.description,
        cases=suite.cases,
        scorer=suite.scorer,
    )
    assert bumped.content_hash != suite.content_hash


def test_manifest_is_complete_for_seeding() -> None:
    manifest = suite_manifest()
    assert len(manifest) == 8
    for item in manifest:
        assert set(item) >= {"key", "version", "component", "case_count", "content_hash", "description", "config"}
        assert item["content_hash"] == BENCHMARK_SUITES[item["key"]].content_hash
        assert len(item["key"]) <= 64 and len(item["version"]) <= 24 and len(item["component"]) <= 48
    json.dumps(manifest, allow_nan=False)


def test_fixture_loader_refuses_unmarked_or_traversal_paths(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("../states.py", "/etc/passwd", ".hidden.json", "a/b.json"):
        with pytest.raises(ValueError):
            load_fixture(bad)
    import engines.lab.benchmarks.base as base

    class FakeFiles:
        def joinpath(self, *parts: str) -> Any:
            return self

        def read_text(self, encoding: str = "utf-8") -> str:
            return json.dumps({"cases": []})

    monkeypatch.setattr(base.resources, "files", lambda _pkg: FakeFiles())
    with pytest.raises(ValueError, match="not marked as a benchmark fixture"):
        load_fixture("unmarked.json")


# ---------------------------------------------------------------------------------------------
# Perfect and bad subjects
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", LABEL_SUITES)
def test_perfect_subject_scores_one(key: str) -> None:
    suite = get_suite(key)
    result = suite.run(oracle(suite))
    assert result.score == 1.0
    assert result.n_passed == result.n_cases and result.n_errors == 0
    assert result.content_hash == suite.content_hash


def test_perfect_evolution_subject_scores_near_one() -> None:
    suite = get_suite("strategy_evolution_bench")
    result = suite.run(oracle(suite))
    assert result.score >= 0.98  # Pareto-optimal decision vectors: 1.0 up to front discretisation
    assert result.n_passed == result.n_cases


@pytest.mark.parametrize("key", LABEL_SUITES)
def test_invalid_output_subject_scores_zero(key: str) -> None:
    result = get_suite(key).run(lambda _: None)
    assert result.score == 0.0 and result.n_passed == 0


@pytest.mark.parametrize(
    ("key", "constant", "ceiling"),
    [
        ("failure_analysis_bench", "CODE_FAILURE", 0.2),
        ("reproducibility_bench", "REPRODUCED", 0.3),
        ("claim_verification_bench", "VERIFIED", 0.2),
        ("hypothesis_bench", {"falsifiable": True, "measurable": True, "metric_named": True}, 0.6),
        ("experiment_design_bench", ["DATA_LEAKAGE"], 0.3),
        ("literature_bench", [], 0.0),
    ],
)
def test_constant_subjects_score_low(key: str, constant: Any, ceiling: float) -> None:
    assert get_suite(key).run(lambda _: constant).score <= ceiling


def test_bad_evolution_subjects_score_low() -> None:
    suite = get_suite("strategy_evolution_bench")
    dominated = suite.run(lambda inp: {"solutions": [[1.0] * inp["dims"]]})
    assert dominated.score < 0.2
    crippled = suite.run(lambda _: CRIPPLED)
    assert crippled.score < 0.35 and crippled.n_errors == 0
    rejected = suite.run(lambda _: {"population_size": 1000})  # the budget is not the subject's to change
    assert rejected.score == 0.0 and rejected.n_errors == rejected.n_cases


def test_evolution_suite_rejects_out_of_range_solutions() -> None:
    suite = get_suite("strategy_evolution_bench")
    result = suite.run(lambda inp: {"solutions": [[2.0] * inp["dims"]]})
    assert result.score == 0.0 and all("within [0, 1]" in r.detail["error"] for r in result.case_results)


def test_reference_subjects_follow_documented_rules() -> None:
    assert get_suite("reproducibility_bench").run(reference_reproduction_verdict).score == 1.0
    assert get_suite("claim_verification_bench").run(reference_claim_status).score == 1.0
    assert get_suite("hypothesis_bench").run(reference_hypothesis_checks).score >= 0.9


def test_fixture_labels_use_the_state_machine_vocabularies() -> None:
    failure_labels = {c.expected["failure_type"] for c in get_suite("failure_analysis_bench").cases}
    assert failure_labels == {ft.value for ft in FailureType}
    claim_labels = {c.expected["status"] for c in get_suite("claim_verification_bench").cases}
    assert claim_labels == {s.value for s in ClaimStatus}


# ---------------------------------------------------------------------------------------------
# Runner robustness
# ---------------------------------------------------------------------------------------------
def test_subject_exceptions_are_recorded_not_raised() -> None:
    def explode(_: Any) -> Any:
        raise RuntimeError("boom " + "x" * 500)

    result = get_suite("coding_experiment_bench").run(explode)
    assert result.score == 0.0 and result.n_errors == result.n_cases
    assert result.case_results[0].detail["error"].startswith("RuntimeError: boom")
    assert len(result.case_results[0].detail["error"]) < 250


def test_subjects_cannot_mutate_fixtures() -> None:
    suite = get_suite("literature_bench")
    before = suite.content_hash
    snapshot = [c.model_dump() for c in suite.cases]

    def vandal(inp: dict[str, Any]) -> list[str]:
        inp["documents"].clear()
        inp["query"] = "tampered"
        return []

    suite.run(vandal)
    assert suite.content_hash == before and [c.model_dump() for c in suite.cases] == snapshot
    assert suite.run(oracle(suite)).score == 1.0


def test_results_are_deterministic_and_json_serialisable() -> None:
    suite = get_suite("hypothesis_bench")
    a = suite.run(reference_hypothesis_checks, seed=3)
    b = suite.run(reference_hypothesis_checks, seed=3)
    assert a == b
    json.dumps(a.model_dump(mode="json"), allow_nan=False)


# ---------------------------------------------------------------------------------------------
# Scoring details
# ---------------------------------------------------------------------------------------------
def test_ranking_metrics() -> None:
    gains = {"a": 2.0, "b": 1.0}
    assert ndcg_at_k(["a", "b", "c"], gains, 5) == pytest.approx(1.0)
    assert ndcg_at_k(["c", "b", "a"], gains, 5) < ndcg_at_k(["a", "c", "b"], gains, 5) < 1.0
    assert ndcg_at_k(["c", "d"], gains, 5) == 0.0
    assert recall_at_k(["x", "y", "z", "w", "v", "a"], ["a", "b"], 5) == 0.0
    assert recall_at_k(["a", "a", "b"], ["a", "b"], 5) == 1.0


def test_set_and_label_metrics() -> None:
    assert prf(0, 0, 0) == (1.0, 1.0, 1.0)
    assert prf(1, 1, 0) == (0.5, 1.0, pytest.approx(2 / 3))
    assert macro_f1(["A", "B"], ["A", None]) == pytest.approx((1.0 + 0.0) / 2)
    with pytest.raises(ValueError):
        macro_f1(["A"], [])


def test_value_matching_tolerances_and_types() -> None:
    assert values_match(1.0, 1.0 + 1e-9, rel_tol=1e-6)
    assert not values_match(1.0, 1.01, rel_tol=1e-6)
    assert values_match([1, 2.0], [1, 2.0000000001])
    assert not values_match(True, 1) and not values_match("1", 1)
    assert values_match({"a": [1.0]}, {"a": [1.0]}) and not values_match({"a": 1}, {"a": 1, "b": 2})
    assert not values_match(float("nan"), float("nan"))


def test_coding_bench_partial_credit_and_design_bench_false_positives() -> None:
    coding = get_suite("coding_experiment_bench")
    case = coding.cases[0]
    [name, *_] = sorted(case.expected["outputs"])
    partial = {name: case.expected["outputs"][name]}
    scored = coding.scorer.score_case(case, partial)
    assert 0 < scored.score < 1 and not scored.passed
    design = get_suite("experiment_design_bench")
    clean = next(c for c in design.cases if not c.expected["issues"])
    noisy = design.scorer.score_case(clean, ["MADE_UP_CODE"])
    assert noisy.score == 0.0 and noisy.detail["unknown_codes"] == ["MADE_UP_CODE"]
    assert design.scorer.score_case(clean, []).score == 1.0


# ---------------------------------------------------------------------------------------------
# Comparison and promotion evidence
# ---------------------------------------------------------------------------------------------
def test_compare_reports_improvement_only_when_ci_excludes_zero() -> None:
    suite = get_suite("claim_verification_bench")
    good = suite.run(oracle(suite))
    bad = suite.run(lambda _: "UNVERIFIED")
    up = compare(good, bad, seed=1)
    assert up.improved and not up.regressed and up.ci[0] > 0 and up.n_paired == suite.case_count
    down = compare(bad, good, seed=1)
    assert down.regressed and not down.improved
    same = compare(good, good)
    assert not same.improved and not same.regressed and same.delta == 0.0
    with pytest.raises(ValueError):
        compare(good, get_suite("hypothesis_bench").run(reference_hypothesis_checks))


def test_benchmark_comparison_feeds_the_promotion_gate() -> None:
    suite = get_suite("reproducibility_bench")
    candidate = suite.run(reference_reproduction_verdict)
    baseline = suite.run(lambda _: "REPRODUCED")
    evidence = [compare(candidate, baseline).to_evidence()]
    incumbent = {"scientific_performance": [0.5, 0.51, 0.49], "safety": [1.0] * 3, "reproducibility": [0.9] * 3}
    improved = {"scientific_performance": [0.6, 0.61, 0.59], "safety": [1.0] * 3, "reproducibility": [0.9] * 3}
    gate = PromotionGate(PromotionPolicy(bootstrap_resamples=500))
    assert gate.evaluate(incumbent, improved, evidence).eligible
    reversed_evidence = [compare(baseline, candidate).to_evidence()]
    assert not gate.evaluate(incumbent, improved, reversed_evidence).eligible


def test_evolution_bench_engine_mode_reports_history_and_is_seeded() -> None:
    suite = get_suite("strategy_evolution_bench")
    case = next(c for c in suite.cases if c.input["problem"] == "dtlz2")
    out_a = suite.execute_case(case, lambda _: {}, seed=5)
    out_b = suite.execute_case(case, lambda _: {}, seed=5)
    assert out_a == out_b
    scored = suite.scorer.score_case(case, out_a)
    assert scored.detail["mode"] == "engine" and scored.detail["front_size"] > 0
    assert len(scored.detail["hv_history"]) == case.input["generations"] + 1
    assert scored.detail["hv_history"][-1] > scored.detail["hv_history"][0]
    assert scored.detail["evaluations"] == case.input["population"] * (case.input["generations"] + 1)
    with pytest.raises(ValueError):
        run_evolution("zdt1", dims=4, generations=2, population=8, overrides={"seed": 1})
    with pytest.raises(ValueError):
        run_evolution("rastrigin", dims=4, generations=2, population=8)
