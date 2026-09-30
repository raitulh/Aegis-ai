"""EvolutionEngine: configuration, generation 0, deterministic NSGA-II steps, survival transitions,
and evidence that the engine genuinely optimises (hypervolume rises on ZDT1)."""

from __future__ import annotations

import json

import pytest

from engines.lab.benchmarks.registry import get_suite
from engines.lab.benchmarks.suites.strategy_evolution import get_problem, run_evolution
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine, survival_transitions
from engines.lab.evolution.guardrails import GuardrailViolation
from engines.lab.evolution.types import Candidate, SchemaError
from engines.lab.states import InvalidTransitionError

SCHEMA = {
    "parameters": {
        "temperature": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.01},
        "breadth": {"type": "int", "min": 1, "max": 20},
        "critic": {"type": "bool"},
        "planner": {"type": "choice", "choices": ["linear", "tree", "graph"]},
        "budget_steps": {"type": "int", "min": 5, "max": 50, "mutable": False},
    },
    "definition_keys": ["tools"],
}
BASE = {"temperature": 0.5, "breadth": 5, "critic": True, "planner": "tree", "budget_steps": 20}
OBJECTIVES = ["scientific_performance", "cost", "safety"]


def evaluate(candidate: Candidate) -> Candidate:
    """A deterministic synthetic evaluator with a performance/cost trade-off and a safety constraint."""
    p = candidate.params
    performance = 0.4 * p["temperature"] + 0.03 * p["breadth"] + (0.1 if p["critic"] else 0.0)
    cost = 0.4 * p["breadth"] + (1.5 if p["planner"] == "graph" else 0.5)
    safety = 1.0 if p["temperature"] <= 0.9 else 0.8
    return candidate.model_copy(
        update={"fitness": {"scientific_performance": min(1.0, performance), "cost": cost, "safety": safety}}
    )


def engine(seed: int = 7, **overrides: object) -> EvolutionEngine:
    config = EvolutionConfig.model_validate(
        {"population_size": 8, "objectives": OBJECTIVES, "seed": seed, "archive_size": 20, **overrides}
    )
    return EvolutionEngine(config, SCHEMA)


def first_population(eng: EvolutionEngine) -> list[Candidate]:
    return [evaluate(c) for c in eng.initialize(BASE, definition={"tools": ["search"]})]


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------
def test_config_resolves_objectives_and_validates() -> None:
    config = EvolutionConfig.model_validate({"objectives": ["cost", {"name": "custom", "direction": "minimize"}]})
    assert [o.name for o in config.objectives] == ["cost", "custom"]
    assert config.effective_offspring_size == config.population_size
    with pytest.raises(ValueError):
        EvolutionConfig.model_validate({"objectives": ["cost", "cost"]})
    with pytest.raises(ValueError):
        EvolutionConfig.model_validate({"objectives": ["cost"], "epsilon": {"other": 0.1}})
    with pytest.raises(ValueError):
        EvolutionConfig.model_validate(
            {"objectives": ["cost"], "constraints": [{"objective": "latency", "op": "<=", "threshold": 1}]}
        )
    with pytest.raises(ValueError):
        EvolutionConfig.model_validate({"population_size": 10, "mutation_rat": 0.1})  # typo is not ignored


def test_run_level_constraints_override_objective_constraints() -> None:
    config = EvolutionConfig.model_validate(
        {
            "objectives": [{"name": "cost", "direction": "minimize", "weight": 1}],
            "constraints": [{"objective": "cost", "op": "<=", "threshold": 2.5}],
            "generations": 10,
            "benchmark_suite": "strategy_evolution_bench",
        }
    )
    [cost] = config.resolved_objectives()
    assert cost.constraint is not None and cost.constraint.threshold == 2.5
    eng = EvolutionEngine(config, SCHEMA)
    assert not eng.fitness.evaluate({"cost": 3.0}).feasible


