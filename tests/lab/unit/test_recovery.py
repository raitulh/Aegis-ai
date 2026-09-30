"""Recovery playbook and lesson extraction: bounded patches, caps respected, human-gated where required."""

from __future__ import annotations

import pytest

from engines.lab.failures import (
    DETERMINISTIC_FLAGS,
    IMPORT_PACKAGE_MAP,
    Classification,
    FailureSignals,
    RecoveryAction,
    RecoveryContext,
    classify_failure,
    extract_lesson,
    merge_patch,
    propose_recovery,
)
from engines.lab.states import FailureType


def classify(**kwargs) -> Classification:
    return classify_failure(FailureSignals(**kwargs))


OOM = classify(exit_code=137, oom_killed=True)
TIMEOUT = classify(timed_out=True)
GPU_OOM = classify(exit_code=1, error_message="RuntimeError: CUDA out of memory")
DISK = classify(exit_code=1, error_message="OSError: [Errno 28] No space left on device")


def missing_module(module: str) -> Classification:
    return classify(exit_code=1, error_message=f"ModuleNotFoundError: No module named '{module}'")


# ---------------------------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------------------------
def test_oom_doubles_memory_within_limit():
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 2048}, limits={"memory_mb": 16384}))
    assert p.action is RecoveryAction.INCREASE_MEMORY
    assert p.spec_patch == {"resources": {"memory_mb": 4096}}
    assert p.requires_approval is False
    assert p.automatic is True


def test_oom_increase_is_capped_at_limit():
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 12000}, limits={"max_memory_mb": 16384}))
    assert p.spec_patch == {"resources": {"memory_mb": 16384}}
    assert "capped" in p.rationale


@pytest.mark.parametrize("current", [1, 100, 1024, 3000, 8191, 8192, 9000, 16383])
@pytest.mark.parametrize("cap", [1024, 8192, 16384])
def test_memory_proposals_never_exceed_the_limit(current, cap):
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": current}, limits={"memory_mb": cap}))
    proposed = p.spec_patch.get("resources", {}).get("memory_mb")
    if proposed is not None:
        assert current < proposed <= cap
    else:
        assert current >= cap
        assert p.requires_approval or p.action is RecoveryAction.REDUCE_BATCH_SIZE


def test_oom_at_cap_halves_batch_size():
    ctx = RecoveryContext(resources={"memory_mb": 8192}, limits={"memory_mb": 8192}, parameters={"batch_size": 64})
    p = propose_recovery(OOM, ctx)
    assert p.action is RecoveryAction.REDUCE_BATCH_SIZE
    assert p.spec_patch == {"parameters": {"batch_size": 32}}
    assert p.requires_approval is False


def test_oom_at_cap_without_batch_size_has_no_automatic_fix():
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 8192}, limits={"memory_mb": 8192}))
    assert p.action is RecoveryAction.NO_AUTOMATIC_FIX
    assert p.spec_patch == {}
    assert p.requires_approval is True
    assert p.automatic is False


def test_oom_at_cap_with_batch_size_one_has_no_automatic_fix():
    ctx = RecoveryContext(resources={"memory_mb": 8192}, limits={"memory_mb": 8192}, parameters={"batch_size": 1})
    assert propose_recovery(OOM, ctx).action is RecoveryAction.NO_AUTOMATIC_FIX


def test_oom_without_limit_requires_approval():
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 1024}))
    assert p.spec_patch == {"resources": {"memory_mb": 2048}}
    assert p.requires_approval is True
    assert p.automatic is False


def test_oom_with_unknown_current_memory():
    p = propose_recovery(OOM, RecoveryContext(limits={"memory_mb": 8192}))
    assert p.action is RecoveryAction.NO_AUTOMATIC_FIX
    p2 = propose_recovery(OOM, RecoveryContext(limits={"memory_mb": 8192}, parameters={"batch_size": "16"}))
    assert p2.spec_patch == {"parameters": {"batch_size": 8}}


def test_gpu_oom_halves_batch_or_needs_human():
    p = propose_recovery(GPU_OOM, RecoveryContext(parameters={"batch_size": 128}, resources={"memory_mb": 1024}))
    assert p.spec_patch == {"parameters": {"batch_size": 64}}
    p2 = propose_recovery(GPU_OOM, RecoveryContext(resources={"memory_mb": 1024}, limits={"memory_mb": 99999}))
    assert p2.action is RecoveryAction.NO_AUTOMATIC_FIX
    assert "resources" not in p2.spec_patch


