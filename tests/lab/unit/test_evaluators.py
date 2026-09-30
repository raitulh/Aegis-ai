"""Built-in evaluators and the registry (pure; files are handed in as bytes)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pytest

from engines.lab.evaluators import (
    BUILTIN_EVALUATORS,
    ArtifactBundle,
    EvalContext,
    Evaluator,
    EvaluatorConfigError,
    ExperimentView,
    ResourceUsage,
    RunRecord,
    UnknownEvaluatorError,
    config_hash,
    evaluator_manifest,
    get_evaluator,
    list_evaluators,
    validate_config,
)
from engines.lab.evaluators.base import EvaluationResult
from engines.lab.evaluators.code_quality import analyze_source
from engines.lab.experiment_spec import ExperimentSpec, StatisticalPlan, SuccessCriterion
from tests.lab.unit.test_experiment_spec import clean_spec

SEEDS = [0, 1, 2, 3, 4]
BASE_ACC = [0.80, 0.81, 0.79, 0.80, 0.82]
CAND_ACC = [0.86, 0.87, 0.85, 0.88, 0.86]
BASE_LAT = [150.0, 152.0, 149.0, 151.0, 150.0]
CAND_LAT = [150.5, 151.0, 150.0, 149.5, 151.0]


def runs(
    values: dict[str, list[float]], seeds: list[int] = SEEDS, status: str = "SUCCEEDED", prefix: str = "cand"
) -> list[RunRecord]:
    return [
        RunRecord(
            run_id=f"{prefix}-{status.lower()}-{seed}",
            seed=seed,
            status=status,
            metrics={k: v[i] for k, v in values.items()},
        )
        for i, seed in enumerate(seeds)
    ]


def view(spec: ExperimentSpec | None = None, **kwargs: Any) -> ExperimentView:
    kwargs.setdefault("runs", runs({"accuracy": CAND_ACC, "latency_ms": CAND_LAT}))
    kwargs.setdefault("baseline_runs", runs({"accuracy": BASE_ACC, "latency_ms": BASE_LAT}, prefix="base"))
    return ExperimentView(
        experiment_id="exp-1", version_id="v1", status="COMPLETED", spec=spec or clean_spec(), **kwargs
    )


def csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    lines = [",".join(header)] + [",".join(str(v) for v in row) for row in rows]
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------
def test_registry_has_nine_pinned_evaluators():
    assert set(BUILTIN_EVALUATORS) == {
        "metric",
        "regression",
        "classification",
        "benchmark",
        "statistical",
        "reproduction",
        "code_quality",
        "resource",
        "custom",
    }
    for key, (cls, version, kind) in BUILTIN_EVALUATORS.items():
        evaluator = get_evaluator(key)
        assert isinstance(evaluator, Evaluator)
        assert (
            (evaluator.key, evaluator.version, evaluator.kind)
            == (key, version, kind)
            == (cls.key, cls.version, cls.kind)
        )
        assert len(key) <= 80 and len(version) <= 24 and len(kind) <= 24  # lab_evaluators column limits
    assert [e["key"] for e in list_evaluators()] == sorted(BUILTIN_EVALUATORS)
    with pytest.raises(UnknownEvaluatorError):
        get_evaluator("vibes")


def test_config_validation_hash_and_manifest():
    assert validate_config("reproduction", {"rel_tol": 0.1})["rel_tol"] == 0.1
    with pytest.raises(EvaluatorConfigError):
        validate_config("reproduction", {"rel_tol": -1})
    with pytest.raises(EvaluatorConfigError):
        validate_config("metric", {"unknown_option": True})
    h = config_hash("metric", {})
    assert len(h) == 64 and h == config_hash("metric", {"aggregate": "mean"})  # defaults normalised
    assert h != config_hash("metric", {"aggregate": "median"})
    manifest = {row["key"]: row for row in evaluator_manifest()}
    assert len(manifest) == 9 and manifest["custom"]["default_config"] is None
    assert manifest["classification"]["independent"] is True and manifest["metric"]["independent"] is False


def test_results_are_json_safe():
    result = EvaluationResult(
        evaluator_key="x",
        evaluator_version="1.0.0",
        kind="metric",
        metrics={"good": 1.0, "bad": math.nan, "nested": {"v": math.inf}},
        passed=None,
        confidence=math.nan,
        independent=False,
    )
    assert "bad" not in result.metrics and result.metrics["nested"] == {"v": None} and result.confidence == 0.0
    json.loads(result.model_dump_json())


# ---------------------------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------------------------
def test_metric_evaluator_checks_criteria_over_seeds():
    result = get_evaluator("metric").evaluate(view())
    assert result.passed is True
    assert result.metrics["criteria_satisfied"] == 2.0
    # accuracy is evaluator-sourced, latency platform-measured → independent, full confidence
    assert result.independent is True and result.confidence == pytest.approx(1.0)
    assert result.evidence and result.evidence[0]["baseline_source"] == "baseline_runs"


def test_metric_evaluator_fails_and_marks_self_reported_dependence():
    spec = clean_spec(
        metrics=[
            {"name": "accuracy", "direction": "maximize", "primary": True},
            {"name": "latency_ms", "direction": "minimize"},
        ],
        success_criteria=[{"metric": "accuracy", "comparator": "delta_gte", "threshold": 0.1}],
    )
    result = get_evaluator("metric").evaluate(view(spec))
    assert result.passed is False and result.independent is False
    assert result.confidence == pytest.approx(0.5)  # self-reported provenance halves confidence
    assert any("self-reported" in w for w in result.warnings)


def test_metric_evaluator_missing_data_and_reference_baseline():
    few = view(runs=runs({"accuracy": CAND_ACC[:2], "latency_ms": CAND_LAT[:2]}, SEEDS[:2]))
    result = get_evaluator("metric").evaluate(few)
    assert result.passed is None and result.confidence == 0.0
    spec = clean_spec(
        baseline={"kind": "reference_values", "reference_metrics": {"accuracy": 0.845}, "justification": "paper"}
    )
    result = get_evaluator("metric").evaluate(view(spec, baseline_runs=[]))
    assert result.passed is True and result.evidence[0]["baseline_source"] == "reference_values"
    worst = get_evaluator("metric").evaluate(
        view(spec, baseline_runs=[]), context=EvalContext(config={"require_all_seeds": True})
    )
    assert worst.passed is False  # the mean passes (0.864 - 0.845 >= 0.01) but seed 0.85 - 0.845 < 0.01 fails
    boundary = clean_spec(
        baseline={"kind": "reference_values", "reference_metrics": {"accuracy": 0.84}, "justification": "x"}
    )
    exact = get_evaluator("metric").evaluate(
        view(boundary, baseline_runs=[]), context=EvalContext(config={"require_all_seeds": True})
    )
    assert exact.passed is True  # 0.85 - 0.84 == 0.01 up to rounding: inclusive comparators hold


# ---------------------------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------------------------
Y_TRUE = [3, -0.5, 2, 7]
Y_PRED = [2.5, 0.0, 2, 8]


def regression_bundle(pred: list[float] = Y_PRED, ids: list[int] | None = None, **meta: Any) -> ArtifactBundle:
    ids = ids or list(range(len(pred)))
    return ArtifactBundle(
        files={
            "predictions.csv": csv_bytes(["id", "prediction"], [[i, p] for i, p in zip(ids, pred, strict=True)]),
            "targets.csv": csv_bytes(["id", "target"], [[i, t] for i, t in enumerate(Y_TRUE)]),
        },
        metadata={"targets.csv": {"visibility": "evaluator_only", "dataset_version_id": "ds-1", **meta}},
    )


def test_regression_metrics_match_hand_computation():
    ctx = EvalContext(config={"thresholds": {"mae_max": 0.6, "r2_min": 0.9}})
    result = get_evaluator("regression").evaluate(view(), regression_bundle(), ctx)
    m = result.metrics
    assert m["mae"] == pytest.approx(0.5)
    assert m["rmse"] == pytest.approx(math.sqrt(0.375))
    assert m["r2"] == pytest.approx(0.9486081370449679, rel=1e-12)
    assert m["mape"] == pytest.approx(100 * (0.5 / 3 + 1.0 + 0 + 1 / 7) / 4)
    assert m["bias"] == pytest.approx(0.25) and m["max_abs_error"] == pytest.approx(1.0)
    assert result.passed is True and result.independent is True
    sha = hashlib.sha256(regression_bundle().files["targets.csv"]).hexdigest()
    assert any(e.get("sha256") == sha and e.get("role") == "targets" for e in result.evidence)
    failing = get_evaluator("regression").evaluate(
        view(), regression_bundle(), EvalContext(config={"thresholds": {"rmse_max": 0.1}})
    )
    assert failing.passed is False


def test_regression_mismatch_and_tamper_yield_no_verdict():
    mismatched = get_evaluator("regression").evaluate(
        view(), regression_bundle(ids=[0, 1, 2, 9]), EvalContext(config={"thresholds": {"mae_max": 10}})
    )
    assert mismatched.passed is None and any("mismatched ids" in w for w in mismatched.warnings)
    tampered = get_evaluator("regression").evaluate(view(), regression_bundle(sha256="0" * 64))
    assert tampered.passed is None and any("checksum mismatch" in w for w in tampered.warnings)
    missing = get_evaluator("regression").evaluate(view(), ArtifactBundle())
    assert missing.passed is None and missing.confidence == 0.0
    unconfigured = get_evaluator("regression").evaluate(view(), regression_bundle())
    assert unconfigured.passed is None and any("no thresholds" in w for w in unconfigured.warnings)


# ---------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------
LABELS = ["a", "a", "b", "b", "b", "c"]
PREDS = ["a", "b", "b", "b", "c", "c"]


def classification_bundle(
    preds: list[str] = PREDS, with_ids: bool = True, visibility: str = "evaluator_only"
) -> ArtifactBundle:
    if with_ids:
        p = csv_bytes(["id", "prediction"], [[i, v] for i, v in enumerate(preds)])
        lab = csv_bytes(["id", "label"], [[i, v] for i, v in enumerate(LABELS)])
    else:
        p = csv_bytes(["prediction"], [[v] for v in preds])
        lab = csv_bytes(["label"], [[v] for v in LABELS])
    return ArtifactBundle(
        files={"predictions.csv": p, "labels.csv": lab}, metadata={"labels.csv": {"visibility": visibility}}
    )


def test_classification_metrics_match_hand_computation():
    ctx = EvalContext(config={"thresholds": {"accuracy_min": 0.6}, "positive_label": "c"})
    result = get_evaluator("classification").evaluate(view(), classification_bundle(), ctx)
    m = result.metrics
    assert m["accuracy"] == pytest.approx(4 / 6)
    assert m["precision_macro"] == pytest.approx((1 + 2 / 3 + 0.5) / 3)
    assert m["recall_macro"] == pytest.approx((0.5 + 2 / 3 + 1) / 3)
    assert m["f1_macro"] == pytest.approx(2 / 3)
    assert m["f1_micro"] == pytest.approx(4 / 6) and m["precision_micro"] == m["recall_micro"] == m["accuracy"]
    assert m["f1_weighted"] == pytest.approx(2 / 3)
    assert m["balanced_accuracy"] == pytest.approx((0.5 + 2 / 3 + 1) / 3)
    assert m["confusion_matrix"] == {"labels": ["a", "b", "c"], "matrix": [[1, 1, 0], [0, 2, 1], [0, 0, 1]]}
    per_class = m["per_class"]
    assert isinstance(per_class, dict) and per_class["b"]["support"] == 3.0
    assert m["precision_binary"] == pytest.approx(0.5) and m["recall_binary"] == pytest.approx(1.0)
    assert result.passed is True and result.independent is True
    strict = get_evaluator("classification").evaluate(
        view(), classification_bundle(), EvalContext(config={"thresholds": {"f1_macro_min": 0.9}})
    )
    assert strict.passed is False


def test_classification_misalignment_and_label_visibility():
    short = get_evaluator("classification").evaluate(
        view(),
        classification_bundle(PREDS[:5], with_ids=False),
        EvalContext(config={"thresholds": {"accuracy_min": 0.1}}),
    )
    assert short.passed is None and any("row count mismatch" in w for w in short.warnings)
    visible = get_evaluator("classification").evaluate(view(), classification_bundle(visibility="experiment"))
    assert any("evaluator_only" in w for w in visible.warnings)
    never_predicted = get_evaluator("classification").evaluate(
        view(), classification_bundle(["a", "a", "b", "b", "b", "b"])
    )
    assert any("never predicted" in w for w in never_predicted.warnings)


# ---------------------------------------------------------------------------------------------
# Benchmark & statistical
# ---------------------------------------------------------------------------------------------
def test_benchmark_passes_on_significant_improvement():
    result = get_evaluator("benchmark").evaluate(view(), context=EvalContext(config={"guard_metrics": ["latency_ms"]}))
    assert result.passed is True and result.details["verdict"] == "improved"
    assert result.details["guard_verdicts"] == {"latency_ms": "no_significant_difference"}
    accuracy = result.metrics["accuracy"]
    assert isinstance(accuracy, dict) and accuracy["p_value_adjusted"] <= 0.05
    assert 0.9 < result.confidence <= 1.0


def test_benchmark_fails_on_guard_regression_and_small_effect():
    slow = view(runs=runs({"accuracy": CAND_ACC, "latency_ms": [190.0, 191.0, 189.0, 192.0, 190.0]}))
    result = get_evaluator("benchmark").evaluate(slow, context=EvalContext(config={"guard_metrics": ["latency_ms"]}))
    assert result.passed is False and result.details["guard_verdicts"]["latency_ms"] == "regressed"
    big_floor = get_evaluator("benchmark").evaluate(view(), context=EvalContext(config={"min_delta": 0.5}))
    assert big_floor.passed is False and big_floor.details["verdict"] == "no_significant_difference"
    none = get_evaluator("benchmark").evaluate(view(baseline_runs=[]))
    assert none.passed is None and none.details["verdict"] == "insufficient_data"


def test_benchmark_pairs_by_seed_for_paired_test():
    spec = clean_spec(statistical_plan={"test": "paired_t", "n_seeds": 5, "min_effect_size": 0.5})
    shuffled_base = runs({"accuracy": BASE_ACC[::-1], "latency_ms": BASE_LAT}, SEEDS[::-1])
    result = get_evaluator("benchmark").evaluate(view(spec, baseline_runs=shuffled_base))
    assert result.evidence[0]["paired_seeds"] == SEEDS and result.passed is True


def test_statistical_evaluator_plan_compliance():
    result = get_evaluator("statistical").evaluate(view())
    assert result.passed is True, result.details
    assert result.details["failed_checks"] == []
    checks = {e["check"]: e["passed"] for e in result.evidence if "passed" in e}
    assert checks == {
        "n_seeds": True,
        "seeds_declared": True,
        "correction": True,
        "significance": True,
        "ci_direction": True,
        "min_effect": True,
    }


def test_statistical_evaluator_detects_violations():
    cherry = view(runs=runs({"accuracy": CAND_ACC, "latency_ms": CAND_LAT}, [0, 1, 2, 3, 99]))
    result = get_evaluator("statistical").evaluate(cherry)
    assert result.passed is False and "seeds_declared" in result.details["failed_checks"]
    uncorrected = clean_spec(statistical_plan={"n_seeds": 5, "correction": "none", "min_effect_size": 0.5})
    result = get_evaluator("statistical").evaluate(view(uncorrected))
    assert result.passed is False and "correction" in result.details["failed_checks"]
    noise = view(runs=runs({"accuracy": [0.80, 0.83, 0.78, 0.81, 0.79], "latency_ms": CAND_LAT}))
    result = get_evaluator("statistical").evaluate(noise)
    assert {"significance", "ci_direction"} <= set(result.details["failed_checks"])
    few = get_evaluator("statistical").evaluate(
        view(baseline_runs=runs({"accuracy": BASE_ACC[:2], "latency_ms": BASE_LAT[:2]}, [0, 1]))
    )
    assert few.passed is False and "n_seeds" in few.details["failed_checks"]


# ---------------------------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------------------------
def repro(original: dict[str, float], reproduced: dict[str, float], **config: Any) -> Any:
    ctx = EvalContext(config={"original": original, "reproduced": reproduced, **config})
    return get_evaluator("reproduction").evaluate(view(), context=ctx)


def test_reproduction_verdicts_with_tolerance():
    ok = repro(
        {"accuracy": 0.86, "latency_ms": 150},
        {"accuracy": 0.855, "latency_ms": 152},
        metrics=["accuracy", "latency_ms"],
    )
    assert ok.details["verdict"] == "reproduced" and ok.passed is True
    partial = repro(
        {"accuracy": 0.86, "latency_ms": 150},
        {"accuracy": 0.855, "latency_ms": 180},
        metrics=["accuracy", "latency_ms"],
    )
    assert partial.details["verdict"] == "partially_reproduced" and partial.passed is False
    failed = repro(
        {"accuracy": 0.86, "latency_ms": 150}, {"accuracy": 0.70, "latency_ms": 151}, metrics=["accuracy", "latency_ms"]
    )
    assert failed.details["verdict"] == "not_reproduced"  # the primary metric failed
    abs_only = repro({"accuracy": 0.86}, {"accuracy": 0.85}, metrics=["accuracy"], rel_tol=0.0, abs_tol=0.005)
    assert abs_only.details["verdict"] == "not_reproduced"
    per_metric = repro(
        {"accuracy": 0.86},
        {"accuracy": 0.85},
        metrics=["accuracy"],
        rel_tol=0.0,
        per_metric={"accuracy": {"abs_tol": 0.02}},
    )
    assert per_metric.details["verdict"] == "reproduced"
    better = repro({"accuracy": 0.86}, {"accuracy": 0.95}, metrics=["accuracy"], one_sided=True)
    assert better.details["verdict"] == "reproduced"
    nothing = repro({}, {}, metrics=["nonexistent"])
    assert nothing.details["verdict"] == "inconclusive" and nothing.passed is None


def test_reproduction_from_runs():
    original = view(baseline_runs=runs({"accuracy": CAND_ACC, "latency_ms": CAND_LAT}))
    result = get_evaluator("reproduction").evaluate(original)
    assert result.details["verdict"] == "reproduced" and result.metrics["reproduced_fraction"] == 1.0


# ---------------------------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------------------------
def codes(source: str, **kw: Any) -> set[str]:
    return {f.code for f in analyze_source(source, "x.py", **kw).findings}


def test_code_quality_flags_dangerous_code():
    assert "FORBIDDEN_IMPORT" in codes("import subprocess\nsubprocess.run(['ls'])")
    assert "FORBIDDEN_IMPORT" in codes("from socket import socket")
    assert "FORBIDDEN_IMPORT" in codes("from multiprocessing import managers")
    assert "FORBIDDEN_IMPORT" in codes("import ctypes.util")
    assert "DANGEROUS_CALL" in codes("eval('1+1')")
    assert "DANGEROUS_CALL" in codes("exec('x=1')")
    assert "DANGEROUS_CALL" in codes("__import__('os')")
    assert "DANGEROUS_CALL" in codes("import os as o\no.system('id')")
    assert "DANGEROUS_CALL" in codes("from os import popen")
    assert "DANGEROUS_CALL" in codes("import os\nos.execv('/bin/sh', ['sh'])")
    assert "DANGEROUS_CALL" in codes("import os\ngetattr(os, 'system')('id')")
    assert "UNSAFE_DESERIALIZATION" in codes("import pickle\npickle.loads(b'')")
    assert "UNSAFE_DESERIALIZATION" in codes("import yaml\nyaml.load(open('c.yml'))")
    assert "UNSAFE_DESERIALIZATION" not in codes("import yaml\nyaml.load(s, Loader=yaml.SafeLoader)")
    assert "DYNAMIC_IMPORT" in codes("import importlib\nimportlib.import_module('x')")
    assert "SUSPICIOUS_PATH" in codes("open('/etc/shadow')")
    assert "SUSPICIOUS_PATH" in codes("x = 'unix:///var/run/docker.sock'")
    assert "PARSE_ERROR" in codes("def broken(:\n")


def test_code_quality_write_heuristics():
    assert "WRITE_OUTSIDE_OUTPUT" in codes("open('/etc/cron.d/x', 'w')")
    assert "WRITE_OUTSIDE_OUTPUT" in codes("df.to_csv('/data/out.csv')")
    assert "WRITE_OUTSIDE_OUTPUT" in codes("import torch\ntorch.save(model, '/root/m.pt')")
    assert "WRITE_OUTSIDE_OUTPUT" in codes("from pathlib import Path\nPath('results.txt').write_text('x')")
    assert "WRITE_OUTSIDE_OUTPUT" in codes("open('../x.txt', 'a')")
    assert "WRITE_OUTSIDE_OUTPUT" not in codes("open('/workspace/output/metrics.json', 'w')")
    assert "WRITE_OUTSIDE_OUTPUT" not in codes("open('/workspace/input/params.json')")
    assert "WRITE_OUTSIDE_OUTPUT" not in codes("s = 'a,b'.replace(',', ';')")
    assert "WRITE_OUTSIDE_OUTPUT" not in codes("p.write_text('hello')")


def test_code_quality_seed_detection():
    assert analyze_source("import numpy as np\nrng = np.random.default_rng(42)", "x.py").seeded
    assert not analyze_source("import numpy as np\nrng = np.random.default_rng()", "x.py").seeded
    assert analyze_source("import torch\ntorch.manual_seed(0)", "x.py").seeded
    assert analyze_source("import os\nseed = int(os.environ['AEGIS_SEED'])", "x.py").seeded
    assert analyze_source("from sklearn.ensemble import RandomForestClassifier as R\nR(random_state=1)", "x.py").seeded
    assert not analyze_source("import random\nx = random.random()", "x.py").seeded


def test_code_quality_evaluator_end_to_end():
    clean = get_evaluator("code_quality").evaluate(view())  # the spec's inline train.py is seeded and safe
    assert clean.passed is True and clean.metrics["seed_handling"] == 1.0 and clean.metrics["error_count"] == 0.0
    notebook = json.dumps(
        {
            "cells": [
                {"cell_type": "code", "source": ["!pip install requests\n", "import random\n"]},
                {"cell_type": "markdown", "source": "hi"},
            ]
        }
    ).encode()
    bundle = ArtifactBundle(files={"analysis.ipynb": notebook, "bad.py": b"import subprocess\n"})
    result = get_evaluator("code_quality").evaluate(view(), bundle, EvalContext(config={"include_spec_files": False}))
    found = {f["code"] for f in result.details["findings"]}
    assert result.passed is False and {"SHELL_ESCAPE", "FORBIDDEN_IMPORT", "NO_SEED_HANDLING"} <= found
    strict = get_evaluator("code_quality").evaluate(
        view(),
        ArtifactBundle(files={"a.py": b"print(1)\n"}),
        EvalContext(config={"include_spec_files": False, "require_seed_handling": True}),
    )
    assert strict.passed is False
    allowed = get_evaluator("code_quality").evaluate(
        view(),
        ArtifactBundle(files={"a.py": b"import socket\nimport random\nrandom.seed(1)\n"}),
        EvalContext(config={"include_spec_files": False, "allowed_modules": ["socket"]}),
    )
    assert allowed.passed is True
    empty = get_evaluator("code_quality").evaluate(ExperimentView())
    assert empty.passed is None


# ---------------------------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------------------------
def test_resource_efficiency_cost_and_oom():
    usage = [
        ResourceUsage(
            run_id="r1",
            cpu_seconds=50,
            wall_seconds=100,
            peak_memory_mb=512,
            requested_cpu=1,
            requested_memory_mb=1024,
            cost_usd=0.02,
        ),
        ResourceUsage(
            run_id="r2",
            cpu_seconds=30,
            wall_seconds=100,
            peak_memory_mb=960,
            requested_cpu=1,
            requested_memory_mb=1024,
            cost_usd=0.03,
        ),
    ]
    result = get_evaluator("resource").evaluate(view(resource_usage=usage))
    m = result.metrics
    assert m["cpu_efficiency"] == pytest.approx(0.4) and m["max_memory_ratio"] == pytest.approx(0.9375)
    assert m["total_cost_usd"] == pytest.approx(0.05)
    assert m["improvement"] == pytest.approx(0.06) and m["cost_per_unit_improvement"] == pytest.approx(0.05 / 0.06)
    assert result.details["oom_risk"] == "high" and result.passed is True and result.independent is True
    limited = get_evaluator("resource").evaluate(
        view(resource_usage=usage), context=EvalContext(config={"max_cost_usd": 0.01, "min_cpu_efficiency": 0.5})
    )
    assert limited.passed is False and set(limited.details["violations"]) == {"cost", "cpu_efficiency"}
    oom = get_evaluator("resource").evaluate(
        view(resource_usage=[ResourceUsage(oom_killed=True, cpu_seconds=1, wall_seconds=2)])
    )
    assert oom.passed is False and oom.details["oom_risk"] == "oom"
    assert get_evaluator("resource").evaluate(view()).passed is None


# ---------------------------------------------------------------------------------------------
# Custom
# ---------------------------------------------------------------------------------------------
def custom(expression: str, experiment: ExperimentView | None = None, **config: Any) -> Any:
    return get_evaluator("custom").evaluate(
        experiment or view(), context=EvalContext(config={"expression": expression, **config})
    )


def test_custom_expression_evaluation():
    assert custom("accuracy >= 0.85 and latency_ms < 200").passed is True
    assert custom("accuracy >= 0.9").passed is False
    assert custom("accuracy - baseline_accuracy >= 0.05").passed is True
    numeric = custom("accuracy * 100")
    assert numeric.passed is None and numeric.metrics["value"] == pytest.approx(86.4)
    missing = custom("precision > 0.5")
    assert missing.passed is None and any("not available" in w for w in missing.warnings)
    slashed = view(runs=runs({"val/loss": [0.3, 0.31, 0.29, 0.3, 0.3]}))
    assert custom("val_loss < 0.5", slashed).passed is True
    assert custom("loss < 0.5", slashed, variables={"loss": "val/loss"}).passed is True


@pytest.mark.parametrize("expression", ["__import__('os').system('id')", "accuracy.real > 0", "10 ** 1000 > 1"])
def test_custom_rejects_unsafe_expressions(expression):
    if expression.startswith("10 **"):
        # syntactically allowed; bounded at evaluation time → no verdict, never a crash
        assert custom(expression).passed is None
        return
    with pytest.raises(EvaluatorConfigError):
        custom(expression)


def test_evaluators_are_deterministic():
    for key in ("metric", "benchmark", "statistical", "reproduction", "code_quality"):
        a = get_evaluator(key).evaluate(view())
        b = get_evaluator(key).evaluate(view())
        assert a == b


def test_untrusted_run_metrics_are_filtered_not_fatal():
    untrusted: dict[str, Any] = {"accuracy": 0.9, "notes": "ok", "flag": True, "curve": [0.1, 0.2], "loss": math.nan}
    run = RunRecord.model_validate({"seed": 0, "metrics": untrusted})
    assert run.metrics["accuracy"] == 0.9 and math.isnan(run.metrics["loss"])
    assert run.ignored_metrics == ["curve", "flag", "notes"]
    experiment = ExperimentView(runs=[run])
    assert experiment.values("loss") == [] and experiment.non_finite_count("loss") == 1


def test_failed_runs_are_ignored_for_metrics():
    failed = runs({"accuracy": [0.99] * 5, "latency_ms": CAND_LAT}, SEEDS, status="FAILED")
    experiment = view(runs=runs({"accuracy": CAND_ACC, "latency_ms": CAND_LAT}) + failed)
    assert experiment.values("accuracy") == CAND_ACC
    result = get_evaluator("metric").evaluate(
        experiment,
        context=EvalContext(success_criteria=[SuccessCriterion(metric="accuracy", comparator="lt", threshold=0.9)]),
    )
    assert result.passed is True
    assert (
        get_evaluator("benchmark").evaluate(experiment, context=EvalContext(plan=StatisticalPlan(n_seeds=5))).passed
        is True
    )
