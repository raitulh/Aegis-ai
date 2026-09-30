"""Numerically careful statistics for the AI Scientist Lab — numpy + stdlib only (no scipy).

Everything here is deterministic. Functions that resample take an explicit integer ``seed`` which is fed
to :func:`numpy.random.default_rng`, so identical inputs always produce identical outputs.

Conventions
-----------
* Two-sample functions follow the "``a`` minus ``b``" convention: ``welch_t_test(a, b)`` tests
  ``mean(a) - mean(b)``, ``cohens_d(a, b)`` is positive when ``a`` is larger, ``bootstrap_ci(a, b)``
  estimates ``stat(a) - stat(b)``. Comparisons elsewhere call them as ``(candidate, baseline)``.
* ``alternative`` is ``"two-sided"``, ``"greater"`` (``a > b``) or ``"less"`` (``a < b``).
* Sample variances use ``ddof=1`` (Bessel's correction) unless stated otherwise.
* Inputs must be finite; :class:`StatisticsError` (a ``ValueError``) is raised for NaN/inf or too few values.
  Callers that want to *exclude* non-finite observations must do so explicitly (see ``comparison.py``).

Special functions
-----------------
* Student-t CDF/SF via the regularized incomplete beta function ``I_x(a, b)``, evaluated with the modified
  Lentz continued fraction (Numerical Recipes §6.4) and the symmetry ``I_x(a,b) = 1 - I_{1-x}(b,a)`` so the
  fraction always converges quickly. Tail probabilities are computed directly (never as ``1 - cdf``) to avoid
  catastrophic cancellation.
* Normal CDF via :func:`math.erfc`; normal quantile via Wichura's AS241 (PPND16, ~1e-16 relative accuracy)
  followed by one Halley refinement step.
* Student-t quantile by safeguarded Newton iteration on the survival function (closed forms for df = 1, 2).

Validated against 50-digit ``mpmath`` on random (t, df) grids with df ∈ [0.3, 5e5]: t CDF relative error
< 3e-11 (worst for df > 1e5, from rounding in the continued fraction; ~1e-15 for typical df), quantiles
reproduce their tail probability to < 3e-11, including tails down to 1e-200 where |t| exceeds 1e150 and
``t²`` would overflow (handled in the log domain). Normal quantile: ~1e-16 relative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np

STATISTICS_ENGINE_VERSION = "statistics-1.0.0"

Alternative = Literal["two-sided", "greater", "less"]
_ALTERNATIVES = ("two-sided", "greater", "less")

# Above this many degrees of freedom the t distribution is replaced by the standard normal
# (absolute CDF error < 1e-7); the continued fraction would otherwise need very many iterations.
_T_NORMAL_DF = 1e7
_CF_MAX_ITER = 20_000
_CF_EPS = 1e-15
_CF_TINY = 1e-300
_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)
# Bootstrap resamples are generated in blocks of at most this many values so memory stays bounded. The
# block size depends only on the sample size, so results are identical on every machine.
_BOOTSTRAP_BLOCK_VALUES = 2_000_000
# Exact Mann-Whitney distributions are computed only while n1 * n2 stays below this bound.
_MWU_EXACT_MAX_CELLS = 2_500


class StatisticsError(ValueError):
    """Invalid input for a statistical routine (non-finite values, too few observations, bad arguments)."""


# ---------------------------------------------------------------------------------------------
# Input handling & descriptive statistics
# ---------------------------------------------------------------------------------------------
def _as_array(values: Sequence[float] | np.ndarray, name: str = "values", min_n: int = 0) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise StatisticsError(f"{name} must be one-dimensional")
    if arr.size and not np.all(np.isfinite(arr)):
        raise StatisticsError(f"{name} contains non-finite values (NaN or infinity)")
    if arr.size < min_n:
        raise StatisticsError(f"{name} needs at least {min_n} values (got {arr.size})")
    return arr


def _check_alternative(alternative: str) -> None:
    if alternative not in _ALTERNATIVES:
        raise StatisticsError(f"alternative must be one of {_ALTERNATIVES} (got {alternative!r})")


def _check_level(level: float, name: str = "ci_level") -> None:
    if not (0.0 < level < 1.0) or not math.isfinite(level):
        raise StatisticsError(f"{name} must be strictly between 0 and 1 (got {level!r})")


def mean(values: Sequence[float] | np.ndarray) -> float:
    """Arithmetic mean using compensated (``math.fsum``) summation."""
    arr = _as_array(values, min_n=1)
    return math.fsum(arr.tolist()) / arr.size


def variance(values: Sequence[float] | np.ndarray, ddof: int = 1) -> float:
    """Sample variance with the corrected two-pass algorithm (Chan, Golub & LeVeque 1983)."""
    arr = _as_array(values, min_n=ddof + 1)
    m = math.fsum(arr.tolist()) / arr.size
    dev = (arr - m).tolist()
    ss = math.fsum(d * d for d in dev) - math.fsum(dev) ** 2 / arr.size
    return max(ss, 0.0) / (arr.size - ddof)


def sd(values: Sequence[float] | np.ndarray, ddof: int = 1) -> float:
    """Sample standard deviation (``ddof=1`` by default)."""
    return math.sqrt(variance(values, ddof=ddof))


def standard_error(values: Sequence[float] | np.ndarray) -> float:
    """Standard error of the mean, ``sd / sqrt(n)`` (needs n >= 2)."""
    arr = _as_array(values, min_n=2)
    return sd(arr) / math.sqrt(arr.size)


def median(values: Sequence[float] | np.ndarray) -> float:
    arr = _as_array(values, min_n=1)
    return float(np.median(arr))


def describe(values: Sequence[float] | np.ndarray) -> dict[str, float | int | None]:
    """Descriptive summary used in reports: n, mean, sd, se, min, median, max (``None`` where undefined)."""
    arr = _as_array(values)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "se": None, "min": None, "median": None, "max": None}
    return {
        "n": n,
        "mean": mean(arr),
        "sd": sd(arr) if n > 1 else None,
        "se": standard_error(arr) if n > 1 else None,
        "min": float(arr.min()),
        "median": float(np.median(arr)),
        "max": float(arr.max()),
    }


# ---------------------------------------------------------------------------------------------
# Special functions
# ---------------------------------------------------------------------------------------------
_HALF_LOG_2PI = 0.5 * math.log(2.0 * math.pi)
_STIRLING_MIN = 15.0


def _stirling_remainder(x: float) -> float:
    """``lgamma(x) - [(x - 1/2) log x - x + log(2π)/2]`` — asymptotic series for large x, direct otherwise."""
    if x < _STIRLING_MIN:
        return math.lgamma(x) - ((x - 0.5) * math.log(x) - x + _HALF_LOG_2PI)
    inv2 = 1.0 / (x * x)
    return (1.0 / 12.0 - inv2 * (1.0 / 360.0 - inv2 * (1.0 / 1260.0 - inv2 * (1.0 / 1680.0 - inv2 / 1188.0)))) / x


def log_beta(a: float, b: float) -> float:
    """``log B(a, b)`` without the cancellation of ``lgamma(a) + lgamma(b) - lgamma(a + b)`` for large arguments.

    For large arguments the Stirling expansion is rearranged into ``log1p`` terms so that the huge
    ``x log x`` contributions cancel analytically instead of numerically.
    """
    if a <= 0 or b <= 0:
        raise StatisticsError("log_beta requires a > 0 and b > 0")
    big, small = (a, b) if a >= b else (b, a)
    if big < _STIRLING_MIN:
        return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    if small < _STIRLING_MIN:
        # lgamma(small) + [lgamma(big) - lgamma(big + small)]
        return (
            math.lgamma(small)
            - (big - 0.5) * math.log1p(small / big)
            - small * math.log(big + small)
            + small
            + _stirling_remainder(big)
            - _stirling_remainder(big + small)
        )
    return (
        _HALF_LOG_2PI
        - (a - 0.5) * math.log1p(b / a)
        - (b - 0.5) * math.log1p(a / b)
        - 0.5 * math.log(a + b)
        + _stirling_remainder(a)
        + _stirling_remainder(b)
        - _stirling_remainder(a + b)
    )


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction for ``I_x(a, b)`` (modified Lentz). Converges fast for ``x < (a+1)/(a+b+2)``."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _CF_TINY:
        d = _CF_TINY
    d = 1.0 / d
    h = d
    for m in range(1, _CF_MAX_ITER + 1):
        m2 = 2 * m
        # Even step.
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _CF_TINY:
            d = _CF_TINY
        c = 1.0 + aa / c
        if abs(c) < _CF_TINY:
            c = _CF_TINY
        d = 1.0 / d
        h *= d * c
        # Odd step.
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _CF_TINY:
            d = _CF_TINY
        c = 1.0 + aa / c
        if abs(c) < _CF_TINY:
            c = _CF_TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _CF_EPS:
            return h
    raise StatisticsError(f"incomplete beta continued fraction did not converge (a={a}, b={b}, x={x})")


def _incomplete_beta_pair(
    x: float,
    a: float,
    b: float,
    y: float | None = None,
    *,
    log_x: float | None = None,
    log_y: float | None = None,
) -> tuple[float, float]:
    """Return ``(I_x(a,b), 1 - I_x(a,b))`` without cancellation.

    ``y`` may pass ``1 - x`` exactly, and ``log_x``/``log_y`` their logarithms (computed by the caller with
    ``log1p`` where that is more accurate), which matters when ``a`` or ``b`` is large.
    """
    if a <= 0 or b <= 0 or not (math.isfinite(a) and math.isfinite(b)):
        raise StatisticsError("incomplete beta requires finite a > 0 and b > 0")
    if y is None:
        y = 1.0 - x
    if x <= 0.0 and (log_x is None or not math.isfinite(log_x)):
        return 0.0, 1.0  # (a finite log_x means x merely underflowed: the log-domain front factor still works)
    if y <= 0.0 and (log_y is None or not math.isfinite(log_y)):
        return 1.0, 0.0
    lx = math.log(x) if log_x is None else log_x
    ly = math.log(y) if log_y is None else log_y
    log_front = a * lx + b * ly - log_beta(a, b)
    if x < (a + 1.0) / (a + b + 2.0):
        value = math.exp(log_front) * _beta_continued_fraction(a, b, x) / a
        return value, 1.0 - value
    complement = math.exp(log_front) * _beta_continued_fraction(b, a, y) / b
    return 1.0 - complement, complement


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function ``I_x(a, b)`` for ``0 <= x <= 1``."""
    if not (0.0 <= x <= 1.0):
        raise StatisticsError("x must lie in [0, 1]")
    return _incomplete_beta_pair(x, a, b)[0]


def normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def normal_cdf(x: float) -> float:
    """Standard normal CDF ``Φ(x)`` via ``erfc`` (accurate in both tails)."""
    return 0.5 * math.erfc(-x / _SQRT2)


def normal_sf(x: float) -> float:
    """Standard normal survival function ``1 - Φ(x)`` computed without cancellation."""
    return 0.5 * math.erfc(x / _SQRT2)


# Wichura (1988), Algorithm AS241 PPND16.
_AS241_A = (
    3.3871328727963666080e0,
    1.3314166789178437745e2,
    1.9715909503065514427e3,
    1.3731693765509461125e4,
    4.5921953931549871457e4,
    6.7265770927008700853e4,
    3.3430575583588128105e4,
    2.5090809287301226727e3,
)
_AS241_B = (
    1.0,
    4.2313330701600911252e1,
    6.8718700749205790830e2,
    5.3941960214247511077e3,
    2.1213794301586595867e4,
    3.9307895800092710610e4,
    2.8729085735721942674e4,
    5.2264952788528545610e3,
)
_AS241_C = (
    1.42343711074968357734e0,
    4.63033784615654529590e0,
    5.76949722146069140550e0,
    3.64784832476320460504e0,
    1.27045825245236838258e0,
    2.41780725177450611770e-1,
    2.27238449892691845833e-2,
    7.74545014278341407640e-4,
)
_AS241_D = (
    1.0,
    2.05319162663775882187e0,
    1.67638483018380384940e0,
    6.89767334985100004550e-1,
    1.48103976427480074590e-1,
    1.51986665636164571966e-2,
    5.47593808499534494600e-4,
    1.05075007164441684324e-9,
)
_AS241_E = (
    6.65790464350110377720e0,
    5.46378491116411436990e0,
    1.78482653991729133580e0,
    2.96560571828504891230e-1,
    2.65321895265761230930e-2,
    1.24266094738807843860e-3,
    2.71155556874348757815e-5,
    2.01033439929228813265e-7,
)
_AS241_F = (
    1.0,
    5.99832206555887937690e-1,
    1.36929880922735805310e-1,
    1.48753612908506148525e-2,
    7.86869131145613259100e-4,
    1.84631831751005468180e-5,
    1.42151175831644588870e-7,
    2.04426310338993978564e-15,
)