def test_disk_full_doubles_disk_capped():
    p = propose_recovery(DISK, RecoveryContext(resources={"disk_mb": 3000}, limits={"disk_mb": 5000}))
    assert p.action is RecoveryAction.INCREASE_DISK
    assert p.spec_patch == {"resources": {"disk_mb": 5000}}


def test_timeout_doubles_and_caps():
    p = propose_recovery(TIMEOUT, RecoveryContext(timeout_seconds=900, limits={"timeout_seconds": 3600}))
    assert p.action is RecoveryAction.INCREASE_TIMEOUT
    assert p.spec_patch == {"timeout_seconds": 1800}
    p2 = propose_recovery(TIMEOUT, RecoveryContext(timeout_seconds=3000, limits={"max_timeout_seconds": 3600}))
    assert p2.spec_patch == {"timeout_seconds": 3600}


def test_timeout_at_cap_has_no_automatic_fix():
    p = propose_recovery(TIMEOUT, RecoveryContext(timeout_seconds=3600, limits={"timeout_seconds": 3600}))
    assert p.action is RecoveryAction.NO_AUTOMATIC_FIX
    assert p.requires_approval is True


def test_timeout_unknown_current():
    assert propose_recovery(TIMEOUT, RecoveryContext()).action is RecoveryAction.NO_AUTOMATIC_FIX


# ---------------------------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------------------------
def test_missing_module_with_known_version_adds_pinned_dependency():
    ctx = RecoveryContext(dependencies=["numpy==2.1.0"], known_versions={"scikit-learn": "1.5.2"})
    p = propose_recovery(missing_module("sklearn"), ctx)
    assert p.action is RecoveryAction.ADD_DEPENDENCY
    assert p.spec_patch == {"environment": {"dependencies": ["numpy==2.1.0", "scikit-learn==1.5.2"]}}
    assert p.requires_approval is False


@pytest.mark.parametrize(("module", "package"), sorted(IMPORT_PACKAGE_MAP.items()))
def test_import_package_map(module, package):
    p = propose_recovery(missing_module(module), RecoveryContext(known_versions={package: "1.0.0"}))
    assert p.spec_patch["environment"]["dependencies"] == [f"{package}==1.0.0"]


def test_known_versions_match_normalized_distribution_names():
    p = propose_recovery(missing_module("yaml"), RecoveryContext(known_versions={"pyyaml": "6.0.2"}))
    assert p.spec_patch == {"environment": {"dependencies": ["PyYAML==6.0.2"]}}


def test_missing_module_without_version_never_guesses():
    p = propose_recovery(missing_module("sklearn"), RecoveryContext())
    assert p.action is RecoveryAction.DEPENDENCY_PIN_REQUIRED
    assert p.spec_patch == {}
    assert p.requires_approval is True
    assert "scikit-learn" in p.rationale


def test_unmapped_module_requires_review_even_with_version():
    p = propose_recovery(missing_module("einops"), RecoveryContext(known_versions={"einops": "0.8.0"}))
    assert p.action is RecoveryAction.ADD_DEPENDENCY
    assert p.requires_approval is True
    assert p.automatic is False


def test_invalid_known_version_is_rejected():
    p = propose_recovery(missing_module("sklearn"), RecoveryContext(known_versions={"scikit-learn": "1.0; rm -rf /"}))
    assert p.action is RecoveryAction.DEPENDENCY_PIN_REQUIRED
    assert p.spec_patch == {}


def test_already_declared_dependency_needs_human():
    ctx = RecoveryContext(dependencies=["scikit_learn==1.4.0"], known_versions={"scikit-learn": "1.5.2"})
    p = propose_recovery(missing_module("sklearn"), ctx)
    assert p.action is RecoveryAction.NO_AUTOMATIC_FIX
    assert p.requires_approval is True


def test_unidentified_module_needs_pin():
    c = classify(exit_code=1, error_class="ImportError", error_message="DLL load failed")
    p = propose_recovery(c, RecoveryContext(known_versions={"numpy": "2.0"}))
    assert p.action is RecoveryAction.DEPENDENCY_PIN_REQUIRED
    assert p.requires_approval is True


# ---------------------------------------------------------------------------------------------
# Other failure types
# ---------------------------------------------------------------------------------------------
def test_code_errors_need_a_code_fix_without_patch():
    p = propose_recovery(classify(exit_code=1, error_message="KeyError: 'x'"))
    assert p.action is RecoveryAction.FIX_CODE
    assert p.spec_patch == {}


