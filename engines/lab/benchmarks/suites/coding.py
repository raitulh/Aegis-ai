"""CodingExperimentBench — small computational tasks (component ``coding``).

Input: ``{task_id, description, entrypoint, data, output_names}``. The subject returns the produced
outputs (e.g. parsed from an executed run's ``metrics.json``) as ``{name: value}`` (or
``{"outputs": {...}}``). Each expected output matches when numbers agree within the case tolerance
and everything else is equal; the case score is the fraction of matched outputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from engines.lab.benchmarks.base import BenchmarkCase, BenchmarkSuite, CaseResult, CaseScore, Scorer, load_fixture
from engines.lab.benchmarks.scoring import mean, values_match

KEY = "coding_experiment_bench"


class CodingScorer(Scorer):
    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        if isinstance(output, Mapping) and isinstance(output.get("outputs"), Mapping):
            output = output["outputs"]
        if not isinstance(output, Mapping):
            return CaseScore(
                0.0, False, {"error": "output must be an object of named outputs", "matched": 0, "total": 0}
            )
        expected: Mapping[str, Any] = case.expected["outputs"]
        rel_tol = float(case.expected.get("rel_tol", 1e-6))
        abs_tol = float(case.expected.get("abs_tol", 1e-9))
        matched = [
            name
            for name in expected
            if name in output and values_match(expected[name], output[name], rel_tol=rel_tol, abs_tol=abs_tol)
        ]
        missing = [name for name in expected if name not in output]
        wrong = [name for name in expected if name in output and name not in matched]
        score = len(matched) / len(expected) if expected else 1.0
        return CaseScore(
            score,
            len(matched) == len(expected),
            {"matched": len(matched), "total": len(expected), "missing": missing, "mismatched": wrong},
        )

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        total = sum(len(c.expected["outputs"]) for c in cases)
        matched = sum(int(r.detail.get("matched", 0)) for r in results)
        return {
            "output_match_rate": matched / total if total else 1.0,
            "task_success_rate": mean([float(r.passed) for r in results]),
            "mean_case_score": mean([r.score for r in results]),
        }

    def suite_score(self, metrics: Any, results: Sequence[CaseResult]) -> float:
        return float(metrics["output_match_rate"])


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    return BenchmarkSuite(
        key=KEY,
        name="CodingExperimentBench",
        version=str(meta["version"]),
        component="coding",
        description=str(meta["description"]),
        cases=cases,
        scorer=CodingScorer(),
        config={"headline_metric": "output_match_rate"},
    )