def _poly(coefficients: tuple[float, ...], x: float) -> float:
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * x + coefficient
    return result


def normal_ppf(p: float) -> float:
    """Standard normal quantile ``Φ⁻¹(p)`` (Wichura AS241 + one Halley step). ``p`` must be in (0, 1)."""
    if not (0.0 < p < 1.0):
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        raise StatisticsError("p must lie in [0, 1]")
    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        x = q * _poly(_AS241_A, r) / _poly(_AS241_B, r)
    else:
        r = p if q < 0 else 1.0 - p
        r = math.sqrt(-math.log(r))
        if r <= 5.0:
            r -= 1.6
            x = _poly(_AS241_C, r) / _poly(_AS241_D, r)
        else:
            r -= 5.0
            x = _poly(_AS241_E, r) / _poly(_AS241_F, r)
        if q < 0:
            x = -x
    # One Halley refinement step against the erfc-based CDF. For x > 0 the error Φ(x) - p is computed as
    # (1 - p) - sf(x); 1 - p is exact there (Sterbenz) so no cancellation occurs in the upper tail.
    if abs(x) < 37.0:
        err = normal_cdf(x) - p if x <= 0 else (1.0 - p) - normal_sf(x)
        u = err * _SQRT2PI * math.exp(0.5 * x * x)
        x = x - u / (1.0 + 0.5 * x * u)
    return x


def student_t_pdf(t: float, df: float) -> float:
    _check_df(df)
    if df > _T_NORMAL_DF:
        return normal_pdf(t)
    log_norm = math.lgamma((df + 1.0) / 2.0) - math.lgamma(df / 2.0) - 0.5 * math.log(df * math.pi)
    return math.exp(log_norm - (df + 1.0) / 2.0 * math.log1p(t * t / df))


def _check_df(df: float) -> None:
    if not (df > 0) or math.isnan(df):
        raise StatisticsError(f"degrees of freedom must be positive (got {df!r})")


def student_t_sf(t: float, df: float) -> float:
    """Survival function ``P(T > t)`` of Student's t with ``df`` degrees of freedom (df may be fractional)."""
    _check_df(df)
    if math.isnan(t):
        raise StatisticsError("t must not be NaN")
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    if df > _T_NORMAL_DF or math.isinf(df):
        return normal_sf(t)
    if t == 0.0:
        return 0.5
    abs_t = abs(t)
    if abs_t > 1e150:
        # t² would overflow: work with log(t²/df). For such ratios log1p(r) = log r + O(1/r) exactly in double
        # precision, x = 1/(1+r) may underflow (its log does not) and y = 1 - x == 1.
        log_ratio = 2.0 * math.log(abs_t) - math.log(df)
        log_x = -log_ratio
        x = math.exp(log_x)
        y, log_y = 1.0, -x
    else:
        ratio = t * t / df
        if ratio == 0.0:  # |t| so small that t² underflows: P(T > t) = 1/2 to double precision
            return 0.5
        x = 1.0 / (1.0 + ratio)
        y = ratio / (1.0 + ratio)
        log_x = -math.log1p(ratio)
        log_y = math.log(ratio) + log_x
    tail = 0.5 * _incomplete_beta_pair(x, df / 2.0, 0.5, y, log_x=log_x, log_y=log_y)[0]  # P(T > |t|)
    return tail if t > 0 else 1.0 - tail


def student_t_cdf(t: float, df: float) -> float:
    """Cumulative distribution function ``P(T <= t)`` of Student's t."""
    return student_t_sf(-t, df)


def student_t_ppf(p: float, df: float) -> float:
    """Quantile function of Student's t (inverse CDF)."""
    _check_df(df)
    if not (0.0 <= p <= 1.0) or math.isnan(p):
        raise StatisticsError("p must lie in [0, 1]")
    if p == 0.0:
        return -math.inf
    if p == 1.0:
        return math.inf
    if p == 0.5:
        return 0.0
    if df > _T_NORMAL_DF or math.isinf(df):
        return normal_ppf(p)
    if df == 1.0:
        return math.tan(math.pi * (p - 0.5))
    if df == 2.0:
        return (2.0 * p - 1.0) / math.sqrt(2.0 * p * (1.0 - p))
    upper_tail = min(p, 1.0 - p)  # solve sf(t) = upper_tail for t > 0, then mirror
    t = _t_upper_quantile(upper_tail, df)
    return t if p > 0.5 else -t


