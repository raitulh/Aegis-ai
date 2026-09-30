"""Benchmark subject adapters: map a strategy version, a platform component or an experiment onto the callable a
suite of :mod:`engines.lab.benchmarks` expects.

Every adapter is a *real* platform behaviour, never a lookup of the expected answers:

* ``failure_analysis_bench``   → :func:`engines.lab.failures.classify_failure` on the observed signals;
* ``experiment_design_bench``  → :class:`engines.lab.design_validator.ExperimentDesignValidator` on an
  :class:`~engines.lab.experiment_spec.ExperimentSpec` built from the design (the *experiment* strategy's
  ``n_seeds`` and ``baseline_policy`` set the statistical plan and the experiment kind);
* ``claim_verification_bench`` → :func:`engines.lab.verification.decide` on criteria derived from the checks;
* ``reproducibility_bench``    → the reproduction evaluator's tolerance rule and verdict;
* ``literature_bench``         → :func:`engines.lab.search.combine` over lexical hits, parameterized by the
  *search* strategy (``title_boost``, ``stemming``, ``max_results``, ``sources``, ``recency_half_life_days``);
* ``hypothesis_bench``         → the reference hypothesis quality checks, with the *hypothesis* strategy's
  ``testability_weight`` / ``evidence_weight`` as the fraction of supporting evidence each label requires;
* ``strategy_evolution_bench`` → :class:`~engines.lab.evolution.engine.EvolutionEngine` configured from the
  *optimization* strategy (or random / grid search, as the strategy selects);
* ``coding_experiment_bench``  → the outputs *recorded* by completed runs of an experiment (subject type
  ``experiment``). Nothing is fabricated: a task without a recorded run scores 0 with an explicit error.

The adapters are pure (no database access); :mod:`aegis_api.lab.strategies.benchmarks` loads what they need
(strategy parameters, recorded experiment outputs) and hands it over in a :class:`SubjectContext`.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from engines.lab.benchmarks.suites.hypothesis import reference_hypothesis_checks
from engines.lab.design_validator import ExperimentDesignValidator, ValidationReport
from engines.lab.evaluators.reproduction import reproduction_verdict, within_tolerance
from engines.lab.failures import FailureSignals, classify_failure
from engines.lab.search import FusionWeights, combine, recency_decay
from engines.lab.states import StrategyKind
from engines.lab.verification import (
    CheckResults,
    CriteriaProfile,
    Criterion,
    EvaluatorOutcome,
    ProvenanceReport,
    ReproductionOutcome,
    StatisticalEvidence,
    VerifierIdentity,
    decide,
)

Subject = Callable[[Any], Any]

SUBJECT_TYPES: tuple[str, ...] = ("strategy_version", "component", "experiment")

# Suites whose subject is parameterized by a strategy of this kind (the others measure a fixed component).
SUITE_STRATEGY_KIND: dict[str, str | None] = {
    "literature_bench": StrategyKind.SEARCH.value,
    "hypothesis_bench": StrategyKind.HYPOTHESIS.value,
    "experiment_design_bench": StrategyKind.EXPERIMENT.value,
    "strategy_evolution_bench": StrategyKind.OPTIMIZATION.value,
    "failure_analysis_bench": None,
    "claim_verification_bench": None,
    "reproducibility_bench": None,
    "coding_experiment_bench": None,
}
# Suites that only accept a specific subject type.
SUITE_SUBJECT_TYPES: dict[str, tuple[str, ...]] = {
    "coding_experiment_bench": ("experiment",),
}


class AdapterError(ValueError):
    """The subject cannot be benchmarked on this suite (wrong subject type, missing data)."""


@dataclass(frozen=True)
class SubjectContext:
    """Everything an adapter needs about the system under test (loaded by the benchmarks service)."""

    suite_key: str
    subject_type: str
    subject_id: str
    strategy_kind: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    # coding bench: task id → outputs recorded by a completed run of the experiment
    recorded_outputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    recorded_runs: Mapping[str, str] = field(default_factory=dict)  # task id → experiment run id

    def params_for(self, kind: str) -> Mapping[str, Any]:
        """The strategy parameters when the subject is a strategy of ``kind`` (else the defaults apply)."""
        return self.parameters if self.strategy_kind == kind else {}


def default_suites_for_kind(kind: str) -> list[str]:
    """Suites that are sensitive to strategies of ``kind`` (empty when no internal suite measures the kind)."""
    return sorted(key for key, k in SUITE_STRATEGY_KIND.items() if k == kind)


def suite_measures_kind(suite_key: str, kind: str) -> bool:
    return SUITE_STRATEGY_KIND.get(suite_key) == kind


# ---------------------------------------------------------------------------------------------
# Failure analysis → engines.lab.failures.classify_failure
# ---------------------------------------------------------------------------------------------
_STAGE_MAP: dict[str, str] = {
    "data": "data",
    "data_loading": "data",
    "data_validation": "data",
    "dataset": "data",
    "execution": "execution",
    "execution_submit": "execution",
    "run": "execution",
    "tool": "tool",
    "tool_call": "tool",
    "agent": "agent",
    "agent_step": "agent",
    "design": "design",
    "design_validation": "design",
    "evaluation": "evaluation",
    "analysis": "statistics",
    "statistics": "statistics",
    "reproduction": "reproduction",
    "strategy": "strategy",
    "strategy_review": "strategy",
    "policy": "policy",
}
_MAX_TEXT = 8_000


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def failure_signals_from_observation(observation: Mapping[str, Any]) -> FailureSignals:
    """Translate observed failure facts (stage, exit code, error, resources, context) into ``FailureSignals``."""
    resource = observation.get("resource") if isinstance(observation.get("resource"), Mapping) else {}
    context = observation.get("context") if isinstance(observation.get("context"), Mapping) else {}
    assert isinstance(resource, Mapping) and isinstance(context, Mapping)
    stage = _STAGE_MAP.get(str(observation.get("stage") or "execution").lower(), "execution")
    error_type = observation.get("error_type")
    message = observation.get("message")
    peak, limit = resource.get("memory_peak_mb"), resource.get("memory_limit_mb")
    oom = str(error_type or "").lower() in {"oomkilled", "outofmemory"} or (
        isinstance(peak, int | float) and isinstance(limit, int | float) and limit > 0 and peak >= limit
    )
    timed_out = bool(resource.get("timed_out")) or str(error_type or "").lower() in {"timeout", "timeouterror"}
    codes = context.get("validator_codes") or context.get("validation_issue_codes") or []
    n_seeds = _int_or_none(context.get("seeds", context.get("n_seeds")))
    required = _int_or_none(context.get("required_seeds"))
    tool = context.get("tool") or context.get("tool_name")
    policy = context.get("policy_decision")
    return FailureSignals(
        stage=stage,  # type: ignore[arg-type]
        exit_code=_int_or_none(observation.get("exit_code")),
        oom_killed=oom,
        timed_out=timed_out,
        error_message=str(message)[:_MAX_TEXT] if message else None,
        error_class=str(error_type)[:200] if error_type else None,
        logs=str(observation.get("logs"))[:_MAX_TEXT] if observation.get("logs") else None,
        traceback=str(observation.get("traceback"))[:_MAX_TEXT] if observation.get("traceback") else None,
        policy_decision=str(policy) if isinstance(policy, str) else None,
        validation_issue_codes=[str(c) for c in codes if isinstance(c, str)][:50],
        n_seeds=n_seeds if n_seeds is not None and n_seeds >= 0 else None,
        required_seeds=required if required is not None and required >= 0 else None,
        tool_name=str(tool)[:200] if isinstance(tool, str) else None,
        reproduction_verdict=str(context["reproduction_verdict"])
        if isinstance(context.get("reproduction_verdict"), str)
        else None,
    )


def failure_classification_subject(_context: SubjectContext) -> Subject:
    def subject(observation: Any) -> Any:
        if not isinstance(observation, Mapping):
            raise AdapterError("failure observation must be an object")
        classification = classify_failure(failure_signals_from_observation(observation))
        return {
            "failure_type": classification.failure_type.value,
            "subtype": classification.subtype,
            "rule": classification.rule_id,
            "confidence": classification.confidence,
        }

    return subject


# ---------------------------------------------------------------------------------------------
# Experiment design → engines.lab.design_validator
# ---------------------------------------------------------------------------------------------
DESIGN_DATASET_ID = "benchmark-dataset"
DESIGN_IMAGE = "python:3.12-slim"
BASELINE_POLICIES: tuple[str, ...] = ("required", "when_comparative", "optional")
_NAME_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")
_METRIC_SAFE = re.compile(r"[^A-Za-z0-9_.:/@-]+")
_MINIMIZE_TOKENS = frozenset(
    {
        "rmse",
        "mae",
        "mse",
        "loss",
        "latency",
        "cost",
        "error",
        "ece",
        "mce",
        "brier",
        "nll",
        "divergence",
        "perplexity",
        "time",
    }
)
_MAX_DESIGN_SEEDS = 1000


def _safe_name(raw: Any, pattern: re.Pattern[str], fallback: str) -> str:
    text = pattern.sub("_", str(raw or "")).strip("_") or fallback
    if not (text[0].isalpha() or text[0] == "_"):
        text = f"m_{text}"
    return text[:120]


def _metric_direction(name: str) -> str:
    tokens = set(re.split(r"[^a-z0-9]+", name.lower()))
    return "minimize" if tokens & _MINIMIZE_TOKENS else "maximize"


def design_to_spec(design: Mapping[str, Any], parameters: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Represent a benchmark design as an ``ExperimentSpec`` document plus its ``ValidationContext``.

    ``parameters`` are the experiment strategy's: ``n_seeds`` (replicates the statistical plan requires) and
    ``baseline_policy`` (``required``: every design needs a control arm; ``when_comparative``: only designs with
    several arms; ``optional``: a design without a control is exploratory).
    """
    n_seeds = parameters.get("n_seeds", 3)
    required_seeds = int(n_seeds) if isinstance(n_seeds, int) and not isinstance(n_seeds, bool) else 3
    policy = parameters.get("baseline_policy", "required")
    policy = policy if policy in BASELINE_POLICIES else "required"

    arms = [a for a in (design.get("arms") or []) if isinstance(a, Mapping)]
    arm_names = [str(a.get("name")) for a in arms]
    control = design.get("control_arm")
    has_control = isinstance(control, str) and control in arm_names
    if policy == "required":
        kind = "candidate"
    elif policy == "when_comparative":
        kind = "candidate" if (has_control or len(arms) > 1) else "exploratory"
    else:
        kind = "candidate" if has_control else "exploratory"
    baseline: dict[str, Any] = (
        {"kind": "experiment", "experiment_id": f"arm:{_safe_name(control, _NAME_SAFE, 'control')}"}
        if has_control
        else {"kind": "none"}
    )

    factors: dict[str, list[Any]] = {}
    for arm in arms:
        if has_control and arm.get("name") == control:
            continue
        changes = arm.get("changes") if isinstance(arm.get("changes"), Mapping) else {}
        assert isinstance(changes, Mapping)
        for factor, value in changes.items():
            values = factors.setdefault(_safe_name(factor, _NAME_SAFE, "factor"), [])
            if value not in values:
                values.append(value)
    variables = [{"name": name, "role": "independent", "values": values} for name, values in sorted(factors.items())]

    metric_names: list[str] = []
    primary_raw = design.get("primary_metric")
    primary = _safe_name(primary_raw, _METRIC_SAFE, "metric") if isinstance(primary_raw, str) and primary_raw else None
    if primary:
        metric_names.append(primary)
    for raw in design.get("secondary_metrics") or []:
        name = _safe_name(raw, _METRIC_SAFE, "metric")
        if name not in metric_names:
            metric_names.append(name)
    metrics = [
        {"name": name, "direction": _metric_direction(name), "primary": name == primary, "source": "platform"}
        for name in metric_names
    ]
    relative = "baseline" if has_control else "absolute"
    criteria = [
        {
            "metric": m["name"],
            "comparator": ("delta_gt" if m["direction"] == "maximize" else "delta_lt")
            if relative == "baseline"
            else ("gt" if m["direction"] == "maximize" else "lt"),
            "threshold": 0.0,
            "relative_to": relative,
        }
        for m in metrics
    ]
    correction_raw = design.get("multiple_comparison_correction")
    correction = "none"
    if isinstance(correction_raw, str) and correction_raw.strip() and correction_raw.strip().lower() != "none":
        correction = correction_raw.strip().lower() if correction_raw.strip().lower() in ("holm", "bh") else "holm"
    effect = design.get("expected_effect_size")
    plan: dict[str, Any] = {"n_seeds": min(max(required_seeds, 1), _MAX_DESIGN_SEEDS), "correction": correction}
    if design.get("power_analysis") is True and isinstance(effect, int | float) and not isinstance(effect, bool):
        plan["min_effect_size"] = max(0.0, float(effect))

    declared = _int_or_none(design.get("seeds")) or 0
    seeds = list(range(1, min(max(declared, 0), _MAX_DESIGN_SEEDS) + 1))

    dataset = design.get("dataset") if isinstance(design.get("dataset"), Mapping) else {}
    assert isinstance(dataset, Mapping)
    fit_on_all = str(dataset.get("preprocessing_fit_on") or "train").lower() == "all"
    tuning_on = str(dataset.get("tuning_on") or "").lower()
    datasets: list[dict[str, Any]] = [
        {
            "dataset_version_id": DESIGN_DATASET_ID,
            "split": None if fit_on_all else "train",
            "role": "train",
            "mount_path": "data/train",
        },
        {"dataset_version_id": DESIGN_DATASET_ID, "split": "test", "role": "test", "mount_path": "data/test"},
    ]
    if tuning_on == "validation":
        datasets.append(
            {
                "dataset_version_id": DESIGN_DATASET_ID,
                "split": "validation",
                "role": "validation",
                "mount_path": "data/validation",
            }
        )
    design_parameters: dict[str, Any] = {
        key: design[key]
        for key in ("units_per_arm", "expected_effect_size", "power_analysis", "assignment")
        if key in design and (design[key] is None or isinstance(design[key], str | int | float | bool))
    }
    split_kind = dataset.get("split")
    if isinstance(split_kind, str):
        design_parameters["split"] = split_kind
    if isinstance(dataset.get("group_key"), str):
        design_parameters["group_key"] = dataset["group_key"]
    if tuning_on == "test":
        design_parameters["hyperparameter_search"] = True

    spec = {
        "objective": str(design.get("objective") or "Benchmark design")[:4000],
        "kind": kind,
        "baseline": baseline,
        "method": f"Design under review: {str(design.get('objective') or 'benchmark design')[:1000]}",
        "variables": variables,
        "metrics": metrics,
        "success_criteria": criteria,
        "statistical_plan": plan,
        "seeds": seeds,
        "environment": {"image": DESIGN_IMAGE},
        "datasets": datasets,
        "parameters": design_parameters,
    }
    context: dict[str, Any] = {
        "dataset_versions": {DESIGN_DATASET_ID: {"splits": {"train": {}, "validation": {}, "test": {}}}},
    }
    return spec, context


