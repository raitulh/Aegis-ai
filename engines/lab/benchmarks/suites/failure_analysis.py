"""FailureAnalysisBench — classifying failures from observed signals (component ``failure_classification``).

Input: failure signals (stage, exit code, error type, message, resource usage, context). The subject
returns a failure type (``engines.lab.states.FailureType``), bare or as ``{"failure_type": ...}``.
Scored by accuracy (headline) and macro-F1.
"""

from __future__ import annotations

from engines.lab.benchmarks.base import BenchmarkSuite, LabelScorer, load_fixture
from engines.lab.states import FailureType

KEY = "failure_analysis_bench"


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    labels = [ft.value for ft in FailureType]
    unknown = {c.expected["failure_type"] for c in cases} - set(labels)
    if unknown:
        raise ValueError(f"{KEY}: fixture uses unknown failure types {sorted(unknown)}")
    return BenchmarkSuite(
        key=KEY,
        name="FailureAnalysisBench",
        version=str(meta["version"]),
        component="failure_classification",
        description=str(meta["description"]),
        cases=cases,
        scorer=LabelScorer(expected_key="failure_type", output_keys=("failure_type", "type", "label"), labels=labels),
        config={"labels": labels, "headline_metric": "accuracy"},
    )