def _t_upper_quantile(q: float, df: float) -> float:
    """Positive ``t`` with ``P(T > t) = q`` (0 < q < 0.5) via bracketed Newton iteration."""
    lo, hi = 0.0, max(1.0, abs(normal_ppf(q)))  # Φ⁻¹(q) is accurate even for tiny q (1 - q would round to 1)
    while student_t_sf(hi, df) > q:
        lo = hi
        hi *= 2.0
        if hi > 1e307:
            return math.inf  # the quantile is beyond the double-precision range
    t = 0.5 * (lo + hi)
    for _ in range(200):
        f = student_t_sf(t, df) - q  # decreasing in t
        if f > 0:
            lo = t
        else:
            hi = t
        density = student_t_pdf(t, df)
        candidate = t + f / density if density > 0 else math.nan
        t_next = candidate if lo < candidate < hi else 0.5 * (lo + hi)
        if abs(t_next - t) <= 1e-15 * max(1.0, abs(t)):
            return t_next
        t = t_next
    return t


def _t_p_value(statistic: float, df: float, alternative: str) -> float:
    if math.isnan(statistic):
        return 1.0
    if alternative == "greater":
        return student_t_sf(statistic, df)
    if alternative == "less":
        return student_t_cdf(statistic, df)
    return min(1.0, 2.0 * student_t_sf(abs(statistic), df))


def _normal_p_value(z: float, alternative: str) -> float:
    if alternative == "greater":
        return normal_sf(z)
    if alternative == "less":
        return normal_cdf(z)
    return min(1.0, 2.0 * normal_sf(abs(z)))


# ---------------------------------------------------------------------------------------------
# t tests
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TTestResult:
    """Result of a t test. ``estimate`` is ``mean(a) - mean(b)`` (or the mean paired difference).

    ``ci_low``/``ci_high`` form a two-sided confidence interval for ``estimate`` at ``ci_level`` regardless of
    ``alternative``. ``degenerate`` is set when the standard error is zero (all values identical within each
    sample): the statistic is then ``0`` (equal means, p = 1) or ``±inf`` (different means, p = 0).
    """

    test: str
    statistic: float
    df: float
    p_value: float
    alternative: str
    estimate: float
    standard_error: float
    ci_low: float
    ci_high: float
    ci_level: float
    n_a: int
    n_b: int
    degenerate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _t_result(
    test: str,
    estimate: float,
    se: float,
    df: float,
    alternative: str,
    ci_level: float,
    n_a: int,
    n_b: int,
) -> TTestResult:
    if se == 0.0:
        if estimate == 0.0:
            statistic, p_value = 0.0, 1.0
        else:
            statistic = math.copysign(math.inf, estimate)
            favourable = (
                alternative == "two-sided"
                or (alternative == "greater" and estimate > 0)
                or (alternative == "less" and estimate < 0)
            )
            p_value = 0.0 if favourable else 1.0
        return TTestResult(
            test, statistic, df, p_value, alternative, estimate, 0.0, estimate, estimate, ci_level, n_a, n_b, True
        )
    statistic = estimate / se
    p_value = _t_p_value(statistic, df, alternative)
    t_crit = student_t_ppf(1.0 - (1.0 - ci_level) / 2.0, df)
    return TTestResult(
        test,
        statistic,
        df,
        p_value,
        alternative,
        estimate,
        se,
        estimate - t_crit * se,
        estimate + t_crit * se,
        ci_level,
        n_a,
        n_b,
    )


def welch_t_test(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
    *,
    alternative: Alternative = "two-sided",
    ci_level: float = 0.95,
) -> TTestResult:
    """Welch's unequal-variance t test of ``mean(a) - mean(b)`` with Welch–Satterthwaite df."""
    _check_alternative(alternative)
    _check_level(ci_level)
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    va, vb = variance(xa) / xa.size, variance(xb) / xb.size
    se = math.sqrt(va + vb)
    estimate = mean(xa) - mean(xb)
    if va + vb == 0.0:
        df = float(xa.size + xb.size - 2)
    else:
        df = (va + vb) ** 2 / (va * va / (xa.size - 1) + vb * vb / (xb.size - 1))
    return _t_result("welch_t", estimate, se, df, alternative, ci_level, int(xa.size), int(xb.size))


def student_t_test(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
    *,
    alternative: Alternative = "two-sided",
    ci_level: float = 0.95,
) -> TTestResult:
    """Classic pooled-variance two-sample t test (equal variances assumed). Prefer :func:`welch_t_test`."""
    _check_alternative(alternative)
    _check_level(ci_level)
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    df = float(xa.size + xb.size - 2)
    pooled = ((xa.size - 1) * variance(xa) + (xb.size - 1) * variance(xb)) / df
    se = math.sqrt(pooled * (1.0 / xa.size + 1.0 / xb.size))
    return _t_result("student_t", mean(xa) - mean(xb), se, df, alternative, ci_level, int(xa.size), int(xb.size))


def one_sample_t_test(
    values: Sequence[float] | np.ndarray,
    popmean: float = 0.0,
    *,
    alternative: Alternative = "two-sided",
    ci_level: float = 0.95,
) -> TTestResult:
    """One-sample t test of ``mean(values) - popmean``."""
    _check_alternative(alternative)
    _check_level(ci_level)
    x = _as_array(values, "values", min_n=2)
    se = standard_error(x)
    result = _t_result("one_sample_t", mean(x) - popmean, se, float(x.size - 1), alternative, ci_level, int(x.size), 0)
    return result


