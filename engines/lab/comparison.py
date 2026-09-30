"""Baseline-vs-candidate comparison with a pre-registered statistical plan.

:func:`compare_samples` turns two samples of one metric (typically one value per seed) into a
:class:`ComparisonResult` with a direction-aware verdict:

* ``insufficient_data`` — fewer than ``max(2, plan.n_seeds)`` finite observations in either arm (pairs for
  ``paired_t``), or the chosen test is undefined for the data;
* ``improved`` / ``regressed`` — the (multiplicity-adjusted) p-value is ``<= plan.alpha`` *and* the effect is
  at least the minimum effect size (and minimum raw delta, when given); the sign comes from the test's own
  effect estimate and the metric direction (for ``minimize`` metrics lower is better);
* ``no_significant_difference`` — otherwise, including "statistically significant but practically negligible"
  (``statistics.practically_significant`` is then ``False`` and the rationale says so).

Tests (two-sided; the verdict's direction comes from the sign of the effect):

========== ============================== ================== ================================
test       p-value                        effect size        confidence interval (cand - base)
========== ============================== ================== ================================
welch_t    Welch t, Satterthwaite df      Hedges' g          t interval of the mean difference
paired_t   paired t (pairs by position)   Cohen's d_z        t interval of the mean difference
mann_whitney Mann–Whitney U               Cliff's delta      Hodges–Lehmann shift interval
bootstrap  studentized bootstrap test     Hedges' g          percentile bootstrap of mean diff
========== ============================== ================== ================================

Non-finite observations (e.g. a diverged run reporting NaN) are excluded, counted in ``n_excluded_*`` and
reported in ``warnings`` — never silently dropped. Everything is deterministic (bootstrap seeded from the plan).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engines.lab import statistics as st
from engines.lab.experiment_spec import StatisticalPlan

COMPARISON_ENGINE_VERSION = "comparison-1.0.0"

Verdict = Literal["improved", "regressed", "no_significant_difference", "insufficient_data"]
VERDICTS: tuple[str, ...] = ("improved", "regressed", "no_significant_difference", "insufficient_data")

_TEST_LABEL = {
    "welch_t": "Welch's t-test",
    "paired_t": "paired t-test",
    "mann_whitney": "Mann–Whitney U test",
    "bootstrap": "bootstrap test",
}
_STAT_LABEL = {"welch_t": "t", "paired_t": "t", "mann_whitney": "U", "bootstrap": "studentized t"}
_EFFECT_LABEL = {"hedges_g": "Hedges' g", "cohens_dz": "Cohen's d_z", "cliffs_delta": "Cliff's delta"}


class ComparisonStatistics(BaseModel):
    """All numbers behind a verdict. Undefined quantities are ``None`` (never NaN/inf, so it is JSON-safe)."""

    model_config = ConfigDict(extra="forbid")

    test: str
    alternative: str = "two-sided"
    statistic: float | None = None
    p_value: float | None = None
    p_value_adjusted: float | None = None
    correction: str = "none"
    family_size: int = 1
    df: float | None = None
    effect_size: float | None = None
    effect_size_kind: str
    ci_low: float | None = None
    ci_high: float | None = None
    ci_level: float
    ci_estimand: str
    n_baseline: int
    n_candidate: int
    n_excluded_baseline: int = 0
    n_excluded_candidate: int = 0
    mean_baseline: float | None = None
    mean_candidate: float | None = None
    sd_baseline: float | None = None
    sd_candidate: float | None = None
    median_baseline: float | None = None
    median_candidate: float | None = None
    delta: float | None = None
    relative_delta: float | None = None
    improvement: float | None = None
    alpha: float
    min_effect: float | None = None
    min_delta: float | None = None
    practically_significant: bool | None = None
    bootstrap_seed: int | None = None
    bootstrap_resamples: int | None = None


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    direction: Literal["maximize", "minimize"]
    statistics: ComparisonStatistics
    verdict: Verdict
    rationale: str
    warnings: list[str] = Field(default_factory=list)
    engine_version: str = COMPARISON_ENGINE_VERSION

    @property
    def p_value_for_decision(self) -> float | None:
        s = self.statistics
        return s.p_value_adjusted if s.p_value_adjusted is not None else s.p_value

    @property
    def ci_excludes_zero_in_improving_direction(self) -> bool | None:
        """``True`` when the CI of ``candidate - baseline`` lies entirely on the improving side of zero."""
        s = self.statistics
        if s.ci_low is None or s.ci_high is None:
            return None
        return s.ci_low > 0 if self.direction == "maximize" else s.ci_high < 0


class MetricSamples(BaseModel):
    """One metric's baseline and candidate samples for :func:`compare_many`."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    direction: Literal["maximize", "minimize"]
    baseline: list[float]
    candidate: list[float]
    min_effect: float | None = None
    min_delta: float | None = None


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _clean(values: Sequence[float]) -> tuple[list[float], int]:
    kept: list[float] = []
    excluded = 0
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            excluded += 1
            continue
        if math.isfinite(f):
            kept.append(f)
        else:
            excluded += 1
    return kept, excluded


