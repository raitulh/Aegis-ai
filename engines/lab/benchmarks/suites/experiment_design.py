"""ExperimentDesignBench — detecting experiment-design issues (component ``design_validation``).

Input: an experiment specification. The subject returns issue codes (a list, or ``{"issues": [...]}``)
from the vocabulary declared in the fixture; codes outside the vocabulary count as false positives.
Headline score: micro-F1 over issue codes; per-case score: F1 of the case's code sets (a clean design
answered with no issues scores 1.0).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from engines.lab.benchmarks.base import BenchmarkCase, BenchmarkSuite, CaseResult, CaseScore, Scorer, load_fixture
from engines.lab.benchmarks.scoring import extract_string_list, mean, prf, set_counts

KEY = "experiment_design_bench"


class DesignScorer(Scorer):
    def __init__(self, vocabulary: Sequence[str]) -> None:
        self.vocabulary = frozenset(vocabulary)

    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        predicted = extract_string_list(output, ("issues", "issue_codes", "codes"))
        if predicted is None:
            return CaseScore(0.0, False, {"error": "output must be a list of issue codes"})
        predicted_set = {p.strip().upper() for p in predicted}
        expected_set = set(case.expected["issues"])
        tp, fp, fn = set_counts(predicted_set, expected_set)
        _, _, f1 = prf(tp, fp, fn)
        return CaseScore(
            f1,
            predicted_set == expected_set,
            {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "unknown_codes": sorted(predicted_set - self.vocabulary),
                "missed": sorted(expected_set - predicted_set),
                "spurious": sorted(predicted_set - expected_set),
            },
        )

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        tp = sum(int(r.detail.get("tp", 0)) for r in results)
        fp = sum(int(r.detail.get("fp", 0)) for r in results)
        # unscorable outputs miss every expected issue of their case
        fn = 0
        for case, result in zip(cases, results, strict=True):
            fn += int(result.detail["fn"]) if "fn" in result.detail else len(case.expected["issues"])
        precision, recall, f1 = prf(tp, fp, fn)
        return {
            "micro_precision": precision,
            "micro_recall": recall,
            "micro_f1": f1,
            "mean_case_f1": mean([r.score for r in results]),
            "exact_match_rate": mean([float(r.passed) for r in results]),
        }

    def suite_score(self, metrics: Any, results: Sequence[CaseResult]) -> float:
        return float(metrics["micro_f1"])


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    vocabulary = sorted(meta["issue_vocabulary"])
    return BenchmarkSuite(
        key=KEY,
        name="ExperimentDesignBench",
        version=str(meta["version"]),
        component="design_validation",
        description=str(meta["description"]),
        cases=cases,
        scorer=DesignScorer(vocabulary),
        config={"issue_vocabulary": vocabulary, "headline_metric": "micro_f1"},
    )
