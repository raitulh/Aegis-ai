"""Child generation (guardrails, de-duplication, retry budget, lineage) and population bookkeeping."""

from __future__ import annotations

import pytest

from engines.lab.evolution.candidates import CandidateGenerator
from engines.lab.evolution.population import DuplicateCandidate, PopulationManager, param_hash
from engines.lab.evolution.types import Candidate

SCHEMA = {
    "parameters": {
        "alpha": {"type": "float", "min": 0.0, "max": 1.0},
        "beta": {"type": "float", "min": 0.0, "max": 1.0},
        "k": {"type": "int", "min": 1, "max": 9},
        "mode": {"type": "choice", "choices": ["x", "y", "z"]},
    },
    "definition_keys": ["tools"],
}
DEFINITION = {"tools": ["search"]}


def pool(n: int = 6) -> list[Candidate]:
    return [
        Candidate(
            id=f"p{i}",
            params={"alpha": i / 10, "beta": 1 - i / 10, "k": 1 + i, "mode": "xyz"[i % 3]},
            definition=DEFINITION,
        )
        for i in range(n)
    ]


def test_generates_unique_valid_children_with_lineage() -> None:
    parents = pool()
    generator = CandidateGenerator(SCHEMA, seed=3, crossover_rate=0.9)
    batch = generator.generate(parents, 12, generation=4, existing_hashes={param_hash(p.params) for p in parents})
    assert len(batch.children) == 12 and not batch.exhausted
    hashes = {c.param_hash for c in batch.children}
    assert len(hashes) == 12 and not hashes & {p.param_hash for p in parents}
    ids = {p.id for p in parents}
    for child in batch.children:
        assert child.generation == 4 and child.fitness is None
        assert child.definition == DEFINITION
        assert set(child.parents) <= ids and child.mutations
        assert all(set(m.parents) <= ids for m in child.mutations)
    assert any(len(c.parents) == 2 for c in batch.children)  # some children come from crossover
    assert len({c.id for c in batch.children}) == 12


def test_generation_is_deterministic() -> None:
    a = CandidateGenerator(SCHEMA, seed=5).generate(pool(), 8, generation=2)
    b = CandidateGenerator(SCHEMA, seed=5).generate(pool(), 8, generation=2)
    c = CandidateGenerator(SCHEMA, seed=6).generate(pool(), 8, generation=2)
    assert a.model_dump() == b.model_dump()
    assert [x.params for x in a.children] != [x.params for x in c.children]


def test_duplicates_are_rejected_and_budget_is_bounded() -> None:
    tiny = {"parameters": {"a": {"type": "bool"}, "b": {"type": "bool"}}}
    parents = [Candidate(id="p", params={"a": True, "b": True})]
    everything = {param_hash({"a": a, "b": b}) for a in (True, False) for b in (True, False)}
    generator = CandidateGenerator(tiny, seed=1, max_attempts_factor=5)
    batch = generator.generate(
        parents, 3, generation=1, existing_hashes=everything - {param_hash({"a": False, "b": False})}
    )
    assert len(batch.children) == 1 and batch.children[0].params == {"a": False, "b": False}
    assert batch.exhausted and batch.attempts == 15
    assert {r.reason for r in batch.rejected} == {"duplicate"}


def test_children_of_an_unsafe_parent_definition_are_rejected() -> None:
    unsafe = [Candidate(id="p0", params=pool(1)[0].params, definition={"tools": ["search"], "secrets": {"k": "v"}})]
    batch = CandidateGenerator(SCHEMA, seed=2, max_attempts_factor=2).generate(unsafe, 3, generation=1)
    assert batch.children == [] and batch.exhausted
    assert {r.reason for r in batch.rejected} == {"guardrail"}
    assert "FORBIDDEN_KEY" in batch.rejected[0].detail["codes"]


def test_generator_argument_validation() -> None:
    with pytest.raises(ValueError):
        CandidateGenerator(SCHEMA, mutation_rate=0.0)
    with pytest.raises(ValueError):
        CandidateGenerator(SCHEMA, crossover_rate=1.5)
    with pytest.raises(ValueError):
        CandidateGenerator(SCHEMA).generate([], 2, generation=1)
    assert CandidateGenerator(SCHEMA).effective_mutation_rate == pytest.approx(1 / 4)


def test_population_manager_bookkeeping() -> None:
    a = Candidate(id="a", params={"x": 1}, fitness={"f": 1.0})
    b = Candidate(id="b", params={"x": 1}, parents=("a",))
    c = Candidate(id="c", params={"x": 2}, parents=("b",), generation=2)
    population = PopulationManager([a, b])
    assert population.add(c) is True
    assert population.duplicates() == [("a", "b")]
    with pytest.raises(DuplicateCandidate):
        population.add(a)
    assert population.contains_params({"x": 2}) and not population.contains_params({"x": 3})
    assert [x.id for x in population.evaluated()] == ["a"]
    assert [x.id for x in population.unevaluated()] == ["b", "c"]
    assert population.lineage("c") == ["b", "a"]
    assert [x.id for x in population.by_generation(2)] == ["c"]
    population.remove("b")
    assert population.duplicates() == [] and len(population) == 2 and "b" not in population
