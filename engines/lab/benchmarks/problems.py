"""Analytic multi-objective problems for StrategyEvolutionBench (ZDT1, ZDT2) — deterministic, no I/O."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from engines.lab.enums import StrategyKind
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine
from engines.lab.evolution.fitness import Objective
from engines.lab.evolution.genome import ParameterDef, StrategyDefinition
from engines.lab.evolution.pareto import hypervolume
from engines.lab.evolution.population import Individual


def zdt(problem: str, x: list[float]) -> tuple[float, float]:
    f1 = x[0]
    g = 1 + 9 * sum(x[1:]) / (len(x) - 1)
    if problem == "zdt1":
        f2 = g * (1 - math.sqrt(f1 / g))
    elif problem == "zdt2":
        f2 = g * (1 - (f1 / g) ** 2)
    else:
        raise ValueError(f"unknown problem {problem}")
    return f1, f2


def run_evolution_problem(spec: dict[str, Any]) -> dict[str, Any]:
    problem = spec.get("problem", "zdt1")
    dim = int(spec.get("dim", 5))
    generations = int(spec.get("generations", 30))
    config = EvolutionConfig.model_validate(
        {
            **spec.get("config", {}),
            "objectives": [
                Objective(name="f1", metric="f1", direction="minimize"),
                Objective(name="f2", metric="f2", direction="minimize"),
            ],
        }
    )
    engine = EvolutionEngine(config)
    space = [ParameterDef(name=f"x{i}", kind="float", low=0.0, high=1.0) for i in range(dim)]
    rng = np.random.default_rng(int(spec.get("init_seed", 1)))

    def make(idx: str, params: dict[str, float], generation: int = 0) -> Individual:
        d = StrategyDefinition(kind=StrategyKind.OPTIMIZATION, parameter_space=space, parameters=params)
        f1, f2 = zdt(problem, [params[f"x{i}"] for i in range(dim)])
        return Individual(id=idx, definition=d, generation=generation, metrics={"f1": f1, "f2": f2})

    population = [
        make(f"g0-{k}", {f"x{i}": float(rng.random()) for i in range(dim)}) for k in range(config.population_size)
    ]
    evaluations = len(population)
    for g in range(1, generations + 1):
        outcome = engine.step(population, g)
        keep = set(outcome.survivors)
        survivors = [i for i in population if i.id in keep]
        children = [make(f"g{g}-{j}", c.definition.parameters, g) for j, c in enumerate(outcome.candidates)]
        evaluations += len(children)
        population = survivors + children
    reference = tuple(spec.get("reference_point", [-1.1, -11.0]))
    hv = hypervolume([(-i.metrics["f1"], -i.metrics["f2"]) for i in population], reference)
    return {"hypervolume": round(hv, 6), "evaluations": evaluations, "archive_size": len(engine.archive.members)}
