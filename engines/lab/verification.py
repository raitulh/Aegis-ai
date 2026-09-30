"""Verification criteria and the claim-status decision.

A scientific claim is judged against a *criteria profile* — the list of criteria that must all be satisfied
before the claim may be called ``VERIFIED``, plus the thresholds those criteria use. Each criterion evaluates to
``satisfied``, ``failed`` or ``missing`` (not checked yet / not enough information) from deterministic inputs
(:class:`CheckResults`):

``replicated``                ≥ ``min_reproductions`` independent reproductions with verdict ``reproduced`` and
                              none ``not_reproduced``
``statistically_supported``   adjusted p ≤ alpha AND the CI of the effect excludes 0 in the claimed direction
``baseline_validated``        baseline runs completed AND the baseline evaluator passed
``evaluator_passed``          ≥ ``min_independent_evaluators`` independent evaluators passed and none failed
``provenance_complete``       the lineage completeness report (computed elsewhere) is complete
``independent_verifier``      the verifier's agent versions, models and actors are disjoint from the generator's
``no_contradicting_evidence`` a contradiction search ran and found nothing

Decision (first matching rule wins):

1. ``CANDIDATE`` — nothing has been checked yet (every criterion ``missing``);
2. ``REJECTED`` — reproductions failed with no successful reproduction, or the effect is significant in the
   *opposite* direction (CI entirely on the wrong side of zero);
3. ``CONTESTED`` — contradicting evidence (contradicting sources, mixed reproduction verdicts, or a failing
   independent evaluator) exists — alongside supporting evidence, or on its own;
4. ``VERIFIED`` — every required criterion is satisfied;
5. ``PARTIALLY_VERIFIED`` — at least one, but not every, required criterion is satisfied;
6. ``CANDIDATE`` — otherwise (checked, but no required criterion satisfied yet).

A model judgment (e.g. a verifier agent's opinion) never satisfies a criterion and never changes the status;
it can only move the confidence by at most ±``MODEL_JUDGMENT_WEIGHT`` within the status' band.

Confidence (transparent, recorded in ``confidence_components``)::

    support    = (Σ satisfied required + ½ Σ satisfied optional) / (|required| + ½ |optional|)
    penalised  = support × ½^(number of failed criteria)
    adjusted   = penalised ± MODEL_JUDGMENT_WEIGHT × judgment.confidence   (sign = judgment.supports)
    confidence = clamp(adjusted, 0, STATUS_CAP[status])
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engines.lab.states import ClaimStatus

VERIFICATION_ENGINE_VERSION = "verification-1.0.0"
MODEL_JUDGMENT_WEIGHT = 0.1
STATUS_CAP: dict[str, float] = {
    ClaimStatus.VERIFIED: 1.0,
    ClaimStatus.PARTIALLY_VERIFIED: 0.8,
    ClaimStatus.CONTESTED: 0.5,
    ClaimStatus.CANDIDATE: 0.5,
    ClaimStatus.UNVERIFIED: 0.3,
    ClaimStatus.REJECTED: 0.1,
}


class Criterion(StrEnum):
    REPLICATED = "replicated"
    STATISTICALLY_SUPPORTED = "statistically_supported"
    BASELINE_VALIDATED = "baseline_validated"
    EVALUATOR_PASSED = "evaluator_passed"
    PROVENANCE_COMPLETE = "provenance_complete"
    INDEPENDENT_VERIFIER = "independent_verifier"
    NO_CONTRADICTING_EVIDENCE = "no_contradicting_evidence"


ALL_CRITERIA: tuple[Criterion, ...] = tuple(Criterion)
CriterionState = Literal["satisfied", "failed", "missing"]


class CriteriaProfile(BaseModel):
    """Required criteria and thresholds for one kind of claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    description: str
    required: tuple[Criterion, ...]
    min_reproductions: int = Field(default=1, ge=1)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    min_independent_evaluators: int = Field(default=1, ge=1)

    @field_validator("required")
    @classmethod
    def _non_empty(cls, value: tuple[Criterion, ...]) -> tuple[Criterion, ...]:
        if not value:
            raise ValueError("a profile must require at least one criterion")
        if len(set(value)) != len(value):
            raise ValueError("duplicate criteria")
        return value

    @property
    def optional(self) -> tuple[Criterion, ...]:
        return tuple(c for c in ALL_CRITERIA if c not in self.required)


