"""Lifecycle enumerations and state machines for every AI Scientist Lab entity.

Each state machine is an explicit transition table. Services must call :func:`assert_transition`
before changing a status so illegal transitions (e.g. a hypothesis jumping from ``GENERATED`` to
``SUPPORTED`` without being tested) are impossible regardless of which caller — human, agent or
workflow — requested them.

Values are stored as their upper-case names, matching the public API contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition is not permitted by the state machine."""

    def __init__(self, machine: str, current: str, target: str, allowed: frozenset[str]) -> None:
        self.machine = machine
        self.current = current
        self.target = target
        self.allowed = allowed
        super().__init__(
            f"{machine}: cannot transition from {current} to {target} (allowed: {', '.join(sorted(allowed)) or 'none'})"
        )


# ---------------------------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------------------------
class MissionStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class MissionPhase(StrEnum):
    PLANNING = "PLANNING"
    LITERATURE = "LITERATURE"
    HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
    HYPOTHESIS_CRITIQUE = "HYPOTHESIS_CRITIQUE"
    EXPERIMENT_DESIGN = "EXPERIMENT_DESIGN"
    EXPERIMENT_VALIDATION = "EXPERIMENT_VALIDATION"
    EXECUTION = "EXECUTION"
    EVALUATION = "EVALUATION"
    FAILURE_ANALYSIS = "FAILURE_ANALYSIS"
    EVOLUTION = "EVOLUTION"
    REPRODUCTION = "REPRODUCTION"
    VERIFICATION = "VERIFICATION"
    DISCOVERY = "DISCOVERY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REPORTING = "REPORTING"
    DONE = "DONE"


class AutonomyLevel(StrEnum):
    L0_ASSISTED = "L0_ASSISTED"
    L1_RESEARCH_AUTOMATION = "L1_RESEARCH_AUTOMATION"
    L2_AUTOMATED_EXPERIMENT_DESIGN = "L2_AUTOMATED_EXPERIMENT_DESIGN"
    L3_AUTOMATED_EXECUTION = "L3_AUTOMATED_EXECUTION"
    L4_CLOSED_LOOP_EVOLUTION = "L4_CLOSED_LOOP_EVOLUTION"
    L5_LONG_HORIZON_AUTONOMOUS_RND = "L5_LONG_HORIZON_AUTONOMOUS_RND"


AUTONOMY_RANK: dict[str, int] = {level.value: i for i, level in enumerate(AutonomyLevel)}


def autonomy_rank(level: str) -> int:
    try:
        return AUTONOMY_RANK[level]
    except KeyError as exc:
        raise ValueError(f"Unknown autonomy level: {level}") from exc


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


RISK_RANK: dict[str, int] = {level.value: i for i, level in enumerate(RiskLevel)}


# ---------------------------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------------------------
class AgentRole(StrEnum):
    QUEST = "QuestAgent"
    PLANNER = "PlannerAgent"
    LITERATURE = "LiteratureAgent"
    KNOWLEDGE = "KnowledgeAgent"
    HYPOTHESIS = "HypothesisAgent"
    HYPOTHESIS_CRITIC = "HypothesisCriticAgent"
    EXPERIMENT_DESIGNER = "ExperimentDesignerAgent"
    CODING = "CodingAgent"
    SIMULATION = "SimulationAgent"
    DATA_ANALYST = "DataAnalystAgent"
    STATISTICAL_ANALYST = "StatisticalAnalystAgent"
    FAILURE_ANALYZER = "FailureAnalyzerAgent"
    EVOLUTION = "EvolutionAgent"
    REPRODUCTION = "ReproductionAgent"
    VERIFIER = "VerifierAgent"
    SCIENTIFIC_REVIEWER = "ScientificReviewerAgent"
    REPORT = "ReportAgent"


class AgentRunStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_HUMAN = "WAITING_HUMAN"
    WAITING_CHILD = "WAITING_CHILD"
    EVALUATING = "EVALUATING"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------------------------
# Execution fabric
# ---------------------------------------------------------------------------------------------
class ExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"


