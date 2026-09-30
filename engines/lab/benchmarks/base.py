"""Internal benchmark framework.

A :class:`BenchmarkSuite` is a versioned, content-hashed set of :class:`BenchmarkCase` s plus a
:class:`Scorer`. :meth:`BenchmarkSuite.run` feeds every case's ``input`` (a deep copy — subjects can
never alter fixtures) to the *system under test*, an injected callable, and scores its outputs.
Exceptions raised by the subject score 0 for that case and are recorded, never propagated.

:func:`compare` pairs two results of the same suite version case by case and reports whether the
bootstrap CI of the mean per-case score difference excludes zero — the only basis on which a benchmark
may be said to show an *improvement*.

Case files live in ``engines/lab/benchmarks/data/*.json``. They are **benchmark fixtures**: synthetic
cases authored for internal regression benchmarking, never research findings or discoveries.
:func:`load_fixture` refuses any file not explicitly marked ``"benchmark_fixture": true``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.benchmarks.scoring import extract_label, macro_f1
from engines.lab.evolution.rng import derive_seed
from engines.lab.evolution.stats import bootstrap_mean_ci
from engines.lab.evolution.types import canonical_json

Subject = Callable[[Any], Any]
_MAX_ERROR_CHARS = 200


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    input: dict[str, Any]
    expected: Any
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseScore:
    """A scorer's verdict on one output. ``detail`` must be JSON-serialisable."""

    score: float
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    score: float = Field(ge=0, le=1)
    passed: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class BenchResult(BaseModel):
    """Result of one suite run (maps onto ``benchmark_runs``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_key: str
    suite_version: str
    component: str
    content_hash: str
    score: float = Field(ge=0, le=1)
    metrics: dict[str, float]
    case_results: list[CaseResult]
    n_cases: int
    n_passed: int
    n_errors: int
    seed: int


class BenchComparison(BaseModel):
    """Case-paired comparison of a result against a baseline result of the same suite version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_key: str
    suite_version: str
    score: float
    baseline_score: float
    delta: float
    mean_case_delta: float
    ci: tuple[float, float]
    ci_level: float
    improved: bool
    regressed: bool
    n_paired: int
    resamples: int
    seed: int

    def to_evidence(self) -> dict[str, Any]:
        """The evidence item consumed by ``PromotionGate.evaluate(benchmark_evidence=[...])``."""
        return {
            "suite": self.suite_key,
            "suite_version": self.suite_version,
            "score": self.score,
            "baseline_score": self.baseline_score,
            "improved": self.improved,
            "ci": list(self.ci),
            "delta": self.delta,
        }


class Scorer(ABC):
    """Scores one case output; aggregates case results into suite metrics and a headline score."""

    @abstractmethod
    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore: ...

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        return {
            "mean_case_score": _mean([r.score for r in results]),
            "pass_rate": _mean([float(r.passed) for r in results]),
        }

    def suite_score(self, metrics: Mapping[str, float], results: Sequence[CaseResult]) -> float:
        return _mean([r.score for r in results])


class LabelScorer(Scorer):
    """Single-label classification: exact (case-insensitive) label match; accuracy + macro-F1.

    ``expected_key`` names the label inside ``case.expected``; the subject may return the bare label
    or an object holding it under one of ``output_keys``. Labels outside ``labels`` score 0.
    """

    def __init__(self, *, expected_key: str, output_keys: Sequence[str], labels: Sequence[str]) -> None:
        self.expected_key = expected_key
        self.output_keys = tuple(output_keys)
        self.labels = frozenset(label.upper() for label in labels)

    def score_case(self, case: BenchmarkCase, output: Any) -> CaseScore:
        predicted = extract_label(output, self.output_keys)
        expected = str(case.expected[self.expected_key]).upper()
        valid = predicted is not None and predicted in self.labels
        ok = valid and predicted == expected
        return CaseScore(
            1.0 if ok else 0.0,
            ok,
            {"expected": expected, "predicted": predicted if valid else None, "invalid_output": not valid},
        )

    def aggregate(self, cases: Sequence[BenchmarkCase], results: Sequence[CaseResult]) -> dict[str, float]:
        expected = [str(c.expected[self.expected_key]).upper() for c in cases]
        predicted = [r.detail.get("predicted") for r in results]
        return {
            "accuracy": _mean([r.score for r in results]),
            "macro_f1": macro_f1(expected, predicted),
            "invalid_output_rate": _mean([float(r.detail.get("invalid_output", True)) for r in results]),
        }

    def suite_score(self, metrics: Mapping[str, float], results: Sequence[CaseResult]) -> float:
        return float(metrics["accuracy"])


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