C = Criterion
CRITERIA_PROFILES: dict[str, CriteriaProfile] = {
    "default": CriteriaProfile(
        key="default",
        description="General claims: replicated, statistically supported, independently evaluated, full provenance.",
        required=(
            C.REPLICATED,
            C.STATISTICALLY_SUPPORTED,
            C.EVALUATOR_PASSED,
            C.PROVENANCE_COMPLETE,
            C.NO_CONTRADICTING_EVIDENCE,
        ),
        min_reproductions=1,
        alpha=0.05,
        min_independent_evaluators=1,
    ),
    "ml_benchmark": CriteriaProfile(
        key="ml_benchmark",
        description=(
            "Benchmark improvements: every criterion, including a validated baseline and an independent verifier."
        ),
        required=ALL_CRITERIA,
        min_reproductions=1,
        alpha=0.05,
        min_independent_evaluators=1,
    ),
    "simulation": CriteriaProfile(
        key="simulation",
        description=(
            "Simulation results: cheap to re-run, so two reproductions and a stricter alpha; no external baseline "
            "evaluator is required."
        ),
        required=(
            C.REPLICATED,
            C.STATISTICALLY_SUPPORTED,
            C.PROVENANCE_COMPLETE,
            C.INDEPENDENT_VERIFIER,
            C.NO_CONTRADICTING_EVIDENCE,
        ),
        min_reproductions=2,
        alpha=0.01,
        min_independent_evaluators=1,
    ),
    "computational_science": CriteriaProfile(
        key="computational_science",
        description=(
            "Deterministic computational results: two independent reproductions, an independent evaluator "
            "(e.g. numerical verification) and verifier; statistics are optional."
        ),
        required=(
            C.REPLICATED,
            C.EVALUATOR_PASSED,
            C.BASELINE_VALIDATED,
            C.PROVENANCE_COMPLETE,
            C.INDEPENDENT_VERIFIER,
            C.NO_CONTRADICTING_EVIDENCE,
        ),
        min_reproductions=2,
        alpha=0.05,
        min_independent_evaluators=1,
    ),
}


def get_profile(profile: str | CriteriaProfile) -> CriteriaProfile:
    if isinstance(profile, CriteriaProfile):
        return profile
    try:
        return CRITERIA_PROFILES[profile]
    except KeyError as exc:
        raise KeyError(f"unknown criteria profile {profile!r}; known: {', '.join(sorted(CRITERIA_PROFILES))}") from exc


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ReproductionOutcome(_In):
    id: str | None = None
    verdict: Literal["reproduced", "partially_reproduced", "not_reproduced", "inconclusive"]
    independent: bool = True


class StatisticalEvidence(_In):
    """Statistics of the claimed effect (``candidate - baseline`` of the claim's metric)."""

    p_value: float | None = Field(default=None, ge=0, le=1)
    p_value_adjusted: float | None = Field(default=None, ge=0, le=1)
    alpha: float | None = Field(default=None, gt=0, lt=1)
    ci_low: float | None = None
    ci_high: float | None = None
    effect_size: float | None = None
    claimed_direction: Literal["increase", "decrease"] = "increase"

    @model_validator(mode="before")
    @classmethod
    def _direction_alias(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            direction = data.get("claimed_direction")
            if direction in ("maximize", "minimize"):
                data = {**data, "claimed_direction": "increase" if direction == "maximize" else "decrease"}
        return data

    @property
    def p(self) -> float | None:
        return self.p_value_adjusted if self.p_value_adjusted is not None else self.p_value


class EvaluatorOutcome(_In):
    key: str
    passed: bool | None
    independent: bool = True
    confidence: float | None = Field(default=None, ge=0, le=1)


class ProvenanceReport(_In):
    complete: bool
    missing: list[str] = Field(default_factory=list)


class VerifierIdentity(_In):
    """Who generated the claim and who verified it (agent version ids, model ids, actor ids)."""

    generator_agent_version_ids: list[str] = Field(default_factory=list)
    generator_models: list[str] = Field(default_factory=list)
    generator_actor_ids: list[str] = Field(default_factory=list)
    verifier_agent_version_ids: list[str] = Field(default_factory=list)
    verifier_models: list[str] = Field(default_factory=list)
    verifier_actor_ids: list[str] = Field(default_factory=list)
    verifier_is_human: bool = False


class ModelJudgment(_In):
    """An LLM verifier's opinion. Advisory only: it can nudge confidence, never the status."""

    supports: bool
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=4000)
    model: str | None = None


