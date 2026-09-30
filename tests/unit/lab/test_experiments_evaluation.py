"""Unit tests: experiment design validation, statistics and evaluators."""

from __future__ import annotations

import math

import pytest

from engines.lab.evaluation import statistics as st
from engines.lab.evaluation.base import EvaluationContext, Evaluator, EvaluatorRegistry
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY, CodeQualityEvaluator
from engines.lab.evaluation.expressions import ExpressionError, evaluate
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.experiments.validator import EnvironmentInfo, ExperimentDesignValidator, ResourceLimits


def _spec(**over: object) -> ExperimentSpec:
    base: dict[str, object] = {
        "objective": "Minimize rastrigin with annealing vs random search",
        "baseline": {"name": "random search", "parameters": {"method": "random"}},
        "metrics": [
            {"name": "objective_value", "direction": "minimize", "primary": True},
            {"name": "runtime", "direction": "minimize"},
        ],
        "success_criteria": [
            {"metric": "objective_value", "comparator": "improves_over_baseline_by", "threshold": 0.1, "relative": True}
        ],
        "variables": [
            {"name": "method", "kind": "independent", "values": ["random", "annealing"]},
            {"name": "budget", "kind": "control", "values": [2000]},
        ],
        "seeds": [1, 2, 3],
        "environment": {"environment_id": "py312"},
        "harness": {"key": "objective"},
        "parameters": {"method": "annealing"},
    }
    base.update(over)
    return ExperimentSpec.model_validate(base)


ENVS = {"py312": EnvironmentInfo("py312", "python:3.12-alpine", {"numpy": "2.1.0"})}


def _validator(**limits: object) -> ExperimentDesignValidator:
    return ExperimentDesignValidator(
        limits=ResourceLimits(**limits),
        environments=ENVS,
        known_harnesses=frozenset({"objective", "classification", "self_reported"}),
    )  # type: ignore[arg-type]


def test_valid_design_passes() -> None:
    report = _validator().validate(_spec())
    assert report.valid, report.to_dict()


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"baseline": None}, "missing_baseline"),
        ({"metrics": [], "success_criteria": []}, "missing_metrics"),
        ({"success_criteria": []}, "unclear_success_criteria"),
        ({"success_criteria": [{"metric": "accuracy", "comparator": "gt", "threshold": 0.9}]}, "metric_mismatch"),
        ({"seeds": [1]}, "missing_reproducibility"),
        ({"seeds": [1, 1, 2]}, "missing_reproducibility"),
        ({"harness": None}, "missing_harness"),
        ({"harness": {"key": "self_reported"}}, "missing_harness"),
        (
            {
                "variables": [
                    {"name": "m", "kind": "independent", "values": [1, 2]},
                    {"name": "m", "kind": "control", "values": [1]},
                ]
            },
            "invalid_controls",
        ),
        ({"resources": {"cpu": 64}}, "impossible_resources"),
        ({"resources": {"runtime": "cpu", "gpu_count": 1}}, "impossible_resources"),
        ({"resources": {"network": "allowlist", "egress_allowlist": ["pypi.org"]}}, "invalid_network"),
        ({"environment": {"environment_id": "py312", "dependencies": ["numpy>=2"]}}, "invalid_dependencies"),
        ({"environment": {"environment_id": "py312", "dependencies": ["torch==2.4.0"]}}, "invalid_dependencies"),
        ({"environment": {"environment_id": "py312", "dependencies": ["numpy==1.26.0"]}}, "invalid_dependencies"),
        ({"environment": {"environment_id": "missing"}}, "invalid_environment"),
        ({"dataset": {"dataset_id": "d", "splits": {"train": "train"}}}, "missing_reproducibility"),
        (
            {
                "dataset": {
                    "dataset_version_id": "v",
                    "splits": {"all": "train", "ALL": "test"},
                    "target_column": "y",
                    "feature_columns": ["y", "x"],
                }
            },
            "data_leakage",
        ),
    ],
)
def test_design_issues_detected(override: dict[str, object], code: str) -> None:
    report = _validator().validate(_spec(**override))
    assert code in report.codes(), report.to_dict()
    assert not report.valid


