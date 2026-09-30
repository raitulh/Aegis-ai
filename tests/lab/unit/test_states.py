"""State machines: every lifecycle transition table is closed and enforces the documented rules."""

from __future__ import annotations

from itertools import pairwise

import pytest

from engines.lab.states import (
    AGENT_RUN_TERMINAL,
    MACHINES,
    MISSION_TERMINAL,
    AgentRunStatus,
    AutonomyLevel,
    ClaimStatus,
    DiscoveryStatus,
    ExperimentStatus,
    HypothesisStatus,
    InvalidTransitionError,
    MissionStatus,
    StrategyStatus,
    allowed_transitions,
    assert_transition,
    autonomy_rank,
    can_transition,
    validate_tables,
)


def test_tables_are_closed():
    validate_tables()
    for name, table in MACHINES.items():
        assert table, name


def test_mission_lifecycle_happy_path():
    path = [
        MissionStatus.DRAFT,
        MissionStatus.PLANNED,
        MissionStatus.APPROVED,
        MissionStatus.RUNNING,
        MissionStatus.PAUSED,
        MissionStatus.RUNNING,
        MissionStatus.COMPLETED,
        MissionStatus.ARCHIVED,
    ]
    for a, b in pairwise(path):
        assert_transition("mission", a, b)


def test_terminal_missions_cannot_restart():
    for state in MISSION_TERMINAL - {MissionStatus.FAILED}:
        assert not can_transition("mission", state, MissionStatus.RUNNING)
    with pytest.raises(InvalidTransitionError) as exc:
        assert_transition("mission", MissionStatus.DRAFT, MissionStatus.RUNNING)
    assert exc.value.current == "DRAFT" and exc.value.target == "RUNNING"
    assert MissionStatus.PLANNED in exc.value.allowed


def test_hypothesis_cannot_be_supported_without_testing():
    for state in (HypothesisStatus.GENERATED, HypothesisStatus.CRITIQUED, HypothesisStatus.SELECTED):
        assert not can_transition("hypothesis", state, HypothesisStatus.SUPPORTED)
    assert can_transition("hypothesis", HypothesisStatus.TESTING, HypothesisStatus.SUPPORTED)


def test_experiment_verification_requires_evaluation_or_reproduction():
    assert not can_transition("experiment", ExperimentStatus.RUNNING, ExperimentStatus.VERIFIED)
    assert not can_transition("experiment", ExperimentStatus.COMPLETED, ExperimentStatus.VERIFIED)
    assert can_transition("experiment", ExperimentStatus.REPRODUCING, ExperimentStatus.VERIFIED)


def test_discovery_approval_requires_human_review_stage():
    assert not can_transition("discovery", DiscoveryStatus.CANDIDATE, DiscoveryStatus.APPROVED)
    assert not can_transition("discovery", DiscoveryStatus.VERIFIED, DiscoveryStatus.APPROVED)
    assert can_transition("discovery", DiscoveryStatus.HUMAN_REVIEW, DiscoveryStatus.APPROVED)
    assert not can_transition("discovery", DiscoveryStatus.HUMAN_REVIEW, DiscoveryStatus.PUBLISHED)
    assert allowed_transitions("discovery", DiscoveryStatus.REJECTED) == frozenset()


def test_claims_cannot_jump_to_verified_from_unverified():
    assert not can_transition("claim", ClaimStatus.UNVERIFIED, ClaimStatus.VERIFIED)


def test_strategy_rollback_path():
    assert can_transition("strategy", StrategyStatus.PROMOTED, StrategyStatus.ROLLED_BACK)
    assert can_transition("strategy", StrategyStatus.RETIRED, StrategyStatus.PROMOTED)
    assert not can_transition("strategy", StrategyStatus.CANDIDATE, StrategyStatus.PROMOTED)


def test_agent_run_terminal_states_are_final():
    for state in AGENT_RUN_TERMINAL:
        assert allowed_transitions("agent_run", state) == frozenset()
    assert can_transition("agent_run", AgentRunStatus.WAITING_TOOL, AgentRunStatus.EXECUTING)


def test_autonomy_ranks_are_ordered():
    ranks = [autonomy_rank(level) for level in AutonomyLevel]
    assert ranks == sorted(ranks) == list(range(6))
    with pytest.raises(ValueError):
        autonomy_rank("L9_SKYNET")
