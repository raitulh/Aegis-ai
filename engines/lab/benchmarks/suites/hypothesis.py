"""HypothesisBench — hypothesis quality assessment (component ``hypothesis_quality``).

Input: a hypothesis dict (statement, independent/dependent variable, metric, prediction,
falsification criterion). The subject returns ``{falsifiable, measurable, metric_named}`` booleans;
each case scores the fraction of correct labels (passed = all three correct).

:func:`reference_hypothesis_checks` is a deterministic, rule-based reference implementation of the
label definitions stored in the fixture; it is usable as a baseline subject.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from engines.lab.benchmarks.base import BenchmarkCase, BenchmarkSuite, CaseResult, CaseScore, Scorer, load_fixture
from engines.lab.benchmarks.scoring import mean

KEY = "hypothesis_bench"
LABELS: tuple[str, ...] = ("falsifiable", "measurable", "metric_named")

_METRIC_TERMS = re.compile(
    r"\b(accuracy|auroc|auc|f1|precision|recall|rmse|mae|mse|r2|r\^2|calibration error|ece|brier|perplexity|bleu|"
    r"rouge|ndcg|mrr|faradaic efficiency|coulombic efficiency|cycle life|capacity retention|divergence rate|"
    r"p-value|latency|throughput|top-1|top-5|half-life|ic50|ec50|log-likelihood|nll)\b",
    re.IGNORECASE,
)
_QUANTITY_TERMS = re.compile(
    r"\b(rate|fraction|percentage|proportion|ratio|count|number of|time|duration|concentration|yield|measured|"
    r"error|score|cycles|voltage|current|temperature|mass|latency|loss|efficiency|density|frequency)\b",
    re.IGNORECASE,
)
_HEDGES = re.compile(
    r"\b(may or may not|might|could|possibly|perhaps|in some way|in some cases|under certain conditions|sometimes)\b",
    re.IGNORECASE,
)
_TAUTOLOGY = re.compile(r"\beither\b.*\bor\b.*\bnot\b", re.IGNORECASE)
_DIRECTIONAL = re.compile(
    r"\b(increase|increases|decrease|decreases|reduce|reduces|lower|higher|more|less|fewer|improve|improves|"
    r"outperform|outperforms|exceed|exceeds|below|above|at least|at most|greater|smaller|drop|rise|than)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def reference_hypothesis_checks(hypothesis: Mapping[str, Any]) -> dict[str, bool]:
    """Rule-based labels following the fixture's label definitions (a transparent baseline)."""
    statement = _text(hypothesis.get("statement"))
    prediction = _text(hypothesis.get("prediction"))
    criterion = _text(hypothesis.get("falsification_criterion"))
    dependent = _text(hypothesis.get("dependent_variable"))
    metric = _text(hypothesis.get("metric")).strip()
    claim_text = f"{statement} {prediction}"

    metric_named = bool(metric) or bool(_METRIC_TERMS.search(f"{statement} {prediction} {dependent}"))
    measurable = metric_named or bool(dependent and _QUANTITY_TERMS.search(dependent))
    hedged = bool(_HEDGES.search(claim_text))
    tautology = bool(_TAUTOLOGY.search(statement))
    directional = bool(_DIRECTIONAL.search(claim_text))
    falsifiable = not hedged and not tautology and (directional or bool(criterion.strip()))
    return {"falsifiable": falsifiable, "measurable": measurable, "metric_named": metric_named}


class HypothesisScorer(Scorer):
    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        if not isinstance(output, Mapping):
            return CaseScore(0.0, False, {"error": "output must be an object of boolean labels"})
        labels: dict[str, Any] = {}
        correct = 0
        for label in LABELS:
            predicted = output.get(label)
            expected = bool(case.expected[label])
            ok = isinstance(predicted, bool) and predicted == expected
            correct += int(ok)
            labels[label] = {
                "expected": expected,
                "predicted": predicted if isinstance(predicted, bool) else None,
                "correct": ok,
            }
        return CaseScore(correct / len(LABELS), correct == len(LABELS), {"labels": labels})

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for label in LABELS:
            metrics[f"{label}_accuracy"] = mean(
                [float(r.detail.get("labels", {}).get(label, {}).get("correct", False)) for r in results]
            )
        metrics["label_accuracy"] = mean([r.score for r in results])
        metrics["exact_match_rate"] = mean([float(r.passed) for r in results])
        return metrics

    def suite_score(self, metrics: Any, results: Sequence[CaseResult]) -> float:
        return float(metrics["label_accuracy"])


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    return BenchmarkSuite(
        key=KEY,
        name="HypothesisBench",
        version=str(meta["version"]),
        component="hypothesis_quality",
        description=str(meta["description"]),
        cases=cases,
        scorer=HypothesisScorer(),
        config={"labels": list(LABELS), "headline_metric": "label_accuracy"},
    )
