"""Autonomy levels: which mission actions may proceed without a human in the loop.

Autonomy is a *ceiling* set by humans on a mission. It never grants permissions — authorization is decided by
RBAC and the policy engine — it only decides whether an otherwise-permitted step needs human approval.
Agents and workflows can never change a mission's autonomy level or grant themselves permissions.
"""

from __future__ import annotations

from enum import StrEnum

from engines.lab.enums import ActorType, AutonomyLevel


class LabAction(StrEnum):
    PLAN_EXECUTE = "plan.execute"
    RESEARCH_RUN = "research.run"
    HYPOTHESIS_GENERATE = "hypothesis.generate"
    HYPOTHESIS_SELECT = "hypothesis.select"
    EXPERIMENT_DESIGN = "experiment.design"
    EXPERIMENT_EXECUTE = "experiment.execute"
    EVOLUTION_MUTATE = "evolution.mutate"
    STRATEGY_PROMOTE = "strategy.promote"
    MISSION_REPLAN = "mission.replan"
    MISSION_NEXT_CYCLE = "mission.next_cycle"
    REPRODUCTION_RUN = "reproduction.run"
    DISCOVERY_CREATE = "discovery.create"
    DISCOVERY_APPROVE = "discovery.approve"
    PUBLICATION = "publication"
    MEMORY_PROMOTE = "memory.promote"
    TOOL_CALL = "tool.call"
    AUTONOMY_CHANGE = "autonomy.change"
    PERMISSION_GRANT = "permission.grant"


# Minimum autonomy rank at which an action may run unattended (without a human approval step).
_UNATTENDED_MIN_RANK: dict[LabAction, int] = {
    LabAction.PLAN_EXECUTE: 1,
    LabAction.RESEARCH_RUN: 1,
    LabAction.HYPOTHESIS_GENERATE: 1,
    LabAction.HYPOTHESIS_SELECT: 2,
    LabAction.EXPERIMENT_DESIGN: 2,
    LabAction.EXPERIMENT_EXECUTE: 3,
    LabAction.REPRODUCTION_RUN: 3,
    LabAction.EVOLUTION_MUTATE: 4,
    LabAction.STRATEGY_PROMOTE: 4,
    LabAction.MISSION_NEXT_CYCLE: 4,
    LabAction.MISSION_REPLAN: 5,
    LabAction.DISCOVERY_CREATE: 1,
    LabAction.TOOL_CALL: 1,
    LabAction.MEMORY_PROMOTE: 4,
}

# Actions that ALWAYS require a human, regardless of autonomy level.
ALWAYS_HUMAN: frozenset[LabAction] = frozenset({LabAction.DISCOVERY_APPROVE, LabAction.PUBLICATION})

# Actions that non-human actors may never perform at all.
HUMAN_ONLY: frozenset[LabAction] = frozenset(
    {LabAction.AUTONOMY_CHANGE, LabAction.PERMISSION_GRANT, LabAction.DISCOVERY_APPROVE, LabAction.PUBLICATION}
)

NON_HUMAN_ACTORS: frozenset[str] = frozenset({ActorType.AGENT, ActorType.WORKFLOW, ActorType.SYSTEM})

LEVEL_DESCRIPTIONS: dict[AutonomyLevel, str] = {
    AutonomyLevel.L0_ASSISTED: "Agents draft; every step waits for a human decision.",
    AutonomyLevel.L1_RESEARCH_AUTOMATION: "Automated planning, literature research and hypothesis generation.",
    AutonomyLevel.L2_AUTOMATED_EXPERIMENT_DESIGN: "Adds automated hypothesis selection and experiment design/validation.",
    AutonomyLevel.L3_AUTOMATED_EXECUTION: "Adds sandboxed execution and reproduction within budget and policy.",
    AutonomyLevel.L4_CLOSED_LOOP_EVOLUTION: "Adds closed-loop strategy evolution and gated strategy promotion.",
    AutonomyLevel.L5_LONG_HORIZON_AUTONOMOUS_RND: "Adds multi-cycle re-planning over long horizons.",
}


class AutonomyViolation(PermissionError):
    """An actor attempted an action that autonomy rules forbid."""


def rank_of(level: str) -> int:
    return AutonomyLevel(level).rank


def requires_human(level: str, action: str) -> bool:
    """True when ``action`` needs an explicit human approval at mission autonomy ``level``."""
    act = LabAction(action)
    if act in ALWAYS_HUMAN or act in HUMAN_ONLY:
        return True
    minimum = _UNATTENDED_MIN_RANK.get(act)
    if minimum is None:
        return True  # unknown/unclassified actions default to human-in-the-loop
    return rank_of(level) < minimum


def assert_actor_may(actor_type: str, action: str) -> None:
    """Raise if a non-human actor attempts a human-only action (e.g. raising its own autonomy)."""
    if actor_type in NON_HUMAN_ACTORS and LabAction(action) in HUMAN_ONLY:
        raise AutonomyViolation(f"{actor_type} actors may never perform '{action}'")


def validate_autonomy_change(actor_type: str, current: str, requested: str, *, max_allowed: str) -> str:
    """Validate a change of mission autonomy. Only humans may change it, never above the org ceiling."""
    assert_actor_may(actor_type, LabAction.AUTONOMY_CHANGE)
    if rank_of(requested) > rank_of(max_allowed):
        raise AutonomyViolation(f"Autonomy {requested} exceeds the organization ceiling {max_allowed}")
    return AutonomyLevel(requested).value