class CheckResults(_In):
    reproductions: list[ReproductionOutcome] = Field(default_factory=list)
    statistics: StatisticalEvidence | None = None
    baseline_runs_completed: bool | None = None
    baseline_evaluator_passed: bool | None = None
    evaluators: list[EvaluatorOutcome] = Field(default_factory=list)
    provenance: ProvenanceReport | None = None
    verifier: VerifierIdentity | None = None
    supporting_evidence_count: int = Field(default=0, ge=0)
    contradicting_evidence_count: int | None = Field(default=None, ge=0)
    model_judgment: ModelJudgment | None = None


# ---------------------------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------------------------
class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Criterion
    state: CriterionState
    required: bool
    detail: str


class VerdictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClaimStatus
    confidence: float = Field(ge=0, le=1)
    satisfied: list[str]
    missing: list[str]
    failed: list[str]
    rationale: str
    profile: str
    criteria: dict[str, CriterionResult]
    confidence_components: dict[str, float]
    engine_version: str = VERIFICATION_ENGINE_VERSION


# ---------------------------------------------------------------------------------------------
# Criterion evaluation
# ---------------------------------------------------------------------------------------------
def _replicated(checks: CheckResults, profile: CriteriaProfile) -> tuple[CriterionState, str]:
    independent = [r for r in checks.reproductions if r.independent]
    if not independent:
        return "missing", "no independent reproduction yet"
    ok = sum(1 for r in independent if r.verdict == "reproduced")
    failed = sum(1 for r in independent if r.verdict == "not_reproduced")
    partial = sum(1 for r in independent if r.verdict == "partially_reproduced")
    if failed:
        return "failed", f"{failed} reproduction(s) did not reproduce the result ({ok} did)"
    if ok >= profile.min_reproductions:
        return "satisfied", f"{ok} independent reproduction(s) reproduced the result"
    if partial and ok + partial >= profile.min_reproductions:
        return "failed", f"only partial reproduction ({partial} partial, {ok} full)"
    return "missing", f"{ok} of {profile.min_reproductions} required reproduction(s)"


def _effect_reversed(stats: StatisticalEvidence, alpha: float) -> bool:
    p = stats.p
    if p is None or p > alpha or stats.ci_low is None or stats.ci_high is None:
        return False
    if stats.claimed_direction == "increase":
        return stats.ci_high < 0
    return stats.ci_low > 0


def _statistically_supported(checks: CheckResults, profile: CriteriaProfile) -> tuple[CriterionState, str]:
    stats = checks.statistics
    if stats is None or stats.p is None:
        return "missing", "no statistical test result"
    alpha = min(profile.alpha, stats.alpha) if stats.alpha is not None else profile.alpha
    if _effect_reversed(stats, alpha):
        return (
            "failed",
            f"significant effect in the opposite direction (p={stats.p:.3g}, CI [{stats.ci_low}, {stats.ci_high}])",
        )
    if stats.p > alpha:
        return "failed", f"not significant (adjusted p={stats.p:.3g} > alpha {alpha:g})"
    if stats.ci_low is None or stats.ci_high is None:
        return "missing", f"significant (p={stats.p:.3g}) but no confidence interval provided"
    excludes = stats.ci_low > 0 if stats.claimed_direction == "increase" else stats.ci_high < 0
    if not excludes:
        return "failed", f"CI [{stats.ci_low:.4g}, {stats.ci_high:.4g}] does not exclude 0 in the claimed direction"
    return (
        "satisfied",
        f"p={stats.p:.3g} <= alpha {alpha:g} and CI [{stats.ci_low:.4g}, {stats.ci_high:.4g}] excludes 0",
    )


def _baseline_validated(checks: CheckResults) -> tuple[CriterionState, str]:
    if checks.baseline_runs_completed is None:
        return "missing", "baseline runs not checked"
    if not checks.baseline_runs_completed:
        return "failed", "baseline runs did not complete"
    if checks.baseline_evaluator_passed is None:
        return "missing", "baseline evaluator has not run"
    if not checks.baseline_evaluator_passed:
        return "failed", "baseline evaluator failed"
    return "satisfied", "baseline runs completed and the baseline evaluator passed"