def design_issue_codes(report: ValidationReport) -> list[str]:
    """Map validator issues onto the design-flaw vocabulary of ExperimentDesignBench."""
    codes: set[str] = set()
    for issue in report.issues:
        if issue.code == "INVALID_SPEC":
            raise AdapterError(f"design cannot be represented as an experiment specification ({issue.field})")
        if issue.code == "MISSING_BASELINE":
            codes.add("MISSING_CONTROL")
        elif issue.code in ("NO_PRIMARY_METRIC", "MISSING_METRICS"):
            codes.add("NO_PRIMARY_METRIC")
        elif issue.code == "MISSING_REPRODUCIBILITY" and issue.field == "seeds":
            codes.add("INSUFFICIENT_SEEDS")
        elif issue.code == "STATISTICAL_PLAN_WEAK" and issue.field == "statistical_plan.correction":
            codes.add("MULTIPLE_COMPARISONS_UNCORRECTED")
        elif issue.code == "DATA_LEAKAGE" and issue.severity == "error":
            codes.add("DATA_LEAKAGE")
    return sorted(codes)


def design_validation_subject(context: SubjectContext) -> Subject:
    parameters = context.params_for(StrategyKind.EXPERIMENT.value)
    validator = ExperimentDesignValidator()

    def subject(design: Any) -> Any:
        if not isinstance(design, Mapping):
            raise AdapterError("design must be an object")
        spec, validation_context = design_to_spec(design, parameters)
        report = validator.validate(spec, validation_context)
        return {"issues": design_issue_codes(report)}

    return subject


