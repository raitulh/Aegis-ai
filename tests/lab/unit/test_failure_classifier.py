"""Failure classifier: every failure type, subtypes, rule precedence, signatures and similarity."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from engines.lab.failures import (
    RULES,
    Classification,
    FailureSignals,
    classify_failure,
    failure_signature,
    find_similar,
    non_finite_metric_keys,
    normalize_traceback,
    rank_similar,
    similarity,
)
from engines.lab.states import FailureType


def _tb(exc_line: str, *, path: str = "/workspace/code/train.py", line: int = 42, func: str = "main") -> str:
    return (
        "Traceback (most recent call last):\n"
        f'  File "{path}", line {line + 10}, in <module>\n'
        "    main()\n"
        f'  File "{path}", line {line}, in {func}\n'
        "    do_work()\n"
        f"{exc_line}\n"
    )


def classify(**kwargs) -> Classification:
    return classify_failure(FailureSignals(**kwargs))


# ---------------------------------------------------------------------------------------------
# One test (at least) per failure type
# ---------------------------------------------------------------------------------------------
def test_policy_denied_by_decision():
    c = classify(stage="execution", policy_decision="deny", error_message="blocked")
    assert c.failure_type is FailureType.POLICY_FAILURE
    assert c.subtype == "denied"
    assert c.matched_rules[0] == "policy.denied"
    assert c.confidence >= 0.95


def test_policy_denied_by_error_class():
    c = classify(error_class="PolicyDenied", error_message="network egress not allowed")
    assert (c.failure_type, c.subtype) == (FailureType.POLICY_FAILURE, "denied")


def test_policy_budget_exceeded():
    c = classify(error_class="BudgetExceeded", error_message="mission budget exhausted")
    assert (c.failure_type, c.subtype) == (FailureType.POLICY_FAILURE, "budget_exceeded")


def test_policy_approval_required():
    c = classify(stage="policy", policy_decision="require_approval")
    assert (c.failure_type, c.subtype) == (FailureType.POLICY_FAILURE, "approval_required")


def test_resource_oom_killed():
    c = classify(exit_code=137, oom_killed=True)
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "oom")
    assert c.matched_rules == ["resource.oom_killed", "resource.sigkill"]
    assert "out-of-memory" in c.root_cause


def test_resource_exit_137_without_flag_is_probable_oom():
    c = classify(exit_code=137)
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "oom")
    assert c.matched_rules[0] == "resource.sigkill"
    assert c.confidence < 0.9


def test_resource_memory_error_traceback():
    c = classify(exit_code=1, traceback=_tb("MemoryError"))
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "oom")
    assert c.matched_rules[0] == "resource.memory_error"


def test_resource_timeout():
    c = classify(timed_out=True, exit_code=124)
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "timeout")


def test_resource_disk_full():
    c = classify(exit_code=1, traceback=_tb("OSError: [Errno 28] No space left on device"))
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "disk_full")


def test_resource_gpu_oom():
    c = classify(
        exit_code=1,
        traceback=_tb("torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"),
    )
    assert (c.failure_type, c.subtype) == (FailureType.RESOURCE_FAILURE, "gpu_oom")


def test_code_dependency():
    c = classify(exit_code=1, traceback=_tb("ModuleNotFoundError: No module named 'sklearn'"))
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "dependency")
    assert c.evidence["module"] == "sklearn"
    assert "sklearn" in c.root_cause


def test_code_dependency_submodule_uses_top_level_package():
    c = classify(exit_code=1, error_message="ModuleNotFoundError: No module named 'skimage.filters'")
    assert c.evidence["module"] == "skimage"


def test_code_import_error_cannot_import_name():
    c = classify(exit_code=1, traceback=_tb("ImportError: cannot import name 'foo' from 'numpy'"))
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "dependency")
    assert c.evidence["module"] == "numpy"


@pytest.mark.parametrize("exc", ["SyntaxError: invalid syntax", "IndentationError: unexpected indent"])
def test_code_syntax(exc):
    c = classify(exit_code=1, traceback=_tb(exc))
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "syntax")


@pytest.mark.parametrize(
    "exc",
    [
        "NameError: name 'x' is not defined",
        "TypeError: unsupported operand type(s)",
        "AttributeError: 'NoneType' object has no attribute 'fit'",
        "IndexError: list index out of range",
        "KeyError: 'label'",
        "ZeroDivisionError: division by zero",
    ],
)
def test_code_runtime_in_user_code(exc):
    c = classify(exit_code=1, traceback=_tb(exc))
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "runtime")
    assert c.evidence["in_library_code"] is False
    assert c.confidence == 0.8


def test_code_runtime_in_library_code_has_lower_confidence():
    c = classify(
        exit_code=1,
        traceback=_tb("TypeError: bad operand", path="/usr/local/lib/python3.12/site-packages/pandas/core/frame.py"),
    )
    assert c.subtype == "runtime"
    assert c.evidence["in_library_code"] is True
    assert c.confidence == 0.6


def test_file_not_found_outside_data_paths_is_code():
    c = classify(exit_code=1, traceback=_tb("FileNotFoundError: [Errno 2] No such file or directory: 'config.yaml'"))
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "runtime")


def test_data_missing_input():
    c = classify(
        exit_code=1,
        traceback=_tb("FileNotFoundError: [Errno 2] No such file or directory: '/workspace/input/train.csv'"),
    )
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "missing_input")
    assert c.evidence["missing_path"] == "train.csv"


def test_data_missing_input_shell_style_message():
    c = classify(exit_code=1, logs="cat: /workspace/input/data/x.jsonl: No such file or directory")
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "missing_input")


def test_data_parse_error():
    c = classify(
        exit_code=1,
        traceback=_tb("pandas.errors.ParserError: Error tokenizing data. C error: Expected 3 fields in line 5, saw 4"),
    )
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "parse_error")


def test_data_encoding_error():
    c = classify(
        exit_code=1,
        traceback=_tb("UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"),
    )
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "encoding_error")


def test_data_type_conversion_beats_generic_value_error():
    c = classify(exit_code=1, traceback=_tb("ValueError: could not convert string to float: 'abc'"))
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "type_conversion")
    assert "code.runtime_generic" in c.matched_rules


def test_data_shape_mismatch():
    c = classify(
        exit_code=1,
        traceback=_tb("ValueError: Found input variables with inconsistent numbers of samples: [100, 99]"),
    )
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "shape_mismatch")


def test_data_empty_dataset():
    c = classify(exit_code=1, traceback=_tb("pandas.errors.EmptyDataError: No columns to parse from file"))
    assert (c.failure_type, c.subtype) == (FailureType.DATA_FAILURE, "empty_dataset")


def test_evaluation_metrics_missing_on_success():
    c = classify(exit_code=0, metrics_file_present=False)
    assert (c.failure_type, c.subtype) == (FailureType.EVALUATION_FAILURE, "metrics_missing")


def test_evaluation_non_finite_metrics():
    c = classify(exit_code=0, metrics_file_present=True, metrics={"loss": math.nan, "acc": 0.9, "f1": "inf"})
    assert (c.failure_type, c.subtype) == (FailureType.EVALUATION_FAILURE, "non_finite_metrics")
    assert c.evidence["non_finite_metrics"] == ["f1", "loss"]


def test_evaluation_metrics_empty():
    c = classify(exit_code=0, metrics_file_present=True, metrics={})
    assert (c.failure_type, c.subtype) == (FailureType.EVALUATION_FAILURE, "metrics_empty")


def test_evaluation_evaluator_error_beats_code_rules():
    c = classify(stage="evaluation", traceback=_tb("TypeError: predictions must be a list"))
    assert (c.failure_type, c.subtype) == (FailureType.EVALUATION_FAILURE, "evaluator_error")
    assert "code.runtime" in c.matched_rules


@pytest.mark.parametrize(
    ("provider_error", "subtype"),
    [
        ("RateLimitError", "rate_limited"),
        ("DeadlineExceeded", "timeout"),
        ("ServiceUnavailable", "unavailable"),
        ("ContextLengthExceeded", "context_overflow"),
        ("SafetyBlocked", "content_blocked"),
        ("AuthenticationError", "auth_error"),
        ("WeirdProviderFailure", "provider_error"),
    ],
)
def test_model_provider_errors(provider_error, subtype):
    c = classify(stage="agent", provider_error_class=provider_error)
    assert (c.failure_type, c.subtype) == (FailureType.MODEL_FAILURE, subtype)


def test_model_invalid_output():
    c = classify(stage="agent", error_class="LLMOutputInvalid", error_message="response did not match schema")
    assert (c.failure_type, c.subtype) == (FailureType.MODEL_FAILURE, "invalid_output")


def test_model_unavailable_error_class():
    c = classify(stage="agent", error_class="ModelUnavailable")
    assert (c.failure_type, c.subtype) == (FailureType.MODEL_FAILURE, "unavailable")


def test_tool_stage_error():
    c = classify(stage="tool", tool_name="url_fetch", error_message="HTTP 502 from upstream")
    assert (c.failure_type, c.subtype) == (FailureType.TOOL_FAILURE, "error")
    assert "url_fetch" in c.root_cause


def test_tool_timeout_is_tool_not_resource():
    c = classify(stage="tool", tool_name="python_execution", timed_out=True)
    assert (c.failure_type, c.subtype) == (FailureType.TOOL_FAILURE, "timeout")
    assert "resource.timeout" in c.matched_rules


def test_tool_named_error_in_agent_stage():
    c = classify(stage="agent", tool_name="paper_search", error_message="upstream returned invalid JSON")
    assert c.failure_type is FailureType.TOOL_FAILURE


def test_experiment_design_failure():
    c = classify(stage="design", validation_issue_codes=["DATA_LEAKAGE"])
    assert (c.failure_type, c.subtype) == (FailureType.EXPERIMENT_DESIGN_FAILURE, "data_leakage")
    assert c.evidence["validation_issue_codes"] == ["DATA_LEAKAGE"]


def test_experiment_design_multiple_issues_and_warnings_ignored():
    c = classify(
        stage="design", validation_issue_codes=["MISSING_BASELINE", "warning:NETWORK_REQUESTED", "NO_PRIMARY_METRIC"]
    )
    assert c.subtype == "multiple_issues"
    assert c.evidence["validation_issue_codes"] == ["MISSING_BASELINE", "NO_PRIMARY_METRIC"]


def test_only_warning_codes_do_not_trigger_design_failure():
    c = classify(stage="design", validation_issue_codes=["warning:STATISTICAL_PLAN_WEAK"])
    assert c.failure_type is not FailureType.EXPERIMENT_DESIGN_FAILURE


def test_statistical_insufficient_seeds():
    c = classify(stage="statistics", p_value=0.2, n_seeds=2, required_seeds=5)
    assert (c.failure_type, c.subtype) == (FailureType.STATISTICAL_FAILURE, "insufficient_seeds")


def test_statistical_underpowered():
    c = classify(stage="statistics", p_value=0.3, adequately_powered=False, power=0.35)
    assert (c.failure_type, c.subtype) == (FailureType.STATISTICAL_FAILURE, "underpowered")


@pytest.mark.parametrize("p_value", [None, math.nan])
def test_statistical_p_value_missing(p_value):
    c = classify(stage="statistics", p_value=p_value)
    assert (c.failure_type, c.subtype) == (FailureType.STATISTICAL_FAILURE, "p_value_missing")


def test_hypothesis_non_significant_is_negative_result():
    c = classify(stage="statistics", p_value=0.41, adequately_powered=True, power=0.9, effect_direction="expected")
    assert (c.failure_type, c.subtype) == (FailureType.HYPOTHESIS_FAILURE, "non_significant")
    assert "legitimate negative result" in c.root_cause


def test_hypothesis_opposite_effect():
    c = classify(stage="statistics", p_value=0.001, adequately_powered=True, power=0.8, effect_direction="opposite")
    assert (c.failure_type, c.subtype) == (FailureType.HYPOTHESIS_FAILURE, "opposite_effect")


def test_hypothesis_confidence_reflects_power():
    low = classify(stage="statistics", p_value=0.5, adequately_powered=True, power=0.8)
    high = classify(stage="statistics", p_value=0.5, adequately_powered=True, power=0.99)
    unknown = classify(stage="statistics", p_value=0.5, adequately_powered=True)
    assert low.confidence < high.confidence
    assert unknown.confidence == 0.75


def test_significant_expected_effect_is_not_a_hypothesis_failure():
    c = classify(stage="statistics", p_value=0.01, adequately_powered=True, power=0.9, effect_direction="expected")
    assert c.failure_type is not FailureType.HYPOTHESIS_FAILURE


@pytest.mark.parametrize(
    ("verdict", "subtype"), [("not_reproduced", "not_reproduced"), ("partially_reproduced", "partially_reproduced")]
)
def test_reproducibility(verdict, subtype):
    c = classify(stage="reproduction", reproduction_verdict=verdict)
    assert (c.failure_type, c.subtype) == (FailureType.REPRODUCIBILITY_FAILURE, subtype)


def test_strategy_repeated_failures_at_strategy_stage():
    c = classify(stage="strategy", repeated_failures_for_strategy=5, error_message="3 experiments failed")
    assert (c.failure_type, c.subtype) == (FailureType.STRATEGY_FAILURE, "repeated_failures")
    assert c.confidence > 0.8


def test_strategy_below_threshold_is_not_strategy_failure():
    c = classify(stage="strategy", repeated_failures_for_strategy=2)
    assert c.failure_type is not FailureType.STRATEGY_FAILURE


def test_strategy_repeats_elsewhere_are_secondary_to_specific_cause():
    c = classify(exit_code=137, oom_killed=True, repeated_failures_for_strategy=4)
    assert c.failure_type is FailureType.RESOURCE_FAILURE
    assert "strategy.repeated_failures_any_stage" in c.matched_rules
    assert c.evidence["secondary_types"] == ["STRATEGY_FAILURE"]


def test_strategy_repeats_alone_classify_as_strategy():
    c = classify(stage="execution", repeated_failures_for_strategy=3)
    assert c.failure_type is FailureType.STRATEGY_FAILURE


def test_fallback_unknown_has_low_confidence():
    c = classify(exit_code=3, error_message="something odd happened")
    assert (c.failure_type, c.subtype) == (FailureType.CODE_FAILURE, "unknown")
    assert c.matched_rules == ["fallback.unknown"]
    assert c.confidence <= 0.3


def test_fallback_without_any_signal():
    c = classify()
    assert c.subtype == "unknown"
    assert c.confidence < 0.3


def test_all_twelve_failure_types_are_reachable_by_rules():
    assert {rule.failure_type for rule in RULES} == set(FailureType)


def test_rule_ids_are_unique():
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------------------------
def test_oom_beats_generic_code_error():
    c = classify(exit_code=137, oom_killed=True, traceback=_tb("KeyError: 'x'"))
    assert c.failure_type is FailureType.RESOURCE_FAILURE
    assert c.matched_rules.index("resource.oom_killed") < c.matched_rules.index("code.runtime")


def test_memory_error_beats_runtime_error():
    c = classify(exit_code=1, traceback=_tb("MemoryError: Unable to allocate 32.0 GiB for an array"))
    assert c.failure_type is FailureType.RESOURCE_FAILURE


def test_timeout_beats_sigkill_subtype():
    c = classify(timed_out=True, exit_code=137)
    assert c.subtype == "timeout"


def test_policy_beats_resource():
    c = classify(policy_decision="deny", oom_killed=True, exit_code=137)
    assert c.failure_type is FailureType.POLICY_FAILURE


def test_design_errors_beat_execution_signals():
    c = classify(stage="design", validation_issue_codes=["IMPOSSIBLE_RESOURCES"], exit_code=1)
    assert c.failure_type is FailureType.EXPERIMENT_DESIGN_FAILURE


def test_explicit_error_class_is_used_over_traceback():
    c = classify(error_class="ZeroDivisionError", traceback="some unstructured log")
    assert c.subtype == "runtime"


def test_classification_is_deterministic():
    signals = FailureSignals(exit_code=1, traceback=_tb("KeyError: 'label'"), logs="epoch 1\nepoch 2")
    assert classify_failure(signals) == classify_failure(signals.model_copy())


def test_evidence_does_not_leak_quoted_data_values():
    c = classify(exit_code=1, traceback=_tb("ValueError: could not convert string to float: 'patient-4711-secret'"))
    assert "patient-4711-secret" not in c.root_cause
    assert "patient-4711-secret" not in str(c.evidence)


def test_warning_lines_in_logs_are_not_taken_as_the_exception():
    logs = "UserWarning: something deprecated\nKilled"
    c = classify(exit_code=137, logs=logs)
    assert c.failure_type is FailureType.RESOURCE_FAILURE


def test_signals_reject_unknown_fields():
    with pytest.raises(ValidationError):
        FailureSignals(stage="execution", exitcode=1)  # type: ignore[call-arg]


def test_signals_reject_unknown_stage():
    with pytest.raises(ValidationError):
        FailureSignals(stage="deployment")  # type: ignore[arg-type]


def test_huge_logs_are_bounded():
    logs = ("noise line\n" * 200_000) + "MemoryError"
    c = classify(exit_code=1, logs=logs)
    assert c.failure_type is FailureType.RESOURCE_FAILURE


# ---------------------------------------------------------------------------------------------
# Normalization, signatures, similarity
# ---------------------------------------------------------------------------------------------
def test_normalize_traceback_strips_volatile_parts():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/tmpab12cd34/run/model.py", line 118, in forward\n'
        "    out = layer(x)\n"
        "           ^^^^^^^^\n"
        "RuntimeError: tensor at 0x7f3a9c0b2d10 for run 123e4567-e89b-12d3-a456-426614174000 failed at "
        "2026-09-30T12:00:01Z with value 'secret' after 3.5 s in C:\\Users\\bob\\AppData\\Local\\Temp\\x.py"
    )
    out = normalize_traceback(text)
    assert 'File "model.py", line N, in forward' in out
    assert "/tmp/" not in out and "tmpab12cd34" not in out
    assert "0x7f3a" not in out and "<hex>" in out
    assert "<uuid>" in out and "123e4567" not in out
    assert "<ts>" in out
    assert "'<v>'" in out and "secret" not in out
    assert "3.5" not in out and "118" not in out
    assert "Users" not in out and "x.py" in out
    assert "^^^" not in out
    assert "RuntimeError" in out


def test_normalize_keeps_identifiers_with_digits():
    out = normalize_traceback("TypeError: expected float64 but got int32 from resnet50")
    assert "float64" in out and "int32" in out and "resnet50" in out


def test_normalize_does_not_mangle_urls():
    out = normalize_traceback("ConnectionError: https://api.example.org/v1/items failed")
    assert "api.example.org" in out


def test_normalize_empty():
    assert normalize_traceback(None) == ""
    assert normalize_traceback("") == ""


def test_signature_stable_across_paths_and_line_numbers():
    a = _tb("KeyError: 'label'", path="/home/alice/proj/train.py", line=10)
    b = _tb("KeyError: 'target'", path="/workspace/code/train.py", line=250)
    sig_a = failure_signature("CODE_FAILURE", "runtime", a)
    sig_b = failure_signature("CODE_FAILURE", "runtime", b)
    assert sig_a == sig_b
    assert len(sig_a) == 32 and all(ch in "0123456789abcdef" for ch in sig_a)


def test_signature_differs_for_different_exception_function_or_type():
    base = failure_signature("CODE_FAILURE", "runtime", _tb("KeyError: 'x'"))
    assert base != failure_signature("CODE_FAILURE", "runtime", _tb("IndexError: list index out of range"))
    assert base != failure_signature("CODE_FAILURE", "runtime", _tb("KeyError: 'x'", func="evaluate"))
    assert base != failure_signature("DATA_FAILURE", "runtime", _tb("KeyError: 'x'"))
    assert base != failure_signature("CODE_FAILURE", "syntax", _tb("KeyError: 'x'"))


def test_signature_discriminator_separates_missing_modules():
    tb = _tb("ModuleNotFoundError: No module named 'x'")
    assert failure_signature("CODE_FAILURE", "dependency", tb, discriminator="torch") != failure_signature(
        "CODE_FAILURE", "dependency", tb, discriminator="sklearn"
    )


def test_classification_signature_distinguishes_missing_modules_but_not_paths():
    a = classify(exit_code=1, traceback=_tb("ModuleNotFoundError: No module named 'sklearn'", line=3))
    b = classify(
        exit_code=1, traceback=_tb("ModuleNotFoundError: No module named 'sklearn'", line=99, path="/x/train.py")
    )
    c = classify(exit_code=1, traceback=_tb("ModuleNotFoundError: No module named 'torch'"))
    assert a.signature == b.signature
    assert a.signature != c.signature


def test_signature_of_unstructured_text():
    assert failure_signature("RESOURCE_FAILURE", "oom", "Killed at 12:00") == failure_signature(
        "RESOURCE_FAILURE", "oom", "Killed at 13:37"
    )


def test_similarity_identical_and_unrelated():
    a = _tb("KeyError: 'label'")
    assert similarity(a, a) == 1.0
    assert similarity(a, "network unreachable while downloading weights from the hub") < 0.2


def test_similarity_near_duplicates_high_and_symmetric():
    a = _tb("KeyError: 'label'", line=10)
    b = _tb("KeyError: 'target'", line=77, path="/other/train.py")
    assert similarity(a, b) == 1.0
    c = a + "During handling, extra context line\n"
    assert 0.5 < similarity(a, c) < 1.0
    assert similarity(a, c) == similarity(c, a)


def test_similarity_empty_inputs():
    assert similarity("", "") == 0.0
    assert similarity(None, "x") == 0.0


def test_similarity_short_texts():
    assert similarity("KeyError", "KeyError") == 1.0
    assert similarity("KeyError", "IndexError") == 0.0


def test_find_similar_ranks_and_thresholds():
    target = _tb("KeyError: 'label'")
    candidates = [
        ("f3", None, "totally different network failure while fetching"),
        ("f2", None, _tb("KeyError: 'other'") + "extra trailing log line here\n"),
        ("f1", None, _tb("KeyError: 'label'", line=5)),
    ]
    assert find_similar(target, candidates, threshold=0.5) == ["f1", "f2"]
    assert find_similar(target, candidates, threshold=0.5, limit=1) == ["f1"]


def test_find_similar_exact_signature_scores_one():
    ranked = rank_similar(
        "text a", [("f9", "sig-1", "unrelated"), ("f1", "sig-2", "text a")], 0.9, target_signature="sig-1"
    )
    assert ranked == [("f1", 1.0), ("f9", 1.0)]  # tie broken by id


def test_find_similar_rejects_bad_threshold():
    with pytest.raises(ValueError):
        find_similar("a", [], threshold=1.5)


def test_non_finite_metric_keys_nested_and_strings():
    metrics = {"a": 1.0, "b": {"c": math.inf, "d": [0.1, -math.inf]}, "e": "NaN", "f": "fine", "g": True}
    assert non_finite_metric_keys(metrics) == ["b.c", "b.d[1]", "e"]
    assert non_finite_metric_keys(None) == []


def test_adversarial_logs_are_processed_in_linear_time():
    import time

    started = time.perf_counter()
    classify(exit_code=1, logs="a/" * 30_000 + ": No such file or directory")
    classify(exit_code=1, logs="'" * 60_000)
    assert time.perf_counter() - started < 5.0