# ---------------------------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------------------------
class ResearchTaskStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HypothesisStatus(StrEnum):
    GENERATED = "GENERATED"
    CRITIQUED = "CRITIQUED"
    SELECTED = "SELECTED"
    EXPERIMENT_DESIGNED = "EXPERIMENT_DESIGNED"
    TESTING = "TESTING"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ARCHIVED = "ARCHIVED"


class ExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    EVALUATING = "EVALUATING"
    REPRODUCING = "REPRODUCING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


# ---------------------------------------------------------------------------------------------
# Failures, strategies, verification, discoveries, approvals, memory, workflows
# ---------------------------------------------------------------------------------------------
class FailureType(StrEnum):
    DATA_FAILURE = "DATA_FAILURE"
    CODE_FAILURE = "CODE_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    EXPERIMENT_DESIGN_FAILURE = "EXPERIMENT_DESIGN_FAILURE"
    EVALUATION_FAILURE = "EVALUATION_FAILURE"
    STATISTICAL_FAILURE = "STATISTICAL_FAILURE"
    HYPOTHESIS_FAILURE = "HYPOTHESIS_FAILURE"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    REPRODUCIBILITY_FAILURE = "REPRODUCIBILITY_FAILURE"
    STRATEGY_FAILURE = "STRATEGY_FAILURE"
    POLICY_FAILURE = "POLICY_FAILURE"


class FailureStatus(StrEnum):
    OPEN = "OPEN"
    DIAGNOSED = "DIAGNOSED"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    WONT_FIX = "WONT_FIX"


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
    CANDIDATE = "CANDIDATE"
    EXPERIMENTAL = "EXPERIMENTAL"
    SURVIVING = "SURVIVING"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"
    ROLLED_BACK = "ROLLED_BACK"


class ClaimStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    CANDIDATE = "CANDIDATE"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    CONTESTED = "CONTESTED"


class DiscoveryStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONTESTED = "CONTESTED"
    PUBLISHED = "PUBLISHED"


class RunState(StrEnum):
    """Generic status for verification, reproduction, evaluation, evolution and benchmark runs."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class MemoryCategory(StrEnum):
    SHORT_TERM = "SHORT_TERM"
    MISSION = "MISSION"
    PROJECT = "PROJECT"
    ORGANIZATION = "ORGANIZATION"
    LITERATURE = "LITERATURE"
    EXPERIMENT = "EXPERIMENT"
    FAILURE = "FAILURE"
    STRATEGY = "STRATEGY"
    EVIDENCE = "EVIDENCE"
    DISCOVERY = "DISCOVERY"


class MemoryStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class MCPServerStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------------------------
# Transition tables
# ---------------------------------------------------------------------------------------------
_M = MissionStatus
MISSION_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _M.DRAFT: frozenset({_M.PLANNED, _M.CANCELLED, _M.ARCHIVED}),
    _M.PLANNED: frozenset({_M.APPROVED, _M.DRAFT, _M.CANCELLED}),
    _M.APPROVED: frozenset({_M.RUNNING, _M.PLANNED, _M.CANCELLED}),
    _M.RUNNING: frozenset({_M.PAUSED, _M.COMPLETED, _M.FAILED, _M.CANCELLED}),
    _M.PAUSED: frozenset({_M.RUNNING, _M.FAILED, _M.CANCELLED}),
    _M.COMPLETED: frozenset({_M.ARCHIVED}),
    _M.FAILED: frozenset({_M.PLANNED, _M.ARCHIVED}),
    _M.CANCELLED: frozenset({_M.ARCHIVED}),
    _M.ARCHIVED: frozenset(),
}
MISSION_TERMINAL = frozenset({_M.COMPLETED, _M.FAILED, _M.CANCELLED, _M.ARCHIVED})
MISSION_EDITABLE = frozenset({_M.DRAFT, _M.PLANNED})

_A = AgentRunStatus
_A_ACTIVE = frozenset({_A.PLANNING, _A.EXECUTING, _A.WAITING_TOOL, _A.WAITING_HUMAN, _A.WAITING_CHILD, _A.EVALUATING})
AGENT_RUN_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _A.CREATED: frozenset({_A.PLANNING, _A.EXECUTING, _A.FAILED, _A.CANCELLED}),
    **{s: (_A_ACTIVE - {s}) | {_A.FAILED, _A.COMPLETED, _A.CANCELLED} for s in _A_ACTIVE},
    _A.FAILED: frozenset(),
    _A.COMPLETED: frozenset(),
    _A.CANCELLED: frozenset(),
}
AGENT_RUN_TERMINAL = frozenset({_A.FAILED, _A.COMPLETED, _A.CANCELLED})

_E = ExecutionStatus
EXECUTION_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _E.QUEUED: frozenset({_E.PROVISIONING, _E.CANCELLED, _E.FAILED}),
    _E.PROVISIONING: frozenset({_E.RUNNING, _E.FAILED, _E.CANCELLED, _E.TIMED_OUT, _E.QUEUED}),
    _E.RUNNING: frozenset({_E.SUCCEEDED, _E.FAILED, _E.TIMED_OUT, _E.CANCELLED, _E.PAUSED}),
    _E.PAUSED: frozenset({_E.RUNNING, _E.CANCELLED, _E.FAILED}),
    _E.SUCCEEDED: frozenset({_E.VERIFICATION_PENDING}),
    _E.VERIFICATION_PENDING: frozenset({_E.VERIFIED, _E.FAILED}),
    _E.FAILED: frozenset(),
    _E.TIMED_OUT: frozenset(),
    _E.CANCELLED: frozenset(),
    _E.VERIFIED: frozenset(),
}
EXECUTION_TERMINAL = frozenset({_E.SUCCEEDED, _E.FAILED, _E.TIMED_OUT, _E.CANCELLED, _E.VERIFIED})

_R = ResearchTaskStatus
RESEARCH_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _R.CREATED: frozenset({_R.PLANNING, _R.RUNNING, _R.CANCELLED, _R.FAILED}),
    _R.PLANNING: frozenset({_R.PLAN_REVIEW, _R.FAILED, _R.CANCELLED}),
    _R.PLAN_REVIEW: frozenset({_R.APPROVED, _R.PLANNING, _R.CANCELLED}),
    _R.APPROVED: frozenset({_R.RUNNING, _R.CANCELLED}),
    _R.RUNNING: frozenset({_R.COMPLETED, _R.FAILED, _R.CANCELLED}),
    _R.COMPLETED: frozenset(),
    _R.FAILED: frozenset(),
    _R.CANCELLED: frozenset(),
}

_H = HypothesisStatus
HYPOTHESIS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _H.GENERATED: frozenset({_H.CRITIQUED, _H.SELECTED, _H.REJECTED, _H.ARCHIVED}),
    _H.CRITIQUED: frozenset({_H.SELECTED, _H.REJECTED, _H.ARCHIVED, _H.CRITIQUED}),
    _H.SELECTED: frozenset({_H.EXPERIMENT_DESIGNED, _H.REJECTED, _H.ARCHIVED}),
    _H.EXPERIMENT_DESIGNED: frozenset({_H.TESTING, _H.SELECTED, _H.ARCHIVED}),
    _H.TESTING: frozenset({_H.SUPPORTED, _H.REJECTED, _H.INCONCLUSIVE}),
    _H.SUPPORTED: frozenset({_H.TESTING, _H.ARCHIVED}),
    _H.REJECTED: frozenset({_H.ARCHIVED}),
    _H.INCONCLUSIVE: frozenset({_H.TESTING, _H.EXPERIMENT_DESIGNED, _H.ARCHIVED}),
    _H.ARCHIVED: frozenset(),
}

_X = ExperimentStatus
EXPERIMENT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _X.DRAFT: frozenset({_X.VALIDATING, _X.ARCHIVED}),
    _X.VALIDATING: frozenset({_X.QUEUED, _X.DRAFT, _X.REJECTED}),
    _X.QUEUED: frozenset({_X.RUNNING, _X.FAILED, _X.DRAFT, _X.ARCHIVED}),
    _X.RUNNING: frozenset({_X.COMPLETED, _X.FAILED}),
    _X.COMPLETED: frozenset({_X.EVALUATING, _X.REPRODUCING, _X.QUEUED, _X.ARCHIVED}),
    _X.FAILED: frozenset({_X.RETRYING, _X.DRAFT, _X.REJECTED, _X.ARCHIVED}),
    _X.RETRYING: frozenset({_X.QUEUED, _X.FAILED}),
    _X.EVALUATING: frozenset({_X.COMPLETED, _X.REPRODUCING, _X.VERIFIED, _X.REJECTED, _X.FAILED}),
    _X.REPRODUCING: frozenset({_X.VERIFIED, _X.REJECTED, _X.EVALUATING, _X.FAILED}),
    _X.VERIFIED: frozenset({_X.ARCHIVED, _X.REPRODUCING}),
    _X.REJECTED: frozenset({_X.ARCHIVED}),
    _X.ARCHIVED: frozenset(),
}

_F = FailureStatus
FAILURE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _F.OPEN: frozenset({_F.DIAGNOSED, _F.WONT_FIX, _F.RESOLVED}),
    _F.DIAGNOSED: frozenset({_F.RECOVERING, _F.RESOLVED, _F.WONT_FIX}),
    _F.RECOVERING: frozenset({_F.RESOLVED, _F.DIAGNOSED, _F.WONT_FIX}),
    _F.RESOLVED: frozenset({_F.OPEN}),
    _F.WONT_FIX: frozenset({_F.OPEN}),
}

_S = StrategyStatus
STRATEGY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _S.CANDIDATE: frozenset({_S.EXPERIMENTAL, _S.RETIRED}),
    _S.EXPERIMENTAL: frozenset({_S.SURVIVING, _S.RETIRED}),
    _S.SURVIVING: frozenset({_S.PROMOTED, _S.EXPERIMENTAL, _S.RETIRED}),
    _S.PROMOTED: frozenset({_S.RETIRED, _S.ROLLED_BACK}),
    _S.RETIRED: frozenset({_S.PROMOTED}),  # rollback target re-promotion
    _S.ROLLED_BACK: frozenset({_S.RETIRED}),
}

_C = ClaimStatus
CLAIM_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _C.UNVERIFIED: frozenset({_C.CANDIDATE, _C.REJECTED, _C.CONTESTED}),
    _C.CANDIDATE: frozenset({_C.PARTIALLY_VERIFIED, _C.VERIFIED, _C.REJECTED, _C.CONTESTED}),
    _C.PARTIALLY_VERIFIED: frozenset({_C.VERIFIED, _C.REJECTED, _C.CONTESTED, _C.CANDIDATE}),
    _C.VERIFIED: frozenset({_C.CONTESTED, _C.REJECTED}),
    _C.REJECTED: frozenset({_C.CANDIDATE}),
    _C.CONTESTED: frozenset({_C.CANDIDATE, _C.PARTIALLY_VERIFIED, _C.VERIFIED, _C.REJECTED}),
}

_D = DiscoveryStatus
DISCOVERY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _D.CANDIDATE: frozenset({_D.VERIFICATION_PENDING, _D.REJECTED}),
    _D.VERIFICATION_PENDING: frozenset({_D.VERIFIED, _D.REJECTED, _D.CONTESTED, _D.CANDIDATE}),
    _D.VERIFIED: frozenset({_D.HUMAN_REVIEW, _D.CONTESTED, _D.REJECTED}),
    _D.HUMAN_REVIEW: frozenset({_D.APPROVED, _D.REJECTED, _D.CONTESTED}),
    _D.APPROVED: frozenset({_D.PUBLISHED, _D.CONTESTED}),
    _D.CONTESTED: frozenset({_D.VERIFICATION_PENDING, _D.REJECTED}),
    _D.PUBLISHED: frozenset({_D.CONTESTED}),
    _D.REJECTED: frozenset(),
}

_P = ApprovalStatus
APPROVAL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _P.PENDING: frozenset({_P.APPROVED, _P.REJECTED, _P.EXPIRED, _P.CANCELLED}),
    _P.APPROVED: frozenset(),
    _P.REJECTED: frozenset(),
    _P.EXPIRED: frozenset(),
    _P.CANCELLED: frozenset(),
}

_Me = MemoryStatus
MEMORY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _Me.PROPOSED: frozenset({_Me.ACTIVE, _Me.REJECTED, _Me.QUARANTINED}),
    _Me.ACTIVE: frozenset({_Me.SUPERSEDED, _Me.QUARANTINED}),
    _Me.QUARANTINED: frozenset({_Me.ACTIVE, _Me.REJECTED}),
    _Me.SUPERSEDED: frozenset(),
    _Me.REJECTED: frozenset(),
}

_W = WorkflowStatus
WORKFLOW_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _W.PENDING: frozenset({_W.RUNNING, _W.CANCELLED, _W.FAILED}),
    _W.RUNNING: frozenset({_W.WAITING, _W.COMPLETED, _W.FAILED, _W.CANCELLED, _W.TIMED_OUT, _W.RUNNING}),
    _W.WAITING: frozenset({_W.RUNNING, _W.CANCELLED, _W.FAILED, _W.TIMED_OUT}),
    _W.COMPLETED: frozenset(),
    _W.FAILED: frozenset({_W.PENDING}),  # explicit retry
    _W.CANCELLED: frozenset(),
    _W.TIMED_OUT: frozenset({_W.PENDING}),
}

_G = RunState
RUN_TRANSITIONS: Mapping[str, frozenset[str]] = {
    _G.PENDING: frozenset({_G.RUNNING, _G.CANCELLED, _G.FAILED}),
    _G.RUNNING: frozenset({_G.WAITING, _G.COMPLETED, _G.FAILED, _G.CANCELLED}),
    _G.WAITING: frozenset({_G.RUNNING, _G.COMPLETED, _G.FAILED, _G.CANCELLED}),
    _G.COMPLETED: frozenset(),
    _G.FAILED: frozenset(),
    _G.CANCELLED: frozenset(),
}

MACHINES: dict[str, Mapping[str, frozenset[str]]] = {
    "mission": MISSION_TRANSITIONS,
    "agent_run": AGENT_RUN_TRANSITIONS,
    "execution": EXECUTION_TRANSITIONS,
    "research_task": RESEARCH_TRANSITIONS,
    "hypothesis": HYPOTHESIS_TRANSITIONS,
    "experiment": EXPERIMENT_TRANSITIONS,
    "failure": FAILURE_TRANSITIONS,
    "strategy": STRATEGY_TRANSITIONS,
    "claim": CLAIM_TRANSITIONS,
    "discovery": DISCOVERY_TRANSITIONS,
    "approval": APPROVAL_TRANSITIONS,
    "memory": MEMORY_TRANSITIONS,
    "workflow": WORKFLOW_TRANSITIONS,
    "run": RUN_TRANSITIONS,
}


def allowed_transitions(machine: str, current: str) -> frozenset[str]:
    table = MACHINES[machine]
    return table.get(current, frozenset())


def can_transition(machine: str, current: str, target: str) -> bool:
    return target in allowed_transitions(machine, current)


def assert_transition(machine: str, current: str, target: str) -> None:
    """Raise :class:`InvalidTransitionError` unless ``current → target`` is permitted."""
    allowed = allowed_transitions(machine, current)
    if target not in allowed:
        raise InvalidTransitionError(machine, current, target, allowed)


def validate_tables() -> None:
    """Sanity check: every target state is itself a known state (used by unit tests)."""
    for name, table in MACHINES.items():
        states = set(table)
        for source, targets in table.items():
            unknown = set(targets) - states
            if unknown:
                raise AssertionError(f"{name}: {source} → unknown states {sorted(unknown)}")