# ---------------------------------------------------------------------------------------------
# Claim verification → engines.lab.verification.decide
# ---------------------------------------------------------------------------------------------
_C = Criterion
CLAIM_CHECK_CRITERIA: dict[str, Criterion] = {
    "statistical_significance": _C.STATISTICALLY_SUPPORTED,
    "significance": _C.STATISTICALLY_SUPPORTED,
    "effect_size_ci": _C.STATISTICALLY_SUPPORTED,
    "reproduction": _C.REPLICATED,
    "replication": _C.REPLICATED,
    "independent_replication": _C.REPLICATED,
    "baseline_comparison": _C.BASELINE_VALIDATED,
    "baseline": _C.BASELINE_VALIDATED,
    "provenance": _C.PROVENANCE_COMPLETE,
    "lineage": _C.PROVENANCE_COMPLETE,
    "independent_verifier": _C.INDEPENDENT_VERIFIER,
    "literature_support": _C.NO_CONTRADICTING_EVIDENCE,
    "contradiction_search": _C.NO_CONTRADICTING_EVIDENCE,
}


def _criterion_state(statuses: Sequence[str]) -> str | None:
    if "failed" in statuses:
        return "failed"
    if "passed" in statuses:
        return "passed"
    return None


def claim_checks_to_engine(claim: Mapping[str, Any]) -> tuple[CriteriaProfile | str, CheckResults]:
    """Translate generic named checks into the verification engine's criteria inputs.

    Well-known check names map onto their criterion; any other named check is an independent evaluator
    (``passed``/``failed``; ``pending`` checks are simply not yet available). Required checks make their
    criterion required; with no required check the engine's ``default`` profile applies.
    """
    profile = claim.get("profile") if isinstance(claim.get("profile"), Mapping) else {}
    assert isinstance(profile, Mapping)
    checks = [c for c in (claim.get("checks") or []) if isinstance(c, Mapping)]
    by_criterion: dict[Criterion, list[str]] = {}
    required: set[Criterion] = set()
    evaluators: list[EvaluatorOutcome] = []
    for check in checks:
        name = str(check.get("name") or "check").strip().lower()
        status = str(check.get("status") or "pending").strip().lower()
        criterion = CLAIM_CHECK_CRITERIA.get(name)
        if criterion is None:
            criterion = _C.EVALUATOR_PASSED
            if status in ("passed", "failed"):
                evaluators.append(EvaluatorOutcome(key=name[:80] or "check", passed=status == "passed"))
        else:
            by_criterion.setdefault(criterion, []).append(status)
        if check.get("required", True):
            required.add(criterion)

    reproductions: list[ReproductionOutcome] = []
    for status in by_criterion.get(_C.REPLICATED, []):
        if status in ("passed", "failed"):
            reproductions.append(ReproductionOutcome(verdict="reproduced" if status == "passed" else "not_reproduced"))
    statistics: StatisticalEvidence | None = None
    stat_state = _criterion_state(by_criterion.get(_C.STATISTICALLY_SUPPORTED, []))
    if stat_state == "passed":
        statistics = StatisticalEvidence(p_value=0.001, ci_low=0.05, ci_high=0.25, claimed_direction="increase")
    elif stat_state == "failed":  # not significant: the CI of the effect includes zero
        statistics = StatisticalEvidence(p_value=0.5, ci_low=-0.1, ci_high=0.1, claimed_direction="increase")
    baseline_state = _criterion_state(by_criterion.get(_C.BASELINE_VALIDATED, []))
    provenance_state = _criterion_state(by_criterion.get(_C.PROVENANCE_COMPLETE, []))
    verifier_state = _criterion_state(by_criterion.get(_C.INDEPENDENT_VERIFIER, []))
    contradiction_state = _criterion_state(by_criterion.get(_C.NO_CONTRADICTING_EVIDENCE, []))
    verifier: VerifierIdentity | None = None
    if verifier_state is not None:
        verifier = VerifierIdentity(
            generator_actor_ids=["generator"],
            verifier_actor_ids=["verifier" if verifier_state == "passed" else "generator"],
        )
    evidence_count = _int_or_none(profile.get("evidence_count")) or 0
    results = CheckResults(
        reproductions=reproductions,
        statistics=statistics,
        baseline_runs_completed=True if baseline_state is not None else None,
        baseline_evaluator_passed=None if baseline_state is None else baseline_state == "passed",
        evaluators=evaluators,
        provenance=None
        if provenance_state is None
        else ProvenanceReport(
            complete=provenance_state == "passed", missing=[] if provenance_state == "passed" else ["lineage"]
        ),
        verifier=verifier,
        supporting_evidence_count=max(0, evidence_count),
        contradicting_evidence_count=None if contradiction_state is None else int(contradiction_state == "failed"),
    )
    if not required:
        return "default", results
    ordered = tuple(c for c in Criterion if c in required)
    return (
        CriteriaProfile(
            key="benchmark_claim",
            description="Criteria required by the claim's own verification checks.",
            required=ordered,
            min_reproductions=1,
        ),
        results,
    )


