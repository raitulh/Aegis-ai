"""Explicit lifecycle state machines.

Every status change in the lab goes through ``<machine>.ensure(current, target)``. Transitions not listed
here are rejected, which keeps lifecycles auditable and prevents "shortcuts" such as marking an experiment
VERIFIED without evaluation, or a discovery APPROVED without verification and human review.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from engines.lab.enums import (
    AgentRunStatus,
    ApprovalStatus,
    ClaimStatus,
    DiscoveryStatus,
    ExecutionStatus,
    ExperimentStatus,
    HypothesisStatus,
    MissionStatus,
    ResearchTaskStatus,
    StrategyStatus,
)


class InvalidTransition(ValueError):
    """A lifecycle transition that the state machine does not allow."""

    def __init__(self, machine: str, current: str, target: str, allowed: Iterable[str]) -> None:
        self.machine = machine
        self.current = current
        self.target = target
        self.allowed = sorted(allowed)
        super().__init__(
            f"{machine}: cannot transition from '{current}' to '{target}'"
            + (f" (allowed: {', '.join(self.allowed)})" if self.allowed else " (terminal state)")
        )


@dataclass(frozen=True)
class StateMachine:
    name: str
    transitions: Mapping[str, frozenset[str]]
    initial: str

    def allowed(self, current: str) -> frozenset[str]:
        return self.transitions.get(current, frozenset())

    def can(self, current: str, target: str) -> bool:
        return target in self.allowed(current)

    def ensure(self, current: str, target: str) -> str:
        if current == target:
            return target
        if not self.can(current, target):
            raise InvalidTransition(self.name, current, target, self.allowed(current))
        return target

    def is_terminal(self, state: str) -> bool:
        return not self.allowed(state)

    @property
    def states(self) -> frozenset[str]:
        out: set[str] = set(self.transitions)
        for targets in self.transitions.values():
            out |= targets
        return frozenset(out)


def _machine(name: str, initial: str, table: Mapping[str, Iterable[str]]) -> StateMachine:
    return StateMachine(name=name, initial=initial, transitions={k: frozenset(v) for k, v in table.items()})


M = MissionStatus
MISSION = _machine(
    "mission",
    M.DRAFT,
    {
        M.DRAFT: {M.PLANNED, M.CANCELLED, M.ARCHIVED},
        M.PLANNED: {M.APPROVED, M.DRAFT, M.CANCELLED},
        M.APPROVED: {M.RUNNING, M.PLANNED, M.CANCELLED},
        M.RUNNING: {M.PAUSED, M.COMPLETED, M.FAILED, M.CANCELLED},
        M.PAUSED: {M.RUNNING, M.CANCELLED, M.FAILED},
        M.FAILED: {M.RUNNING, M.ARCHIVED},
        M.COMPLETED: {M.ARCHIVED},
        M.CANCELLED: {M.ARCHIVED},
        M.ARCHIVED: set(),
    },
)

H = HypothesisStatus
HYPOTHESIS = _machine(
    "hypothesis",
    H.GENERATED,
    {
        H.GENERATED: {H.CRITIQUED, H.REJECTED, H.ARCHIVED},
        H.CRITIQUED: {H.SELECTED, H.REJECTED, H.ARCHIVED},
        H.SELECTED: {H.EXPERIMENT_DESIGNED, H.ARCHIVED},
        H.EXPERIMENT_DESIGNED: {H.TESTING, H.ARCHIVED},
        H.TESTING: {H.SUPPORTED, H.REJECTED, H.INCONCLUSIVE},
        H.SUPPORTED: {H.TESTING, H.ARCHIVED},
        H.INCONCLUSIVE: {H.TESTING, H.ARCHIVED},
        H.REJECTED: {H.ARCHIVED},
        H.ARCHIVED: set(),
    },
)

E = ExperimentStatus
EXPERIMENT = _machine(
    "experiment",
    E.DRAFT,
    {
        E.DRAFT: {E.VALIDATING, E.ARCHIVED},
        E.VALIDATING: {E.QUEUED, E.DRAFT, E.REJECTED},
        E.QUEUED: {E.RUNNING, E.FAILED, E.ARCHIVED},
        E.RUNNING: {E.COMPLETED, E.FAILED},
        E.FAILED: {E.RETRYING, E.REJECTED, E.ARCHIVED},
        E.RETRYING: {E.QUEUED, E.RUNNING, E.FAILED},
        E.COMPLETED: {E.EVALUATING, E.ARCHIVED},
        E.EVALUATING: {E.REPRODUCING, E.VERIFIED, E.REJECTED, E.FAILED, E.COMPLETED, E.ARCHIVED},
        E.REPRODUCING: {E.VERIFIED, E.REJECTED, E.FAILED, E.EVALUATING},
        E.VERIFIED: {E.ARCHIVED, E.REPRODUCING},
        E.REJECTED: {E.ARCHIVED},
        E.ARCHIVED: set(),
    },
)

X = ExecutionStatus
EXECUTION = _machine(
    "execution_job",
    X.QUEUED,
    {
        X.QUEUED: {X.PROVISIONING, X.CANCELLED, X.FAILED},
        X.PROVISIONING: {X.RUNNING, X.FAILED, X.CANCELLED, X.TIMED_OUT},
        X.RUNNING: {X.PAUSED, X.SUCCEEDED, X.FAILED, X.TIMED_OUT, X.CANCELLED},
        X.PAUSED: {X.RUNNING, X.CANCELLED},
        X.SUCCEEDED: {X.VERIFICATION_PENDING},
        X.VERIFICATION_PENDING: {X.VERIFIED, X.FAILED},
        X.FAILED: set(),
        X.TIMED_OUT: set(),
        X.CANCELLED: set(),
        X.VERIFIED: set(),
    },
)

A = AgentRunStatus
AGENT_RUN = _machine(
    "agent_run",
    A.CREATED,
    {
        A.CREATED: {A.PLANNING, A.EXECUTING, A.CANCELLED, A.FAILED},
        A.PLANNING: {A.EXECUTING, A.WAITING_HUMAN, A.FAILED, A.CANCELLED},
        A.EXECUTING: {
            A.WAITING_TOOL,
            A.WAITING_HUMAN,
            A.WAITING_CHILD,
            A.EVALUATING,
            A.COMPLETED,
            A.FAILED,
            A.CANCELLED,
        },
        A.WAITING_TOOL: {A.EXECUTING, A.WAITING_HUMAN, A.FAILED, A.CANCELLED},
        A.WAITING_HUMAN: {A.EXECUTING, A.FAILED, A.CANCELLED},
        A.WAITING_CHILD: {A.EXECUTING, A.FAILED, A.CANCELLED},
        A.EVALUATING: {A.EXECUTING, A.COMPLETED, A.FAILED},
        A.COMPLETED: set(),
        A.FAILED: set(),
        A.CANCELLED: set(),
    },
)

S = StrategyStatus
STRATEGY = _machine(
    "strategy",
    S.CANDIDATE,
    {
        S.CANDIDATE: {S.EXPERIMENTAL, S.RETIRED},
        S.EXPERIMENTAL: {S.SURVIVING, S.RETIRED},
        S.SURVIVING: {S.PROMOTED, S.EXPERIMENTAL, S.RETIRED},
        S.PROMOTED: {S.RETIRED, S.ROLLED_BACK},
        S.ROLLED_BACK: {S.EXPERIMENTAL, S.RETIRED},
        S.RETIRED: set(),
    },
)

C = ClaimStatus
CLAIM = _machine(
    "claim",
    C.UNVERIFIED,
    {
        C.UNVERIFIED: {C.CANDIDATE, C.REJECTED},
        C.CANDIDATE: {C.PARTIALLY_VERIFIED, C.VERIFIED, C.REJECTED, C.CONTESTED},
        C.PARTIALLY_VERIFIED: {C.VERIFIED, C.REJECTED, C.CONTESTED, C.CANDIDATE},
        C.VERIFIED: {C.CONTESTED},
        C.CONTESTED: {C.VERIFIED, C.REJECTED, C.PARTIALLY_VERIFIED, C.CANDIDATE},
        C.REJECTED: {C.CANDIDATE, C.CONTESTED},
    },
)

D = DiscoveryStatus
DISCOVERY = _machine(
    "discovery",
    D.CANDIDATE,
    {
        D.CANDIDATE: {D.VERIFICATION_PENDING, D.REJECTED},
        D.VERIFICATION_PENDING: {D.VERIFIED, D.REJECTED, D.CONTESTED},
        D.VERIFIED: {D.HUMAN_REVIEW, D.CONTESTED},
        D.HUMAN_REVIEW: {D.APPROVED, D.REJECTED, D.CONTESTED},
        D.APPROVED: {D.PUBLISHED, D.CONTESTED},
        D.PUBLISHED: {D.CONTESTED},
        D.CONTESTED: {D.VERIFICATION_PENDING, D.REJECTED},
        D.REJECTED: set(),
    },
)

P = ApprovalStatus
APPROVAL = _machine(
    "approval",
    P.PENDING,
    {
        P.PENDING: {P.APPROVED, P.REJECTED, P.EXPIRED, P.CANCELLED},
        P.APPROVED: set(),
        P.REJECTED: set(),
        P.EXPIRED: set(),
        P.CANCELLED: set(),
    },
)

R = ResearchTaskStatus
RESEARCH_TASK = _machine(
    "research_task",
    R.CREATED,
    {
        R.CREATED: {R.PLANNING, R.RUNNING, R.CANCELLED, R.FAILED},
        R.PLANNING: {R.PLAN_REVIEW, R.APPROVED, R.FAILED, R.CANCELLED},
        R.PLAN_REVIEW: {R.APPROVED, R.PLANNING, R.CANCELLED},
        R.APPROVED: {R.RUNNING, R.CANCELLED},
        R.RUNNING: {R.COMPLETED, R.FAILED, R.CANCELLED},
        R.COMPLETED: set(),
        R.FAILED: set(),
        R.CANCELLED: set(),
    },
)

ALL_MACHINES: dict[str, StateMachine] = {
    m.name: m
    for m in (
        MISSION,
        HYPOTHESIS,
        EXPERIMENT,
        EXECUTION,
        AGENT_RUN,
        STRATEGY,
        CLAIM,
        DISCOVERY,
        APPROVAL,
        RESEARCH_TASK,
    )
}
