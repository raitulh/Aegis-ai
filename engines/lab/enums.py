"""Lab domain enumerations. Values are short lowercase strings (portable, readable SQL) except where the
identifier itself is the contract (autonomy levels, live event types)."""

from __future__ import annotations

from enum import StrEnum


class MissionStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class MissionPhase(StrEnum):
    PLANNING = "planning"
    LITERATURE = "literature"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_CRITIQUE = "hypothesis_critique"
    EXPERIMENT_DESIGN = "experiment_design"
    EXPERIMENT_VALIDATION = "experiment_validation"
    EXECUTION = "execution"
    EVALUATION = "evaluation"
    FAILURE_ANALYSIS = "failure_analysis"
    EVOLUTION = "evolution"
    REPRODUCTION = "reproduction"
    VERIFICATION = "verification"
    DISCOVERY = "discovery"
    HUMAN_REVIEW = "human_review"
    REPORTING = "reporting"
    DONE = "done"


class AutonomyLevel(StrEnum):
    L0_ASSISTED = "L0_ASSISTED"
    L1_RESEARCH_AUTOMATION = "L1_RESEARCH_AUTOMATION"
    L2_AUTOMATED_EXPERIMENT_DESIGN = "L2_AUTOMATED_EXPERIMENT_DESIGN"
    L3_AUTOMATED_EXECUTION = "L3_AUTOMATED_EXECUTION"
    L4_CLOSED_LOOP_EVOLUTION = "L4_CLOSED_LOOP_EVOLUTION"
    L5_LONG_HORIZON_AUTONOMOUS_RND = "L5_LONG_HORIZON_AUTONOMOUS_RND"

    @property
    def rank(self) -> int:
        return int(self.value[1])


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class AgentRole(StrEnum):
    QUEST = "quest"
    PLANNER = "planner"
    LITERATURE = "literature"
    KNOWLEDGE = "knowledge"
    HYPOTHESIS = "hypothesis"
    HYPOTHESIS_CRITIC = "hypothesis_critic"
    EXPERIMENT_DESIGNER = "experiment_designer"
    CODING = "coding"
    SIMULATION = "simulation"
    DATA_ANALYST = "data_analyst"
    STATISTICAL_ANALYST = "statistical_analyst"
    FAILURE_ANALYZER = "failure_analyzer"
    EVOLUTION = "evolution"
    REPRODUCTION = "reproduction"
    VERIFIER = "verifier"
    SCIENTIFIC_REVIEWER = "scientific_reviewer"
    REPORT = "report"


class AgentRunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_TOOL = "waiting_tool"
    WAITING_HUMAN = "waiting_human"
    WAITING_CHILD = "waiting_child"
    EVALUATING = "evaluating"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskClass(StrEnum):
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    PLANNING = "planning"
    RESEARCH = "research"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_CRITIQUE = "hypothesis_critique"
    EXPERIMENT_DESIGN = "experiment_design"
    CODING = "coding"
    RESULT_ANALYSIS = "result_analysis"
    FAILURE_ANALYSIS = "failure_analysis"
    STRATEGY_EVOLUTION = "strategy_evolution"
    SCIENTIFIC_REVIEW = "scientific_review"
    REPORT_GENERATION = "report_generation"
    VERIFICATION = "verification"


class ModelTier(StrEnum):
    FAST = "fast"
    DEFAULT = "default"
    REASONING = "reasoning"
    DEEP_RESEARCH = "deep_research"

    @property
    def rank(self) -> int:
        return {"fast": 0, "default": 1, "reasoning": 2, "deep_research": 3}[self.value]


class ResearchTaskStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HypothesisStatus(StrEnum):
    GENERATED = "generated"
    CRITIQUED = "critiqued"
    SELECTED = "selected"
    EXPERIMENT_DESIGNED = "experiment_designed"
    TESTING = "testing"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ARCHIVED = "archived"


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    EVALUATING = "evaluating"
    REPRODUCING = "reproducing"
    VERIFIED = "verified"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"


TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.VERIFIED,
        ExecutionStatus.VERIFICATION_PENDING,
    }
)