def claim_verification_subject(_context: SubjectContext) -> Subject:
    def subject(claim: Any) -> Any:
        if not isinstance(claim, Mapping):
            raise AdapterError("claim must be an object")
        profile = claim.get("profile") if isinstance(claim.get("profile"), Mapping) else {}
        assert isinstance(profile, Mapping)
        # A claim without attached evidence is never evaluated: it stays UNVERIFIED.
        if (_int_or_none(profile.get("evidence_count")) or 0) <= 0:
            return {"status": "UNVERIFIED", "rationale": "no evidence is attached to the claim"}
        criteria_profile, checks = claim_checks_to_engine(claim)
        verdict = decide(criteria_profile, checks)
        return {"status": verdict.status.value, "confidence": verdict.confidence}

    return subject


# ---------------------------------------------------------------------------------------------
# Reproduction decision → reproduction evaluator rule
# ---------------------------------------------------------------------------------------------
def reproduction_decision(case: Mapping[str, Any]) -> str:
    """The reproduction evaluator's decision for one original/reproduced pair."""
    original = case.get("original") if isinstance(case.get("original"), Mapping) else {}
    reproduced = case.get("reproduced") if isinstance(case.get("reproduced"), Mapping) else {}
    tolerance = case.get("tolerance") if isinstance(case.get("tolerance"), Mapping) else {}
    assert isinstance(original, Mapping) and isinstance(reproduced, Mapping) and isinstance(tolerance, Mapping)
    min_runs = _int_or_none(case.get("min_runs")) or 1
    if (_int_or_none(reproduced.get("runs")) or 0) < min_runs:
        return "INCONCLUSIVE"
    original_metrics = original.get("metrics") if isinstance(original.get("metrics"), Mapping) else {}
    reproduced_metrics = reproduced.get("metrics") if isinstance(reproduced.get("metrics"), Mapping) else {}
    assert isinstance(original_metrics, Mapping) and isinstance(reproduced_metrics, Mapping)
    abs_tol = float(tolerance.get("absolute") or 0.0)
    rel_tol = float(tolerance.get("relative") or 0.0)
    outcomes: dict[str, bool] = {}
    for name, value in original_metrics.items():
        other = reproduced_metrics.get(name)
        if not isinstance(value, int | float) or not isinstance(other, int | float):
            continue
        if not (math.isfinite(float(value)) and math.isfinite(float(other))):
            outcomes[str(name)] = False
            continue
        ok, _ = within_tolerance(float(value), float(other), abs_tol=abs_tol, rel_tol=rel_tol)
        outcomes[str(name)] = ok
    return reproduction_verdict(outcomes, None).upper()