def test_missing_data_verifies_mounts_without_patch():
    c = classify(exit_code=1, error_message="FileNotFoundError: No such file or directory: '/workspace/input/a.csv'")
    p = propose_recovery(c)
    assert p.action is RecoveryAction.VERIFY_DATASET_MOUNTS
    assert p.spec_patch == {}


def test_other_data_failures_inspect_data():
    c = classify(exit_code=1, error_message="ValueError: could not convert string to float: 'x'")
    p = propose_recovery(c)
    assert p.action is RecoveryAction.INSPECT_DATA
    assert "non-numeric" in p.rationale


def test_nan_metrics_propose_numeric_guards_without_patch():
    c = classify(exit_code=0, metrics_file_present=True, metrics={"loss": float("nan")})
    p = propose_recovery(c)
    assert p.action is RecoveryAction.ADD_NUMERIC_GUARDS
    assert p.spec_patch == {}


def test_metrics_missing_asks_for_metrics_file():
    p = propose_recovery(classify(exit_code=0, metrics_file_present=False))
    assert p.action is RecoveryAction.WRITE_METRICS_FILE


def test_evaluator_error_inspects_evaluator():
    p = propose_recovery(classify(stage="evaluation", error_message="boom"))
    assert p.action is RecoveryAction.INSPECT_EVALUATOR


def test_insufficient_seeds_increase_to_plan():
    c = classify(stage="statistics", p_value=0.2, n_seeds=2, required_seeds=5)
    p = propose_recovery(c, RecoveryContext(seeds=[7, 3], statistical_plan={"n_seeds": 5}))
    assert p.action is RecoveryAction.INCREASE_SEEDS
    assert p.spec_patch == {"seeds": [7, 3, 0, 1, 2]}


def test_seed_increase_respects_seed_limit():
    c = classify(stage="statistics", adequately_powered=False, p_value=0.3)
    p = propose_recovery(c, RecoveryContext(seeds=[0], statistical_plan={"n_seeds": 10}, limits={"max_seeds": 4}))
    assert p.spec_patch == {"seeds": [0, 1, 2, 3]}
    assert "limit" in p.rationale


def test_seeds_already_at_plan_require_plan_revision():
    c = classify(stage="statistics", adequately_powered=False, p_value=0.3)
    p = propose_recovery(c, RecoveryContext(seeds=[0, 1, 2], statistical_plan={"n_seeds": 3}))
    assert p.action is RecoveryAction.REVISE_STATISTICAL_PLAN
    assert p.requires_approval is True
    assert p.spec_patch == {}


def test_missing_p_value_runs_the_test():
    p = propose_recovery(classify(stage="statistics"))
    assert p.action is RecoveryAction.RUN_STATISTICAL_TEST


def test_reproducibility_pins_digest_flags_and_seeds():
    c = classify(stage="reproduction", reproduction_verdict="not_reproduced")
    ctx = RecoveryContext(image_digest="sha256:" + "a" * 64, deterministic_flags=["CUSTOM=1"])
    p = propose_recovery(c, ctx)
    assert p.action is RecoveryAction.PIN_ENVIRONMENT
    assert p.spec_patch["environment"] == {"image_digest": "sha256:" + "a" * 64}
    assert p.spec_patch["reproducibility"]["require_digest_pin"] is True
    assert p.spec_patch["reproducibility"]["deterministic_flags"] == ["CUSTOM=1", *DETERMINISTIC_FLAGS]
    assert p.spec_patch["seeds"] == [0, 1, 2]


def test_reproducibility_without_digest_keeps_existing_seeds():
    c = classify(stage="reproduction", reproduction_verdict="partially_reproduced")
    p = propose_recovery(c, RecoveryContext(seeds=[11, 12]))
    assert "environment" not in p.spec_patch
    assert "seeds" not in p.spec_patch
    assert "digest is unknown" in p.rationale


def test_policy_failure_has_no_automatic_recovery():
    p = propose_recovery(classify(policy_decision="deny"), RecoveryContext(resources={"memory_mb": 1}))
    assert p.action is RecoveryAction.HUMAN_REVIEW
    assert p.spec_patch == {}
    assert p.requires_approval is True


def test_hypothesis_failure_records_negative_result():
    c = classify(stage="statistics", p_value=0.6, adequately_powered=True, power=0.9)
    p = propose_recovery(c, RecoveryContext(seeds=[0], statistical_plan={"n_seeds": 10}))
    assert p.action is RecoveryAction.RECORD_NEGATIVE_RESULT
    assert p.spec_patch == {}
    assert p.requires_approval is False