def _evaluator_passed(checks: CheckResults, profile: CriteriaProfile) -> tuple[CriterionState, str]:
    independent = [e for e in checks.evaluators if e.independent]
    passed = [e.key for e in independent if e.passed is True]
    failed = [e.key for e in independent if e.passed is False]
    if failed:
        return "failed", f"independent evaluator(s) failed: {', '.join(sorted(failed))}"
    if len(passed) >= profile.min_independent_evaluators:
        return "satisfied", f"independent evaluator(s) passed: {', '.join(sorted(passed))}"
    if not independent:
        suffix = " (only non-independent evaluators ran)" if checks.evaluators else ""
        return "missing", "no independent evaluator result" + suffix
    return "missing", f"{len(passed)} of {profile.min_independent_evaluators} independent evaluator pass(es)"


def _provenance(checks: CheckResults) -> tuple[CriterionState, str]:
    if checks.provenance is None:
        return "missing", "lineage not checked"
    if checks.provenance.complete:
        return "satisfied", "lineage complete"
    missing = ", ".join(checks.provenance.missing) or "unspecified links"
    return "failed", f"lineage incomplete: missing {missing}"


def _independent_verifier(checks: CheckResults) -> tuple[CriterionState, str]:
    v = checks.verifier
    if v is None:
        return "missing", "no verifier identity recorded"
    has_identity = bool(
        v.verifier_agent_version_ids or v.verifier_models or v.verifier_actor_ids or v.verifier_is_human
    )
    if not has_identity:
        return "missing", "verifier identity is empty"
    overlaps = []
    if set(v.verifier_agent_version_ids) & set(v.generator_agent_version_ids):
        overlaps.append("agent version")
    if set(v.verifier_models) & set(v.generator_models):
        overlaps.append("model")
    if set(v.verifier_actor_ids) & set(v.generator_actor_ids):
        overlaps.append("actor")
    if overlaps:
        return "failed", f"verifier shares {', '.join(overlaps)} with the generator"
    return "satisfied", "verifier is independent of the generator"


def _no_contradiction(checks: CheckResults) -> tuple[CriterionState, str]:
    count = checks.contradicting_evidence_count
    if count is None:
        return "missing", "no contradiction search recorded"
    if count > 0:
        return "failed", f"{count} contradicting evidence item(s)"
    return "satisfied", "no contradicting evidence found"


def evaluate_criteria(
    profile: str | CriteriaProfile, checks: CheckResults | Mapping[str, Any]
) -> dict[str, CriterionResult]:
    """Evaluate every criterion (required or not) → ``{criterion: CriterionResult}``."""
    prof = get_profile(profile)
    data = checks if isinstance(checks, CheckResults) else CheckResults.model_validate(dict(checks))
    evaluated: dict[Criterion, tuple[CriterionState, str]] = {
        C.REPLICATED: _replicated(data, prof),
        C.STATISTICALLY_SUPPORTED: _statistically_supported(data, prof),
        C.BASELINE_VALIDATED: _baseline_validated(data),
        C.EVALUATOR_PASSED: _evaluator_passed(data, prof),
        C.PROVENANCE_COMPLETE: _provenance(data),
        C.INDEPENDENT_VERIFIER: _independent_verifier(data),
        C.NO_CONTRADICTING_EVIDENCE: _no_contradiction(data),
    }
    return {
        c.value: CriterionResult(criterion=c, state=state, required=c in prof.required, detail=detail)
        for c, (state, detail) in evaluated.items()
    }


