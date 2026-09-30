"""Evolution benchmarks: versioned suites of cases + deterministic scorers.

Suites evaluate a *subject* (an agent definition, a strategy version or a platform component) on fixed cases.
The runner (application layer) produces one output per case; ``score_case`` grades it with an explicit rubric.
``compare_runs`` pairs cases between two runs and only reports "improved" when a paired permutation test
supports it — the platform never claims self-improvement without benchmark evidence.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engines.common.stats import paired_permutation_pvalue
from engines.lab.agents import schemas as agent_schemas
from engines.lab.evaluation.base import EvaluationContext
from engines.lab.evaluation.evaluators import CodeQualityEvaluator
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.experiments.validator import ExperimentDesignValidator
from engines.lab.failures.classifier import FailureClassifier, FailureSignal
from engines.lab.reproducibility import ReproducibilityManifest, completeness
from engines.lab.verification.claims import overclaiming_terms
from engines.lab.verification.criteria import CheckResult, ClaimVerifier, criteria_for

CASES_DIR = Path(__file__).parent / "cases"


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


@dataclass
class CaseScore:
    case_id: str
    score: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


Scorer = Callable[[BenchmarkCase, Any], CaseScore]


@dataclass(frozen=True)
class BenchmarkSuite:
    key: str
    version: str
    description: str
    subject_kind: str  # agent | strategy | platform
    subject_role: str | None
    cases: tuple[BenchmarkCase, ...]
    scorer: Scorer
    pass_threshold: float = 0.7

    def score(self, case: BenchmarkCase, output: Any) -> CaseScore:
        try:
            return self.scorer(case, output)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            return CaseScore(case.id, 0.0, False, {"error": f"{type(exc).__name__}: {exc}"[:300]})


# ------------------------------------------------------------------------------------------------
# Scorers
# ------------------------------------------------------------------------------------------------


def _score_literature(case: BenchmarkCase, output: Any) -> CaseScore:
    review = agent_schemas.LiteratureReview.model_validate(output)
    allowed = set(case.input.get("source_ids", []))
    cited = {sid for f in review.findings for sid in f.source_ids}
    fabricated = sorted(cited - allowed)
    findings_with_citation = sum(1 for f in review.findings if f.source_ids)
    text = " ".join(f.statement.lower() for f in review.findings)
    facts = [k.lower() for k in case.expected.get("key_facts", [])]
    coverage = sum(1 for k in facts if k in text) / len(facts) if facts else 1.0
    cite_rate = findings_with_citation / len(review.findings) if review.findings else 0.0
    score = 0.4 * coverage + 0.3 * cite_rate + 0.3 * (0.0 if fabricated else 1.0)
    return CaseScore(
        case.id,
        round(score, 4),
        not fabricated and score >= 0.7,
        {"fabricated_ids": fabricated, "coverage": coverage, "citation_rate": cite_rate},
    )


def _score_hypotheses(case: BenchmarkCase, output: Any) -> CaseScore:
    hs = agent_schemas.HypothesisSet.model_validate(output)
    allowed = set(case.input.get("source_ids", []))
    n = len(hs.hypotheses)
    measurable = sum(
        1 for h in hs.hypotheses if h.measurable_prediction.metric and h.measurable_prediction.direction != "no_change"
    )
    with_assumptions = sum(1 for h in hs.hypotheses if h.assumptions)
    fabricated = sorted(
        {s for h in hs.hypotheses for s in h.supporting_source_ids + h.contradicting_source_ids} - allowed
    )
    overclaim = sorted({t for h in hs.hypotheses for t in overclaiming_terms(h.statement + " " + h.rationale)})
    overconfident = sum(1 for h in hs.hypotheses if h.confidence >= 0.95)
    distinct = len({h.statement.strip().lower() for h in hs.hypotheses}) / n
    score = (
        0.35 * measurable / n
        + 0.15 * with_assumptions / n
        + 0.2 * distinct
        + 0.15 * (0.0 if fabricated else 1.0)
        + 0.1 * (0.0 if overclaim else 1.0)
        + 0.05 * (1 - overconfident / n)
    )
    return CaseScore(
        case.id,
        round(score, 4),
        score >= 0.7 and not fabricated,
        {"measurable": measurable, "fabricated_ids": fabricated, "overclaiming": overclaim, "count": n},
    )


def _score_design(case: BenchmarkCase, output: Any) -> CaseScore:
    design = agent_schemas.ExperimentDesign.model_validate(output)
    spec = ExperimentSpec.model_validate(design.spec)
    report = ExperimentDesignValidator(known_harnesses=frozenset(case.input.get("harnesses", []))).validate(spec)
    required = set(case.expected.get("must_include", ["baseline", "harness", "seeds"]))
    present = {
        "baseline": spec.baseline is not None,
        "harness": spec.harness is not None and spec.harness.key != "self_reported",
        "seeds": len(set(spec.seeds)) >= spec.statistical_plan.min_seeds,
        "ablation": bool(spec.ablations),
        "controls": bool(spec.controls or any(v.kind == "control" for v in spec.variables)),
    }
    coverage = sum(1 for r in required if present.get(r)) / len(required) if required else 1.0
    score = 0.5 * (1.0 if report.valid else 0.0) + 0.4 * coverage + 0.1 * (1.0 if not report.warnings else 0.5)
    return CaseScore(
        case.id,
        round(score, 4),
        report.valid and coverage == 1.0,
        {"errors": sorted(report.codes()), "present": present},
    )


def _score_coding(case: BenchmarkCase, output: Any) -> CaseScore:
    bundle = agent_schemas.CodeBundle.model_validate(output)
    artifacts = {f.path: f.content.encode() for f in bundle.files}
    spec = ExperimentSpec.model_validate(case.input["spec"])
    result = CodeQualityEvaluator().evaluate(spec, artifacts, EvaluationContext())
    violations = int(result.metrics.get("violations", 0))
    entry_ok = bundle.entrypoint[-1] in artifacts or any(bundle.entrypoint[-1].endswith(p) for p in artifacts)
    sandbox_ok = bool(case.expected.get("sandbox_succeeded", True)) if "sandbox_succeeded" in case.expected else True
    score = (
        0.5 * (1.0 if result.passed else 0.0)
        + 0.2 * (1.0 if entry_ok else 0.0)
        + 0.2 * (0.0 if result.warnings else 1.0)
        + 0.1 * (1.0 if sandbox_ok else 0.0)
    )
    return CaseScore(
        case.id,
        round(score, 4),
        bool(result.passed) and entry_ok,
        {"violations": violations, "warnings": result.warnings},
    )


def _score_failure(case: BenchmarkCase, output: Any) -> CaseScore:
    predicted = output.get("failure_type") if isinstance(output, Mapping) else getattr(output, "failure_type", None)
    expected = case.expected["failure_type"]
    ok = str(predicted) == expected
    return CaseScore(case.id, 1.0 if ok else 0.0, ok, {"predicted": str(predicted), "expected": expected})


def _score_repro(case: BenchmarkCase, output: Any) -> CaseScore:
    manifest = ReproducibilityManifest.model_validate(output)
    score, missing = completeness(manifest, requires_dataset=bool(case.input.get("requires_dataset")))
    expected_missing = set(case.expected.get("missing", []))
    ok = set(missing) == expected_missing
    return CaseScore(case.id, 1.0 if ok else 0.0, ok, {"missing": missing, "completeness": score})


def _score_verification(case: BenchmarkCase, output: Any) -> CaseScore:
    predicted = output.get("status") if isinstance(output, Mapping) else getattr(output, "status", None)
    ok = str(predicted) == case.expected["status"]
    return CaseScore(
        case.id, 1.0 if ok else 0.0, ok, {"predicted": str(predicted), "expected": case.expected["status"]}
    )


def _score_evolution(case: BenchmarkCase, output: Any) -> CaseScore:
    achieved = float(output["hypervolume"]) if isinstance(output, Mapping) else float(output)
    reference = float(case.expected["reference_hypervolume"])
    ratio = max(0.0, min(achieved / reference, 1.0)) if reference > 0 else 0.0
    return CaseScore(
        case.id,
        round(ratio, 4),
        ratio >= float(case.expected.get("pass_ratio", 0.9)),
        {"hypervolume": achieved, "ratio": ratio},
    )


# ------------------------------------------------------------------------------------------------
# Built-in platform subjects (deterministic components benchmarked directly)
# ------------------------------------------------------------------------------------------------


def platform_failure_subject(case: BenchmarkCase) -> dict[str, Any]:
    return FailureClassifier().classify(FailureSignal.model_validate(case.input["signal"])).to_dict()


def platform_verification_subject(case: BenchmarkCase) -> dict[str, Any]:
    criteria = criteria_for(case.input.get("domain", "default"))
    checks = {
        k: CheckResult(k, v.get("passed"), contradicts=bool(v.get("contradicts")))
        for k, v in case.input["checks"].items()
    }
    decision = ClaimVerifier(criteria).decide(
        checks,
        reproductions_passed=int(case.input.get("reproductions_passed", 0)),
        reproductions_failed=int(case.input.get("reproductions_failed", 0)),
    )
    return decision.to_dict()


def platform_repro_subject(case: BenchmarkCase) -> dict[str, Any]:
    return dict(case.input["manifest"])


def platform_evolution_subject(case: BenchmarkCase) -> dict[str, Any]:
    from engines.lab.benchmarks.problems import run_evolution_problem

    return run_evolution_problem(case.input)


PLATFORM_SUBJECTS: dict[str, Callable[[BenchmarkCase], Any]] = {
    "failure_analysis_bench": platform_failure_subject,
    "claim_verification_bench": platform_verification_subject,
    "reproducibility_bench": platform_repro_subject,
    "strategy_evolution_bench": platform_evolution_subject,
}


# ------------------------------------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------------------------------------


def _load_cases(name: str) -> tuple[BenchmarkCase, ...]:
    path = CASES_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(BenchmarkCase.model_validate(c) for c in data["cases"])


@lru_cache(maxsize=1)
def suites() -> dict[str, BenchmarkSuite]:
    defs: list[tuple[str, str, str, str, str | None, Scorer]] = [
        (
            "literature_bench",
            "1.0.0",
            "Cited, non-fabricated literature synthesis",
            "agent",
            "literature",
            _score_literature,
        ),
        (
            "hypothesis_bench",
            "1.0.0",
            "Measurable, falsifiable, non-overclaiming hypotheses",
            "agent",
            "hypothesis",
            _score_hypotheses,
        ),
        (
            "experiment_design_bench",
            "1.0.0",
            "Valid, controlled, reproducible experiment designs",
            "agent",
            "experiment_designer",
            _score_design,
        ),
        ("coding_experiment_bench", "1.0.0", "Sandbox-safe, seeded experiment code", "agent", "coding", _score_coding),
        ("failure_analysis_bench", "1.0.0", "Correct failure classification", "platform", None, _score_failure),
        (
            "strategy_evolution_bench",
            "1.0.0",
            "Hypervolume on analytic multi-objective problems",
            "strategy",
            None,
            _score_evolution,
        ),
        ("reproducibility_bench", "1.0.0", "Manifest completeness detection", "platform", None, _score_repro),
        (
            "claim_verification_bench",
            "1.0.0",
            "Verification status under configured criteria",
            "platform",
            None,
            _score_verification,
        ),
    ]
    return {
        key: BenchmarkSuite(key, version, desc, kind, role, _load_cases(key), scorer)
        for key, version, desc, kind, role, scorer in defs
    }


@dataclass
class RunComparison:
    suite: str
    n_pairs: int
    mean_a: float
    mean_b: float
    mean_delta: float
    p_value: float
    verdict: str  # improved | regressed | no_significant_difference | insufficient_data

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def compare_runs(
    suite: str, scores_a: Mapping[str, float], scores_b: Mapping[str, float], *, alpha: float = 0.05
) -> RunComparison:
    """Compare run B against run A on shared cases (positive delta = B better)."""
    shared = sorted(set(scores_a) & set(scores_b))
    if len(shared) < 3:
        return RunComparison(
            suite,
            len(shared),
            _avg([scores_a[c] for c in shared]),
            _avg([scores_b[c] for c in shared]),
            0.0,
            1.0,
            "insufficient_data",
        )
    deltas = [scores_b[c] - scores_a[c] for c in shared]
    p = paired_permutation_pvalue(deltas)
    mean_delta = _avg(deltas)
    if p < alpha and mean_delta > 0:
        verdict = "improved"
    elif p < alpha and mean_delta < 0:
        verdict = "regressed"
    else:
        verdict = "no_significant_difference"
    return RunComparison(
        suite,
        len(shared),
        _avg([scores_a[c] for c in shared]),
        _avg([scores_b[c] for c in shared]),
        round(mean_delta, 6),
        round(p, 6),
        verdict,
    )


def _avg(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan
