"""Parameter-space distance, novelty, niche count and population diversity metrics."""

from __future__ import annotations

import pytest

from engines.lab.evolution.diversity import DiversityManager
from engines.lab.evolution.mutation import MutationEngine

SCHEMA = {
    "parameters": {
        "rate": {"type": "float", "min": 0, "max": 2},
        "depth": {"type": "int", "min": 0, "max": 10},
        "verify": {"type": "bool"},
        "style": {"type": "choice", "choices": ["terse", "verbose", "socratic"]},
        "tools": {"type": "ordered_list", "choices": ["search", "read", "code", "plot"]},
    }
}
BASE = {"rate": 1.0, "depth": 5, "verify": True, "style": "terse", "tools": ["search", "read"]}


def test_distance_is_zero_for_identical_vectors_and_bounded() -> None:
    dm = DiversityManager(SCHEMA)
    assert dm.distance(BASE, dict(BASE)) == 0.0
    opposite = {
        "rate": 0.0 if BASE["rate"] else 2.0,
        "depth": 0,
        "verify": False,
        "style": "verbose",
        "tools": ["code"],
    }
    assert 0 < dm.distance(BASE, opposite) <= 1


def test_per_type_distance_components() -> None:
    dm = DiversityManager(SCHEMA)
    spec = dm.schema.parameters
    assert dm.parameter_distance(spec["rate"], 0.5, 1.5) == pytest.approx(0.5)  # |Δ| / range
    assert dm.parameter_distance(spec["verify"], True, False) == 1.0
    assert dm.parameter_distance(spec["style"], "terse", "terse") == 0.0
    assert dm.parameter_distance(spec["tools"], ["search", "read"], ["read", "code"]) == pytest.approx(1 - 1 / 3)
    assert dm.parameter_distance(spec["tools"], [], []) == 0.0
    one_changed = {**BASE, "style": "verbose"}
    assert dm.distance(BASE, one_changed) == pytest.approx(1 / 5)
    assert dm.distance(BASE, {k: v for k, v in BASE.items() if k != "depth"}) == pytest.approx(1 / 5)


def test_novelty_is_mean_distance_to_k_nearest() -> None:
    dm = DiversityManager(SCHEMA)
    reference = [{**BASE, "style": "verbose"}, dict(BASE), {**BASE, "verify": False, "style": "socratic"}]
    assert dm.novelty(BASE, [], k=3) == 1.0
    assert dm.novelty(BASE, reference, k=1) == 0.0
    assert dm.novelty(BASE, reference, k=2) == pytest.approx((0.0 + 0.2) / 2)
    with pytest.raises(ValueError):
        dm.novelty(BASE, reference, k=0)


def test_niche_count_counts_self_and_close_neighbours() -> None:
    dm = DiversityManager(SCHEMA)
    far = {"rate": 0.0, "depth": 0, "verify": False, "style": "socratic", "tools": ["plot"]}
    assert dm.niche_count(BASE, [BASE], sigma_share=0.3) == pytest.approx(1.0)
    assert dm.niche_count(BASE, [BASE, far], sigma_share=0.3) == pytest.approx(1.0)
    close = {**BASE, "style": "verbose"}  # distance 0.2
    assert dm.niche_count(BASE, [BASE, close], sigma_share=0.4) == pytest.approx(1.5)


def test_population_metrics_unique_ratio_and_pairwise_stats() -> None:
    dm = DiversityManager(SCHEMA)
    clones = dm.metrics([BASE, dict(BASE), dict(BASE)])
    assert clones.unique_ratio == pytest.approx(1 / 3) and clones.mean_pairwise_distance == 0.0
    varied = dm.metrics([BASE, {**BASE, "style": "verbose"}, {**BASE, "verify": False}])
    assert varied.unique_ratio == 1.0 and varied.min_pairwise_distance == pytest.approx(0.2)
    assert varied.max_pairwise_distance == pytest.approx(0.4)
    assert dm.metrics([]).n == 0 and dm.metrics([BASE]).mean_pairwise_distance == 0.0


def test_vectorised_distances_equal_scalar_definition() -> None:
    dm = DiversityManager({"parameters": {**SCHEMA["parameters"], "free": {"type": "float"}}})
    engine = MutationEngine(5)
    population = [engine.sample(SCHEMA, index=i) | {"free": float(i) - 3.5} for i in range(10)]
    population[3].pop("depth")
    population[6]["rate"] = "broken"
    matrix = dm.cross_distances(population, population)
    for i in range(10):
        for j in range(10):
            assert matrix[i, j] == pytest.approx(dm.distance(population[i], population[j]), abs=1e-12)
    scores = dm.novelty_all(population[:5], population[5:], k=3)
    for i in range(5):
        others = [p for j, p in enumerate(population[:5]) if j != i] + population[5:]
        assert scores[i] == pytest.approx(dm.novelty(population[i], others, k=3), abs=1e-12)