# ---------------------------------------------------------------------------------------------
# Generation 0
# ---------------------------------------------------------------------------------------------
def test_initialize_returns_distinct_valid_mutations_of_the_base() -> None:
    eng = engine()
    drafts = eng.initialize(BASE, n=8, definition={"tools": ["search"]}, base_id="v1")
    assert len(drafts) == 8
    assert len({d.param_hash for d in drafts}) == 8
    for draft in drafts:
        assert draft.params != BASE and draft.params["budget_steps"] == 20  # immutable preserved
        assert draft.parents == ("v1",) and draft.generation == 0 and draft.mutations
        assert eng.guardrails.validate(draft.definition, draft.params, SCHEMA).ok
    assert [d.params for d in drafts] == [
        d.params for d in engine().initialize(BASE, n=8, definition={"tools": ["search"]}, base_id="v1")
    ]


def test_initialize_refuses_an_unsafe_base() -> None:
    with pytest.raises(GuardrailViolation):
        engine().initialize({**BASE, "temperature": 3.0})
    with pytest.raises(GuardrailViolation):
        engine().initialize(BASE, definition={"tools": ["search"], "policy": {"allow": "*"}})
    with pytest.raises(SchemaError):
        EvolutionEngine({"objectives": OBJECTIVES}).initialize(BASE)


# ---------------------------------------------------------------------------------------------
# One generation
# ---------------------------------------------------------------------------------------------
def test_step_is_deterministic_for_the_same_seed() -> None:
    population = first_population(engine())
    plan_a = engine().step(population, 1)
    plan_b = engine().step(population, 1)
    plan_c = engine(seed=8).step(population, 1)
    assert plan_a.to_json_dict() == plan_b.to_json_dict()
    assert [c.params for c in plan_a.children] != [c.params for c in plan_c.children]


def test_step_produces_survivors_children_front_and_archive() -> None:
    eng = engine()
    population = first_population(eng)
    plan = eng.step(population, 1)
    children = [evaluate(c) for c in plan.children]
    combined = [c for c in population if c.id in plan.survivors] + children
    plan2 = eng.step(combined, 2, archive=plan.archive)
    assert len(plan2.survivors) == 8 and len(plan2.eliminated) == len(combined) - 8
    assert set(plan2.survivors) | set(plan2.eliminated) == {c.id for c in combined}
    assert len(plan2.children) == 8
    existing = {c.param_hash for c in combined}
    for child in plan2.children:
        assert child.generation == 2 and child.param_hash not in existing
        assert set(child.parents) <= set(plan2.survivors)
        assert child.params["budget_steps"] == 20
    assert set(plan2.front) <= {c.id for c in combined}
    assert all(plan2.assessments[i].rank == 0 for i in plan2.front)
    assert plan2.archive["entries"] and plan2.hypervolume is not None and plan2.hypervolume > 0
    assert plan2.diversity.n == 8
    json.dumps(plan2.to_json_dict(), allow_nan=False)  # boundary crowding (inf) is serialised as null


def test_infeasible_candidates_never_displace_feasible_ones() -> None:
    eng = engine()
    population = first_population(eng)
    unsafe = [
        c.model_copy(
            update={"id": f"unsafe{i}", "fitness": {"scientific_performance": 1.0, "cost": 0.0, "safety": 0.5}}
        )
        for i, c in enumerate(population[:4])
    ]
    plan = eng.step(population + unsafe, 1)
    assert set(plan.survivors) == {c.id for c in population}
    assert {f"unsafe{i}" for i in range(4)} == set(plan.eliminated)
    assert all(not plan.assessments[f"unsafe{i}"].feasible for i in range(4))
    assert all(e["id"] not in {f"unsafe{i}" for i in range(4)} for e in plan.archive["entries"])


def test_unevaluated_candidates_are_treated_as_infeasible() -> None:
    eng = engine()
    population = first_population(eng)
    pending = population[0].model_copy(update={"id": "pending", "fitness": None})
    plan = eng.step([*population, pending], 1)
    assert "pending" in plan.eliminated
    assert any("unevaluated" in note for note in plan.notes)


