"""StrategyEvolutionBench — measures the Evolution Engine itself (component ``evolution``).

Each case names a synthetic multi-objective test problem with a known Pareto front — ZDT1 (convex),
ZDT2 (concave) or DTLZ2 (3-objective spherical) — plus an evaluation budget (population size,
generations). The subject is an *evolution-config callable*: given the case input it returns
overrides of the engine configuration (``mutation_rate``, ``crossover_rate``, ``blend_probability``,
``epsilon``, ``archive_size``, ``novelty_weight``, ``novelty_k``, ``mutation_scale``,
``offspring_size``); the budget itself cannot be overridden. The suite then runs
:class:`~engines.lab.evolution.engine.EvolutionEngine` exactly as the strategies service would
(sample → evaluate → step → evaluate children …) and scores the final ε-archive:

* ``normalized_hypervolume`` = HV(archive) / HV(true Pareto front), both against the case's reference
  point; the true-front hypervolume is analytic (ZDT1: ``r₂−1 + 2/3 + (r₁−1)·r₂``; ZDT2:
  ``r₂−1 + 1/3 + (r₁−1)·r₂``; DTLZ2: ``r₁r₂r₃ − π/6``);
* ``igd`` — inverted generational distance to a dense sample of the true front (lower is better);
* ``hv_history`` — normalised hypervolume after the initial population and after every generation.

Alternatively a subject may return ``{"solutions": [[x₁…x_n], …]}`` — decision vectors from any
optimiser. The suite evaluates them itself (so objective values cannot be misreported) and scores
the resulting front the same way.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from engines.lab.benchmarks.base import BenchmarkCase, BenchmarkSuite, CaseResult, CaseScore, Scorer, load_fixture
from engines.lab.benchmarks.scoring import mean
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine
from engines.lab.evolution.hypervolume import hypervolume
from engines.lab.evolution.types import (
    Candidate,
    Direction,
    ObjectiveSpec,
    ParameterSchema,
    ParameterSpec,
    ParamType,
)

KEY = "strategy_evolution_bench"
ALLOWED_OVERRIDES: frozenset[str] = frozenset(
    {
        "mutation_rate",
        "crossover_rate",
        "blend_probability",
        "epsilon",
        "archive_size",
        "novelty_weight",
        "novelty_k",
        "mutation_scale",
        "offspring_size",
    }
)
DEFAULT_MUTATION_SCALE = 0.1
DEFAULT_EPSILON = 0.005
DEFAULT_ARCHIVE_SIZE = 100
MAX_SOLUTIONS = 5000
MAX_DIMS = 64
MAX_BUDGET = 200_000  # population × generations


# ---------------------------------------------------------------------------------------------
# Test problems (all objectives minimised, decision variables in [0, 1])
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Problem:
    name: str
    n_objectives: int
    min_dims: int
    evaluate: Callable[[Sequence[float]], tuple[float, ...]]
    true_hypervolume: Callable[[Sequence[float]], float]
    front_sample: Callable[[int], list[tuple[float, ...]]]


def _zdt_g(x: Sequence[float]) -> float:
    return 1.0 + 9.0 * sum(x[1:]) / (len(x) - 1)


def _zdt1(x: Sequence[float]) -> tuple[float, ...]:
    f1 = float(x[0])
    g = _zdt_g(x)
    return f1, g * (1.0 - math.sqrt(f1 / g))


def _zdt2(x: Sequence[float]) -> tuple[float, ...]:
    f1 = float(x[0])
    g = _zdt_g(x)
    return f1, g * (1.0 - (f1 / g) ** 2)


def _dtlz2(x: Sequence[float]) -> tuple[float, ...]:
    g = sum((xi - 0.5) ** 2 for xi in x[2:])
    a, b = x[0] * math.pi / 2, x[1] * math.pi / 2
    return (1 + g) * math.cos(a) * math.cos(b), (1 + g) * math.cos(a) * math.sin(b), (1 + g) * math.sin(a)


def _check_ref(ref: Sequence[float], n: int) -> None:
    if len(ref) != n or any(r < 1.0 for r in ref):
        raise ValueError(f"reference point must have {n} components, each >= 1.0")


def _zdt1_hv(ref: Sequence[float]) -> float:
    _check_ref(ref, 2)
    return (ref[1] - 1.0) + 2.0 / 3.0 + (ref[0] - 1.0) * ref[1]


def _zdt2_hv(ref: Sequence[float]) -> float:
    _check_ref(ref, 2)
    return (ref[1] - 1.0) + 1.0 / 3.0 + (ref[0] - 1.0) * ref[1]


def _dtlz2_hv(ref: Sequence[float]) -> float:
    _check_ref(ref, 3)
    return ref[0] * ref[1] * ref[2] - math.pi / 6.0


def _zdt1_front(n: int) -> list[tuple[float, ...]]:
    return [(float(f), 1.0 - math.sqrt(float(f))) for f in np.linspace(0.0, 1.0, n)]


def _zdt2_front(n: int) -> list[tuple[float, ...]]:
    return [(float(f), 1.0 - float(f) ** 2) for f in np.linspace(0.0, 1.0, n)]


def _dtlz2_front(n: int) -> list[tuple[float, ...]]:
    side = max(2, round(math.sqrt(n)))
    grid = np.linspace(0.0, 1.0, side)
    return [_dtlz2([float(a), float(b)]) for a in grid for b in grid]


PROBLEMS: dict[str, Problem] = {
    "zdt1": Problem("zdt1", 2, 2, _zdt1, _zdt1_hv, _zdt1_front),
    "zdt2": Problem("zdt2", 2, 2, _zdt2, _zdt2_hv, _zdt2_front),
    "dtlz2": Problem("dtlz2", 3, 3, _dtlz2, _dtlz2_hv, _dtlz2_front),
}


def get_problem(name: str) -> Problem:
    try:
        return PROBLEMS[name]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark problem {name!r}") from exc


def pareto_optimal_solutions(problem: str, dims: int, n: int) -> list[list[float]]:
    """Decision vectors on the true Pareto front (ZDT: x₂…=0; DTLZ2: x₃…=0.5) — a reference optimiser."""
    p = get_problem(problem)
    if p.n_objectives == 2:
        return [[float(f)] + [0.0] * (dims - 1) for f in np.linspace(0.0, 1.0, n)]
    side = max(2, round(math.sqrt(n)))
    grid = np.linspace(0.0, 1.0, side)
    return [[float(a), float(b)] + [0.5] * (dims - 2) for a in grid for b in grid]


def igd(front: Sequence[Sequence[float]], reference: Sequence[Sequence[float]]) -> float:
    """Inverted generational distance: mean distance from each reference point to its nearest front point."""
    if not front:
        return math.inf
    f = np.asarray(front, dtype=float)
    r = np.asarray(reference, dtype=float)
    distances = np.sqrt(((r[:, None, :] - f[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
    return float(distances.mean())


# ---------------------------------------------------------------------------------------------
# Running the engine on a problem
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EvolutionRunResult:
    objectives: list[tuple[float, ...]]
    hv_history: list[float]
    evaluations: int
    generations: int


def _var(i: int) -> str:
    return f"x{i:02d}"


def problem_schema(dims: int, mutation_scale: float = DEFAULT_MUTATION_SCALE) -> ParameterSchema:
    return ParameterSchema(
        parameters={
            _var(i): ParameterSpec(name=_var(i), type=ParamType.FLOAT, min=0.0, max=1.0, mutation_scale=mutation_scale)
            for i in range(dims)
        }
    )


def run_evolution(
    problem: str,
    *,
    dims: int,
    generations: int,
    population: int,
    overrides: Mapping[str, Any] | None = None,
    seed: int = 0,
    reference_point: Sequence[float] | None = None,
) -> EvolutionRunResult:
    """Run the engine on ``problem`` exactly as the strategies service drives it.

    ``hv_history[0]`` is the hypervolume of the ε-archive after the initial population;
    ``hv_history[g]`` after generation ``g`` (raw hypervolume, minimisation, against
    ``reference_point`` — default ``1.1`` on every objective).
    """
    p = get_problem(problem)
    if not p.min_dims <= dims <= MAX_DIMS:
        raise ValueError(f"dims must be in [{p.min_dims}, {MAX_DIMS}] for {problem}")
    if population < 2 or generations < 0 or population * max(generations, 1) > MAX_BUDGET:
        raise ValueError("invalid evaluation budget")
    opts = dict(overrides or {})
    unknown = set(opts) - ALLOWED_OVERRIDES
    if unknown:
        raise ValueError(f"unsupported config overrides: {sorted(unknown)}")
    ref = list(reference_point) if reference_point is not None else [1.1] * p.n_objectives
    schema = problem_schema(dims, float(opts.pop("mutation_scale", DEFAULT_MUTATION_SCALE)))
    config = EvolutionConfig(
        population_size=population,
        objectives=tuple(
            ObjectiveSpec(name=f"f{j + 1}", direction=Direction.MINIMIZE, bounds=(0.0, 1.0))
            for j in range(p.n_objectives)
        ),
        epsilon=opts.pop("epsilon", DEFAULT_EPSILON),
        archive_size=opts.pop("archive_size", DEFAULT_ARCHIVE_SIZE),
        seed=seed,
        **opts,
    )
    engine = EvolutionEngine(config, schema)

    def evaluate(candidate: Candidate) -> Candidate:
        values = p.evaluate([float(candidate.params[_var(i)]) for i in range(dims)])
        return candidate.model_copy(update={"fitness": {f"f{j + 1}": v for j, v in enumerate(values)}})

    def archive_hv(archive: Mapping[str, Any]) -> float:
        points = [[e["objectives"][f"f{j + 1}"] for j in range(p.n_objectives)] for e in archive["entries"]]
        return hypervolume(points, ref)

    pop = [evaluate(c) for c in engine.sample_population(population)]
    evaluations = len(pop)
    archive: dict[str, Any] | None = None
    history: list[float] = []
    for generation in range(1, generations + 1):
        plan = engine.step(pop, generation, archive=archive)
        archive = plan.archive
        history.append(archive_hv(archive))
        survivors = set(plan.survivors)
        children = [evaluate(c) for c in plan.children]
        evaluations += len(children)
        pop = [c for c in pop if c.id in survivors] + children
    final = engine.assess(pop, archive=archive)
    history.append(archive_hv(final.archive))
    objectives = [
        tuple(float(e["objectives"][f"f{j + 1}"]) for j in range(p.n_objectives)) for e in final.archive["entries"]
    ]
    return EvolutionRunResult(
        objectives=objectives, hv_history=history, evaluations=evaluations, generations=generations
    )


# ---------------------------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------------------------
class StrategyEvolutionSuite(BenchmarkSuite):
    def execute_case(self, case: BenchmarkCase, subject: Any, *, seed: int) -> Any:
        spec = case.input
        problem = get_problem(str(spec["problem"]))
        dims = int(spec["dims"])
        output = subject(copy.deepcopy(spec))
        if isinstance(output, Mapping) and "solutions" in output:
            solutions = output["solutions"]
            if not isinstance(solutions, list) or not 0 < len(solutions) <= MAX_SOLUTIONS:
                raise ValueError(f"solutions must be a non-empty list of at most {MAX_SOLUTIONS} vectors")
            objectives: list[tuple[float, ...]] = []
            for x in solutions:
                if not isinstance(x, list | tuple) or len(x) != dims:
                    raise ValueError(f"each solution must have {dims} decision variables")
                values = [float(v) for v in x]
                if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values):
                    raise ValueError("decision variables must be finite and within [0, 1]")
                objectives.append(problem.evaluate(values))
            return {"mode": "solutions", "objectives": objectives, "evaluations": len(objectives), "hv_history": []}
        if output is not None and not isinstance(output, Mapping):
            raise ValueError("subject must return config overrides (object), {'solutions': [...]} or None")
        ref = case.expected["reference_point"]
        result = run_evolution(
            problem.name,
            dims=dims,
            generations=int(spec["generations"]),
            population=int(spec["population"]),
            overrides=output or {},
            seed=seed,
            reference_point=ref,
        )
        return {
            "mode": "engine",
            "objectives": result.objectives,
            "evaluations": result.evaluations,
            "hv_history": result.hv_history,
        }


class EvolutionScorer(Scorer):
    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        problem = get_problem(str(case.input["problem"]))
        ref = [float(r) for r in case.expected["reference_point"]]
        true_hv = problem.true_hypervolume(ref)
        front = [tuple(float(v) for v in point) for point in output["objectives"]]
        hv = hypervolume(front, ref)
        normalized = min(1.0, max(0.0, hv / true_hv))
        distance = igd(front, problem.front_sample(400))
        history = [round(min(1.0, h / true_hv), 6) for h in output.get("hv_history", [])]
        threshold = float(case.expected["pass_threshold"])
        return CaseScore(
            normalized,
            normalized >= threshold,
            {
                "mode": output["mode"],
                "normalized_hypervolume": round(normalized, 6),
                "hypervolume": round(hv, 9),
                "true_hypervolume": round(true_hv, 9),
                "igd": round(distance, 6) if math.isfinite(distance) else None,
                "front_size": len(front),
                "evaluations": int(output["evaluations"]),
                "hv_history": history,
                "pass_threshold": threshold,
            },
        )

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        gains = [
            r.detail["hv_history"][-1] - r.detail["hv_history"][0]
            for r in results
            if len(r.detail.get("hv_history", [])) >= 2
        ]
        igds = [float(r.detail["igd"]) for r in results if r.detail.get("igd") is not None]
        return {
            "normalized_hypervolume": mean([r.score for r in results]),
            "mean_igd": mean(igds) if igds else -1.0,
            "mean_hv_gain": mean(gains),
            "pass_rate": mean([float(r.passed) for r in results]),
        }

    def suite_score(self, metrics: Any, results: Sequence[CaseResult]) -> float:
        return float(metrics["normalized_hypervolume"])


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    for case in cases:
        get_problem(str(case.input["problem"]))
    return StrategyEvolutionSuite(
        key=KEY,
        name="StrategyEvolutionBench",
        version=str(meta["version"]),
        component="evolution",
        description=str(meta["description"]),
        cases=cases,
        scorer=EvolutionScorer(),
        config={
            "problems": sorted(PROBLEMS),
            "allowed_overrides": sorted(ALLOWED_OVERRIDES),
            "headline_metric": "normalized_hypervolume",
        },
    )
