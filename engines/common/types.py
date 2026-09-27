"""Canonical domain enumerations shared by the engines and the API."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_WEIGHT: dict[str, float] = {"info": 0.5, "low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}


def max_severity(*values: str | None) -> str:
    present = [v for v in values if v]
    if not present:
        return Severity.INFO
    return max(present, key=lambda v: SEVERITY_RANK.get(v, 0))


class Category(StrEnum):
    FAIRNESS = "fairness"
    HALLUCINATION = "hallucination"
    GROUNDEDNESS = "groundedness"
    SAFETY = "safety"
    PRIVACY = "privacy"
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    POLICY = "policy"
    AGENT_ACTION = "agent_action"
    TOOL_ABUSE = "tool_abuse"


class Dimension(StrEnum):
    FAIRNESS = "fairness"
    TRUTHFULNESS = "truthfulness"
    SAFETY = "safety"
    PRIVACY = "privacy"
    SECURITY = "security"
    GOVERNANCE = "governance"


CATEGORY_DIMENSION: dict[str, str] = {
    Category.FAIRNESS: Dimension.FAIRNESS,
    Category.HALLUCINATION: Dimension.TRUTHFULNESS,
    Category.GROUNDEDNESS: Dimension.TRUTHFULNESS,
    Category.SAFETY: Dimension.SAFETY,
    Category.PRIVACY: Dimension.PRIVACY,
    Category.PROMPT_INJECTION: Dimension.SECURITY,
    Category.JAILBREAK: Dimension.SECURITY,
    Category.TOOL_ABUSE: Dimension.SECURITY,
    Category.POLICY: Dimension.GOVERNANCE,
    Category.AGENT_ACTION: Dimension.GOVERNANCE,
}


class TestType(StrEnum):
    __test__ = False

    COUNTERFACTUAL = "counterfactual"
    GROUNDEDNESS = "groundedness"
    PII_LEAKAGE = "pii_leakage"
    HUMAN_OVERSIGHT = "human_oversight"
    AUTHORIZATION = "authorization"
    OUTPUT_CONSTRAINT = "output_constraint"
    SOURCE_REQUIRED = "source_required"
    TOOL_PERMISSION = "tool_permission"
    PROMPT_INJECTION = "prompt_injection"
    SAFETY_REFUSAL = "safety_refusal"
    CUSTOM_RULE = "custom_rule"


class ResultStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProbeResult(StrEnum):
    BLOCKED = "blocked"
    BYPASSED = "bypassed"
    PARTIAL = "partial"
    ERROR = "error"


class EvidenceKind(StrEnum):
    PROMPT = "prompt"
    MODEL_OUTPUT = "model_output"
    SOURCE = "source"
    DATASET = "dataset"
    TRACE = "trace"
    TOOL_CALL = "tool_call"
    POLICY_EXCERPT = "policy_excerpt"
    METRIC_RESULT = "metric_result"
    SCREENSHOT = "screenshot"
    EVALUATOR_RESULT = "evaluator_result"
    CONFIGURATION = "configuration"
    MODEL_VERSION = "model_version"
