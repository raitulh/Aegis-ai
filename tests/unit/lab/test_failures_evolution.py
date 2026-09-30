"""Unit tests: failure intelligence and the evolution engine (incl. guardrails)."""

from __future__ import annotations

import numpy as np
import pytest

from engines.lab.enums import AutonomyLevel, FailureType, StrategyKind
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine
from engines.lab.evolution.fitness import FitnessEngine, Objective
from engines.lab.evolution.genome import (
    GuardrailViolation,
    ParameterDef,
    StrategyDefinition,
    StrategyGovernance,
    check_escalation,
)
from engines.lab.evolution.mutation import MutationEngine
from engines.lab.evolution.pareto import dominates, hypervolume, non_dominated_sort
from engines.lab.evolution.population import Individual
from engines.lab.evolution.promotion import CandidateStats, PromotionGate, RollbackManager, VersionRecord
from engines.lab.failures.classifier import FailureClassifier, FailureSignal, failure_signature
from engines.lab.failures.recovery import extract_lesson, propose_recovery


def test_failure_signature_stable_across_noise() -> None:
    a = failure_signature("code_failure", "KeyError: 'col_17' at /tmp/run-1/main.py line 42")
    b = failure_signature("code_failure", "KeyError: 'col_99' at /tmp/run-7/other.py line 3")
    assert a == b


def test_classifier_rules_and_evidence() -> None:
    c = FailureClassifier().classify(
        FailureSignal(exit_code=1, stderr_tail="Traceback\nModuleNotFoundError: No module named 'torch'")
    )
    assert c.failure_type == FailureType.CODE_FAILURE and c.rule_id == "code.dependency"
    assert any("torch" in line for line in c.evidence_lines)
    unknown = FailureClassifier().classify(FailureSignal(stage="execution"))
    assert unknown.rule_id == "fallback.unclassified" and unknown.confidence < 0.5


def test_recovery_proposals_are_bounded_and_flag_approval() -> None:
    timeout = FailureClassifier().classify(FailureSignal(timed_out=True))
    actions = propose_recovery(timeout, {"resources": {"timeout_seconds": 1800}})
    inc = next(a for a in actions if a.kind == "increase_timeout")
    assert inc.spec_patch["resources"]["timeout_seconds"] == 3600 and inc.requires_approval
    oom = FailureClassifier().classify(FailureSignal(exit_code=137))
    kinds = {
        a.kind for a in propose_recovery(oom, {"resources": {"memory_mb": 1024}, "parameters": {"batch_size": 64}})
    }
    assert {"increase_memory", "reduce_parameter"} <= kinds
    hyp = FailureClassifier().classify(
        FailureSignal(stage="evaluation", evaluator_verdicts={"benchmark": "fail", "statistical": "fail"})
    )
    assert all(not a.retry for a in propose_recovery(hyp, {}))
    lesson = extract_lesson(timeout, context="experiment E1", recovery=inc, resolved=True)
    assert "resolved it" in lesson


SPACE = [
    ParameterDef(name="lr", kind="float", low=1e-4, high=1.0, log=True),
    ParameterDef(name="restarts", kind="int", low=0, high=10),
    ParameterDef(name="method", kind="categorical", choices=["random", "annealing", "es"]),
    ParameterDef(name="adaptive", kind="bool"),
]
GOV = StrategyGovernance(tools=["python_execution"], max_autonomy=AutonomyLevel.L3_AUTOMATED_EXECUTION)


def _def(**params: object) -> StrategyDefinition:
    p = {"lr": 0.01, "restarts": 2, "method": "annealing", "adaptive": False} | params
    return StrategyDefinition(
        kind=StrategyKind.OPTIMIZATION,
        parameter_space=SPACE,
        parameters=p,
        governance=GOV,
        behavior={"tool_sequence": ["python_execution"]},
    )


def test_definition_validation() -> None:
    with pytest.raises(ValueError):
        _def(lr=5.0)
    with pytest.raises(ValueError):
        StrategyDefinition(kind=StrategyKind.OPTIMIZATION, parameter_space=SPACE, parameters={"unknown": 1})
    with pytest.raises(ValueError):
        StrategyDefinition(
            kind=StrategyKind.OPTIMIZATION,
            parameter_space=SPACE,
            parameters={},
            behavior={"tool_sequence": ["web_search"]},
            governance=GOV,
        )


def test_mutation_is_bounded_deterministic_and_non_escalating() -> None:
    engine = MutationEngine(0.5)
    parent = _def()
    c1, r1 = engine.mutate(parent, np.random.default_rng(7))
    c2, _r2 = engine.mutate(parent, np.random.default_rng(7))
    assert c1.parameters == c2.parameters and r1.changes
    assert c1.governance == parent.governance
    for _ in range(50):
        child, _ = engine.mutate(parent, np.random.default_rng(_))
        assert 1e-4 <= child.parameters["lr"] <= 1.0 and 0 <= child.parameters["restarts"] <= 10