class RunKind(StrEnum):
    """Why an experiment run exists — baseline and candidate runs are compared explicitly."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"
    ABLATION = "ablation"
    SENSITIVITY = "sensitivity"
    REPRODUCTION = "reproduction"


class FailureType(StrEnum):
    DATA_FAILURE = "data_failure"
    CODE_FAILURE = "code_failure"
    TOOL_FAILURE = "tool_failure"
    MODEL_FAILURE = "model_failure"
    EXPERIMENT_DESIGN_FAILURE = "experiment_design_failure"
    EVALUATION_FAILURE = "evaluation_failure"
    STATISTICAL_FAILURE = "statistical_failure"
    HYPOTHESIS_FAILURE = "hypothesis_failure"
    RESOURCE_FAILURE = "resource_failure"
    REPRODUCIBILITY_FAILURE = "reproducibility_failure"
    STRATEGY_FAILURE = "strategy_failure"
    POLICY_FAILURE = "policy_failure"


class StrategyKind(StrEnum):
    SEARCH = "search"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    OPTIMIZATION = "optimization"
    PROMPT = "prompt"
    AGENT_TOPOLOGY = "agent_topology"
    TOOL_SEQUENCING = "tool_sequencing"
    MODEL_ROUTING = "model_routing"


class StrategyStatus(StrEnum):
    CANDIDATE = "candidate"
    EXPERIMENTAL = "experimental"
    SURVIVING = "surviving"
    PROMOTED = "promoted"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    CANDIDATE = "candidate"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    REJECTED = "rejected"
    CONTESTED = "contested"


class DiscoveryStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    HUMAN_REVIEW = "human_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONTESTED = "contested"
    PUBLISHED = "published"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalKind(StrEnum):
    PLAN_REVIEW = "plan_review"
    EXPENSIVE_COMPUTE = "expensive_compute"
    EXTERNAL_NETWORK = "external_network"
    HIGH_RISK_TOOL = "high_risk_tool"
    PRODUCTION_INTEGRATION = "production_integration"
    SECRET_ACCESS = "secret_access"  # noqa: S105 - approval kind, not a secret
    DISCOVERY_PROMOTION = "discovery_promotion"
    PUBLICATION = "publication"
    STRATEGY_PROMOTION = "strategy_promotion"
    EXPERIMENT_EXECUTION = "experiment_execution"
    BUDGET_INCREASE = "budget_increase"
    MEMORY_PROMOTION = "memory_promotion"
    GENERIC = "generic"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class MemoryScope(StrEnum):
    SHORT_TERM = "short_term"
    MISSION = "mission"
    PROJECT = "project"
    ORGANIZATION = "organization"


class MemoryCategory(StrEnum):
    LITERATURE = "literature"
    EXPERIMENT = "experiment"
    FAILURE = "failure"
    STRATEGY = "strategy"
    EVIDENCE = "evidence"
    DISCOVERY = "discovery"
    GENERAL = "general"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    QUARANTINED = "quarantined"


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"
    AGENT = "agent"
    WORKFLOW = "workflow"
    SYSTEM = "system"


class LabEventType(StrEnum):
    """Live, persisted mission events (served over SSE with monotonically increasing IDs)."""

    MISSION_CREATED = "MISSION_CREATED"
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_PHASE_CHANGED = "MISSION_PHASE_CHANGED"
    MISSION_PAUSED = "MISSION_PAUSED"
    MISSION_RESUMED = "MISSION_RESUMED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    MISSION_FAILED = "MISSION_FAILED"
    MISSION_CANCELLED = "MISSION_CANCELLED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    TOOL_CALLED = "TOOL_CALLED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    SEARCH_PROGRESS = "SEARCH_PROGRESS"
    RESEARCH_COMPLETED = "RESEARCH_COMPLETED"
    HYPOTHESIS_CREATED = "HYPOTHESIS_CREATED"
    HYPOTHESIS_SELECTED = "HYPOTHESIS_SELECTED"
    EXPERIMENT_DESIGNED = "EXPERIMENT_DESIGNED"
    EXPERIMENT_QUEUED = "EXPERIMENT_QUEUED"
    EXPERIMENT_STARTED = "EXPERIMENT_STARTED"
    EXPERIMENT_LOG = "EXPERIMENT_LOG"
    EXPERIMENT_COMPLETED = "EXPERIMENT_COMPLETED"
    EXPERIMENT_FAILED = "EXPERIMENT_FAILED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    FAILURE_ANALYZED = "FAILURE_ANALYZED"
    STRATEGY_MUTATED = "STRATEGY_MUTATED"
    STRATEGY_PROMOTED = "STRATEGY_PROMOTED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    DISCOVERY_CREATED = "DISCOVERY_CREATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    REPORT_GENERATED = "REPORT_GENERATED"


# Outbound webhook event names for major lab events (dotted, lowercase — the public webhook contract).
WEBHOOK_EVENT_NAMES: dict[str, str] = {
    LabEventType.MISSION_COMPLETED: "mission.completed",
    LabEventType.MISSION_FAILED: "mission.failed",
    LabEventType.EXPERIMENT_FAILED: "experiment.failed",
    LabEventType.EXPERIMENT_COMPLETED: "experiment.completed",
    LabEventType.VERIFICATION_COMPLETED: "verification.completed",
    LabEventType.DISCOVERY_CREATED: "discovery.created",
    LabEventType.APPROVAL_REQUESTED: "approval.required",
    LabEventType.STRATEGY_PROMOTED: "strategy.promoted",
    LabEventType.BUDGET_EXCEEDED: "budget.exceeded",
}
