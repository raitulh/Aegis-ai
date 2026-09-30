"""Parameter specs/schemas, canonical hashing and multi-objective fitness evaluation."""

from __future__ import annotations

import math

import pytest

from engines.lab.evolution.fitness import CANONICAL_OBJECTIVES, FitnessEngine, default_objectives, resolve_objectives
from engines.lab.evolution.types import (
    ConstraintSpec,
    Direction,
    ObjectiveSpec,
    ParameterSchema,
    ParameterSpec,
    ParamType,
    SchemaError,
    canonical_hash,
)


# ---------------------------------------------------------------------------------------------
# ParameterSpec / ParameterSchema
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec",
    [
        {"name": "t", "type": "float", "min": 1.0, "max": 0.0},
        {"name": "c", "type": "choice"},
        {"name": "c", "type": "choice", "choices": ["a", "a"]},
        {"name": "b", "type": "bool", "min": 0},
        {"name": "t", "type": "float", "prompt_variant": True},
        {"name": "1bad", "type": "bool"},
        {"name": "i", "type": "int", "min": 0.5, "max": 3},
        {"name": "s", "type": "float", "step": 0},
        {"name": "l", "type": "ordered_list", "choices": ["a", "b"], "min": 3},
        {"name": "t", "type": "float", "min": 0, "max": 1, "default": 2.0},
        {"name": "t", "type": "float", "min": 0, "max": 1, "unexpected": True},
    ],
)
def test_parameter_spec_rejects_inconsistent_definitions(spec: dict) -> None:
    with pytest.raises(ValueError):
        ParameterSpec.model_validate(spec)


def test_schema_from_dict_fills_names_and_round_trips() -> None:
    raw = {
        "parameters": {
            "temperature": {"type": "float", "min": 0, "max": 1.5, "step": 0.05},
            "depth": {"type": "int", "min": 1, "max": 9},
        },
        "definition_keys": ["steps"],
    }
    schema = ParameterSchema.from_dict(raw)
    assert schema.parameters["temperature"].name == "temperature"
    assert schema.mutable_names == ("depth", "temperature")
    assert ParameterSchema.from_dict(schema.to_dict()) == schema
    assert schema.to_dict()["parameters"]["depth"]["min"] == 1  # ints stay ints in the stored form


@pytest.mark.parametrize(
    "raw",
    [
        {"parameters": {"a": {"name": "b", "type": "bool"}}},
        {"parameters": {}, "surprise": 1},
        {"parameters": {"a": {"type": "unknown"}}},
        "not-an-object",
    ],
)
def test_schema_from_dict_raises_schema_error(raw: object) -> None:
    with pytest.raises(SchemaError):
        ParameterSchema.from_dict(raw)  # type: ignore[arg-type]


def test_check_value_reports_type_bound_step_choice_and_list_problems() -> None:
    f = ParameterSpec(name="f", type=ParamType.FLOAT, min=0, max=1, step=0.25)
    assert f.check_value(0.5) == []
    assert [c for c, _ in f.check_value(0.3)] == ["STEP_MISALIGNED"]
    assert [c for c, _ in f.check_value(1.5)] == ["OUT_OF_BOUNDS"]
    assert [c for c, _ in f.check_value(float("nan"))] == ["NOT_FINITE"]
    assert [c for c, _ in f.check_value("0.5")] == ["TYPE_MISMATCH"]
    i = ParameterSpec(name="i", type=ParamType.INT, min=0, max=4)
    assert [c for c, _ in i.check_value(True)] == ["TYPE_MISMATCH"]  # bools are not ints here
    c = ParameterSpec(name="c", type=ParamType.CHOICE, choices=(1, 2))
    assert [code for code, _ in c.check_value(True)] == ["INVALID_CHOICE"]  # True is not the choice 1
    lst = ParameterSpec(name="l", type=ParamType.ORDERED_LIST, choices=("a", "b", "c"), min=1, max=2)
    codes = {code for code, _ in lst.check_value(["a", "a", "z"])}
    assert codes == {"LENGTH_OUT_OF_BOUNDS", "DUPLICATE_ITEM", "INVALID_ITEM"}


def test_coerce_clips_and_snaps_numeric_values() -> None:
    f = ParameterSpec(name="f", type=ParamType.FLOAT, min=0, max=1, step=0.05)
    assert f.coerce(0.333) == pytest.approx(0.35)
    assert f.coerce(-3) == 0.0
    assert f.coerce(7.2) == 1.0
    odd = ParameterSpec(name="o", type=ParamType.INT, min=1, max=10, step=2)  # grid 1,3,5,7,9
    assert odd.coerce(100) == 9
    assert odd.check_value(odd.coerce(4.4)) == []