def reproduction_subject(_context: SubjectContext) -> Subject:
    def subject(case: Any) -> Any:
        if not isinstance(case, Mapping):
            raise AdapterError("reproduction case must be an object")
        return {"verdict": reproduction_decision(case)}

    return subject


# ---------------------------------------------------------------------------------------------
# Literature retrieval → engines.lab.search.combine
# ---------------------------------------------------------------------------------------------
SEARCH_SOURCES: tuple[str, ...] = ("openalex", "arxiv")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "we",
        "that",
        "this",
        "their",
        "its",
        "was",
        "were",
        "than",
        "not",
    }
)
_SUFFIXES: tuple[str, ...] = ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s")


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def _terms(text: str, *, stemming: bool) -> set[str]:
    tokens = {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS}
    return {_stem(t) for t in tokens} if stemming else tokens


def _published(doc: Mapping[str, Any]) -> date | None:
    raw = doc.get("published_at") or doc.get("published") or doc.get("date")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    year = doc.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 9999:
        return date(year, 7, 1)
    return None


def rank_documents(case: Mapping[str, Any], parameters: Mapping[str, Any]) -> list[str]:
    """Rank a case's documents for its query with the search strategy's parameters."""
    stemming = bool(parameters.get("stemming", False))
    title_boost = float(parameters.get("title_boost", 1.0))
    max_results = int(parameters.get("max_results", 20))
    half_life = float(parameters.get("recency_half_life_days", 730))
    sources = parameters.get("sources") or list(SEARCH_SOURCES)
    query = _terms(str(case.get("query") or ""), stemming=stemming)
    if not query:
        return []
    documents = [d for d in (case.get("documents") or []) if isinstance(d, Mapping) and d.get("id")]
    # Documents that declare a source outside the strategy's sources are not retrieved at all.
    documents = [d for d in documents if not isinstance(d.get("source"), str) or d["source"] in sources]
    hits: dict[str, float] = {}
    for doc in documents:
        body = _terms(str(doc.get("text") or ""), stemming=stemming)
        title = _terms(str(doc.get("title") or ""), stemming=stemming)
        score = len(query & body) / len(query) + title_boost * len(query & title) / len(query)
        if score > 0:
            hits[str(doc["id"])] = score
    dated = {str(d["id"]): _published(d) for d in documents}
    known_dates = [d for d in dated.values() if d is not None]
    recency: dict[str, float] | None = None
    if known_dates:
        reference = max(known_dates)  # deterministic "now": the newest document of the case
        recency = {
            doc_id: recency_decay(published, reference, half_life)
            for doc_id, published in dated.items()
            if published is not None
        }
    ranked = combine(hits, None, recency=recency, weights=FusionWeights(), limit=max(1, max_results))
    return [hit.id for hit in ranked]