def align_by_seed(
    baseline: Mapping[int, float], candidate: Mapping[int, float]
) -> tuple[list[float], list[float], list[int]]:
    """Pair baseline and candidate values by seed (for ``paired_t``) → ``(baseline, candidate, seeds)``."""
    seeds = sorted(set(baseline) & set(candidate))
    return [float(baseline[s]) for s in seeds], [float(candidate[s]) for s in seeds], seeds


@dataclass
class _Raw:
    """Intermediate, unadjusted statistics (before the family-wise correction and the verdict)."""

    stats: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    insufficient: str | None = None


def _effect_kind(test: str) -> tuple[str, str]:
    if test == "paired_t":
        return "cohens_dz", "mean_difference"
    if test == "mann_whitney":
        return "cliffs_delta", "hodges_lehmann_shift"
    return "hedges_g", "mean_difference"


def _compute(baseline: Sequence[float], candidate: Sequence[float], direction: str, plan: StatisticalPlan) -> _Raw:
    base, excl_b = _clean(baseline)
    cand, excl_c = _clean(candidate)
    effect_kind, estimand = _effect_kind(plan.test)
    stats: dict[str, Any] = {
        "test": plan.test,
        "effect_size_kind": effect_kind,
        "ci_level": plan.ci_level,
        "ci_estimand": estimand,
        "n_baseline": len(base),
        "n_candidate": len(cand),
        "n_excluded_baseline": excl_b,
        "n_excluded_candidate": excl_c,
        "alpha": plan.alpha,
    }
    raw = _Raw(stats)
    if excl_b or excl_c:
        raw.warnings.append(
            f"excluded non-finite observations (baseline: {excl_b}, candidate: {excl_c}); "
            "diverged or failed runs should be investigated, not ignored"
        )
    for label, sample in (("baseline", base), ("candidate", cand)):
        if sample:
            stats[f"mean_{label}"] = st.mean(sample)
            stats[f"median_{label}"] = st.median(sample)
            stats[f"sd_{label}"] = st.sd(sample) if len(sample) > 1 else None
    if base and cand:
        delta = stats["mean_candidate"] - stats["mean_baseline"]
        stats["delta"] = delta
        stats["improvement"] = delta if direction == "maximize" else -delta
        mb = stats["mean_baseline"]
        stats["relative_delta"] = delta / abs(mb) if mb != 0 else None
    required = max(2, plan.n_seeds)
    if plan.test == "paired_t" and len(base) != len(cand):
        raw.insufficient = (
            f"paired test needs equally many paired observations (baseline {len(base)}, candidate {len(cand)}); "
            "pair runs by seed before comparing"
        )
        return raw
    if len(base) < required or len(cand) < required:
        raw.insufficient = (
            f"need at least {required} finite observations per arm (plan n_seeds={plan.n_seeds}); "
            f"got baseline {len(base)}, candidate {len(cand)}"
        )
        return raw
    if plan.test == "welch_t":
        t = st.welch_t_test(cand, base, ci_level=plan.ci_level)
        stats.update(statistic=t.statistic, df=t.df, p_value=t.p_value, ci_low=t.ci_low, ci_high=t.ci_high)
        stats["effect_size"] = st.hedges_g(cand, base)
        if t.degenerate:
            raw.warnings.append("zero variance in both arms: the comparison is exact but has no sampling noise")
    elif plan.test == "paired_t":
        t = st.paired_t_test(cand, base, ci_level=plan.ci_level)
        stats.update(statistic=t.statistic, df=t.df, p_value=t.p_value, ci_low=t.ci_low, ci_high=t.ci_high)
        stats["effect_size"] = st.cohens_dz([c - b for c, b in zip(cand, base, strict=True)])
        if t.degenerate:
            raw.warnings.append("all paired differences are identical: no sampling noise")
    elif plan.test == "mann_whitney":
        u = st.mann_whitney_u(cand, base)
        hl = st.hodges_lehmann(cand, base, ci_level=plan.ci_level)
        stats.update(statistic=u.u, p_value=u.p_value, ci_low=hl.ci_low, ci_high=hl.ci_high)
        stats["effect_size"] = st.cliffs_delta(cand, base)
        stats["hodges_lehmann_shift"] = hl.estimate
        if hl.ci_low is None:
            raw.warnings.append(
                f"a distribution-free {plan.ci_level:.0%} interval is not achievable with "
                f"{len(cand)} vs {len(base)} observations"
            )
    else:  # bootstrap
        test = st.bootstrap_test(cand, base, n_resamples=plan.bootstrap_resamples, seed=plan.bootstrap_seed)
        ci = st.bootstrap_ci(
            cand,
            base,
            n_resamples=plan.bootstrap_resamples,
            ci_level=plan.ci_level,
            seed=plan.bootstrap_seed,
        )
        stats.update(statistic=test.statistic, p_value=test.p_value, ci_low=ci.ci_low, ci_high=ci.ci_high)
        stats["effect_size"] = st.hedges_g(cand, base)
        stats["bootstrap_seed"] = plan.bootstrap_seed
        stats["bootstrap_resamples"] = plan.bootstrap_resamples
    if min(len(base), len(cand)) < 5:
        raw.warnings.append(f"small samples (n={min(len(base), len(cand))}): estimates are imprecise")
    return raw