def paired_t_test(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
    *,
    alternative: Alternative = "two-sided",
    ci_level: float = 0.95,
) -> TTestResult:
    """Paired t test on the differences ``a[i] - b[i]`` (e.g. candidate vs baseline run with the same seed)."""
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    if xa.size != xb.size:
        raise StatisticsError("paired samples must have the same length")
    result = one_sample_t_test(xa - xb, 0.0, alternative=alternative, ci_level=ci_level)
    return TTestResult(
        "paired_t",
        result.statistic,
        result.df,
        result.p_value,
        result.alternative,
        result.estimate,
        result.standard_error,
        result.ci_low,
        result.ci_high,
        result.ci_level,
        int(xa.size),
        int(xb.size),
        result.degenerate,
    )


def mean_confidence_interval(values: Sequence[float] | np.ndarray, ci_level: float = 0.95) -> tuple[float, float]:
    """Two-sided t confidence interval for the mean."""
    result = one_sample_t_test(values, 0.0, ci_level=ci_level)
    return result.ci_low, result.ci_high


# ---------------------------------------------------------------------------------------------
# Rank-based tests
# ---------------------------------------------------------------------------------------------
def rankdata(values: Sequence[float] | np.ndarray) -> np.ndarray:
    """Ranks starting at 1 with ties assigned their average rank."""
    arr = _as_array(values)
    order = np.argsort(arr, kind="mergesort")
    sorted_values = arr[order]
    ranks = np.empty(arr.size, dtype=float)
    i = 0
    n = arr.size
    while i < n:
        j = i
        while j + 1 < n and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def _tie_counts(values: np.ndarray) -> np.ndarray:
    _, counts = np.unique(values, return_counts=True)
    return counts.astype(float)


def mann_whitney_u_distribution(n1: int, n2: int) -> np.ndarray:
    """Exact null probability mass of U (for sample 1) on ``0..n1*n2`` assuming no ties.

    Uses the recursion ``p(u; m, n) = m/(m+n) · p(u-n; m-1, n) + n/(m+n) · p(u; m, n-1)`` on probabilities
    (not counts), so no large integers are formed.
    """
    if n1 < 0 or n2 < 0:
        raise StatisticsError("sample sizes must be non-negative")
    # prev[j] holds the distribution for (i-1, j); build row i from prev and the current row.
    prev = [np.ones(1) for _ in range(n2 + 1)]  # i = 0: U == 0 with probability 1
    for i in range(1, n1 + 1):
        row: list[np.ndarray] = [np.ones(1)]  # j = 0: U == 0
        for j in range(1, n2 + 1):
            dist = np.zeros(i * j + 1)
            from_prev = prev[j]  # (i-1, j): shift by j
            dist[j : j + from_prev.size] += (i / (i + j)) * from_prev
            left = row[j - 1]  # (i, j-1)
            dist[: left.size] += (j / (i + j)) * left
            row.append(dist)
        prev = row
    return prev[n2]


@dataclass(frozen=True)
class MannWhitneyResult:
    """Mann–Whitney U test. ``u`` is U for sample ``a`` (number of pairs with a > b, ties counted 1/2)."""

    u: float
    u_b: float
    p_value: float
    alternative: str
    method: str  # exact | asymptotic
    z: float | None
    n_a: int
    n_b: int
    ties: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def mann_whitney_u(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
    *,
    alternative: Alternative = "two-sided",
    method: Literal["auto", "exact", "asymptotic"] = "auto",
    use_continuity: bool = True,
) -> MannWhitneyResult:
    """Mann–Whitney U test (``a`` stochastically greater than ``b`` for ``alternative="greater"``).

    ``method="auto"`` uses the exact null distribution when there are no ties and at least one sample has
    ≤ 8 observations (the rule used by common reference implementations), otherwise the tie-corrected normal
    approximation with continuity correction.
    """
    _check_alternative(alternative)
    xa = _as_array(a, "a", min_n=1)
    xb = _as_array(b, "b", min_n=1)
    n1, n2 = int(xa.size), int(xb.size)
    combined = np.concatenate([xa, xb])
    ranks = rankdata(combined)
    r1 = math.fsum(ranks[:n1].tolist())
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    tie_counts = _tie_counts(combined)
    has_ties = bool(np.any(tie_counts > 1))
    chosen = method
    if method == "auto":
        chosen = "asymptotic" if (n1 > 8 and n2 > 8) or has_ties else "exact"
    if chosen == "exact" and (has_ties or n1 * n2 > _MWU_EXACT_MAX_CELLS):
        chosen = "asymptotic"  # exact distribution assumes no ties / bounded cost
    if chosen == "exact":
        dist = mann_whitney_u_distribution(n1, n2)
        sf = np.cumsum(dist[::-1])[::-1]  # sf[k] = P(U >= k)
        if alternative == "greater":
            p = float(sf[round(u1)])
        elif alternative == "less":
            p = float(sf[round(u2)])
        else:
            p = 2.0 * float(sf[round(max(u1, u2))])
        return MannWhitneyResult(u1, u2, min(1.0, max(0.0, p)), alternative, "exact", None, n1, n2, has_ties)
    n = n1 + n2
    mu = n1 * n2 / 2.0
    tie_term = float(np.sum(tie_counts**3 - tie_counts))
    sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))) if n > 1 else 0.0
    if sigma == 0.0:
        return MannWhitneyResult(u1, u2, 1.0, alternative, "asymptotic", 0.0, n1, n2, has_ties)
    z_report = (u1 - mu) / sigma
    correction = 0.5 if use_continuity else 0.0
    if alternative == "greater":
        z = (u1 - mu - correction) / sigma
        p = normal_sf(z)
    elif alternative == "less":
        z = (u2 - mu - correction) / sigma
        p = normal_sf(z)
    else:
        deviation = abs(u1 - mu)
        z = max(deviation - correction, 0.0) / sigma
        p = 2.0 * normal_sf(z)
    return MannWhitneyResult(u1, u2, min(1.0, max(0.0, p)), alternative, "asymptotic", z_report, n1, n2, has_ties)


