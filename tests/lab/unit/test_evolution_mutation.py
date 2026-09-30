"""Mutation/crossover operators: bounds, types, immutability, determinism, lineage records."""

from __future__ import annotations

from typing import Any

import pytest

from engines.lab.evolution.mutation import (
    CROSSOVER_OPERATORS,
    MUTATION_OPERATORS,
    MutationEngine,
    MutationError,
    applicable_operators,
)
from engines.lab.evolution.types import ParameterSchema

SCHEMA = ParameterSchema.from_dict(
    {
        "parameters": {
            "temperature": {"type": "float", "min": 0.0, "max": 1.5, "step": 0.05, "mutation_scale": 0.2},
            "top_k": {"type": "int", "min": 1, "max": 50, "step": 1},
            "self_check": {"type": "bool"},
            "search_mode": {"type": "choice", "choices": ["breadth", "depth", "hybrid"]},
            "prompt": {
                "type": "choice",
                "prompt_variant": True,
                "choices": [
                    "persona:skeptic/format:bullets",
                    "persona:skeptic/format:prose",
                    "persona:optimist/format:bullets",
                    "persona:optimist/format:prose",
                ],
            },
            "tool_sequence": {
                "type": "ordered_list",
                "choices": ["search", "read", "extract", "summarize"],
                "min": 1,
                "max": 3,
            },
            "max_iterations": {"type": "int", "min": 1, "max": 20, "mutable": False},
        }
    }
)
BASE: dict[str, Any] = {
    "temperature": 0.7,
    "top_k": 10,
    "self_check": True,
    "search_mode": "hybrid",
    "prompt": "persona:skeptic/format:bullets",
    "tool_sequence": ["search", "read"],
    "max_iterations": 8,
}


def test_operator_catalogue() -> None:
    assert set(MUTATION_OPERATORS) == {
        "gaussian_perturb",
        "integer_step",
        "bool_flip",
        "choice_resample",
        "prompt_segment_swap",
        "ordered_list_swap",
        "ordered_list_insert",
        "ordered_list_remove",
    }
    assert set(CROSSOVER_OPERATORS) == {"uniform_crossover", "blend_crossover"}
    assert applicable_operators(SCHEMA.parameters["max_iterations"], 8) == []  # immutable
    assert applicable_operators(SCHEMA.parameters["prompt"], BASE["prompt"]) == ["prompt_segment_swap"]


@pytest.mark.parametrize("path", ["temperature", "top_k", "self_check", "search_mode", "prompt", "tool_sequence"])
def test_every_mutation_stays_valid_and_changes_only_its_path(path: str) -> None:
    engine = MutationEngine(seed=11)
    for index in range(60):
        child, record = engine.mutate(BASE, SCHEMA, generation=1, index=index, path=path)
        assert SCHEMA.parameters[path].check_value(child[path]) == [], (path, child[path])
        assert child[path] != BASE[path]
        assert record.changed_paths == (path,)
        assert record.before == {path: BASE[path]} and record.after == {path: child[path]}
        assert {k: v for k, v in child.items() if k != path} == {k: v for k, v in BASE.items() if k != path}


def test_gaussian_sigma_scales_with_range() -> None:
    _, record = MutationEngine(3).mutate(BASE, SCHEMA, path="temperature")
    assert record.operator == "gaussian_perturb"
    assert record.detail["sigma"] == pytest.approx(0.2 * 1.5)


def test_prompt_segment_swap_changes_exactly_one_segment() -> None:
    engine = MutationEngine(4)
    for index in range(30):
        child, record = engine.mutate(BASE, SCHEMA, index=index, path="prompt")
        before = BASE["prompt"].split("/")
        after = child["prompt"].split("/")
        assert sum(a != b for a, b in zip(before, after, strict=True)) == 1
        assert record.operator == "prompt_segment_swap"


def test_prompt_segment_swap_falls_back_for_flat_variant_names() -> None:
    schema = {"parameters": {"p": {"type": "choice", "prompt_variant": True, "choices": ["v1", "v2", "v3"]}}}
    child, record = MutationEngine(1).mutate({"p": "v1"}, schema, path="p")
    assert child["p"] in {"v2", "v3"} and record.detail == {"fallback": "choice_resample"}