def retrieval_subject(context: SubjectContext) -> Subject:
    parameters = context.params_for(StrategyKind.SEARCH.value)

    def subject(case: Any) -> Any:
        if not isinstance(case, Mapping):
            raise AdapterError("retrieval case must be an object")
        return {"ranked_ids": rank_documents(case, parameters)}

    return subject


# ---------------------------------------------------------------------------------------------
# Hypothesis quality → reference checks with the hypothesis strategy's evidence thresholds
# ---------------------------------------------------------------------------------------------
def hypothesis_quality(hypothesis: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, bool]:
    """Quality labels of one hypothesis under a hypothesis strategy.

    ``testability_weight`` is the fraction of falsifiability evidence (a directional prediction, an explicit
    falsification criterion) required; ``evidence_weight`` the fraction of measurability evidence (a named
    metric, a quantitative dependent variable). Hedged or tautological statements are never falsifiable.
    """
    testability = float(parameters.get("testability_weight", 0.5))
    evidence = float(parameters.get("evidence_weight", 0.5))
    criterion = str(hypothesis.get("falsification_criterion") or "").strip()
    reference = reference_hypothesis_checks(hypothesis)
    well_formed = reference_hypothesis_checks({**hypothesis, "falsification_criterion": "stated"})["falsifiable"]
    directional = reference_hypothesis_checks({**hypothesis, "falsification_criterion": ""})["falsifiable"]
    falsifiability = (float(directional) + float(bool(criterion))) / 2
    falsifiable = well_formed and falsifiability > 0 and falsifiability + 1e-9 >= testability
    dependent = str(hypothesis.get("dependent_variable") or "")
    quantitative = bool(dependent) and reference_hypothesis_checks({"dependent_variable": dependent})["measurable"]
    measurability = (float(reference["metric_named"]) + float(quantitative)) / 2
    measurable = measurability > 0 and measurability + 1e-9 >= evidence
    return {"falsifiable": falsifiable, "measurable": measurable, "metric_named": reference["metric_named"]}


