"""Registry of the built-in benchmark suites.

``suite_manifest()`` is what the strategies service uses to seed ``benchmark_suites`` rows (built-in,
``organization_id`` NULL): key, version, component, case_count, content_hash, description, config.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from engines.lab.benchmarks.base import BenchmarkSuite
from engines.lab.benchmarks.suites import SUITE_MODULES


class UnknownBenchmarkSuite(KeyError):
    """Raised by :func:`get_suite` for an unknown key."""


def _build() -> dict[str, BenchmarkSuite]:
    suites: dict[str, BenchmarkSuite] = {}
    for module in SUITE_MODULES:
        suite = module.build_suite()
        if suite.key in suites:
            raise ValueError(f"duplicate benchmark suite key {suite.key!r}")
        suites[suite.key] = suite
    return suites


BENCHMARK_SUITES: MappingProxyType[str, BenchmarkSuite] = MappingProxyType(_build())


def get_suite(key: str) -> BenchmarkSuite:
    try:
        return BENCHMARK_SUITES[key]
    except KeyError as exc:
        raise UnknownBenchmarkSuite(key) from exc


def list_suites() -> list[BenchmarkSuite]:
    """All suites, sorted by key."""
    return [BENCHMARK_SUITES[k] for k in sorted(BENCHMARK_SUITES)]


def suite_manifest() -> list[dict[str, Any]]:
    """Seeding metadata for every suite, sorted by key."""
    return [suite.manifest() for suite in list_suites()]
