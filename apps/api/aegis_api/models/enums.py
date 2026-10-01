"""Domain enumerations (stored as short strings for portability and readable SQL)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    SECURITY_ENGINEER = "security_engineer"
    AUDITOR = "auditor"
    AI_ENGINEER = "ai_engineer"
    ANALYST = "analyst"
    DEVELOPER = "developer"
    VIEWER = "viewer"
    SERVICE_ACCOUNT = "service_account"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class Plan(StrEnum):
    FREE = "free"
    DEVELOPER = "developer"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class SystemType(StrEnum):
    LLM = "llm"
    RAG = "rag"
    AGENT = "agent"
    ML_MODEL = "ml_model"
    MULTI_AGENT = "multi_agent"
    OTHER = "other"


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class RiskTier(StrEnum):
    MINIMAL = "minimal"
    LIMITED = "limited"
    HIGH = "high"
    CRITICAL = "critical"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ProviderKind(StrEnum):
    OLLAMA = "ollama"
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    HTTP_ENDPOINT = "http_endpoint"
    DEMO = "demo"


class ConnectionStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class AuditStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_AUDIT_STATUSES = {
    AuditStatus.COMPLETED,
    AuditStatus.PARTIALLY_COMPLETED,
    AuditStatus.FAILED,
    AuditStatus.CANCELLED,
}


class AuditStage(StrEnum):
    SETUP = "setup"
    TEST_GENERATION = "test_generation"
    INFERENCE = "inference"
    EVALUATION = "evaluation"
    EVIDENCE = "evidence"
    POLICY_MAPPING = "policy_mapping"
    RISK_SCORING = "risk_scoring"
    REPORT_GENERATION = "report_generation"


class Intensity(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    ADVERSARIAL = "adversarial"


class TestCategory(StrEnum):
    __test__ = False  # not a pytest test class

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


CATEGORY_DIMENSION: dict[str, Dimension] = {
    TestCategory.FAIRNESS: Dimension.FAIRNESS,
    TestCategory.HALLUCINATION: Dimension.TRUTHFULNESS,
    TestCategory.GROUNDEDNESS: Dimension.TRUTHFULNESS,
    TestCategory.SAFETY: Dimension.SAFETY,
    TestCategory.PRIVACY: Dimension.PRIVACY,
    TestCategory.PROMPT_INJECTION: Dimension.SECURITY,
    TestCategory.JAILBREAK: Dimension.SECURITY,
    TestCategory.TOOL_ABUSE: Dimension.SECURITY,
    TestCategory.POLICY: Dimension.GOVERNANCE,
    TestCategory.AGENT_ACTION: Dimension.GOVERNANCE,
}


class TestResultStatus(StrEnum):
    __test__ = False  # not a pytest test class

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"  # legacy synonym of "triaged" (kept for backward compatibility)
    TRIAGED = "triaged"
    IN_REMEDIATION = "in_remediation"
    FIXED = "fixed"
    RETESTING = "retesting"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"
    FALSE_POSITIVE = "false_positive"


OPEN_FINDING_STATUSES = {
    FindingStatus.OPEN,
    FindingStatus.ACKNOWLEDGED,
    FindingStatus.TRIAGED,
    FindingStatus.IN_REMEDIATION,
    FindingStatus.FIXED,
    FindingStatus.RETESTING,
}


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


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PolicyVersionStatus(StrEnum):
    DRAFT = "draft"
    COMPILING = "compiling"
    COMPILED = "compiled"
    FAILED = "failed"


class TestType(StrEnum):
    __test__ = False  # not a pytest test class

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


class ControlAutomation(StrEnum):
    AUTOMATED = "automated"
    MANUAL = "manual"


class ControlAssessmentStatus(StrEnum):
    PASSING = "passing"
    FAILING = "failing"
    NOT_TESTED = "not_tested"
    MANUAL = "manual"
    INCONCLUSIVE = "inconclusive"


class RemediationCategory(StrEnum):
    PROMPT_FIX = "prompt_fix"
    POLICY_GUARDRAIL = "policy_guardrail"
    RETRIEVAL_FIX = "retrieval_fix"
    TOOL_PERMISSION_CHANGE = "tool_permission_change"
    MODEL_CHANGE = "model_change"
    INPUT_VALIDATION = "input_validation"
    OUTPUT_FILTERING = "output_filtering"
    HUMAN_APPROVAL = "human_approval"
    REGRESSION_TEST = "regression_test"
    MONITORING_THRESHOLD = "monitoring_threshold"


class RemediationStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    APPLIED = "applied"
    VERIFIED = "verified"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RegressionVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 - not a secret
    FAIL = "fail"
    PARTIAL = "partial"
    ERROR = "error"


class ProbeResult(StrEnum):
    BLOCKED = "blocked"
    BYPASSED = "bypassed"
    PARTIAL = "partial"
    ERROR = "error"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"


class IntegrationKind(StrEnum):
    OLLAMA = "ollama"
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    WEBHOOK = "webhook"
    GITHUB = "github"
    SLACK = "slack"
    EMAIL = "email"