def test_digest_pinning_enforced_when_required() -> None:
    report = _validator(require_digest_pinned_images=True).validate(_spec(environment={"image": "python:3.12"}))
    assert "missing_reproducibility" in report.codes()
    ok = _validator(require_digest_pinned_images=True).validate(
        _spec(environment={"image": "python@sha256:" + "a" * 64})
    )
    assert "missing_reproducibility" not in ok.codes()


def test_statistics_reference_values() -> None:
    r = st.welch_t_test([1, 2, 3, 4, 5], [2, 3, 4, 5, 6, 7])
    assert math.isclose(r.statistic, -1.4412, abs_tol=1e-4)
    assert math.isclose(r.p_value, 0.18348, abs_tol=5e-4)
    assert math.isclose(st.t_ppf(0.975, 10), 2.2281, abs_tol=1e-3)
    assert math.isclose(st.betainc(2, 3, 0.4), 0.5248, abs_tol=1e-4)
    mw = st.mann_whitney_u([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert math.isclose(mw.p_value, 0.01219, abs_tol=5e-4)
    assert st.holm_correction([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    assert st.bonferroni_correction([0.02, 0.5]) == [0.04, 1.0]
    assert st.cohens_d([1, 2, 3], [1, 2, 3]) == 0.0
    lo, hi = st.bootstrap_diff_ci([10, 11, 12, 13], [1, 2, 3, 4])
    assert lo > 0 and hi > lo
    assert st.permutation_test([1, 2, 3], [1, 2, 3]).p_value > 0.5


def test_statistics_deterministic_with_seed() -> None:
    a, b = [0.8, 0.82, 0.81, 0.83], [0.7, 0.72, 0.71, 0.69]
    assert st.bootstrap_diff_ci(a, b, seed=5) == st.bootstrap_diff_ci(a, b, seed=5)
    assert st.bootstrap_test(a, b, seed=5).p_value == st.bootstrap_test(a, b, seed=5).p_value


def _ctx(candidate: list[float], baseline: list[float], **kw: object) -> EvaluationContext:
    return EvaluationContext(candidate={"objective_value": candidate}, baseline={"objective_value": baseline}, **kw)  # type: ignore[arg-type]


def test_benchmark_and_statistical_evaluators_detect_improvement() -> None:
    spec = _spec()
    ctx = _ctx([1.0, 1.1, 0.9, 1.05, 0.95], [3.0, 3.2, 2.9, 3.1, 3.05])
    bench = DEFAULT_REGISTRY.get("benchmark").evaluate(spec, {}, ctx)
    stat = DEFAULT_REGISTRY.get("statistical").evaluate(spec, {}, ctx)
    assert bench.passed is True and stat.passed is True
    assert bench.details["comparisons"]["objective_value"]["improvement"] > 0


def test_statistical_inconclusive_with_too_few_seeds() -> None:
    stat = DEFAULT_REGISTRY.get("statistical").evaluate(_spec(), {}, _ctx([1.0, 1.1], [3.0, 3.1]))
    assert stat.passed is None and stat.verdict == "inconclusive"


def test_no_improvement_fails() -> None:
    ctx = _ctx([3.0, 3.1, 2.9], [3.0, 3.05, 2.95])
    assert DEFAULT_REGISTRY.get("benchmark").evaluate(_spec(), {}, ctx).passed is False
    assert DEFAULT_REGISTRY.get("statistical").evaluate(_spec(), {}, ctx).passed is False


def test_self_reported_lowers_confidence() -> None:
    ctx_a = _ctx([1.0, 1.1, 0.9], [3.0, 3.2, 2.9])
    ctx_b = _ctx([1.0, 1.1, 0.9], [3.0, 3.2, 2.9], self_reported=True)
    a = DEFAULT_REGISTRY.get("benchmark").evaluate(_spec(), {}, ctx_a)
    b = DEFAULT_REGISTRY.get("benchmark").evaluate(_spec(), {}, ctx_b)
    assert b.confidence < a.confidence and any("self-reported" in w for w in b.warnings)


def test_classification_and_regression_evaluators() -> None:
    spec = _spec(
        metrics=[{"name": "accuracy", "direction": "maximize", "primary": True}],
        success_criteria=[{"metric": "accuracy", "comparator": "gte", "threshold": 0.75}],
    )
    preds = b"y_true,y_pred\na,a\nb,b\na,b\nb,b\n"
    res = DEFAULT_REGISTRY.get("classification").evaluate(spec, {"predictions.csv": preds}, EvaluationContext())
    assert res.metrics["accuracy"] == 0.75 and res.passed is True
    reg = DEFAULT_REGISTRY.get("regression").evaluate(
        spec, {"predictions.csv": b"y_true,y_pred\n1,1\n2,2\n3,4\n"}, EvaluationContext()
    )
    assert math.isclose(reg.metrics["mse"], 1 / 3) and reg.passed is None
    missing = DEFAULT_REGISTRY.get("classification").evaluate(spec, {}, EvaluationContext())
    assert missing.passed is False


def test_reproduction_evaluator_tolerance() -> None:
    spec = _spec()
    ok = DEFAULT_REGISTRY.get("reproduction").evaluate(
        spec,
        {},
        EvaluationContext(candidate={"objective_value": [1.0, 1.0]}, reproduction={"objective_value": [1.02, 1.01]}),
    )
    bad = DEFAULT_REGISTRY.get("reproduction").evaluate(
        spec, {}, EvaluationContext(candidate={"objective_value": [1.0]}, reproduction={"objective_value": [1.5]})
    )
    assert ok.passed is True and bad.passed is False


def test_code_quality_evaluator_flags_forbidden_code() -> None:
    bad = b"import subprocess\nimport random\nrandom.seed(1)\neval('1+1')\n"
    res = CodeQualityEvaluator().evaluate(_spec(), {"main.py": bad}, EvaluationContext())
    rules = {v["rule"] for v in res.evidence[0]["violations"]}
    assert res.passed is False and {"forbidden_import", "forbidden_call"} <= rules
    good = b"import json, random\nrandom.seed(3)\nopen('/workspace/output/solution.json','w').write(json.dumps({'x':[0]}))\n"
    assert CodeQualityEvaluator().evaluate(_spec(), {"main.py": good}, EvaluationContext()).passed is True
    syntax = CodeQualityEvaluator().evaluate(_spec(), {"main.py": b"def f(:\n"}, EvaluationContext())
    assert syntax.passed is False


def test_resource_evaluator() -> None:
    res = DEFAULT_REGISTRY.get("resource").evaluate(
        _spec(), {}, EvaluationContext(resources={"runtime_seconds": 10, "exit_code": 0, "peak_memory_mb": 100})
    )
    assert res.passed is True
    oom = DEFAULT_REGISTRY.get("resource").evaluate(
        _spec(), {}, EvaluationContext(resources={"oom_killed": True, "exit_code": 137})
    )
    assert oom.passed is False


def test_custom_evaluator_and_safe_expressions() -> None:
    ctx = _ctx(
        [1.0, 1.2],
        [3.0, 3.0],
        config={"rules": [{"name": "halved", "expr": "candidate.objective_value <= baseline.objective_value / 2"}]},
    )
    assert DEFAULT_REGISTRY.get("custom").evaluate(_spec(), {}, ctx).passed is True
    assert evaluate("max(a, 2) ** 2 >= 4 and not b", {"a": 1, "b": False}) is True
    for bad in ("__import__('os')", "a.__class__", "open('x')", "[1,2]", "'s' * 3", "2 ** 1000", "lambda: 1"):
        with pytest.raises(ExpressionError):
            evaluate(bad, {"a": 1})


def test_evaluator_versions_are_immutable() -> None:
    registry = EvaluatorRegistry()

    class A(Evaluator):
        key = "k"
        version = "1.0.0"

        def evaluate(self, experiment, artifacts, context):  # type: ignore[no-untyped-def]
            return self.result(passed=True, confidence=1.0)

    class B(Evaluator):
        key = "k"
        version = "1.0.0"

        def evaluate(self, experiment, artifacts, context):  # type: ignore[no-untyped-def]
            return self.result(passed=False, confidence=1.0)

    registry.register(A)
    registry.register(A)  # idempotent
    with pytest.raises(ValueError, match="bump the version"):
        registry.register(B)
    assert len(DEFAULT_REGISTRY.catalog()) == 9
