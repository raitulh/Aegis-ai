"""The eight internal benchmark suites (each module exposes ``build_suite()``)."""

from __future__ import annotations

from engines.lab.benchmarks.suites import (
    claim_verification,
    coding,
    experiment_design,
    failure_analysis,
    hypothesis,
    literature,
    reproducibility,
    strategy_evolution,
)

SUITE_MODULES = (
    literature,
    hypothesis,
    experiment_design,
    coding,
    failure_analysis,
    strategy_evolution,
    reproducibility,
    claim_verification,
)

__all__ = ["SUITE_MODULES"]