# ---------------------------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------------------------
def decide(profile: str | CriteriaProfile, checks: CheckResults | Mapping[str, Any]) -> VerdictResult:
    """Decide a claim's status under ``profile`` (see the module docstring for rules and confidence)."""
    prof = get_profile(profile)
    data = checks if isinstance(checks, CheckResults) else CheckResults.model_validate(dict(checks))
    results = evaluate_criteria(prof, data)
    satisfied = [k for k, r in results.items() if r.state == "satisfied"]
    failed = [k for k, r in results.items() if r.state == "failed"]
    missing = [k for k, r in results.items() if r.state == "missing"]
    required = [c.value for c in prof.required]
    req_satisfied = [k for k in required if k in satisfied]
    reasons: list[str] = []

    reproductions = [r for r in data.reproductions if r.independent]
    n_reproduced = sum(1 for r in reproductions if r.verdict == "reproduced")
    n_not_reproduced = sum(1 for r in reproductions if r.verdict == "not_reproduced")
    alpha = (
        prof.alpha
        if data.statistics is None or data.statistics.alpha is None
        else min(prof.alpha, data.statistics.alpha)
    )
    reversed_effect = data.statistics is not None and _effect_reversed(data.statistics, alpha)
    failed_independent_evaluators = any(e.independent and e.passed is False for e in data.evaluators)
    contradiction = (
        (data.contradicting_evidence_count or 0) > 0
        or (n_reproduced > 0 and n_not_reproduced > 0)
        or failed_independent_evaluators
    )
    has_support = bool(satisfied) or data.supporting_evidence_count > 0

    status: ClaimStatus
    if len(missing) == len(results):
        status = ClaimStatus.CANDIDATE
        reasons.append("no verification criterion has been checked yet")
    elif (n_not_reproduced > 0 and n_reproduced == 0) or reversed_effect:
        status = ClaimStatus.REJECTED
        if n_not_reproduced > 0 and n_reproduced == 0:
            reasons.append(f"{n_not_reproduced} independent reproduction(s) failed and none succeeded")
        if reversed_effect:
            reasons.append("the effect is statistically significant in the opposite direction")
    elif contradiction:
        status = ClaimStatus.CONTESTED
        parts = []
        if (data.contradicting_evidence_count or 0) > 0:
            parts.append(f"{data.contradicting_evidence_count} contradicting evidence item(s)")
        if n_reproduced > 0 and n_not_reproduced > 0:
            parts.append(f"mixed reproductions ({n_reproduced} reproduced, {n_not_reproduced} not)")
        if failed_independent_evaluators:
            parts.append("an independent evaluator failed")
        support_text = "alongside supporting evidence" if has_support else "without supporting evidence"
        reasons.append(f"contested: {'; '.join(parts)} {support_text}")
    elif len(req_satisfied) == len(required):
        status = ClaimStatus.VERIFIED
        reasons.append(f"all {len(required)} required criteria of profile {prof.key!r} are satisfied")
    elif req_satisfied:
        status = ClaimStatus.PARTIALLY_VERIFIED
        reasons.append(f"{len(req_satisfied)} of {len(required)} required criteria satisfied")
    else:
        status = ClaimStatus.CANDIDATE
        reasons.append("checked, but no required criterion is satisfied yet")

    # Confidence -----------------------------------------------------------------------------
    optional = [c.value for c in prof.optional]
    opt_satisfied = [k for k in optional if k in satisfied]
    denominator = len(required) + 0.5 * len(optional)
    support = (len(req_satisfied) + 0.5 * len(opt_satisfied)) / denominator
    penalised = support * 0.5 ** len(failed)
    judgment_adjustment = 0.0
    if data.model_judgment is not None:
        sign = 1.0 if data.model_judgment.supports else -1.0
        judgment_adjustment = sign * MODEL_JUDGMENT_WEIGHT * data.model_judgment.confidence
    cap = STATUS_CAP[status]
    confidence = min(cap, max(0.0, penalised + judgment_adjustment))

    unmet_required = [k for k in required if k not in satisfied]
    if status != ClaimStatus.VERIFIED and unmet_required:
        reasons.append(
            "unmet required criteria: "
            + ", ".join(f"{k} ({results[k].state}: {results[k].detail})" for k in unmet_required)
        )
    if data.model_judgment is not None:
        reasons.append(
            f"model judgment ({'supports' if data.model_judgment.supports else 'disputes'}, confidence "
            f"{data.model_judgment.confidence:.2f}) is advisory and adjusted confidence by {judgment_adjustment:+.3f}"
        )
    return VerdictResult(
        status=status,
        confidence=round(confidence, 6),
        satisfied=satisfied,
        missing=missing,
        failed=failed,
        rationale=" ".join(r[0].upper() + r[1:] + "." for r in reasons),
        profile=prof.key,
        criteria=results,
        confidence_components={
            "support": support,
            "failure_penalty": 0.5 ** len(failed),
            "model_judgment_adjustment": judgment_adjustment,
            "status_cap": cap,
        },
    )
