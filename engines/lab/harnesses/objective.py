"""Objective-function harness: independently re-evaluates the candidate's reported solution on a
platform-defined benchmark function (the candidate cannot misreport its own score)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from _common import finite, run

KEY, VERSION = "objective", "1.0.0"


def sphere(x: list[float]) -> float:
    return sum(v * v for v in x)


def rastrigin(x: list[float]) -> float:
    return 10 * len(x) + sum(v * v - 10 * math.cos(2 * math.pi * v) for v in x)


def rosenbrock(x: list[float]) -> float:
    return sum(100 * (x[i + 1] - x[i] ** 2) ** 2 + (1 - x[i]) ** 2 for i in range(len(x) - 1))


def ackley(x: list[float]) -> float:
    n = len(x)
    s1 = sum(v * v for v in x) / n
    s2 = sum(math.cos(2 * math.pi * v) for v in x) / n
    return -20 * math.exp(-0.2 * math.sqrt(s1)) - math.exp(s2) + 20 + math.e


FUNCTIONS = {"sphere": sphere, "rastrigin": rastrigin, "rosenbrock": rosenbrock, "ackley": ackley}


def evaluate(candidate: Path, data: Path, config: dict[str, Any]) -> dict[str, Any]:
    name = config.get("function", "rastrigin")
    if name not in FUNCTIONS:
        raise ValueError(f"unknown function {name}")
    dim = int(config.get("dim", 5))
    lo, hi = (float(b) for b in config.get("bounds", [-5.12, 5.12]))
    solution = json.loads((candidate / config.get("solution_file", "solution.json")).read_text())
    x = [finite(float(v)) for v in solution.get("x", [])]
    if len(x) != dim:
        raise ValueError(f"solution has {len(x)} dims, expected {dim}")
    clipped = [min(max(v, lo), hi) for v in x]
    in_bounds = all(lo <= v <= hi for v in x)
    value = FUNCTIONS[name](clipped)
    return {
        "metrics": {"objective_value": value, "in_bounds": 1.0 if in_bounds else 0.0},
        "n": 1,
        "self_reported": False,
        "function": name,
    }


if __name__ == "__main__":
    run(evaluate, KEY, VERSION)
