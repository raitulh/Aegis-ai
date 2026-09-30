"""Internal benchmark framework of the AI Scientist Evolution Lab.

Eight versioned, content-hashed suites measure lab components (retrieval, hypothesis quality, design
validation, coding, failure classification, the evolution engine, reproduction decisions and claim
verification). Systems under test are injected callables, so the suites stay pure and deterministic.
All case data are *benchmark fixtures* — synthetic, never research findings or discoveries.
"""

from __future__ import annotations

from engines.lab.benchmarks.base import (
    BenchComparison,
    BenchmarkCase,
    BenchmarkSuite,
    BenchResult,
    CaseResult,
    CaseScore,
    LabelScorer,
    Scorer,
    compare,
    load_fixture,
)
from engines.lab.benchmarks.registry import (
    BENCHMARK_SUITES,
    UnknownBenchmarkSuite,
    get_suite,
    list_suites,
    suite_manifest,
)

__all__ = [
    "BENCHMARK_SUITES",
    "BenchComparison",
    "BenchResult",
    "BenchmarkCase",
    "BenchmarkSuite",
    "CaseResult",
    "CaseScore",
    "LabelScorer",
    "Scorer",
    "UnknownBenchmarkSuite",
    "compare",
    "get_suite",
    "list_suites",
    "load_fixture",
    "suite_manifest",
]
