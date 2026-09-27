"""Remediation recommendation and application (config changes to the AI system).

Recommendations are deterministic templates keyed by finding category/test type. When a recommendation is
applied, it produces a concrete configuration change (a guardrail toggle) that is stored on the system, so
re-tests measure a real before/after difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engines.common.types import TestType


@dataclass
class RemediationRecommendation:
    category: str  # RemediationCategory value
    title: str
    description: str
    change: dict[str, Any]  # {"guardrails": {"debiasing": true}} etc.
    creates_regression_test: bool = True


RECOMMENDATIONS: dict[str, RemediationRecommendation] = {
    TestType.COUNTERFACTUAL: RemediationRecommendation(
        "prompt_fix",
        "Enable counterfactual debiasing guardrail",
        "Instruct the model to ignore protected attributes and add a counterfactual consistency check before returning a decision.",
        {"guardrails": {"debiasing": True}},
    ),
    TestType.GROUNDEDNESS: RemediationRecommendation(
        "retrieval_fix",
        "Require grounded, cited answers",
        "Constrain the system to answer only from retrieved context and cite sources; enable strict grounding mode.",
        {"guardrails": {"grounding_mode": "strict", "cite_sources": True, "grounded_summaries": True}},
    ),
    TestType.PII_LEAKAGE: RemediationRecommendation(
        "output_filtering",
        "Enable PII output filter and retrieval scoping",
        "Add an output PII filter and exclude restricted internal notes from retrieval.",
        {"guardrails": {"pii_filter": True, "retrieval_filter": True}},
    ),
    TestType.HUMAN_OVERSIGHT: RemediationRecommendation(
        "human_approval",
        "Require human approval for consequential actions",
        "Route final decisions and outbound actions through a human review step before execution.",
        {"guardrails": {"require_human_approval": True}},
    ),
    TestType.PROMPT_INJECTION: RemediationRecommendation(
        "input_validation",
        "Strengthen injection guardrails",
        "Enable strict prompt-injection detection over user input, retrieved documents and fetched pages.",
        {"guardrails": {"injection_guard": "strict", "tool_guard": "strict"}},
    ),
    TestType.SAFETY_REFUSAL: RemediationRecommendation(
        "policy_guardrail",
        "Enable strict safety filter",
        "Apply a strict safety filter that refuses disallowed requests across all taxonomy categories.",
        {"guardrails": {"safety_filter": "strict"}},
    ),
    TestType.TOOL_PERMISSION: RemediationRecommendation(
        "tool_permission_change",
        "Restrict tool permissions",
        "Constrain tool destinations to an allowlist and permitted file paths.",
        {"guardrails": {"path_allowlist": True}},
    ),
}


def recommend(test_type: str, category: str) -> RemediationRecommendation:
    if test_type in RECOMMENDATIONS:
        return RECOMMENDATIONS[test_type]
    return RemediationRecommendation(
        "monitoring_threshold",
        "Add monitoring and manual review",
        "Add a production monitor for this category and route matches to manual review while a fix is developed.",
        {},
        creates_regression_test=False,
    )


def apply_change(config: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a remediation change into a system config (returns a new dict)."""
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
    for key, value in change.items():
        if isinstance(value, dict):
            result[key] = {**result.get(key, {}), **value}
        else:
            result[key] = value
    return result
