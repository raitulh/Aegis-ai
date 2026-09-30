"""Analytic benchmark objective functions (minimization), shared with the ``objective`` harness.

The harness script keeps its own copy because it runs standalone (stdlib only) inside the sandbox; a unit test
asserts both copies agree.
"""

from __future__ import annotations

import math


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