def test_ordered_list_operators_respect_length_bounds_and_uniqueness() -> None:
    engine = MutationEngine(9)
    seen_ops = set()
    for index in range(200):
        child, record = engine.mutate(BASE, SCHEMA, index=index, path="tool_sequence")
        seq = child["tool_sequence"]
        assert 1 <= len(seq) <= 3 and len(set(seq)) == len(seq)
        seen_ops.add(record.operator)
        if record.operator == "ordered_list_swap":
            assert sorted(seq) == sorted(BASE["tool_sequence"])
    assert seen_ops == {"ordered_list_swap", "ordered_list_insert", "ordered_list_remove"}
    full = {**BASE, "tool_sequence": ["search", "read", "extract"]}
    for index in range(40):
        child, record = engine.mutate(full, SCHEMA, index=index, path="tool_sequence")
        assert record.operator != "ordered_list_insert" and len(child["tool_sequence"]) <= 3


def test_mutation_is_deterministic_and_seed_is_recorded() -> None:
    a = MutationEngine(seed=7).mutate(BASE, SCHEMA, generation=2, index=5, step=1)
    b = MutationEngine(seed=7).mutate(BASE, SCHEMA, generation=2, index=5, step=1)
    c = MutationEngine(seed=7).mutate(BASE, SCHEMA, generation=2, index=6, step=1)
    assert a == b
    assert a[1].seed != c[1].seed
    assert 0 <= a[1].seed <= 2**31 - 1  # fits strategy_mutations.seed (INTEGER)


def test_immutable_parameters_never_change() -> None:
    engine = MutationEngine(2)
    for index in range(100):
        child, _ = engine.mutate(BASE, SCHEMA, index=index)
        assert child["max_iterations"] == 8
    with pytest.raises(MutationError):
        engine.mutate(BASE, SCHEMA, path="max_iterations")
    with pytest.raises(MutationError):
        engine.mutate(BASE, SCHEMA, path="unknown")
    frozen = {"parameters": {"x": {"type": "int", "min": 1, "max": 5, "mutable": False}}}
    with pytest.raises(MutationError):
        engine.mutate({"x": 3}, frozen)


def test_uniform_crossover_takes_each_value_from_a_parent() -> None:
    other: dict[str, Any] = {
        "temperature": 0.2,
        "top_k": 40,
        "self_check": False,
        "search_mode": "depth",
        "prompt": "persona:optimist/format:prose",
        "tool_sequence": ["extract"],
        "max_iterations": 3,
    }
    engine = MutationEngine(5)
    for index in range(30):
        child, record = engine.crossover(BASE, other, SCHEMA, index=index, parents=("p1", "p2"))
        for name, value in child.items():
            if name == "max_iterations":
                assert value == 8  # immutable: always from the first parent
            else:
                assert value in (BASE[name], other[name])
        assert record.operator == "uniform_crossover" and record.parents == ("p1", "p2")
        assert set(record.changed_paths) == {n for n in child if child[n] != BASE[n]}


def test_blend_crossover_keeps_floats_within_expanded_interval_and_bounds() -> None:
    other = {**BASE, "temperature": 1.1}
    engine = MutationEngine(6)
    for index in range(50):
        child, _ = engine.crossover(BASE, other, SCHEMA, operator="blend_crossover", index=index)
        t = child["temperature"]
        assert 0.7 - 0.5 * 0.4 - 1e-9 <= t <= 1.1 + 0.5 * 0.4 + 1e-9
        assert SCHEMA.parameters["temperature"].check_value(t) == []
    with pytest.raises(ValueError):
        engine.crossover(BASE, other, SCHEMA, operator="one_point")


def test_sample_produces_valid_deterministic_vectors() -> None:
    engine = MutationEngine(1)
    a = engine.sample(SCHEMA, index=3)
    assert a == MutationEngine(1).sample(SCHEMA, index=3)
    for name, spec in SCHEMA.parameters.items():
        assert spec.check_value(a[name]) == []
    with pytest.raises(MutationError):
        engine.sample({"parameters": {"x": {"type": "float"}}})
