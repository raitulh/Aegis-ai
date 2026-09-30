"""LiteratureBench — ranking documents for a research query (component ``retrieval``).

Input ``{query, documents: [{id, title, text}]}``; the subject returns ranked document ids (a list, or
``{"ranked_ids": [...]}``). Scored by nDCG@5 with graded relevance (2 = directly relevant,
1 = partially relevant) — the headline score — plus recall@5 and MRR. Unknown ids are ignored and
duplicates count once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from engines.lab.benchmarks.base import BenchmarkCase, BenchmarkSuite, CaseResult, CaseScore, Scorer, load_fixture
from engines.lab.benchmarks.scoring import (
    dedupe,
    extract_string_list,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

KEY = "literature_bench"
PASS_NDCG = 0.8


class LiteratureScorer(Scorer):
    def __init__(self, k: int = 5) -> None:
        self.k = k

    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        ranked = extract_string_list(output, ("ranked_ids", "ids", "ranking"))
        if ranked is None:
            return CaseScore(0.0, False, {"error": "output must be a list of document ids"})
        known = {d["id"] for d in case.input["documents"]}
        ranked = [doc for doc in dedupe(ranked) if doc in known]
        expected = case.expected
        relevant = list(expected["relevant_ids"])
        grades = {k: float(v) for k, v in expected.get("grades", dict.fromkeys(relevant, 1)).items()}
        ndcg = ndcg_at_k(ranked, grades, self.k)
        recall = recall_at_k(ranked, relevant, self.k)
        rr = reciprocal_rank(ranked, relevant)
        return CaseScore(
            ndcg,
            ndcg >= PASS_NDCG,
            {
                f"ndcg@{self.k}": round(ndcg, 6),
                f"recall@{self.k}": round(recall, 6),
                "rr": round(rr, 6),
                "top": ranked[: self.k],
            },
        )

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        def metric(name: str) -> float:
            return mean([float(r.detail.get(name, 0.0)) for r in results])

        return {
            f"ndcg@{self.k}": metric(f"ndcg@{self.k}"),
            f"recall@{self.k}": metric(f"recall@{self.k}"),
            "mrr": metric("rr"),
            "pass_rate": mean([float(r.passed) for r in results]),
        }

    def suite_score(self, metrics: Any, results: Sequence[CaseResult]) -> float:
        return float(metrics[f"ndcg@{self.k}"])


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    k = int(meta.get("k", 5))
    return BenchmarkSuite(
        key=KEY,
        name="LiteratureBench",
        version=str(meta["version"]),
        component="retrieval",
        description=str(meta["description"]),
        cases=cases,
        scorer=LiteratureScorer(k),
        config={"k": k, "headline_metric": f"ndcg@{k}", "pass_threshold": PASS_NDCG},
    )
