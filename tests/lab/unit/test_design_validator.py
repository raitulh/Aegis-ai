"""Experiment design validator: a clean spec passes; every issue code is detected."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from engines.lab.design_validator import (
    ISSUE_CODES,
    ExperimentDesignValidator,
    Issue,
    ValidationContext,
    ValidationLimits,
    ValidationReport,
    check_dependency,
    unsafe_relative_path,
    validate_design,
)
from tests.lab.unit.test_experiment_spec import DIGEST, clean_spec_dict

CONTEXT = ValidationContext.model_validate(
    {
        "dataset_versions": {
            "ds-1": {
                "splits": {
                    "train": {"visibility": "experiment"},
                    "validation": {"visibility": "experiment"},
                    "test": {"visibility": "experiment"},
                    "heldout_labels": {"visibility": "evaluator_only"},
                }
            },
            "ds-2": {"splits": {"all": {"visibility": "experiment"}}},
        },
        "hypothesis_metric": "accuracy",
    }
)


def validate(
    mutate: Callable[[dict[str, Any]], Any] | None = None,
    *,
    context: ValidationContext | dict[str, Any] | None = CONTEXT,
    limits: ValidationLimits | None = None,
) -> ValidationReport:
    data = copy.deepcopy(clean_spec_dict())
    if mutate is not None:
        mutate(data)
    return ExperimentDesignValidator(limits).validate(data, context)


def issues(report: ValidationReport, code: str, severity: str | None = None) -> list[Issue]:
    return [i for i in report.issues if i.code == code and (severity is None or i.severity == severity)]


def test_clean_spec_passes_without_any_issue():
    report = validate()
    assert report.passed, report.issues
    assert report.issues == []
    assert "no issues" in report.summary


def test_invalid_spec_mapping_is_reported_not_raised():
    report = validate(lambda d: d.pop("method"))
    assert not report.passed and issues(report, "INVALID_SPEC")
    assert issues(report, "INVALID_SPEC")[0].field == "method"


def test_missing_baseline_for_candidate():
    report = validate(lambda d: d.update(baseline={"kind": "none"}))
    assert issues(report, "MISSING_BASELINE", "error")
    report = validate(lambda d: d.update(baseline={"kind": "experiment"}))
    assert any(i.field == "baseline.experiment_id" for i in issues(report, "MISSING_BASELINE", "error"))
    report = validate(
        lambda d: d.update(
            kind="reproduction", baseline={"kind": "reference_values", "reference_metrics": {"accuracy": 0.8}}
        )
    )
    assert issues(report, "MISSING_BASELINE", "error")
    report = validate(lambda d: d.update(baseline={"kind": "reference_values", "reference_metrics": {"accuracy": 0.8}}))
    assert issues(report, "MISSING_BASELINE", "warning")  # no justification


def test_missing_metrics():
    report = validate(lambda d: (d.update(metrics=[]), d.update(success_criteria=[])))
    assert issues(report, "MISSING_METRICS", "error")


def test_no_primary_metric():
    def mutate(d: dict[str, Any]) -> None:
        d["metrics"][0]["primary"] = False

    assert issues(validate(mutate), "NO_PRIMARY_METRIC", "error")


def test_multiple_primary_metrics():
    def mutate(d: dict[str, Any]) -> None:
        d["metrics"][1]["primary"] = True

    assert issues(validate(mutate), "MULTIPLE_PRIMARY_METRICS", "error")


def test_self_reported_primary_metric_warning():
    def mutate(d: dict[str, Any]) -> None:
        d["metrics"][0]["source"] = "self_reported"
        d["metrics"][0]["evaluator_key"] = None

    report = validate(mutate)
    assert report.passed and issues(report, "SELF_REPORTED_PRIMARY_METRIC", "warning")


def test_invalid_control_manipulated_and_without_value():
    report = validate(lambda d: d["controls"].append({"name": "model_variant", "value": "baseline"}))
    assert any("both a control" in i.message for i in issues(report, "INVALID_CONTROL", "error"))
    report = validate(lambda d: d["controls"].append({"name": "optimizer"}))
    assert any("no value" in i.message for i in issues(report, "INVALID_CONTROL", "error"))
    report = validate(lambda d: d["variables"].append({"name": "seed_policy", "role": "control"}))
    assert issues(report, "INVALID_CONTROL", "error")


def test_invalid_variable_and_incomplete_design():
    report = validate(
        lambda d: d["variables"].append({"name": "model_variant", "role": "independent", "values": [1, 2]})
    )
    assert issues(report, "INVALID_VARIABLE", "error")
    report = validate(lambda d: d.update(kind="ablation"))
    assert issues(report, "INCOMPLETE_DESIGN", "error")


def test_unclear_success_criteria():
    assert issues(validate(lambda d: d.update(success_criteria=[])), "UNCLEAR_SUCCESS_CRITERIA", "error")
    # baseline-relative criterion without any baseline (exploratory experiments may have none)
    report = validate(lambda d: d.update(kind="exploratory", baseline={"kind": "none"}))
    assert any("relative to a baseline" in i.message for i in issues(report, "UNCLEAR_SUCCESS_CRITERIA", "error"))
    # delta comparator explicitly marked absolute
    report = validate(lambda d: d["success_criteria"][0].update(relative_to="absolute"))
    assert issues(report, "UNCLEAR_SUCCESS_CRITERIA", "error")
    # contradictory thresholds can never hold together
    report = validate(
        lambda d: d["success_criteria"].append({"metric": "latency_ms", "comparator": "gt", "threshold": 300})
    )
    assert any("never be satisfied" in i.message for i in issues(report, "UNCLEAR_SUCCESS_CRITERIA", "error"))


def test_metric_mismatch():
    report = validate(lambda d: d["success_criteria"].append({"metric": "f1", "comparator": "gte", "threshold": 0.5}))
    assert issues(report, "METRIC_MISMATCH", "error") and issues(report, "UNCLEAR_SUCCESS_CRITERIA", "error")
    report = validate(
        lambda d: d["success_criteria"].append({"metric": "accuracy", "comparator": "lt", "threshold": 0.5})
    )
    assert any("against its direction" in i.message for i in issues(report, "METRIC_MISMATCH", "error"))
    report = validate(context={**CONTEXT.model_dump(), "hypothesis_metric": "f1"})
    assert any("does not measure" in i.message for i in issues(report, "METRIC_MISMATCH", "error"))
    report = validate(context={**CONTEXT.model_dump(), "hypothesis_metric": "latency_ms"})
    assert issues(report, "METRIC_MISMATCH", "warning")


def test_impossible_resources():
    limits = ValidationLimits(max_cpu=2, max_memory_mb=2048, max_disk_mb=4096, max_timeout_seconds=600)
    report = validate(
        lambda d: d.update(resources={"cpu": 8, "memory_mb": 65536, "disk_mb": 100_000, "gpu_count": 1}),
        limits=limits,
    )
    fields = {i.field for i in issues(report, "IMPOSSIBLE_RESOURCES", "error")}
    assert {"resources.cpu", "resources.memory_mb", "resources.disk_mb", "timeout_seconds"} <= fields
    assert "resources.gpu_count" in fields and "resources.gpu_type" in fields  # disabled + no type
    ok = validate(
        lambda d: d.update(resources={"gpu_count": 1, "gpu_type": "nvidia-l4"}),
        limits=ValidationLimits(gpu_enabled=True),
    )
    assert not issues(ok, "IMPOSSIBLE_RESOURCES")


def test_image_not_allowed():
    limits = ValidationLimits(allowed_images=["python:3.12-slim"])
    assert not issues(validate(limits=limits), "IMAGE_NOT_ALLOWED")
    report = validate(lambda d: d["environment"].update(image="evil/miner:latest"), limits=limits)
    assert issues(report, "IMAGE_NOT_ALLOWED", "error")


@pytest.mark.parametrize(
    "dep",
    [
        "numpy",
        "numpy>=1.26",
        "numpy~=1.26",
        "numpy==1.*",
        "numpy>=1,<2",
        "git+https://github.com/org/repo.git",
        "https://example.com/pkg-1.0.tar.gz",
        "pkg @ https://example.com/pkg.whl",
        "-e .",
        "--index-url https://mirror.example/simple",
        "--extra-index-url https://mirror.example/simple",
        "./local_pkg",
        "/opt/wheels/pkg-1.0-py3-none-any.whl",
        "bad name==1.0",
        "numpy==not-a-version",
    ],
)
def test_invalid_dependencies_are_rejected(dep):
    assert check_dependency(dep) is not None
    report = validate(lambda d: d["environment"]["dependencies"].append(dep))
    assert issues(report, "INVALID_DEPENDENCY", "error")


@pytest.mark.parametrize(
    "dep",
    [
        "numpy==2.1.3",
        "scikit-learn==1.5.2",
        "torch[cuda]==2.4.0",
        "pkg===1.0-custom",
        "numpy==2.1.3 --hash=sha256:" + "b" * 64,
    ],
)
def test_pinned_dependencies_are_accepted(dep):
    assert check_dependency(dep) is None


def test_conflicting_dependency_pins():
    report = validate(lambda d: d["environment"]["dependencies"].append("NumPy==1.26.4"))
    assert any("conflicting pins" in i.message for i in issues(report, "INVALID_DEPENDENCY", "error"))


def test_missing_reproducibility():
    assert issues(validate(lambda d: d.update(seeds=[])), "MISSING_REPRODUCIBILITY", "error")
    assert issues(validate(lambda d: d.update(seeds=[0, 1])), "MISSING_REPRODUCIBILITY", "error")
    assert issues(validate(lambda d: d.update(seeds=[0, 1, 2, 3, 4, 4])), "MISSING_REPRODUCIBILITY", "error")
    unpinned = validate(lambda d: d["environment"].update(image_digest=None))
    assert unpinned.passed and issues(unpinned, "MISSING_REPRODUCIBILITY", "warning")
    strict = validate(lambda d: d["environment"].update(image_digest=None), limits=ValidationLimits(strict=True))
    assert not strict.passed and issues(strict, "MISSING_REPRODUCIBILITY", "error")

    def required_pin(d: dict[str, Any]) -> None:
        d["environment"]["image_digest"] = None
        d["reproducibility"] = {"require_digest_pin": True}

    assert issues(validate(required_pin), "MISSING_REPRODUCIBILITY", "error")
    no_entry = validate(lambda d: (d.update(command=[]), d["code"].update(entrypoint=None)))
    assert any(i.field == "code.entrypoint" for i in issues(no_entry, "MISSING_REPRODUCIBILITY", "error"))
    no_code = validate(lambda d: d.update(code={"entrypoint": "train.py"}))
    assert any(i.field == "code" for i in issues(no_code, "MISSING_REPRODUCIBILITY", "error"))
    pinned_by_ref = validate(lambda d: d["environment"].update(image=f"python:3.12-slim@{DIGEST}", image_digest=None))
    assert not issues(pinned_by_ref, "MISSING_REPRODUCIBILITY")


def test_data_leakage_same_split_train_and_test():
    report = validate(lambda d: d["datasets"].append({"dataset_version_id": "ds-1", "split": "train", "role": "test"}))
    assert issues(report, "DATA_LEAKAGE", "error")
    whole = validate(
        lambda d: d.update(
            datasets=[
                {"dataset_version_id": "ds-2", "role": "train"},
                {"dataset_version_id": "ds-2", "split": "all", "role": "eval"},
            ]
        )
    )
    assert issues(whole, "DATA_LEAKAGE", "error")


def test_data_leakage_evaluator_only_split():
    report = validate(
        lambda d: d["datasets"].append({"dataset_version_id": "ds-1", "split": "heldout_labels", "role": "eval"})
    )
    assert any("evaluator-only" in i.message for i in issues(report, "DATA_LEAKAGE", "error"))
    whole = validate(
        lambda d: d["datasets"].append({"dataset_version_id": "ds-1", "role": "validation", "mount_path": "all"})
    )
    assert any("contains evaluator-only" in i.message for i in issues(whole, "DATA_LEAKAGE", "error"))


def test_data_leakage_target_as_feature():
    report = validate(lambda d: d["variables"].append({"name": "label", "role": "independent", "values": [0, 1]}))
    assert issues(report, "DATA_LEAKAGE", "error")
    report = validate(lambda d: d["parameters"].update(features=["age", "income", "churned"], target="churned"))
    assert any(i.field == "parameters.features" for i in issues(report, "DATA_LEAKAGE", "error"))


def test_data_leakage_hyperparameter_search_on_test():
    def search_without_validation(d: dict[str, Any]) -> None:
        d["kind"] = "sensitivity"
        d["sensitivity"] = [{"parameter": "learning_rate", "values": [0.1, 0.01, 0.001]}]
        d["datasets"] = [d["datasets"][0], d["datasets"][2]]  # train + test only

    assert issues(validate(search_without_validation), "DATA_LEAKAGE", "error")

    def search_with_validation(d: dict[str, Any]) -> None:
        d["kind"] = "ablation"
        d["ablations"] = [{"name": "lr_sweep", "changes": {"learning_rate": 0.1}}]

    assert issues(validate(search_with_validation), "DATA_LEAKAGE", "warning")


def test_data_leakage_shuffled_temporal_split():
    report = validate(lambda d: d["parameters"].update(split="temporal", shuffle=True))
    assert any(i.field == "parameters.shuffle" for i in issues(report, "DATA_LEAKAGE", "error"))
    assert not issues(validate(lambda d: d["parameters"].update(split="temporal", shuffle=False)), "DATA_LEAKAGE")


def test_unknown_dataset_and_split():
    report = validate(lambda d: d["datasets"].append({"dataset_version_id": "ds-404", "split": "test", "role": "eval"}))
    assert issues(report, "UNKNOWN_DATASET", "error")
    report = validate(
        lambda d: d["datasets"].append({"dataset_version_id": "ds-2", "split": "missing", "role": "eval"})
    )
    assert any(i.field.endswith(".split") for i in issues(report, "UNKNOWN_DATASET", "error"))


def test_unknown_evaluator():
    def mutate(d: dict[str, Any]) -> None:
        d["metrics"][0]["evaluator_key"] = "vibes"

    assert issues(validate(mutate), "UNKNOWN_EVALUATOR", "error")

    def missing_key(d: dict[str, Any]) -> None:
        d["metrics"][0]["evaluator_key"] = None

    assert issues(validate(missing_key), "UNKNOWN_EVALUATOR", "error")
    org = validate(mutate, context={**CONTEXT.model_dump(), "known_evaluators": {"vibes"}})
    assert not issues(org, "UNKNOWN_EVALUATOR")


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "data/../../x", "~/.ssh", "C:/x", "a\\b"])
def test_unsafe_mount_paths(path):
    assert unsafe_relative_path(path) is not None
    report = validate(lambda d: d["datasets"][0].update(mount_path=path))
    assert issues(report, "UNSAFE_MOUNT_PATH", "error")


def test_mount_path_collisions_and_unsafe_code_paths():
    def collide(d: dict[str, Any]) -> None:
        d["datasets"][0]["mount_path"] = "data"
        d["datasets"][1]["mount_path"] = "./data"

    assert any("collides" in i.message for i in issues(validate(collide), "UNSAFE_MOUNT_PATH", "error"))
    report = validate(lambda d: d["code"]["files"].update({"../escape.py": "x = 1"}))
    assert issues(report, "UNSAFE_MOUNT_PATH", "error")
    assert unsafe_relative_path("datasets/ds-1/train") is None


def test_network_requested_is_a_warning():
    report = validate(lambda d: d.update(network={"mode": "allowlist", "hosts": ["pypi.org"]}))
    assert report.passed and issues(report, "NETWORK_REQUESTED", "warning")


def test_statistical_plan_weak():
    report = validate(lambda d: d["statistical_plan"].update(alpha=0.2))
    assert any(i.field == "statistical_plan.alpha" for i in issues(report, "STATISTICAL_PLAN_WEAK", "warning"))
    report = validate(lambda d: (d["statistical_plan"].update(n_seeds=2), d.update(seeds=[0, 1])))
    assert any(i.field == "statistical_plan.n_seeds" for i in issues(report, "STATISTICAL_PLAN_WEAK", "warning"))
    report = validate(
        lambda d: (d["statistical_plan"].update(test="mann_whitney", n_seeds=3), d.update(seeds=[0, 1, 2]))
    )
    assert any("smallest possible" in i.message for i in issues(report, "STATISTICAL_PLAN_WEAK", "warning"))
    report = validate(lambda d: d["statistical_plan"].update(correction="none"))
    assert any(i.field == "statistical_plan.correction" for i in issues(report, "STATISTICAL_PLAN_WEAK"))
    report = validate(lambda d: d["statistical_plan"].update(min_effect_size=None))
    assert any(i.field == "statistical_plan.min_effect_size" for i in issues(report, "STATISTICAL_PLAN_WEAK"))


def test_every_documented_code_is_reachable():
    seen: set[str] = set()
    mutations = [
        lambda d: d.pop("method"),
        lambda d: d.update(baseline={"kind": "none"}, metrics=[], success_criteria=[]),
        lambda d: d["metrics"].__setitem__(0, {**d["metrics"][0], "primary": False}),
        lambda d: d["metrics"].__setitem__(1, {**d["metrics"][1], "primary": True}),
        lambda d: d["metrics"].__setitem__(0, {"name": "accuracy", "direction": "maximize", "primary": True}),
        lambda d: d["controls"].append({"name": "x"}),
        lambda d: d["variables"].append({"name": "model_variant", "role": "independent"}),
        lambda d: d.update(kind="ablation"),
        lambda d: d["success_criteria"].append({"metric": "f1", "comparator": "gt", "threshold": 0}),
        lambda d: d.update(resources={"cpu": 64}),
        lambda d: d["environment"].update(image="other:1"),
        lambda d: d["environment"]["dependencies"].append("numpy"),
        lambda d: d.update(seeds=[]),
        lambda d: d["datasets"].append({"dataset_version_id": "ds-1", "split": "train", "role": "test"}),
        lambda d: d["datasets"].append({"dataset_version_id": "zzz", "role": "eval"}),
        lambda d: d["metrics"].__setitem__(0, {**d["metrics"][0], "evaluator_key": "nope"}),
        lambda d: d["datasets"][0].update(mount_path="/abs"),
        lambda d: d.update(network={"mode": "allowlist", "hosts": ["pypi.org"]}),
        lambda d: d["statistical_plan"].update(alpha=0.5),
    ]
    for mutate in mutations:
        seen |= validate(mutate, limits=ValidationLimits(allowed_images=["python:3.12-slim"])).codes
    assert seen == set(ISSUE_CODES)


def test_validate_design_wrapper_and_determinism():
    a = validate_design(clean_spec_dict(), CONTEXT)
    b = validate_design(clean_spec_dict(), CONTEXT.model_dump())
    assert a == b and a.passed
