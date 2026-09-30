"""ReproducibilityBench — deciding whether a reproduction reproduces a result
(component ``reproduction_decision``).

Input ``{original: {metrics, runs}, reproduced: {metrics, runs}, tolerance: {relative, absolute},
min_runs}``; the subject returns a verdict (bare or ``{"verdict": ...}``). Scored by accuracy.

:func:`reference_reproduction_verdict` implements the fixture's documented decision rules exactly and
is usable as a baseline subject (and as a consistency check of the fixture labels).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from engines.lab.benchmarks.base import BenchmarkSuite, LabelScorer, load_fixture

KEY = "reproducibility_bench"
VERDICTS: tuple[str, ...] = ("REPRODUCED", "PARTIALLY_REPRODUCED", "NOT_REPRODUCED", "INCONCLUSIVE")


def reference_reproduction_verdict(case_input: Mapping[str, Any]) -> str:
    """Apply the documented rules: runs/metric completeness → tolerance check per metric."""
    original = case_input["original"]
    reproduced = case_input["reproduced"]
    tolerance = case_input.get("tolerance", {})
    relative = float(tolerance.get("relative", 0.0))
    absolute = float(tolerance.get("absolute", 0.0))
    min_runs = int(case_input.get("min_runs", 3))
    original_metrics: Mapping[str, float] = original.get("metrics", {})
    reproduced_metrics: Mapping[str, float] = reproduced.get("metrics", {})
    if int(reproduced.get("runs", 0)) < min_runs or any(m not in reproduced_metrics for m in original_metrics):
        return "INCONCLUSIVE"
    if not original_metrics:
        return "INCONCLUSIVE"
    within = []
    for name, value in original_metrics.items():
        o, r = float(value), float(reproduced_metrics[name])
        if not (math.isfinite(o) and math.isfinite(r)):
            within.append(False)
            continue
        within.append(abs(r - o) <= max(absolute, relative * abs(o)) + 1e-12)
    if all(within):
        return "REPRODUCED"
    if not any(within):
        return "NOT_REPRODUCED"
    return "PARTIALLY_REPRODUCED"


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    return BenchmarkSuite(
        key=KEY,
        name="ReproducibilityBench",
        version=str(meta["version"]),
        component="reproduction_decision",
        description=str(meta["description"]),
        cases=cases,
        scorer=LabelScorer(expected_key="verdict", output_keys=("verdict", "label"), labels=VERDICTS),
        config={
            "labels": list(VERDICTS),
            "decision_rules": list(meta.get("decision_rules", [])),
            "headline_metric": "accuracy",
        },
    )
