"""Promotion gate — is a candidate strategy *eligible* to replace the incumbent?

Eligibility requires **all** of:

1. ``sample_size`` — at least ``min_seeds`` independent evaluations (seeds) of both strategies for the
   primary objective and every regression-checked objective;
2. ``feasibility`` — every candidate sample satisfies every hard objective constraint (e.g.
   ``safety >= 1.0``); a constraint that cannot be checked (no samples) fails;
3. ``primary_improvement`` — the bootstrap CI of the (direction-aware) improvement on the primary
   objective lies entirely above zero *and* the point estimate reaches ``min_improvement``
   (absolute, or relative to the incumbent mean);
4. ``no_regression`` — no objective in ``no_regression_objectives`` (default safety,
   reproducibility) or ``max_regression`` gets worse by more than its tolerance (default 0);
5. ``benchmark_evidence`` — when ``require_benchmark``: at least one internal benchmark shows an
   improvement over its baseline and none regresses. *The system is never described as improved
   without benchmark evidence.*

Eligibility is **not** promotion: the strategies service still evaluates governance policy
(``strategy.promote``) and requires human approval before changing any status.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from engines.lab.evolution.fitness import default_objectives, resolve_objectives
from engines.lab.evolution.stats import BootstrapResult, bootstrap_mean_difference
from engines.lab.evolution.types import ObjectiveSpec

_TOL = 1e-12


class BenchmarkEvidence(BaseModel):
    """One benchmark comparison (see ``engines.lab.benchmarks.base.BenchComparison.to_evidence``)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    suite: str
    score: float
    baseline_score: float
    improved: bool | None = None
    ci: tuple[float, float] | None = None


class PromotionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_seeds: int = Field(default=3, ge=1, le=10_000)
    primary_objective: str = "scientific_performance"
    min_improvement: float = Field(default=0.0, ge=0)
    improvement_mode: Literal["absolute", "relative"] = "absolute"
    ci_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    bootstrap_resamples: int = Field(default=2000, ge=100, le=100_000)
    no_regression_objectives: tuple[str, ...] = ("safety", "reproducibility")
    max_regression: dict[str, float] = Field(default_factory=dict)
    require_benchmark: bool = True
    max_benchmark_regression: float = Field(default=0.0, ge=0)
    paired: bool = False
    seed: int = Field(default=0, ge=0)
    objectives: tuple[ObjectiveSpec, ...] = Field(default_factory=lambda: tuple(default_objectives()))

    @field_validator("objectives", mode="before")
    @classmethod
    def _resolve(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(resolve_objectives(value))
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        known = {o.name for o in self.objectives}
        referenced = {self.primary_objective, *self.no_regression_objectives, *self.max_regression}
        unknown = sorted(referenced - known)
        if unknown:
            raise ValueError(f"policy references objectives without a spec: {unknown}")
        if any(v < 0 for v in self.max_regression.values()):
            raise ValueError("max_regression tolerances must be >= 0")
        return self

    def objective(self, name: str) -> ObjectiveSpec:
        return next(o for o in self.objectives if o.name == name)

    @property
    def regression_objectives(self) -> tuple[str, ...]:
        ordered = dict.fromkeys((*self.no_regression_objectives, *sorted(self.max_regression)))
        return tuple(n for n in ordered if n != self.primary_objective)


class PromotionCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    detail: str
    data: dict[str, Any] = Field(default_factory=dict)


class PromotionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reasons: list[str]
    statistics: dict[str, Any]
    checks: list[PromotionCheck]
    requires_human_approval: bool = True

    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


Samples = Mapping[str, Sequence[float]]


class PromotionGate:
    """Evaluates a candidate against the incumbent under a :class:`PromotionPolicy`."""

    def __init__(self, policy: PromotionPolicy | Mapping[str, Any] | None = None) -> None:
        if policy is None:
            self.policy = PromotionPolicy()
        elif isinstance(policy, PromotionPolicy):
            self.policy = policy
        else:
            self.policy = PromotionPolicy.model_validate(dict(policy))

    def evaluate(
        self,
        incumbent_samples: Samples,
        candidate_samples: Samples,
        benchmark_evidence: Sequence[Mapping[str, Any] | BenchmarkEvidence] | None = None,
    ) -> PromotionDecision:
        checks = [
            self._check_sample_size(incumbent_samples, candidate_samples),
            self._check_feasibility(candidate_samples),
        ]
        primary_check, primary_stats = self._check_primary(incumbent_samples, candidate_samples)
        checks.append(primary_check)
        regression_check, regression_stats = self._check_regressions(incumbent_samples, candidate_samples)
        checks.append(regression_check)
        benchmark_check = self._check_benchmarks(benchmark_evidence)
        checks.append(benchmark_check)
        eligible = all(c.passed for c in checks)
        reasons = (
            [
                "all promotion checks passed; eligibility is not promotion — governance policy and human "
                "approval still apply"
            ]
            if eligible
            else [f"{c.name}: {c.detail}" for c in checks if not c.passed]
        )
        statistics = {
            "primary_objective": self.policy.primary_objective,
            "primary": primary_stats,
            "regressions": regression_stats,
            "seeds": {
                "candidate": len(candidate_samples.get(self.policy.primary_objective, ())),
                "incumbent": len(incumbent_samples.get(self.policy.primary_objective, ())),
                "min_seeds": self.policy.min_seeds,
            },
            "benchmarks": benchmark_check.data.get("items", []),
        }
        return PromotionDecision(eligible=eligible, reasons=reasons, statistics=statistics, checks=checks)

    # -- checks ------------------------------------------------------------------------------------
    def _check_sample_size(self, incumbent: Samples, candidate: Samples) -> PromotionCheck:
        required = (self.policy.primary_objective, *self.policy.regression_objectives)
        short: list[str] = []
        counts: dict[str, dict[str, int]] = {}
        for name in required:
            n_c, n_i = len(candidate.get(name, ())), len(incumbent.get(name, ()))
            counts[name] = {"candidate": n_c, "incumbent": n_i}
            if n_c < self.policy.min_seeds or n_i < self.policy.min_seeds:
                short.append(f"{name} (candidate {n_c}, incumbent {n_i})")
        if short:
            return PromotionCheck(
                name="sample_size",
                passed=False,
                detail=f"fewer than {self.policy.min_seeds} seeds for: {', '.join(short)}",
                data={"counts": counts},
            )
        return PromotionCheck(
            name="sample_size",
            passed=True,
            detail=f"at least {self.policy.min_seeds} seeds for every checked objective",
            data={"counts": counts},
        )

    def _check_feasibility(self, candidate: Samples) -> PromotionCheck:
        problems: list[str] = []
        checked: list[str] = []
        for objective in self.policy.objectives:
            if objective.constraint is None:
                continue
            values = list(candidate.get(objective.name, ()))
            label = f"{objective.name}{objective.constraint.op}{objective.constraint.threshold:g}"
            if not values:
                problems.append(f"{label} cannot be verified (no samples)")
                continue
            checked.append(label)
            failing = [v for v in values if objective.constraint.violation(float(v)) > 0]
            if failing:
                problems.append(f"{label} violated in {len(failing)}/{len(values)} samples")
        if problems:
            return PromotionCheck(
                name="feasibility", passed=False, detail="; ".join(problems), data={"checked": checked}
            )
        return PromotionCheck(
            name="feasibility",
            passed=True,
            detail="all hard constraints hold on every sample" if checked else "no hard constraints declared",
            data={"checked": checked},
        )

    def _bootstrap(
        self, name: str, incumbent: Sequence[float], candidate: Sequence[float], *, relative: bool
    ) -> BootstrapResult:
        sign = self.policy.objective(name).sign
        paired = self.policy.paired and len(incumbent) == len(candidate)
        return bootstrap_mean_difference(
            [sign * float(v) for v in candidate],
            [sign * float(v) for v in incumbent],
            ci_level=self.policy.ci_level,
            resamples=self.policy.bootstrap_resamples,
            seed=self.policy.seed,
            paired=paired,
            relative=relative,
        )

    def _check_primary(self, incumbent: Samples, candidate: Samples) -> tuple[PromotionCheck, dict[str, Any]]:
        name = self.policy.primary_objective
        inc, cand = list(incumbent.get(name, ())), list(candidate.get(name, ()))
        if not inc or not cand:
            return (
                PromotionCheck(name="primary_improvement", passed=False, detail=f"no samples for {name}"),
                {},
            )
        relative = self.policy.improvement_mode == "relative"
        try:
            absolute = self._bootstrap(name, inc, cand, relative=False)
            result = self._bootstrap(name, inc, cand, relative=True) if relative else absolute
        except ValueError as exc:
            return (
                PromotionCheck(name="primary_improvement", passed=False, detail=f"{name}: {exc}"),
                {},
            )
        stats: dict[str, Any] = {
            "objective": name,
            "direction": self.policy.objective(name).direction.value,
            "incumbent_mean": round(sum(inc) / len(inc), 12),
            "candidate_mean": round(sum(cand) / len(cand), 12),
            "improvement": absolute.as_dict(),
        }
        if relative:
            stats["relative_improvement"] = result.as_dict()
        mode = "relative " if relative else ""
        if result.lower <= 0:
            detail = (
                f"{self.policy.ci_level:.0%} CI of the {mode}improvement on {name} "
                f"[{result.lower:.4g}, {result.upper:.4g}] includes or lies below 0"
            )
            return PromotionCheck(name="primary_improvement", passed=False, detail=detail, data=stats), stats
        if result.estimate + _TOL < self.policy.min_improvement:
            detail = (
                f"{mode}improvement on {name} ({result.estimate:.4g}) is below the required "
                f"{self.policy.min_improvement:.4g}"
            )
            return PromotionCheck(name="primary_improvement", passed=False, detail=detail, data=stats), stats
        detail = (
            f"{mode}improvement on {name} = {result.estimate:.4g}, {self.policy.ci_level:.0%} CI "
            f"[{result.lower:.4g}, {result.upper:.4g}] excludes 0"
        )
        return PromotionCheck(name="primary_improvement", passed=True, detail=detail, data=stats), stats

    def _check_regressions(self, incumbent: Samples, candidate: Samples) -> tuple[PromotionCheck, dict[str, Any]]:
        problems: list[str] = []
        stats: dict[str, Any] = {}
        for name in self.policy.regression_objectives:
            inc, cand = list(incumbent.get(name, ())), list(candidate.get(name, ()))
            tolerance = self.policy.max_regression.get(name, 0.0)
            if not inc or not cand:
                problems.append(f"{name}: cannot verify (missing samples)")
                continue
            try:
                result = self._bootstrap(name, inc, cand, relative=False)
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
                continue
            regression = max(0.0, -result.estimate)
            stats[name] = {"change": result.as_dict(), "regression": round(regression, 12), "tolerance": tolerance}
            if regression > tolerance + _TOL:
                problems.append(f"{name} regresses by {regression:.4g} (tolerance {tolerance:.4g})")
        if problems:
            return (
                PromotionCheck(name="no_regression", passed=False, detail="; ".join(problems), data=stats),
                stats,
            )
        checked = ", ".join(self.policy.regression_objectives) or "none"
        return (
            PromotionCheck(
                name="no_regression", passed=True, detail=f"no regression beyond tolerance ({checked})", data=stats
            ),
            stats,
        )

    def _check_benchmarks(self, evidence: Sequence[Mapping[str, Any] | BenchmarkEvidence] | None) -> PromotionCheck:
        items: list[BenchmarkEvidence] = []
        invalid = 0
        for raw in evidence or ():
            try:
                items.append(raw if isinstance(raw, BenchmarkEvidence) else BenchmarkEvidence.model_validate(dict(raw)))
            except (ValidationError, TypeError, ValueError):
                invalid += 1
        summary = [item.model_dump(mode="json") for item in items]
        if invalid:
            return PromotionCheck(
                name="benchmark_evidence",
                passed=False,
                detail=f"{invalid} malformed benchmark evidence item(s)",
                data={"items": summary},
            )
        tolerance = self.policy.max_benchmark_regression
        regressed = [i.suite for i in items if i.score < i.baseline_score - tolerance - _TOL]
        improved = [
            i.suite for i in items if (i.improved if i.improved is not None else i.score > i.baseline_score + _TOL)
        ]
        if regressed:
            return PromotionCheck(
                name="benchmark_evidence",
                passed=False,
                detail=f"benchmark regression on: {', '.join(sorted(regressed))}",
                data={"items": summary},
            )
        if not self.policy.require_benchmark:
            return PromotionCheck(
                name="benchmark_evidence",
                passed=True,
                detail="benchmark evidence not required by policy (no regressions reported)",
                data={"items": summary},
            )
        if not items:
            return PromotionCheck(
                name="benchmark_evidence",
                passed=False,
                detail="benchmark evidence is required but none was provided",
                data={"items": summary},
            )
        if not improved:
            return PromotionCheck(
                name="benchmark_evidence",
                passed=False,
                detail="no benchmark shows an improvement over its baseline",
                data={"items": summary},
            )
        return PromotionCheck(
            name="benchmark_evidence",
            passed=True,
            detail=f"benchmark improvement on: {', '.join(sorted(improved))}",
            data={"items": summary},
        )