def _fmt(value: float | None, spec: str = ".4g") -> str:
    return "n/a" if value is None else format(value, spec)


def _finalise(
    metric: str,
    direction: Literal["maximize", "minimize"],
    raw: _Raw,
    *,
    p_adjusted: float | None,
    correction: str,
    family_size: int,
    alpha: float,
    min_effect: float | None,
    min_delta: float | None,
) -> ComparisonResult:
    s = dict(raw.stats)
    s.update(
        p_value_adjusted=p_adjusted,
        correction=correction,
        family_size=family_size,
        min_effect=min_effect,
        min_delta=min_delta,
    )
    hl_shift = s.pop("hodges_lehmann_shift", None)
    effect = s.get("effect_size")
    statistic = s.get("statistic")
    # Sanitise to JSON-safe values; remember infinite effects (constant samples) for the decision.
    effect_infinite = effect is not None and math.isinf(effect)
    for key in ("statistic", "effect_size", "p_value", "df", "ci_low", "ci_high", "delta", "relative_delta"):
        s[key] = _finite(s.get(key))
    warnings = list(raw.warnings)
    if statistic is not None and not math.isfinite(statistic):
        warnings.append("test statistic is infinite (no within-arm variance); reported as null")
    label = _TEST_LABEL.get(str(s["test"]), str(s["test"]))
    header = f"{label} on {metric} ({direction})"
    if raw.insufficient is not None:
        stats = ComparisonStatistics(**s)
        return ComparisonResult(
            metric=metric,
            direction=direction,
            statistics=stats,
            verdict="insufficient_data",
            rationale=f"{header}: insufficient data — {raw.insufficient}.",
            warnings=warnings,
        )
    p_decision = p_adjusted if p_adjusted is not None else s.get("p_value")
    # Direction of the effect according to the test's own estimate.
    if effect is not None and effect != 0:
        sign = 1.0 if effect > 0 else -1.0
    elif s.get("delta"):
        sign = 1.0 if s["delta"] > 0 else -1.0
    else:
        sign = 0.0
    improving_sign = sign if direction == "maximize" else -sign
    practical = True
    reasons: list[str] = []
    if min_effect is not None and effect is not None and not effect_infinite and abs(effect) < min_effect:
        practical = False
        reasons.append(f"|{_EFFECT_LABEL.get(s['effect_size_kind'], 'effect')}| {abs(effect):.3g} < {min_effect:.3g}")
    if min_delta is not None and s.get("improvement") is not None and abs(s["improvement"]) < min_delta:
        practical = False
        reasons.append(f"|delta| {abs(s['improvement']):.4g} < minimum {min_delta:.4g}")
    significant = p_decision is not None and p_decision <= alpha
    s["practically_significant"] = practical if (min_effect is not None or min_delta is not None) else None
    verdict: Verdict
    if p_decision is None:
        verdict = "insufficient_data"
        reasons.append("the test produced no p-value")
    elif not significant:
        verdict = "no_significant_difference"
    elif improving_sign == 0:
        verdict = "no_significant_difference"
        reasons.append("no directional effect")
    elif not practical:
        verdict = "no_significant_difference"
    else:
        verdict = "improved" if improving_sign > 0 else "regressed"
    stats = ComparisonStatistics(**s)
    parts = [
        f"{header}: candidate mean {_fmt(stats.mean_candidate)} vs baseline mean {_fmt(stats.mean_baseline)}",
        f"(n={stats.n_candidate} vs {stats.n_baseline}; delta {_fmt(stats.delta, '+.4g')}"
        + (f", {stats.relative_delta:+.2%}" if stats.relative_delta is not None else "")
        + ")",
    ]
    stat_bits = [f"{_STAT_LABEL.get(stats.test, 'statistic')} = {_fmt(stats.statistic)}"]
    if stats.df is not None:
        stat_bits.append(f"df {stats.df:.3g}")
    stat_bits.append(f"p {_fmt(stats.p_value)}")
    if family_size > 1:
        stat_bits.append(f"{correction}-adjusted p {_fmt(p_adjusted)} over {family_size} comparisons")
    stat_bits.append(f"alpha {alpha:g}")
    parts.append("; ".join(stat_bits))
    effect_label = _EFFECT_LABEL.get(stats.effect_size_kind, stats.effect_size_kind)
    parts.append(f"{effect_label} {_fmt(stats.effect_size, '.3g') if not effect_infinite else '±inf'}")
    if hl_shift is not None:
        parts.append(f"Hodges–Lehmann shift {hl_shift:+.4g}")
    if stats.ci_low is not None and stats.ci_high is not None:
        ci_text = f"{stats.ci_level:.0%} CI of candidate − baseline [{stats.ci_low:.4g}, {stats.ci_high:.4g}]"
        if family_size > 1:
            ci_text += " (per-comparison, not multiplicity-adjusted)"
        parts.append(ci_text)
    else:
        parts.append("confidence interval unavailable")
    verdict_text = verdict.replace("_", " ")
    if verdict == "no_significant_difference" and significant and not practical:
        verdict_text += " (statistically significant but below the practical threshold: " + "; ".join(reasons) + ")"
    elif reasons and verdict == "insufficient_data":
        verdict_text += " (" + "; ".join(reasons) + ")"
    rationale = ". ".join([" ".join(parts[:2]), parts[2], ", ".join(parts[3:])]) + f". Verdict: {verdict_text}."
    return ComparisonResult(
        metric=metric,
        direction=direction,
        statistics=stats,
        verdict=verdict,
        rationale=rationale,
        warnings=warnings,
    )