def hypothesis_quality_subject(context: SubjectContext) -> Subject:
    parameters = context.params_for(StrategyKind.HYPOTHESIS.value)

    def subject(hypothesis: Any) -> Any:
        if not isinstance(hypothesis, Mapping):
            raise AdapterError("hypothesis must be an object")
        return hypothesis_quality(hypothesis, parameters)

    return subject


# ---------------------------------------------------------------------------------------------
# Strategy evolution → EvolutionEngine / random / grid search per the optimization strategy
# ---------------------------------------------------------------------------------------------
SEARCH_METHODS: tuple[str, ...] = ("random", "grid", "evolutionary")
_MAX_SOLUTIONS = 5000


def _case_seed(case: Mapping[str, Any], parameters: Mapping[str, Any]) -> int:
    document = json.dumps({"case": case, "parameters": parameters}, sort_keys=True, default=str)
    return int(hashlib.sha256(document.encode("utf-8")).hexdigest()[:8], 16)


def optimizer_output(case: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Engine overrides (evolutionary) or decision vectors (random/grid) for one evolution-bench case."""
    method = parameters.get("search_method", "evolutionary")
    fraction = float(parameters.get("budget_fraction", 1.0))
    fraction = min(1.0, max(0.01, fraction))
    population = int(case.get("population") or 2)
    generations = int(case.get("generations") or 0)
    dims = int(case.get("dims") or 2)
    if method == "evolutionary":
        overrides: dict[str, Any] = {"offspring_size": max(1, round(population * fraction))}
        if isinstance(parameters.get("mutation_scale"), int | float):
            overrides["mutation_scale"] = float(parameters["mutation_scale"])
        if isinstance(parameters.get("crossover_rate"), int | float):
            overrides["crossover_rate"] = float(parameters["crossover_rate"])
        return overrides
    budget = max(1, min(_MAX_SOLUTIONS, round(fraction * population * (generations + 1))))
    if method == "grid":
        per_dim = max(2, math.floor(budget ** (1 / dims)))
        while per_dim > 2 and per_dim**dims > budget:
            per_dim -= 1
        if per_dim**dims > _MAX_SOLUTIONS:
            per_dim = 1
        axis = [i / (per_dim - 1) for i in range(per_dim)] if per_dim > 1 else [0.5]
        solutions: list[list[float]] = [[]]
        for _ in range(dims):
            solutions = [[*s, v] for s in solutions for v in axis]
        return {"solutions": solutions[:_MAX_SOLUTIONS]}
    rng = random.Random(_case_seed(case, parameters))
    return {"solutions": [[rng.random() for _ in range(dims)] for _ in range(budget)]}


def evolution_subject(context: SubjectContext) -> Subject:
    parameters = context.params_for(StrategyKind.OPTIMIZATION.value)

    def subject(case: Any) -> Any:
        if not isinstance(case, Mapping):
            raise AdapterError("evolution case must be an object")
        return optimizer_output(case, parameters)

    return subject


# ---------------------------------------------------------------------------------------------
# Coding experiments → recorded outputs of completed experiment runs
# ---------------------------------------------------------------------------------------------
def recorded_outputs_subject(context: SubjectContext) -> Subject:
    if context.subject_type != "experiment":
        raise AdapterError(
            "coding_experiment_bench scores the outputs recorded by completed experiment runs; "
            "benchmark it with subject_type 'experiment'"
        )
    recorded = context.recorded_outputs

    def subject(task: Any) -> Any:
        if not isinstance(task, Mapping):
            raise AdapterError("coding task must be an object")
        task_id = str(task.get("task_id") or "")
        outputs = recorded.get(task_id)
        if outputs is None:
            raise LookupError(f"no completed run of the experiment recorded outputs for task {task_id!r}")
        return {"outputs": dict(outputs)}

    return subject


ADAPTERS: dict[str, Callable[[SubjectContext], Subject]] = {
    "failure_analysis_bench": failure_classification_subject,
    "experiment_design_bench": design_validation_subject,
    "claim_verification_bench": claim_verification_subject,
    "reproducibility_bench": reproduction_subject,
    "literature_bench": retrieval_subject,
    "hypothesis_bench": hypothesis_quality_subject,
    "strategy_evolution_bench": evolution_subject,
    "coding_experiment_bench": recorded_outputs_subject,
}


def build_subject(context: SubjectContext) -> Subject:
    """The system under test for ``context.suite_key`` (raises :class:`AdapterError` when not applicable)."""
    factory = ADAPTERS.get(context.suite_key)
    if factory is None:
        raise AdapterError(f"no subject adapter for benchmark suite {context.suite_key!r}")
    allowed = SUITE_SUBJECT_TYPES.get(context.suite_key)
    if allowed is not None and context.subject_type not in allowed:
        raise AdapterError(f"{context.suite_key} accepts subject types: {', '.join(allowed)}")
    return factory(context)
