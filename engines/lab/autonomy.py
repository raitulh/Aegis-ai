"""Autonomy levels: what the lab may do on its own at each level, and who may change the level (pure).

Levels are cumulative — every level can do everything the levels below it can:

* **L0 assisted** — assist only: agents draft and suggest; every action needs a human.
* **L1 research automation** — automatic literature/web search, source reading and memory reads.
* **L2 automated experiment design** — + hypothesis generation/critique and experiment design/validation.
* **L3 automated execution** — + sandboxed execution, evaluation and failure analysis within budget.
* **L4 closed-loop evolution** — + strategy mutation and evaluation (promotion stays human-gated).
* **L5 long-horizon autonomous R&D** — + multi-cycle, long-horizon missions.

Some capabilities are never granted by any level (they always need a human): strategy promotion,
discovery approval/publication, autonomy changes, policy changes and production integrations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from engines.lab.states import AUTONOMY_RANK, AutonomyLevel


class Capability(StrEnum):
    ASSIST = "assist"
    LITERATURE_SEARCH = "literature_search"
    WEB_RESEARCH = "web_research"
    DEEP_RESEARCH = "deep_research"
    MEMORY_READ = "memory_read"
    TOOL_USE = "tool_use"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_CRITIQUE = "hypothesis_critique"
    EXPERIMENT_DESIGN = "experiment_design"
    EXPERIMENT_VALIDATION = "experiment_validation"
    MEMORY_WRITE = "memory_write"
    EXPERIMENT_EXECUTION = "experiment_execution"
    EVALUATION = "evaluation"
    FAILURE_ANALYSIS = "failure_analysis"
    REPRODUCTION = "reproduction"
    STRATEGY_MUTATION = "strategy_mutation"
    STRATEGY_EVALUATION = "strategy_evaluation"
    BENCHMARK = "benchmark"
    MULTI_CYCLE = "multi_cycle"
    LONG_HORIZON = "long_horizon"
    # Never granted by an autonomy level:
    STRATEGY_PROMOTION = "strategy_promotion"
    DISCOVERY_APPROVAL = "discovery_approval"
    DISCOVERY_PUBLICATION = "discovery_publication"
    MEMORY_PROMOTION = "memory_promotion"
    AUTONOMY_CHANGE = "autonomy_change"
    POLICY_CHANGE = "policy_change"
    PRODUCTION_INTEGRATION = "production_integration"


_L = AutonomyLevel
_C = Capability
_ADDED_AT: dict[str, frozenset[Capability]] = {
    _L.L0_ASSISTED: frozenset({_C.ASSIST}),
    _L.L1_RESEARCH_AUTOMATION: frozenset(
        {_C.LITERATURE_SEARCH, _C.WEB_RESEARCH, _C.DEEP_RESEARCH, _C.MEMORY_READ, _C.TOOL_USE}
    ),
    _L.L2_AUTOMATED_EXPERIMENT_DESIGN: frozenset(
        {
            _C.HYPOTHESIS_GENERATION,
            _C.HYPOTHESIS_CRITIQUE,
            _C.EXPERIMENT_DESIGN,
            _C.EXPERIMENT_VALIDATION,
            _C.MEMORY_WRITE,
        }
    ),
    _L.L3_AUTOMATED_EXECUTION: frozenset(
        {_C.EXPERIMENT_EXECUTION, _C.EVALUATION, _C.FAILURE_ANALYSIS, _C.REPRODUCTION}
    ),
    _L.L4_CLOSED_LOOP_EVOLUTION: frozenset({_C.STRATEGY_MUTATION, _C.STRATEGY_EVALUATION, _C.BENCHMARK}),
    _L.L5_LONG_HORIZON_AUTONOMOUS_RND: frozenset({_C.MULTI_CYCLE, _C.LONG_HORIZON}),
}

ALWAYS_HUMAN: frozenset[Capability] = frozenset(
    {
        _C.STRATEGY_PROMOTION,
        _C.DISCOVERY_APPROVAL,
        _C.DISCOVERY_PUBLICATION,
        _C.MEMORY_PROMOTION,
        _C.AUTONOMY_CHANGE,
        _C.POLICY_CHANGE,
        _C.PRODUCTION_INTEGRATION,
    }
)


def _build_table() -> dict[str, frozenset[Capability]]:
    table: dict[str, frozenset[Capability]] = {}
    acc: frozenset[Capability] = frozenset()
    for level in AutonomyLevel:
        acc = acc | _ADDED_AT[level]
        table[level.value] = acc
    return table


CAPABILITIES: dict[str, frozenset[Capability]] = _build_table()

# Governance actions (engines.lab.policy.ACTIONS) → the capability that must be automatic at a level.
ACTION_CAPABILITY: dict[str, Capability] = {
    "research.deep_research": _C.DEEP_RESEARCH,
    "tool.invoke": _C.TOOL_USE,
    "mcp.invoke": _C.TOOL_USE,
    "llm.external_processing": _C.ASSIST,
    "execution.submit": _C.EXPERIMENT_EXECUTION,
    "experiment.execute": _C.EXPERIMENT_EXECUTION,
    "strategy.promote": _C.STRATEGY_PROMOTION,
    "strategy.auto_promote": _C.STRATEGY_PROMOTION,
    "discovery.approve": _C.DISCOVERY_APPROVAL,
    "discovery.publish": _C.DISCOVERY_PUBLICATION,
    "memory.promote": _C.MEMORY_PROMOTION,
    "mission.start": _C.MULTI_CYCLE,
    "mission.autonomy_change": _C.AUTONOMY_CHANGE,
    "integration.production": _C.PRODUCTION_INTEGRATION,
}


class AutonomyChangeCheck(NamedTuple):
    ok: bool
    reason: str


def normalize_level(level: str) -> str:
    """Accept full names (``L3_AUTOMATED_EXECUTION``) or shorthand (``L3``); raise on unknown levels."""
    value = (level or "").strip()
    if value in AUTONOMY_RANK:
        return value
    for known in AutonomyLevel:
        if known.value.split("_", 1)[0] == value.upper():
            return known.value
    raise ValueError(f"Unknown autonomy level: {level!r}")


def rank(level: str) -> int:
    return AUTONOMY_RANK[normalize_level(level)]


def _capability(capability: str) -> Capability:
    try:
        return Capability(capability)
    except ValueError as exc:
        raise ValueError(f"Unknown capability: {capability!r}") from exc


def capabilities(level: str) -> frozenset[Capability]:
    return CAPABILITIES[normalize_level(level)]


def allows(level: str, capability: str) -> bool:
    """True when ``capability`` may run automatically (without a human) at ``level``."""
    cap = _capability(capability)
    if cap in ALWAYS_HUMAN:
        return False
    return cap in CAPABILITIES[normalize_level(level)]


def minimum_level(capability: str) -> str | None:
    """The lowest level that grants ``capability`` automatically (``None`` = always human)."""
    cap = _capability(capability)
    for level in AutonomyLevel:
        if cap in CAPABILITIES[level.value] and cap not in ALWAYS_HUMAN:
            return level.value
    return None


def requires_human(level: str, action: str) -> bool:
    """Whether ``action`` (a governance action or a capability name) needs a human at ``level``.

    Fail-safe: unknown actions and unknown levels always require a human; at L0 everything does.
    """
    try:
        normalized = normalize_level(level)
    except ValueError:
        return True
    if normalized == AutonomyLevel.L0_ASSISTED:
        return True
    cap = ACTION_CAPABILITY.get(action)
    if cap is None:
        try:
            cap = Capability(action)
        except ValueError:
            return True
    return not allows(normalized, cap)


def effective_ceiling(org_ceiling: str, project_ceiling: str | None = None) -> str:
    """The lower of the organization and project ceilings."""
    org = normalize_level(org_ceiling)
    if not project_ceiling:
        return org
    project = normalize_level(project_ceiling)
    return org if AUTONOMY_RANK[org] <= AUTONOMY_RANK[project] else project


def clamp(level: str, org_ceiling: str, project_ceiling: str | None = None) -> str:
    """``level`` lowered to the effective ceiling (never raised)."""
    ceiling = effective_ceiling(org_ceiling, project_ceiling)
    current = normalize_level(level)
    return current if AUTONOMY_RANK[current] <= AUTONOMY_RANK[ceiling] else ceiling


def validate_autonomy_change(
    current: str,
    requested: str,
    org_ceiling: str,
    project_ceiling: str | None,
    actor_is_human: bool,
) -> AutonomyChangeCheck:
    """Decide whether an autonomy change is allowed.

    Only humans may change autonomy (agents and workflows never can, not even to lower it) and the
    requested level may never exceed the organization or project ceiling. Lowering is always allowed.
    """
    if not actor_is_human:
        return AutonomyChangeCheck(False, "Only a signed-in human can change autonomy; agents never change autonomy")
    try:
        cur = normalize_level(current)
        req = normalize_level(requested)
        ceiling = effective_ceiling(org_ceiling, project_ceiling)
    except ValueError as exc:
        return AutonomyChangeCheck(False, str(exc))
    if req == cur:
        return AutonomyChangeCheck(True, "Autonomy level unchanged")
    if AUTONOMY_RANK[req] < AUTONOMY_RANK[cur]:
        return AutonomyChangeCheck(True, f"Autonomy lowered from {cur} to {req}")
    if AUTONOMY_RANK[req] > AUTONOMY_RANK[ceiling]:
        which = "project" if project_ceiling and normalize_level(project_ceiling) == ceiling else "organization"
        return AutonomyChangeCheck(False, f"{req} exceeds the {which} autonomy ceiling {ceiling}")
    return AutonomyChangeCheck(True, f"Autonomy raised from {cur} to {req}")