def _check_direction(direction: str) -> Literal["maximize", "minimize"]:
    if direction == "maximize":
        return "maximize"
    if direction == "minimize":
        return "minimize"
    raise ValueError("direction must be 'maximize' or 'minimize'")


def compare_samples(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    metric: str,
    direction: str,
    plan: StatisticalPlan | None = None,
    min_effect: float | None = None,
    min_delta: float | None = None,
) -> ComparisonResult:
    """Compare one metric's baseline and candidate samples under ``plan`` (see module docstring).

    ``min_effect`` (standardised, compared with ``|effect_size|``) defaults to ``plan.min_effect_size``;
    ``min_delta`` is an optional minimum raw, direction-aware improvement in metric units. A single comparison
    has ``family_size == 1`` and ``p_value_adjusted == p_value``; use :func:`compare_many` for families.
    """
    plan = plan or StatisticalPlan()
    dir_ = _check_direction(direction)
    raw = _compute(baseline, candidate, dir_, plan)
    p = raw.stats.get("p_value")
    return _finalise(
        metric,
        dir_,
        raw,
        p_adjusted=_finite(p),
        correction=plan.correction,
        family_size=1,
        alpha=plan.alpha,
        min_effect=min_effect if min_effect is not None else plan.min_effect_size,
        min_delta=min_delta,
    )


def compare_many(
    samples: Sequence[MetricSamples | Mapping[str, Any]],
    plan: StatisticalPlan | None = None,
    *,
    min_effect: float | None = None,
) -> list[ComparisonResult]:
    """Compare several metrics as one family and apply ``plan.correction`` (Holm / BH / none) across them.

    Only comparisons that produced a p-value are part of the family (``family_size``); insufficient-data
    comparisons are reported as such and do not consume the error budget. Results keep the input order.
    """
    plan = plan or StatisticalPlan()
    items = [s if isinstance(s, MetricSamples) else MetricSamples.model_validate(dict(s)) for s in samples]
    raws = [_compute(i.baseline, i.candidate, _check_direction(i.direction), plan) for i in items]
    tested = [k for k, r in enumerate(raws) if r.insufficient is None and _finite(r.stats.get("p_value")) is not None]
    adjusted = st.adjust_p_values([float(raws[k].stats["p_value"]) for k in tested], plan.correction)
    adjusted_by_index = dict(zip(tested, adjusted, strict=True))
    results = []
    for k, (item, raw) in enumerate(zip(items, raws, strict=True)):
        effect_floor = item.min_effect if item.min_effect is not None else min_effect
        results.append(
            _finalise(
                item.metric,
                _check_direction(item.direction),
                raw,
                p_adjusted=adjusted_by_index.get(k),
                correction=plan.correction,
                family_size=max(1, len(tested)),
                alpha=plan.alpha,
                min_effect=effect_floor if effect_floor is not None else plan.min_effect_size,
                min_delta=item.min_delta,
            )
        )
    return results