def test_canonical_hash_ignores_float_noise_and_key_order() -> None:
    assert canonical_hash({"a": 0.1 + 0.2, "b": 1}) == canonical_hash({"b": 1, "a": 0.3})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 1.5})
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})


# ---------------------------------------------------------------------------------------------
# Objectives and fitness
# ---------------------------------------------------------------------------------------------
def test_canonical_objectives_cover_the_eight_named_objectives() -> None:
    assert [o.name for o in default_objectives()] == [
        "scientific_performance",
        "cost",
        "latency",
        "compute_efficiency",
        "robustness",
        "reproducibility",
        "novelty",
        "safety",
    ]
    safety = CANONICAL_OBJECTIVES["safety"]
    assert safety.constraint is not None and safety.constraint.op == ">=" and safety.constraint.threshold == 1.0
    assert CANONICAL_OBJECTIVES["cost"].direction is Direction.MINIMIZE


def test_fitness_orients_every_objective_for_maximisation() -> None:
    engine = FitnessEngine(["scientific_performance", "cost"])
    cheap = engine.evaluate({"scientific_performance": 0.8, "cost": 1.0})
    pricey = engine.evaluate({"scientific_performance": 0.8, "cost": 9.0})
    assert cheap.oriented[1] > pricey.oriented[1]  # lower cost → larger oriented value
    assert cheap.oriented == pytest.approx((0.8, 0.9))
    assert cheap.feasible and cheap.constraint_violation == 0


def test_hard_constraint_makes_candidate_infeasible() -> None:
    engine = FitnessEngine(["scientific_performance", "safety"])
    unsafe = engine.evaluate({"scientific_performance": 0.99, "safety": 0.9})
    assert not unsafe.feasible
    assert unsafe.constraint_violation == pytest.approx(0.1)
    assert unsafe.violations == ("constraint:safety>=1",)


def test_missing_or_invalid_objective_is_infeasible() -> None:
    engine = FitnessEngine(["scientific_performance", "robustness"])
    missing = engine.evaluate({"scientific_performance": 0.9})
    invalid = engine.evaluate({"scientific_performance": 0.9, "robustness": math.nan})
    none = engine.evaluate(None)
    assert not missing.feasible and "missing:robustness" in missing.violations
    assert not invalid.feasible and "invalid:robustness" in invalid.violations
    assert none.constraint_violation == pytest.approx(2.0)


def test_summary_is_weighted_display_score_in_unit_interval() -> None:
    engine = FitnessEngine(
        [
            ObjectiveSpec(name="a", weight=3.0, bounds=(0.0, 1.0)),
            ObjectiveSpec(name="b", weight=1.0, bounds=(0.0, 1.0)),
            ObjectiveSpec(name="c", weight=1.0, direction=Direction.MINIMIZE),  # unbounded → squashed
        ]
    )
    fv = engine.evaluate({"a": 1.0, "b": 0.0, "c": 0.0})
    assert fv.summary == pytest.approx((3 * 1.0 + 1 * 0.0 + 1 * 0.5) / 5)
    assert 0 <= engine.evaluate({"a": 9.0, "b": -3.0, "c": 50.0}).summary <= 1


def test_resolve_objectives_merges_canonical_defaults_and_rejects_bad_input() -> None:
    [cost] = resolve_objectives([{"name": "cost", "weight": 5.0}])
    assert cost.weight == 5.0 and cost.bounds == (0.0, 10.0) and cost.direction is Direction.MINIMIZE
    with pytest.raises(SchemaError):
        resolve_objectives(["not_an_objective"])
    with pytest.raises(SchemaError):
        resolve_objectives(["cost", "cost"])
    with pytest.raises(SchemaError):
        FitnessEngine([])


@pytest.mark.parametrize(
    ("op", "value", "violated"),
    [
        (">=", 1.0, False),
        (">=", 0.99, True),
        ("<=", 5.0, False),
        ("<=", 5.1, True),
        (">", 1.0, True),
        ("<", 0.5, False),
        ("==", 2.0, False),
        ("==", 2.5, True),
    ],
)
def test_constraint_operators(op: str, value: float, violated: bool) -> None:
    threshold = {">=": 1.0, "<=": 5.0, ">": 1.0, "<": 1.0, "==": 2.0}[op]
    spec = ConstraintSpec(op=op, threshold=threshold)  # type: ignore[arg-type]
    assert (spec.violation(value) > 0) is violated