@dataclass(frozen=True)
class HodgesLehmannResult:
    """Hodges–Lehmann location shift ``a - b`` with its distribution-free confidence interval.

    ``ci_low``/``ci_high`` are ``None`` when the requested level cannot be achieved with these sample sizes
    (e.g. 3 vs 3 observations at 95%); ``achieved_level`` is the actual coverage of the reported interval.
    """

    estimate: float
    ci_low: float | None
    ci_high: float | None
    ci_level: float
    achieved_level: float | None
    method: str


def hodges_lehmann(
    a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray, *, ci_level: float = 0.95
) -> HodgesLehmannResult:
    """Median of all pairwise differences ``a_i - b_j`` and the Mann–Whitney-inverted CI (Bauer 1972).

    With ``D_(1) <= ... <= D_(N)`` the sorted differences, the interval is ``[D_(c), D_(N-c+1)]`` where ``c`` is
    the largest integer with ``P(U <= c - 1) <= α/2`` under the null U distribution (exact when feasible and
    tie-free, otherwise the tie-corrected normal approximation).
    """
    _check_level(ci_level)
    xa = _as_array(a, "a", min_n=1)
    xb = _as_array(b, "b", min_n=1)
    diffs = np.sort((xa[:, None] - xb[None, :]).ravel())
    n_pairs = diffs.size
    estimate = float(np.median(diffs))
    alpha = 1.0 - ci_level
    n1, n2 = int(xa.size), int(xb.size)
    combined = np.concatenate([xa, xb])
    has_ties = bool(np.any(_tie_counts(combined) > 1))
    if not has_ties and n1 * n2 <= _MWU_EXACT_MAX_CELLS:
        cdf = np.cumsum(mann_whitney_u_distribution(n1, n2))  # cdf[k] = P(U <= k)
        c = 0
        while c < n_pairs // 2 + 1 and (cdf[c] if c < cdf.size else 1.0) <= alpha / 2.0:
            c += 1  # after the loop: P(U <= c-1) <= α/2 < P(U <= c)
        method = "exact"
        achieved = 1.0 - 2.0 * float(cdf[c - 1]) if c >= 1 else None
    else:
        n = n1 + n2
        tie_term = float(np.sum(_tie_counts(combined) ** 3 - _tie_counts(combined)))
        sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))) if n > 1 else 0.0
        z = normal_ppf(1.0 - alpha / 2.0)
        c = math.floor(n_pairs / 2.0 - z * sigma)
        method = "asymptotic"
        achieved = ci_level if c >= 1 else None
    if c < 1 or c > n_pairs - c + 1:
        return HodgesLehmannResult(estimate, None, None, ci_level, None, method)
    return HodgesLehmannResult(estimate, float(diffs[c - 1]), float(diffs[n_pairs - c]), ci_level, achieved, method)


# ---------------------------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------------------------
BootstrapStatistic = Literal["mean", "median"]


@dataclass(frozen=True)
class BootstrapResult:
    """Bootstrap confidence interval. ``estimate`` is ``stat(a)`` or ``stat(a) - stat(b)``."""

    estimate: float
    ci_low: float
    ci_high: float
    ci_level: float
    n_resamples: int
    seed: int
    statistic: str
    method: str  # percentile | bca
    standard_error: float
    paired: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stat_fn(statistic: str) -> Any:
    if statistic == "mean":
        return np.mean
    if statistic == "median":
        return np.median
    raise StatisticsError("statistic must be 'mean' or 'median'")