def test_novelty_objective_is_computed_when_not_measured() -> None:
    config = EvolutionConfig.model_validate(
        {"population_size": 6, "objectives": ["scientific_performance", "novelty"], "seed": 1, "novelty_weight": 0.5}
    )
    eng = EvolutionEngine(config, SCHEMA)
    population = [c.model_copy(update={"fitness": {"scientific_performance": 0.5}}) for c in eng.initialize(BASE, n=8)]
    plan = eng.step(population, 1)
    novelties = [a.novelty for a in plan.assessments.values()]
    assert all(n is not None and 0 < n <= 1 for n in novelties)
    assert all(a.objectives["novelty"] == a.novelty for a in plan.assessments.values())


def test_step_validates_inputs() -> None:
    eng = engine()
    population = first_population(eng)
    with pytest.raises(ValueError):
        eng.step(population, 0)
    with pytest.raises(ValueError):
        eng.step([], 1)
    with pytest.raises(ValueError):
        eng.step([population[0], population[0]], 1)
    other = EvolutionEngine({"population_size": 8, "objectives": ["cost"]}, SCHEMA)
    with pytest.raises(ValueError):
        other.step(population, 2, archive=eng.step(population, 1).archive)  # objectives mismatch


def test_survival_transitions_follow_the_strategy_state_machine() -> None:
    eng = engine()
    population = first_population(eng)
    plan = eng.step(population[:6], 1)  # everyone survives (population_size 8)
    statuses = {c.id: "EXPERIMENTAL" for c in population[:6]}
    statuses[population[0].id] = "PROMOTED"
    statuses[population[1].id] = "SURVIVING"
    changes = survival_transitions(plan, statuses)
    assert (population[0].id, "PROMOTED", "SURVIVING") not in changes
    assert all(to == "SURVIVING" and frm == "EXPERIMENTAL" for _, frm, to in changes)
    assert len(changes) == 4
    crowded = eng.step([*population, *[c.model_copy(update={"id": f"x{i}"}) for i, c in enumerate(population[:4])]], 1)
    retire = survival_transitions(crowded, dict.fromkeys(crowded.eliminated, "EXPERIMENTAL"))
    assert retire and all(to == "RETIRED" for _, _, to in retire)
    with pytest.raises(InvalidTransitionError):
        survival_transitions(crowded, {crowded.eliminated[0]: "BOGUS"})


# ---------------------------------------------------------------------------------------------
# Evidence the engine optimises
# ---------------------------------------------------------------------------------------------
def test_hypervolume_increases_over_20_generations_on_zdt1() -> None:
    suite = get_suite("strategy_evolution_bench")
    case = next(c for c in suite.cases if c.input["problem"] == "zdt1" and c.input["generations"] == 20)
    output = suite.execute_case(case, lambda _: {}, seed=0)
    scored = suite.scorer.score_case(case, output)
    history = scored.detail["hv_history"]  # normalised HV: initial population, then after each generation
    assert len(history) == 21
    assert history[0] < 0.1  # random initial population is far from the front
    assert history[-1] >= 0.6  # ... and 20 generations get most of the way there
    assert history[-1] > history[10] > history[0]
    assert sum(history[-5:]) / 5 > sum(history[:5]) / 5 + 0.4
    drawdowns = [history[i] - history[i + 1] for i in range(20)]
    assert max(drawdowns) < 0.05  # elitist archive: no meaningful regression between generations


def test_run_evolution_is_reproducible_and_beats_a_crippled_configuration() -> None:
    a = run_evolution("zdt1", dims=6, generations=10, population=20, seed=3)
    b = run_evolution("zdt1", dims=6, generations=10, population=20, seed=3)
    assert a == b
    crippled = run_evolution(
        "zdt1",
        dims=6,
        generations=10,
        population=20,
        seed=3,
        overrides={"crossover_rate": 0.0, "mutation_scale": 0.005, "mutation_rate": 0.05},
    )
    true_hv = get_problem("zdt1").true_hypervolume([1.1, 1.1])
    assert a.hv_history[-1] / true_hv > crippled.hv_history[-1] / true_hv + 0.3
    assert a.evaluations == 20 + 10 * 20