@pytest.mark.parametrize(
    ("provider_error", "action"),
    [
        ("RateLimitError", RecoveryAction.RETRY_LATER),
        ("ContextLengthExceeded", RecoveryAction.REVISE_OUTPUT_SCHEMA),
        ("SafetyBlocked", RecoveryAction.HUMAN_REVIEW),
    ],
)
def test_model_failures(provider_error, action):
    assert propose_recovery(classify(stage="agent", provider_error_class=provider_error)).action is action


def test_invalid_model_output_revises_schema():
    p = propose_recovery(classify(stage="agent", error_class="LLMOutputInvalid"))
    assert p.action is RecoveryAction.REVISE_OUTPUT_SCHEMA


def test_tool_failures():
    assert propose_recovery(classify(stage="tool", timed_out=True)).action is RecoveryAction.RETRY_TOOL
    assert propose_recovery(classify(stage="tool", error_message="x")).action is RecoveryAction.INSPECT_TOOL


def test_design_failure_revises_design():
    p = propose_recovery(classify(stage="design", validation_issue_codes=["MISSING_BASELINE"]))
    assert p.action is RecoveryAction.REVISE_DESIGN
    assert "MISSING_BASELINE" in p.rationale


def test_strategy_failure_needs_review():
    p = propose_recovery(classify(stage="strategy", repeated_failures_for_strategy=4))
    assert p.action is RecoveryAction.REVIEW_STRATEGY
    assert p.requires_approval is True


def test_unknown_failure_needs_manual_investigation():
    p = propose_recovery(classify(exit_code=2, error_message="odd"))
    assert p.action is RecoveryAction.MANUAL_INVESTIGATION
    assert p.requires_approval is True


def test_proposal_confidence_is_bounded_and_scaled():
    p = propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 1}, limits={"memory_mb": 4}))
    assert 0.0 < p.confidence <= OOM.confidence


def test_recovery_context_rejects_unknown_fields():
    with pytest.raises(ValueError):
        RecoveryContext(memory=1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------------------------
# merge_patch
# ---------------------------------------------------------------------------------------------
def test_merge_patch_semantics():
    spec = {"resources": {"cpu": 1, "memory_mb": 1024}, "seeds": [1], "keep": True, "drop": 1}
    patched = merge_patch(spec, {"resources": {"memory_mb": 2048}, "seeds": [1, 2], "drop": None, "new": {"a": 1}})
    assert patched == {"resources": {"cpu": 1, "memory_mb": 2048}, "seeds": [1, 2], "keep": True, "new": {"a": 1}}
    assert spec["resources"]["memory_mb"] == 1024  # input not mutated


def test_recovery_patch_applies_to_spec():
    spec = {"resources": {"cpu": 2, "memory_mb": 2048}, "timeout_seconds": 900}
    p = propose_recovery(OOM, RecoveryContext(resources=spec["resources"], limits={"memory_mb": 8192}))
    assert merge_patch(spec, p.spec_patch)["resources"] == {"cpu": 2, "memory_mb": 4096}


# ---------------------------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------------------------
def _oom_proposal():
    return propose_recovery(OOM, RecoveryContext(resources={"memory_mb": 1024}, limits={"memory_mb": 4096}))


def test_lesson_confidence_rises_on_success_and_falls_on_failure():
    proposal = _oom_proposal()
    untested = extract_lesson(OOM, proposal)
    success = extract_lesson(OOM, proposal, "succeeded")
    failure = extract_lesson(OOM, proposal, "failed")
    assert untested.confidence == proposal.confidence
    assert success.confidence > untested.confidence > failure.confidence
    assert success.confidence <= 1.0


def test_lesson_contents():
    proposal = _oom_proposal()
    lesson = extract_lesson(OOM, proposal, "succeeded")
    assert lesson.failure_type is FailureType.RESOURCE_FAILURE
    assert lesson.signature == OOM.signature
    assert lesson.statement.startswith("RESOURCE_FAILURE/oom:")
    assert "resolved it" in lesson.statement
    assert lesson.recommendation["action"] == "increase_memory"
    assert lesson.recommendation["spec_patch"] == {"resources": {"memory_mb": 2048}}
    assert lesson.recommendation["applies_to"]["signature"] == OOM.signature
    assert lesson.outcome == "succeeded"


def test_lesson_is_deterministic():
    proposal = _oom_proposal()
    assert extract_lesson(OOM, proposal, "failed") == extract_lesson(OOM, proposal, "failed")
    assert "did not resolve" in extract_lesson(OOM, proposal, "failed").statement
    assert "not yet validated" in extract_lesson(OOM, proposal).statement