def _resample_stats(arr: np.ndarray, n_resamples: int, rng: np.random.Generator, fn: Any) -> np.ndarray:
    n = arr.size
    block = max(1, _BOOTSTRAP_BLOCK_VALUES // max(n, 1))
    out = np.empty(n_resamples)
    done = 0
    while done < n_resamples:
        rows = min(block, n_resamples - done)
        idx = rng.integers(0, n, size=(rows, n))
        out[done : done + rows] = fn(arr[idx], axis=1)
        done += rows
    return out


def _check_resamples(n_resamples: int) -> None:
    if not isinstance(n_resamples, int) or n_resamples < 100 or n_resamples > 1_000_000:
        raise StatisticsError("n_resamples must be an integer in [100, 1_000_000]")


def bootstrap_ci(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray | None = None,
    *,
    statistic: BootstrapStatistic = "mean",
    n_resamples: int = 2000,
    ci_level: float = 0.95,
    seed: int = 0,
    paired: bool = False,
    method: Literal["percentile", "bca"] = "percentile",
) -> BootstrapResult:
    """Seeded bootstrap confidence interval for ``stat(a)`` or for ``stat(a) - stat(b)``.

    * independent two-sample (default): ``a`` and ``b`` are resampled independently;
    * ``paired=True``: pairs ``(a[i], b[i])`` are resampled jointly (the statistic of the differences);
    * ``method="bca"``: bias-corrected and accelerated interval (Efron 1987) with jackknife acceleration;
      falls back to the percentile interval when the bias correction is undefined (all resamples on one
      side of the estimate), which is reported in ``method``.

    Determinism: the RNG is ``numpy.random.default_rng(seed)``; identical inputs yield identical intervals.
    """
    _check_level(ci_level)
    _check_resamples(n_resamples)
    if method not in ("percentile", "bca"):
        raise StatisticsError("method must be 'percentile' or 'bca'")
    fn = _stat_fn(statistic)
    xa = _as_array(a, "a", min_n=1)
    rng = np.random.default_rng(seed)
    if b is None or paired:
        if b is not None:
            xb = _as_array(b, "b", min_n=1)
            if xb.size != xa.size:
                raise StatisticsError("paired samples must have the same length")
            data = xa - xb
        else:
            data = xa
        estimate = float(fn(data))
        boot = _resample_stats(data, n_resamples, rng, fn)
        jackknife = (
            np.array([fn(np.delete(data, i)) for i in range(data.size)]) if method == "bca" and data.size > 1 else None
        )
    else:
        xb = _as_array(b, "b", min_n=1)
        estimate = float(fn(xa) - fn(xb))
        boot = _resample_stats(xa, n_resamples, rng, fn) - _resample_stats(xb, n_resamples, rng, fn)
        jackknife = None
        if method == "bca" and xa.size > 1 and xb.size > 1:
            stat_b, stat_a = float(fn(xb)), float(fn(xa))
            jackknife = np.concatenate(
                [
                    np.array([fn(np.delete(xa, i)) - stat_b for i in range(xa.size)]),
                    np.array([stat_a - fn(np.delete(xb, j)) for j in range(xb.size)]),
                ]
            )
    alpha = 1.0 - ci_level
    lo_q, hi_q = alpha / 2.0, 1.0 - alpha / 2.0
    used = "percentile"
    if method == "bca" and jackknife is not None:
        below = float(np.mean(boot < estimate)) + 0.5 * float(np.mean(boot == estimate))
        if 0.0 < below < 1.0:
            z0 = normal_ppf(below)
            centred = jackknife.mean() - jackknife
            denom = 6.0 * float(np.sum(centred**2)) ** 1.5
            accel = float(np.sum(centred**3)) / denom if denom > 0 else 0.0

            def adjust(q: float) -> float:
                zq = normal_ppf(q)
                return normal_cdf(z0 + (z0 + zq) / (1.0 - accel * (z0 + zq)))

            lo_q, hi_q = adjust(lo_q), adjust(hi_q)
            used = "bca"
    low, high = np.quantile(boot, [lo_q, hi_q])
    return BootstrapResult(
        estimate=estimate,
        ci_low=float(low),
        ci_high=float(high),
        ci_level=ci_level,
        n_resamples=n_resamples,
        seed=seed,
        statistic=statistic,
        method=used,
        standard_error=float(np.std(boot, ddof=1)),
        paired=paired,
    )


@dataclass(frozen=True)
class BootstrapTestResult:
    statistic: float
    p_value: float
    alternative: str
    n_resamples: int
    seed: int


def _welch_statistic(xa: np.ndarray, xb: np.ndarray) -> np.ndarray:
    """Vectorised Welch t statistic over the rows of ``xa`` and ``xb`` (0/0 → 0, x/0 → ±inf)."""
    diff = xa.mean(axis=1) - xb.mean(axis=1)
    se = np.sqrt(xa.var(axis=1, ddof=1) / xa.shape[1] + xb.var(axis=1, ddof=1) / xb.shape[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, diff / np.where(se > 0, se, 1.0), np.where(diff == 0, 0.0, np.sign(diff) * np.inf))
    return np.asarray(t, dtype=float)


def bootstrap_test(
    a: Sequence[float] | np.ndarray,
    b: Sequence[float] | np.ndarray,
    *,
    n_resamples: int = 2000,
    seed: int = 0,
    alternative: Alternative = "two-sided",
) -> BootstrapTestResult:
    """Bootstrap hypothesis test for equal means (Efron & Tibshirani 1993, Algorithm 16.2).

    Both samples are shifted to the pooled mean (imposing H0), resampled independently, and the studentized
    (Welch) statistic of each resample is compared with the observed one. The p-value uses the ``(k + 1) /
    (B + 1)`` estimator so it is never exactly zero.
    """
    _check_alternative(alternative)
    _check_resamples(n_resamples)
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    observed = float(_welch_statistic(xa[None, :], xb[None, :])[0])
    pooled = mean(np.concatenate([xa, xb]))
    sa = xa - xa.mean() + pooled
    sb = xb - xb.mean() + pooled
    rng = np.random.default_rng(seed)
    t_star = np.empty(n_resamples)
    block = max(1, _BOOTSTRAP_BLOCK_VALUES // (xa.size + xb.size))
    done = 0
    while done < n_resamples:
        rows = min(block, n_resamples - done)
        ra = sa[rng.integers(0, sa.size, size=(rows, sa.size))]
        rb = sb[rng.integers(0, sb.size, size=(rows, sb.size))]
        t_star[done : done + rows] = _welch_statistic(ra, rb)
        done += rows
    if alternative == "greater":
        exceed = int(np.sum(t_star >= observed))
    elif alternative == "less":
        exceed = int(np.sum(t_star <= observed))
    else:
        exceed = int(np.sum(np.abs(t_star) >= abs(observed)))
    p_value = (exceed + 1) / (n_resamples + 1)
    return BootstrapTestResult(observed, min(1.0, p_value), alternative, n_resamples, seed)


# ---------------------------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------------------------
def pooled_sd(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    df = xa.size + xb.size - 2
    return math.sqrt(((xa.size - 1) * variance(xa) + (xb.size - 1) * variance(xb)) / df)


def _standardise(diff: float, scale: float) -> float:
    if scale == 0.0:
        return 0.0 if diff == 0.0 else math.copysign(math.inf, diff)
    return diff / scale


def cohens_d(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    """Cohen's d = (mean(a) - mean(b)) / pooled SD (ddof=1). ±inf when both samples are constant."""
    return _standardise(mean(a) - mean(b), pooled_sd(a, b))


def hedges_correction(df: float) -> float:
    """Exact small-sample bias correction J(df) = Γ(df/2) / (sqrt(df/2) Γ((df-1)/2))."""
    if df <= 1:
        raise StatisticsError("Hedges' correction needs df > 1")
    return math.exp(math.lgamma(df / 2.0) - 0.5 * math.log(df / 2.0) - math.lgamma((df - 1.0) / 2.0))


def hedges_g(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    """Hedges' g: Cohen's d multiplied by the exact bias correction J(n_a + n_b - 2)."""
    xa = _as_array(a, "a", min_n=2)
    xb = _as_array(b, "b", min_n=2)
    d = cohens_d(xa, xb)
    if math.isinf(d):
        return d
    return d * hedges_correction(xa.size + xb.size - 2)


def cohens_dz(differences: Sequence[float] | np.ndarray) -> float:
    """Standardised mean of paired differences, mean(d) / sd(d)."""
    x = _as_array(differences, "differences", min_n=2)
    return _standardise(mean(x), sd(x))


def cliffs_delta(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    """Cliff's delta = P(a > b) - P(a < b) over all pairs, in [-1, 1] (equals the rank-biserial correlation)."""
    xa = _as_array(a, "a", min_n=1)
    xb = np.sort(_as_array(b, "b", min_n=1))
    less_than = np.searchsorted(xb, xa, side="left")  # b values strictly below each a
    greater_than = xb.size - np.searchsorted(xb, xa, side="right")  # b values strictly above each a
    return float((int(less_than.sum()) - int(greater_than.sum())) / (xa.size * xb.size))


# ---------------------------------------------------------------------------------------------
# Multiple-comparison corrections
# ---------------------------------------------------------------------------------------------
def _check_p_values(p_values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(p_values, dtype=float)
    if arr.ndim != 1:
        raise StatisticsError("p-values must be one-dimensional")
    if arr.size and (not np.all(np.isfinite(arr)) or np.any(arr < 0) or np.any(arr > 1)):
        raise StatisticsError("p-values must lie in [0, 1]")
    return arr


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (monotone, capped at 1), in the input order."""
    p = _check_p_values(p_values)
    m = p.size
    if m == 0:
        return []
    order = np.argsort(p, kind="mergesort")
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[idx]))
        adjusted[idx] = running
    return adjusted.tolist()


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini–Hochberg (FDR) step-up adjusted p-values (monotone, capped at 1), in the input order."""
    p = _check_p_values(p_values)
    m = p.size
    if m == 0:
        return []
    order = np.argsort(p, kind="mergesort")
    adjusted = np.empty(m)
    running = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        running = min(running, min(1.0, m * p[idx] / (rank + 1)))
        adjusted[idx] = running
    return adjusted.tolist()


def adjust_p_values(p_values: Sequence[float], method: Literal["holm", "bh", "none"]) -> list[float]:
    """Dispatch to Holm, Benjamini–Hochberg or no correction."""
    if method == "holm":
        return holm_bonferroni(p_values)
    if method == "bh":
        return benjamini_hochberg(p_values)
    if method == "none":
        return _check_p_values(p_values).tolist()
    raise StatisticsError(f"unknown correction {method!r}")


# ---------------------------------------------------------------------------------------------
# Proportions & planning
# ---------------------------------------------------------------------------------------------
def wilson_interval(successes: int, n: int, ci_level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. ``n == 0`` returns the uninformative ``(0, 1)``."""
    _check_level(ci_level)
    if n < 0 or successes < 0 or successes > n:
        raise StatisticsError("need 0 <= successes <= n")
    if n == 0:
        return (0.0, 1.0)
    z = normal_ppf(1.0 - (1.0 - ci_level) / 2.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    low = 0.0 if successes == 0 else max(0.0, centre - margin)
    high = 1.0 if successes == n else min(1.0, centre + margin)
    return (low, high)


def required_seeds_estimate(
    effect_size: float,
    alpha: float = 0.05,
    power: float = 0.8,
    *,
    two_sided: bool = True,
    paired: bool = False,
    small_sample_correction: bool = True,
) -> int:
    """Seeds (runs) per arm needed to detect a standardised effect ``effect_size`` (normal approximation).

    Two independent arms: ``n = 2 (z_α + z_β)² / d²``; paired design: ``n = (z_α + z_β)² / d²``, where
    ``z_α = Φ⁻¹(1 - α/2)`` (two-sided) and ``z_β = Φ⁻¹(power)``. With ``small_sample_correction`` Guenther's
    (1981) term ``z_α²/4`` (two arms) or ``z_α²/2`` (paired) is added, which reproduces exact noncentral-t
    sample sizes closely (d = 0.5, α = 0.05, power 0.8 → 64 per arm). Never returns fewer than 2.
    """
    _check_level(alpha, "alpha")
    _check_level(power, "power")
    d = abs(float(effect_size))
    if not math.isfinite(d) or d == 0.0:
        raise StatisticsError("effect_size must be finite and non-zero")
    z_alpha = normal_ppf(1.0 - alpha / 2.0) if two_sided else normal_ppf(1.0 - alpha)
    z_beta = normal_ppf(power)
    base = ((z_alpha + z_beta) / d) ** 2
    n = base if paired else 2.0 * base
    if small_sample_correction:
        n += z_alpha**2 / (2.0 if paired else 4.0)
    return max(2, math.ceil(n - 1e-9))
