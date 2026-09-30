"""Autonomy capability table, human gates and autonomy-change validation."""

from __future__ import annotations

import pytest

from engines.lab.autonomy import (
    ALWAYS_HUMAN,
    CAPABILITIES,
    Capability,
    allows,
    capabilities,
    clamp,
    effective_ceiling,
    minimum_level,
    normalize_level,
    requires_human,
    validate_autonomy_change,
)
from engines.lab.policy import ACTIONS
from engines.lab.states import AutonomyLevel

L0, L1, L2, L3, L4, L5 = (level.value for level in AutonomyLevel)


def test_levels_are_cumulative() -> None:
    previous: frozenset[Capability] = frozenset()
    for level in AutonomyLevel:
        current = CAPABILITIES[level.value]
        assert previous <= current
        assert not (current & ALWAYS_HUMAN)
        previous = current


@pytest.mark.parametrize(
    ("capability", "lowest"),
    [
        (Capability.ASSIST, L0),
        (Capability.LITERATURE_SEARCH, L1),
        (Capability.MEMORY_READ, L1),
        (Capability.HYPOTHESIS_GENERATION, L2),
        (Capability.EXPERIMENT_DESIGN, L2),
        (Capability.EXPERIMENT_VALIDATION, L2),
        (Capability.EXPERIMENT_EXECUTION, L3),
        (Capability.EVALUATION, L3),
        (Capability.STRATEGY_MUTATION, L4),
        (Capability.STRATEGY_EVALUATION, L4),
        (Capability.MULTI_CYCLE, L5),
        (Capability.LONG_HORIZON, L5),
    ],
)
def test_capability_table(capability: Capability, lowest: str) -> None:
    assert minimum_level(capability) == lowest
    for level in AutonomyLevel:
        expected = list(AutonomyLevel).index(level) >= list(AutonomyLevel).index(AutonomyLevel(lowest))
        assert allows(level.value, capability) is expected, (level, capability)


@pytest.mark.parametrize("capability", sorted(ALWAYS_HUMAN))
def test_gated_capabilities_are_never_automatic(capability: Capability) -> None:
    assert minimum_level(capability) is None
    assert not any(allows(level.value, capability) for level in AutonomyLevel)


def test_requires_human() -> None:
    for action in ACTIONS:
        assert requires_human(L0, action)  # L0: every action needs a human
    assert not requires_human(L1, "research.deep_research")
    assert not requires_human(L1, "tool.invoke")
    assert requires_human(L2, "execution.submit")
    assert not requires_human(L3, "experiment.execute")
    assert not requires_human(L4, "strategy_mutation")
    assert requires_human(L5, "strategy.promote")  # promotion is always gated
    assert requires_human(L5, "discovery.publish")
    assert requires_human(L5, "mission.autonomy_change")
    assert requires_human(L5, "integration.production")
    assert requires_human(L5, "unknown.action")  # fail-safe
    assert requires_human("L9", "tool.invoke")


def test_normalization_and_ceilings() -> None:
    assert normalize_level("L3") == L3
    assert normalize_level(L2) == L2
    with pytest.raises(ValueError, match="Unknown autonomy level"):
        normalize_level("L7")
    with pytest.raises(ValueError, match="Unknown capability"):
        allows(L3, "teleport")
    assert capabilities("L1") == CAPABILITIES[L1]
    assert effective_ceiling(L4, L2) == L2
    assert effective_ceiling(L2, L4) == L2
    assert effective_ceiling(L3, None) == L3
    assert clamp(L5, L3) == L3
    assert clamp(L1, L3, L2) == L1


@pytest.mark.parametrize(
    ("current", "requested", "org", "project", "human", "ok", "fragment"),
    [
        (L1, L3, L3, None, True, True, "raised"),
        (L3, L1, L3, None, True, True, "lowered"),
        (L2, L2, L3, None, True, True, "unchanged"),
        (L1, L4, L3, None, True, False, "organization autonomy ceiling"),
        (L1, L3, L4, L2, True, False, "project autonomy ceiling"),
        (L1, L2, L3, None, False, False, "agents never change autonomy"),
        (L3, L1, L3, None, False, False, "agents never change autonomy"),  # not even to lower it
        (L1, "L42", L3, None, True, False, "Unknown autonomy level"),
    ],
)
def test_validate_autonomy_change(
    current: str, requested: str, org: str, project: str | None, human: bool, ok: bool, fragment: str
) -> None:
    result = validate_autonomy_change(current, requested, org, project, human)
    assert result.ok is ok
    assert fragment in result.reason
    assert tuple(result) == (result.ok, result.reason)