def test_agent_proposals_cannot_escape_space() -> None:
    engine = MutationEngine()
    child, record = engine.apply_proposal(_def(), {"restarts": 5})
    assert child.parameters["restarts"] == 5 and record.source == "agent_proposal"
    with pytest.raises(GuardrailViolation):
        engine.apply_proposal(_def(), {"restarts": 50})
    with pytest.raises(GuardrailViolation):
        engine.apply_proposal(_def(), {"allowed_tools": ["shell"]})


def test_escalation_detection() -> None:
    wider = StrategyGovernance(
        tools=["python_execution", "web_search"],
        network="allowlist",
        secrets=["AWS"],
        max_autonomy=AutonomyLevel.L5_LONG_HORIZON_AUTONOMOUS_RND,
        production_access=True,
    )
    problems = check_escalation(GOV, wider)
    assert len(problems) >= 5
    assert check_escalation(wider, GOV) == []


def test_pareto_and_fitness_constraints() -> None:
    fe = FitnessEngine(
        [
            Objective(name="perf", metric="perf", direction="maximize"),
            Objective(name="cost", metric="cost", direction="minimize"),
            Objective(name="safety", metric="viol", direction="minimize", constraint_max=0),
        ]
    )
    safe = fe.compute({"perf": 0.8, "cost": 2, "viol": 0})
    unsafe = fe.compute({"perf": 0.99, "cost": 1, "viol": 3})
    assert not unsafe.feasible and dominates(safe, unsafe, fe.keys)
    a, b, c = (
        fe.compute({"perf": 0.9, "cost": 3, "viol": 0}),
        fe.compute({"perf": 0.8, "cost": 1, "viol": 0}),
        fe.compute({"perf": 0.7, "cost": 4, "viol": 0}),
    )
    fronts = non_dominated_sort([a, b, c], fe.keys)
    assert sorted(fronts[0]) == [0, 1] and fronts[1] == [2]
    assert hypervolume([(1, 1)], (0, 0)) == 1.0
    assert hypervolume([(2, 1), (1, 2)], (0, 0)) == 3.0


def test_evolution_engine_step_is_deterministic() -> None:
    objs = [
        Objective(name="perf", metric="perf", direction="maximize"),
        Objective(name="cost", metric="cost", direction="minimize"),
    ]

    def population() -> list[Individual]:
        rng = np.random.default_rng(0)
        out = []
        for i in range(6):
            d = _def(lr=float(10 ** rng.uniform(-4, 0)), restarts=int(rng.integers(0, 10)))
            out.append(
                Individual(
                    id=f"i{i}",
                    definition=d,
                    metrics={"perf": d.parameters["restarts"] / 10, "cost": d.parameters["lr"]},
                )
            )
        return out

    cfg = EvolutionConfig(
        population_size=4, offspring_per_generation=3, objectives=objs, seed=5, niche_descriptors=[("method", 3)]
    )
    out1 = EvolutionEngine(cfg).step(population(), 1)
    out2 = EvolutionEngine(cfg).step(population(), 1)
    assert out1.summary() == out2.summary()
    assert len(out1.survivors) == 4 and len(out1.candidates) == 3
    assert all(c.definition.governance == GOV for c in out1.candidates)


def test_promotion_gate_requires_evidence() -> None:
    fe = FitnessEngine(
        [
            Objective(name="perf", metric="perf", direction="maximize"),
            Objective(name="safety", metric="viol", direction="minimize", constraint_max=0),
        ]
    )
    gate = PromotionGate(fe.keys)
    inc = CandidateStats("inc", fe.compute({"perf": 0.70, "viol": 0}), [0.70, 0.71, 0.69, 0.70], 4, True)
    good = CandidateStats("c", fe.compute({"perf": 0.80, "viol": 0}), [0.80, 0.81, 0.79, 0.80], 4, True)
    assert gate.evaluate(good, inc).eligible
    unreproduced = CandidateStats("c", good.fitness, good.primary_samples, 4, None)
    assert not gate.evaluate(unreproduced, inc).eligible
    noisy = CandidateStats("c", fe.compute({"perf": 0.72, "viol": 0}), [0.5, 0.95, 0.6, 0.83], 4, True)
    decision = gate.evaluate(noisy, inc)
    assert not decision.eligible and decision.checks["statistically_better"] is False
    unsafe = CandidateStats("c", fe.compute({"perf": 0.9, "viol": 2}), [0.9] * 4, 4, True)
    assert not gate.evaluate(unsafe, inc).eligible


def test_rollback_target() -> None:
    history = [
        VersionRecord("v1", "retired", "2026-01-01"),
        VersionRecord("v2", "promoted", "2026-02-01"),
        VersionRecord("v3", "promoted", "2026-03-01"),
    ]
    assert RollbackManager.rollback_target(history, "v3") == "v2"