class BenchmarkSuite:
    """A versioned benchmark: cases + scorer + metadata."""

    def __init__(
        self,
        *,
        key: str,
        name: str,
        version: str,
        component: str,
        description: str,
        cases: Sequence[BenchmarkCase],
        scorer: Scorer,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        if not cases:
            raise ValueError(f"{key}: a benchmark suite needs at least one case")
        ids = [c.id for c in cases]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{key}: duplicate case ids")
        self.key = key
        self.name = name
        self.version = version
        self.component = component
        self.description = description
        self.cases: tuple[BenchmarkCase, ...] = tuple(cases)
        self.scorer = scorer
        self.config: dict[str, Any] = dict(config or {})
        self._content_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        document = {
            "key": self.key,
            "version": self.version,
            "component": self.component,
            "cases": [c.model_dump(mode="json") for c in self.cases],
        }
        return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        """SHA-256 over key, version, component and cases (stable across processes)."""
        return self._content_hash

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def execute_case(self, case: BenchmarkCase, subject: Subject, *, seed: int) -> Any:
        """Obtain the subject's output for one case (override for suites that drive a process)."""
        return subject(copy.deepcopy(case.input))

    def run(self, subject: Subject, *, seed: int = 0) -> BenchResult:
        results: list[CaseResult] = []
        errors = 0
        for case in self.cases:
            case_seed = derive_seed(seed, self.key, case.id)
            try:
                output = self.execute_case(case, subject, seed=case_seed)
            except Exception as exc:
                errors += 1
                results.append(
                    CaseResult(
                        case_id=case.id,
                        score=0.0,
                        passed=False,
                        detail={"error": f"{type(exc).__name__}: {str(exc)[:_MAX_ERROR_CHARS]}"},
                    )
                )
                continue
            try:
                scored = self.scorer.score_case(case, output)
            except Exception as exc:
                errors += 1
                results.append(
                    CaseResult(
                        case_id=case.id,
                        score=0.0,
                        passed=False,
                        detail={"error": f"unscorable output ({type(exc).__name__}: {str(exc)[:_MAX_ERROR_CHARS]})"},
                    )
                )
                continue
            results.append(
                CaseResult(case_id=case.id, score=_clip01(scored.score), passed=scored.passed, detail=scored.detail)
            )
        metrics = {k: round(float(v), 12) for k, v in self.scorer.aggregate(self.cases, results).items()}
        score = round(_clip01(self.scorer.suite_score(metrics, results)), 12)
        return BenchResult(
            suite_key=self.key,
            suite_version=self.version,
            component=self.component,
            content_hash=self.content_hash,
            score=score,
            metrics=metrics,
            case_results=results,
            n_cases=len(results),
            n_passed=sum(1 for r in results if r.passed),
            n_errors=errors,
            seed=seed,
        )

    def manifest(self) -> dict[str, Any]:
        """Metadata for seeding ``benchmark_suites`` rows."""
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "component": self.component,
            "case_count": self.case_count,
            "content_hash": self.content_hash,
            "description": self.description,
            "config": dict(self.config),
        }


def compare(
    result: BenchResult,
    baseline: BenchResult,
    *,
    ci_level: float = 0.95,
    resamples: int = 2000,
    seed: int = 0,
) -> BenchComparison:
    """Pair ``result`` with ``baseline`` by case id; ``improved`` ⇔ the bootstrap CI of the mean
    per-case score difference lies entirely above zero (``regressed`` ⇔ entirely below)."""
    if (result.suite_key, result.suite_version) != (baseline.suite_key, baseline.suite_version):
        raise ValueError("results must come from the same suite key and version")
    if result.content_hash != baseline.content_hash:
        raise ValueError("results were produced from different case sets (content hash mismatch)")
    baseline_scores = {r.case_id: r.score for r in baseline.case_results}
    deltas = [r.score - baseline_scores[r.case_id] for r in result.case_results if r.case_id in baseline_scores]
    if not deltas:
        raise ValueError("no cases in common between the two results")
    mean_delta, lower, upper = bootstrap_mean_ci(
        deltas, ci_level=ci_level, resamples=resamples, seed=derive_seed(seed, "compare", result.suite_key)
    )
    return BenchComparison(
        suite_key=result.suite_key,
        suite_version=result.suite_version,
        score=result.score,
        baseline_score=baseline.score,
        delta=round(result.score - baseline.score, 12),
        mean_case_delta=round(mean_delta, 12),
        ci=(lower, upper),
        ci_level=ci_level,
        improved=lower > 0,
        regressed=upper < 0,
        n_paired=len(deltas),
        resamples=resamples,
        seed=seed,
    )


def load_fixture(filename: str) -> tuple[dict[str, Any], list[BenchmarkCase]]:
    """Load ``data/<filename>``; returns ``(metadata, cases)``.

    Raises ``ValueError`` unless the file is explicitly marked as a benchmark fixture.
    """
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError("fixture filename must be a plain file name")
    raw = resources.files("engines.lab.benchmarks").joinpath("data", filename).read_text(encoding="utf-8")
    document = json.loads(raw)
    if document.get("benchmark_fixture") is not True:
        raise ValueError(f"{filename} is not marked as a benchmark fixture")
    cases = [BenchmarkCase.model_validate(c) for c in document.get("cases", [])]
    metadata = {k: v for k, v in document.items() if k != "cases"}
    return metadata, cases
