"""Canonical experiment spec: schema strictness, hashing, diffing and success-criterion semantics."""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from pydantic import ValidationError

from engines.lab.experiment_spec import (
    MAX_INLINE_FILE_BYTES,
    ExperimentSpec,
    SuccessCriterion,
    check_criterion,
    criterion_contradicts_direction,
    diff_specs,
    parse_spec,
    relative_improvement,
    spec_hash,
    spec_json_schema,
)

DIGEST = "sha256:" + "a" * 64
TRAIN_PY = "import os\nimport random\nrandom.seed(int(os.environ['AEGIS_SEED']))\nprint('ok')\n"


def clean_spec_dict() -> dict[str, Any]:
    """A scientifically clean candidate-vs-baseline design (shared by the lab unit tests)."""
    return {
        "objective": "Test whether the distilled model improves accuracy over the baseline at equal latency.",
        "hypothesis_id": "hyp-1",
        "kind": "candidate",
        "baseline": {"kind": "experiment", "experiment_id": "exp-baseline"},
        "method": "Train the distilled model and the baseline on the same split with 5 seeds each.",
        "variables": [
            {"name": "model_variant", "role": "independent", "values": ["baseline", "distilled"]},
            {"name": "accuracy", "role": "dependent"},
        ],
        "controls": [{"name": "epochs", "value": 10, "rationale": "fixed training budget"}],
        "datasets": [
            {"dataset_version_id": "ds-1", "split": "train", "role": "train"},
            {"dataset_version_id": "ds-1", "split": "validation", "role": "validation"},
            {"dataset_version_id": "ds-1", "split": "test", "role": "test"},
        ],
        "metrics": [
            {
                "name": "accuracy",
                "direction": "maximize",
                "primary": True,
                "source": "evaluator",
                "evaluator_key": "classification",
            },
            {"name": "latency_ms", "direction": "minimize", "source": "platform", "unit": "ms"},
        ],
        "success_criteria": [
            {"metric": "accuracy", "comparator": "delta_gte", "threshold": 0.01},
            {"metric": "latency_ms", "comparator": "lte", "threshold": 200},
        ],
        "statistical_plan": {
            "test": "welch_t",
            "alpha": 0.05,
            "min_effect_size": 0.5,
            "correction": "holm",
            "n_seeds": 5,
        },
        "seeds": [0, 1, 2, 3, 4],
        "environment": {
            "image": "python:3.12-slim",
            "image_digest": DIGEST,
            "dependencies": ["numpy==2.1.3", "scikit-learn==1.5.2"],
            "python_version": "3.12",
        },
        "code": {"entrypoint": "train.py", "files": {"train.py": TRAIN_PY}},
        "command": ["python", "train.py"],
        "parameters": {"learning_rate": 0.01},
    }


def clean_spec(**overrides: Any) -> ExperimentSpec:
    data = copy.deepcopy(clean_spec_dict())
    data.update(overrides)
    return ExperimentSpec.model_validate(data)


def test_clean_spec_parses_with_defaults():
    spec = clean_spec()
    assert spec.primary_metric is not None and spec.primary_metric.name == "accuracy"
    assert spec.metric_names == ["accuracy", "latency_ms"]
    assert spec.resources.cpu == 1.0 and spec.timeout_seconds == 900
    assert spec.reproducibility.seed_env_var == "AEGIS_SEED"
    assert spec.network.mode == "none"
    assert spec.environment.is_digest_pinned
    assert spec.datasets[0].effective_mount_path == "datasets/ds-1/train"
    assert spec.success_criteria[0].relative_to == "baseline"  # delta comparators default to baseline
    assert spec.success_criteria[1].relative_to == "absolute"


def test_schema_is_strict():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({**clean_spec_dict(), "unexpected": 1})
    with pytest.raises(ValidationError):
        clean_spec(metrics=[{"name": "acc", "direction": "up"}])
    with pytest.raises(ValidationError):
        clean_spec(seeds=[-1])
    with pytest.raises(ValidationError):
        clean_spec(network={"mode": "allowlist", "hosts": ["https://evil.example/path"]})
    with pytest.raises(ValidationError):
        clean_spec(statistical_plan={"alpha": 1.5})
    with pytest.raises(ValidationError):
        clean_spec(datasets=[{"dataset_version_id": "../etc", "role": "train"}])


def test_non_finite_and_oversized_values_are_rejected():
    with pytest.raises(ValidationError):
        clean_spec(parameters={"lr": math.nan})
    with pytest.raises(ValidationError):
        clean_spec(parameters={"blob": "x" * 70_000})
    with pytest.raises(ValidationError):
        clean_spec(expected_cost_usd=math.inf)
    with pytest.raises(ValidationError):
        clean_spec(code={"entrypoint": "a.py", "files": {"a.py": "x" * (MAX_INLINE_FILE_BYTES + 1)}})
    with pytest.raises(ValidationError):
        clean_spec(code={"entrypoint": "a.py", "files": {f"f{i}.py": "" for i in range(65)}})


def test_spec_hash_is_canonical():
    a = clean_spec_dict()
    b = dict(reversed(list(copy.deepcopy(a).items())))  # different key order
    b["resources"] = {"cpu": 1.0, "memory_mb": 1024}  # explicit defaults
    h = spec_hash(a)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert spec_hash(b) == h
    assert spec_hash(parse_spec(a)) == h
    changed = copy.deepcopy(a)
    changed["statistical_plan"]["alpha"] = 0.01
    assert spec_hash(changed) != h


def test_diff_specs_reports_dotted_paths():
    a = clean_spec_dict()
    b = copy.deepcopy(a)
    b["statistical_plan"]["alpha"] = 0.01
    b["seeds"].append(5)
    b["code"]["files"]["src/util.py"] = "X = 1\n"
    b["parameters"] = {}
    diff = diff_specs(a, b)
    assert diff["changed"]["statistical_plan.alpha"] == {"before": 0.05, "after": 0.01}
    assert diff["added"]["seeds.5"] == 5
    assert diff["added"]['code.files["src/util.py"]'] == "X = 1\n"
    assert diff["removed"]["parameters.learning_rate"] == 0.01
    assert diff_specs(a, a) == {"added": {}, "removed": {}, "changed": {}}


def test_json_schema_for_designer_agent():
    schema = spec_json_schema()
    assert "objective" in schema["properties"] and "statistical_plan" in schema["properties"]
    assert schema.get("additionalProperties") is False


def test_digest_pin_detection():
    env = clean_spec().environment.model_copy(update={"image_digest": None})
    assert not env.is_digest_pinned
    pinned = env.model_copy(update={"image": f"python:3.12-slim@{DIGEST}"})
    assert pinned.is_digest_pinned


def crit(comparator: str, threshold: float, relative_to: str | None = None) -> SuccessCriterion:
    data: dict[str, Any] = {"metric": "m", "comparator": comparator, "threshold": threshold}
    if relative_to:
        data["relative_to"] = relative_to
    return SuccessCriterion.model_validate(data)


@pytest.mark.parametrize(
    ("comparator", "threshold", "value", "expected"),
    [
        ("gt", 0.9, 0.91, True),
        ("gt", 0.9, 0.9, False),
        ("gte", 0.9, 0.9, True),
        ("lt", 200, 199.9, True),
        ("lte", 200, 200.1, False),
    ],
)
def test_absolute_comparators(comparator, threshold, value, expected):
    assert check_criterion(crit(comparator, threshold), value).satisfied is expected


def test_baseline_relative_comparators():
    assert check_criterion(crit("gt", 0.0, "baseline"), 0.81, 0.80).satisfied is True  # v > b + 0
    assert check_criterion(crit("gte", 0.02, "baseline"), 0.81, 0.80).satisfied is False  # v >= b + 0.02
    assert check_criterion(crit("delta_gte", 0.01), 0.815, 0.80).satisfied is True
    assert check_criterion(crit("delta_gt", 0.02), 0.815, 0.80).satisfied is False
    assert check_criterion(crit("delta_lte", -5), 90.0, 100.0, "minimize").satisfied is True
    assert check_criterion(crit("delta_lt", -20), 90.0, 100.0, "minimize").satisfied is False
    # direction-aware relative improvement
    assert check_criterion(crit("relative_improvement_gte", 0.05), 0.84, 0.80).satisfied is True
    assert check_criterion(crit("relative_improvement_gte", 0.05), 95.0, 100.0, "minimize").satisfied is True
    assert check_criterion(crit("relative_improvement_gte", 0.05), 105.0, 100.0, "minimize").satisfied is False
    assert relative_improvement(1.0, 0.0, "maximize") is None


def test_unevaluable_criteria_are_none_not_pass_or_fail():
    assert check_criterion(crit("gt", 0.5), None).satisfied is None
    assert check_criterion(crit("gt", 0.5), math.nan).satisfied is None
    assert check_criterion(crit("delta_gt", 0.0), 0.9, None).satisfied is None
    zero = check_criterion(crit("relative_improvement_gte", 0.1), 0.5, 0.0)
    assert zero.satisfied is None and "undefined" in zero.reason


def test_criterion_direction_contradictions():
    assert criterion_contradicts_direction(crit("lt", 0.5), "maximize")
    assert criterion_contradicts_direction(crit("delta_gt", 0.0), "minimize")
    assert not criterion_contradicts_direction(crit("delta_gte", -0.01), "maximize")  # non-inferiority margin
    assert not criterion_contradicts_direction(crit("relative_improvement_gte", 0.1), "minimize")
