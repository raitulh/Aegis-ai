"""Statistics engine vs published tables and reference implementations.

Reference values were produced once with SciPy 1.18 / statsmodels 0.15 (never imported here) and are
hard-coded; closed forms (Cauchy, df=2, incomplete-beta special cases) are checked exactly.
"""

from __future__ import annotations

import itertools
import math

import pytest

from engines.lab import statistics as st

A = [19.8, 20.4, 19.6, 17.8, 18.5, 18.9, 18.3, 18.9, 19.5, 22.0]
B = [
    28.2,
    26.6,
    20.1,
    23.3,
    25.2,
    22.1,
    17.7,
    27.6,
    20.6,
    13.7,
    23.2,
    17.5,
    20.6,
    18.0,
    23.9,
    21.6,
    24.3,
    20.4,
    23.9,
    13.3,
]


def close(x: float | None, y: float, rel: float = 1e-10, abs_: float = 1e-12) -> bool:
    return x is not None and math.isclose(x, y, rel_tol=rel, abs_tol=abs_)


# ---------------------------------------------------------------------------------------------
# Special functions
# ---------------------------------------------------------------------------------------------
def test_t_table_value_two_sided_p_is_005():
    # Published t-table: t(0.975, df=10) = 2.228 → two-sided p ≈ 0.05.
    p = 2 * st.student_t_sf(2.228, 10)
    assert close(p, 0.05001177181711132, rel=1e-12)
    assert abs(p - 0.05) < 1e-4


@pytest.mark.parametrize(
    ("q", "df", "expected"),
    [
        (0.975, 10, 2.228138851986274),
        (0.975, 1, 12.706204736174694),
        (0.995, 5, 4.032142983555228),
        (0.975, 30, 2.0422724563012378),
        (0.95, 4, 2.1318467863266495),
        (0.975, 1000, 1.9623390808264083),
    ],
)
def test_t_quantiles_match_tables(q, df, expected):
    assert close(st.student_t_ppf(q, df), expected, rel=1e-12)
    assert close(st.student_t_cdf(st.student_t_ppf(q, df), df), q, rel=1e-12)


def test_t_cdf_closed_forms():
    for t in (-30.0, -2.5, -0.3, 0.0, 0.7, 4.0, 100.0):
        assert close(st.student_t_cdf(t, 1), 0.5 + math.atan(t) / math.pi, rel=1e-12, abs_=1e-15)  # Cauchy
        assert close(st.student_t_cdf(t, 2), 0.5 + t / (2 * math.sqrt(2 + t * t)), rel=1e-12, abs_=1e-15)


def test_t_cdf_tail_accuracy_and_symmetry():
    assert close(st.student_t_cdf(-40, 7), 7.951089992425182e-10, rel=1e-12)
    assert close(st.student_t_cdf(1.5, 1e6), 0.9331926408816036, rel=1e-12)  # large df (no lgamma cancellation)
    for t, df in ((1.3, 3.3), (2.2, 17.5), (-0.4, 0.8)):
        assert close(st.student_t_cdf(t, df) + st.student_t_cdf(-t, df), 1.0, rel=1e-14)
    assert st.student_t_sf(math.inf, 5) == 0.0
    assert st.student_t_cdf(0.0, 3) == 0.5


def test_extreme_tails_verified_with_arbitrary_precision():
    # 50-digit mpmath references. SciPy's own t.ppf is off by a factor ~2.7 at this p; ours is not.
    assert close(st.student_t_sf(1e200, 0.5), 3.207009754142229e-101, rel=1e-12)  # t² overflows a double
    assert st.student_t_sf(1e160, 3) == 0.0  # true value 1.1e-480 underflows
    q = st.student_t_ppf(3.1574976712450556e-166, 2.1539233254742394)
    assert close(q, -5.225253938408926e76, rel=1e-9)
    assert close(st.student_t_cdf(q, 2.1539233254742394), 3.1574976712450556e-166, rel=1e-9)
    assert st.student_t_ppf(1e-300, 0.5) == -math.inf  # beyond the double range


def test_normal_functions():
    assert close(st.normal_ppf(0.975), 1.959963984540054, rel=1e-15)
    assert close(st.normal_cdf(1.0), 0.8413447460685429, rel=1e-15)
    assert close(st.normal_sf(5.0), 2.866515718791933e-07, rel=1e-13)
    assert close(st.normal_ppf(1e-10), -6.361340902404056, rel=1e-14)
    assert close(st.normal_ppf(1e-300), -37.0470962993612, rel=1e-14)
    for p in (1e-12, 0.01, 0.3, 0.5, 0.77, 0.999):
        assert close(st.normal_cdf(st.normal_ppf(p)), p, rel=1e-13)
    assert st.normal_ppf(0.0) == -math.inf and st.normal_ppf(1.0) == math.inf


def test_incomplete_beta_special_cases_and_reference():
    x = 0.37
    assert close(st.regularized_incomplete_beta(x, 1.0, 4.0), 1 - (1 - x) ** 4, rel=1e-13)
    assert close(st.regularized_incomplete_beta(x, 3.0, 1.0), x**3, rel=1e-13)
    assert close(st.regularized_incomplete_beta(0.5, 7.5, 7.5), 0.5, rel=1e-13)
    assert close(st.regularized_incomplete_beta(0.4, 2.5, 3.5), 0.4869041915261176, rel=1e-12)
    assert close(st.regularized_incomplete_beta(0.3, 10, 20), 0.3640040810719437, rel=1e-12)
    assert st.regularized_incomplete_beta(0.0, 2, 3) == 0.0 and st.regularized_incomplete_beta(1.0, 2, 3) == 1.0
    assert close(st.log_beta(1000, 1000), math.lgamma(1000) * 2 - math.lgamma(2000), rel=1e-12)


# ---------------------------------------------------------------------------------------------
# t tests
# ---------------------------------------------------------------------------------------------
def test_welch_t_test_matches_reference():
    r = st.welch_t_test(A, B)
    assert close(r.statistic, -2.225512039969852)
    assert close(r.p_value, 0.035484530830010394, rel=1e-9)
    assert close(r.df, 24.524634944257343)
    assert close(r.ci_low, -4.276458650120555, rel=1e-9)
    assert close(r.ci_high, -0.16354134987944358, rel=1e-8)
    assert close(st.welch_t_test(A, B, alternative="less").p_value, 0.017742265415005197, rel=1e-9)
    assert close(st.welch_t_test(A, B, alternative="greater").p_value, 1 - 0.017742265415005197, rel=1e-9)


def test_student_and_one_sample_t():
    r = st.student_t_test(A, B)
    assert close(r.statistic, -1.6544465858664001) and close(r.p_value, 0.10920550418088569, rel=1e-9)
    r = st.one_sample_t_test([0.81, 0.79, 0.84, 0.80, 0.82], 0.8)
    assert close(r.statistic, 1.3949716649258352) and close(r.p_value, 0.23549636025944204, rel=1e-9)


def test_paired_t_test_matches_reference():
    x = [0.81, 0.79, 0.84, 0.80, 0.82]
    y = [0.78, 0.77, 0.80, 0.79, 0.78]
    r = st.paired_t_test(x, y)
    assert close(r.statistic, 4.8019603839902585)
    assert close(r.p_value, 0.008635792607551472, rel=1e-9)
    assert close(r.ci_low, 0.011810682152912994, rel=1e-9) and close(r.ci_high, 0.04418931784708696, rel=1e-9)
    with pytest.raises(st.StatisticsError):
        st.paired_t_test([1, 2, 3], [1, 2])


def test_t_test_degenerate_zero_variance():
    same = st.welch_t_test([0.8, 0.8, 0.8], [0.8, 0.8, 0.8])
    assert same.degenerate and same.statistic == 0.0 and same.p_value == 1.0
    diff = st.welch_t_test([0.9, 0.9, 0.9], [0.8, 0.8, 0.8])
    assert diff.degenerate and diff.statistic == math.inf and diff.p_value == 0.0
    assert st.welch_t_test([0.9, 0.9, 0.9], [0.8, 0.8, 0.8], alternative="less").p_value == 1.0


def test_input_validation():
    with pytest.raises(st.StatisticsError):
        st.welch_t_test([1.0, math.nan], [1.0, 2.0])
    with pytest.raises(st.StatisticsError):
        st.welch_t_test([1.0], [1.0, 2.0])
    with pytest.raises(st.StatisticsError):
        st.welch_t_test([1.0, 2.0], [1.0, 2.0], alternative="bigger")  # type: ignore[arg-type]
    with pytest.raises(st.StatisticsError):
        st.welch_t_test([1.0, 2.0], [1.0, 2.0], ci_level=1.0)


def test_descriptive_statistics_are_numerically_stable():
    shifted = [1e9 + v for v in (4.0, 7.0, 13.0, 16.0)]
    assert close(st.variance(shifted), 30.0, rel=1e-12)  # naive sum-of-squares would lose all digits
    assert close(st.mean([0.1] * 10), 0.1, rel=1e-15)
    d = st.describe([1.0, 2.0, 3.0, 4.0])
    assert d["n"] == 4 and d["median"] == 2.5 and close(float(d["sd"] or 0.0), math.sqrt(5 / 3))
    assert st.describe([])["mean"] is None


# ---------------------------------------------------------------------------------------------
# Mann-Whitney & Hodges-Lehmann
# ---------------------------------------------------------------------------------------------
def test_mann_whitney_exact_small_samples():
    r = st.mann_whitney_u([1, 2, 3], [4, 5, 6])
    assert r.method == "exact" and r.u == 0.0 and close(r.p_value, 0.1)
    a, b = [1.1, 2.3, 3.8, 4.4, 7.0], [2.9, 5.5, 6.1, 8.2, 9.9, 10.5]
    assert close(st.mann_whitney_u(a, b).p_value, 0.08225108225108226, rel=1e-12)
    assert close(st.mann_whitney_u(a, b, alternative="less").p_value, 0.04112554112554113, rel=1e-12)


def test_mann_whitney_asymptotic_with_ties():
    t1 = [1, 2, 2, 3, 4, 4, 4, 5, 6, 7, 8]
    t2 = [3, 4, 5, 5, 6, 7, 8, 8, 9, 10]
    r = st.mann_whitney_u(t1, t2)
    assert r.method == "asymptotic" and r.ties and r.u == 25.0
    assert close(r.p_value, 0.036409329456525766, rel=1e-10)
    assert close(st.mann_whitney_u(t1, t2, alternative="greater").p_value, 0.9847391254573133, rel=1e-10)
    assert close(st.mann_whitney_u(t1, t2, use_continuity=False).p_value, 0.033354537099246226, rel=1e-10)
    big1 = [0.0, 0.0083, 0.0331, 0.0744, 0.1322, 0.2066, 0.2975, 0.405, 0.5289, 0.6694, 0.8264, 1.0]
    big2 = [0.0316, 0.0755, 0.1304, 0.1945, 0.2667, 0.346, 0.432, 0.524, 0.6219, 0.7251, 0.8336, 0.9469, 1.065]
    big2 += [1.1876, 1.3145]
    r = st.mann_whitney_u(big1, big2)
    assert r.u == 59.0 and close(r.p_value, 0.1366856053627003, rel=1e-10)


def test_mann_whitney_exact_distribution_matches_enumeration():
    n1, n2 = 4, 5
    counts = [0] * (n1 * n2 + 1)
    for positions in itertools.combinations(range(n1 + n2), n1):
        # U for sample 1 = number of (sample1 > sample2) pairs when values are ranks.
        others = [p for p in range(n1 + n2) if p not in positions]
        counts[sum(1 for x in positions for y in others if x > y)] += 1
    total = math.comb(n1 + n2, n1)
    dist = st.mann_whitney_u_distribution(n1, n2)
    assert len(dist) == len(counts)
    for got, count in zip(dist, counts, strict=True):
        assert close(float(got), count / total, rel=1e-12, abs_=1e-15)


def test_hodges_lehmann_estimate_and_interval():
    a, b = [1.1, 2.3, 3.8, 4.4, 7.0], [2.9, 5.5, 6.1, 8.2, 9.9, 10.5]
    hl = st.hodges_lehmann(a, b)
    diffs = sorted(x - y for x in a for y in b)
    assert close(hl.estimate, (diffs[14] + diffs[15]) / 2)
    # Exact: P(U <= 3) = 0.0152 <= 0.025 < P(U <= 4) → c = 4 → [D_(4), D_(27)], coverage 1 - 2·P(U <= 3).
    assert close(hl.ci_low, diffs[3]) and close(hl.ci_high, diffs[26])
    assert close(hl.achieved_level, 0.9696969696969697, rel=1e-12)
    # 3 vs 3 cannot reach 95%: the smallest two-sided exact p is 0.1.
    small = st.hodges_lehmann([1, 2, 3], [4, 5, 6])
    assert small.ci_low is None and small.ci_high is None and small.estimate == -3.0


# ---------------------------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------------------------
def test_bootstrap_is_seeded_and_reproducible():
    data = [0.71, 0.74, 0.69, 0.77, 0.73, 0.75, 0.70, 0.72]
    first = st.bootstrap_ci(data, seed=42)
    again = st.bootstrap_ci(data, seed=42)
    other = st.bootstrap_ci(data, seed=43)
    assert first == again
    assert (first.ci_low, first.ci_high) != (other.ci_low, other.ci_high)
    assert first.ci_low < st.mean(data) < first.ci_high
    assert first.estimate == pytest.approx(st.mean(data))


def test_bootstrap_two_sample_and_paired_and_bca():
    base = [0.70, 0.72, 0.71, 0.69, 0.73]
    cand = [0.78, 0.80, 0.77, 0.79, 0.81]
    diff = st.bootstrap_ci(cand, base, seed=7, n_resamples=5000)
    assert diff.estimate == pytest.approx(0.08) and 0.05 < diff.ci_low < diff.ci_high < 0.11
    paired = st.bootstrap_ci(cand, base, paired=True, seed=7)
    assert paired.paired and paired.estimate == pytest.approx(0.08)
    bca = st.bootstrap_ci([1.0, 1.2, 0.9, 5.0, 1.1, 1.3, 0.8, 1.05], seed=3, method="bca")
    assert bca.method == "bca" and bca.ci_low < bca.estimate < bca.ci_high
    median = st.bootstrap_ci([3.0, 1.0, 2.0, 10.0, 4.0], statistic="median", seed=1)
    assert median.estimate == 3.0
    constant = st.bootstrap_ci([2.0, 2.0, 2.0], seed=0, method="bca")
    assert (constant.ci_low, constant.ci_high) == (2.0, 2.0)  # BCa degenerates to the point estimate
    with pytest.raises(st.StatisticsError):
        st.bootstrap_ci(base, n_resamples=10)


def test_bootstrap_test_p_values():
    far = st.bootstrap_test([5.1, 5.3, 5.2, 5.4, 5.25], [4.1, 4.2, 4.0, 4.3, 4.15], seed=3)
    near = st.bootstrap_test([5.1, 5.3, 5.2, 5.4, 5.25], [5.2, 5.1, 5.35, 5.3, 5.2], seed=3)
    assert far.p_value == pytest.approx(1 / 2001)  # never exactly zero
    assert near.p_value > 0.5
    assert st.bootstrap_test([5.1, 5.3, 5.2, 5.4, 5.25], [4.1, 4.2, 4.0, 4.3, 4.15], seed=3) == far


# ---------------------------------------------------------------------------------------------
# Effect sizes
# ---------------------------------------------------------------------------------------------
def test_cohens_d_and_hedges_g():
    a, b = [2.0, 4.0, 6.0], [1.0, 2.0, 3.0]
    # pooled sd = sqrt((2·4 + 2·1) / 4) = sqrt(2.5)
    assert close(st.cohens_d(a, b), 2.0 / math.sqrt(2.5))
    assert close(st.hedges_correction(13), 0.9409824674711481, rel=1e-12)
    assert close(st.hedges_g(a, b), st.cohens_d(a, b) * st.hedges_correction(4))
    assert st.hedges_correction(4) < 1.0 and st.hedges_correction(10_000) == pytest.approx(1.0, abs=1e-4)
    assert st.cohens_d([1.0, 1.0], [0.0, 0.0]) == math.inf
    assert close(st.cohens_dz([0.03, 0.02, 0.04, 0.01, 0.04]), 0.028 / st.sd([0.03, 0.02, 0.04, 0.01, 0.04]))


def test_cliffs_delta_matches_brute_force():
    a = [1.0, 3.0, 3.0, 7.0, 9.0]
    b = [2.0, 3.0, 4.0, 4.0]
    brute = sum((x > y) - (x < y) for x in a for y in b) / (len(a) * len(b))
    assert close(st.cliffs_delta(a, b), brute)
    assert st.cliffs_delta([5, 6], [1, 2]) == 1.0 and st.cliffs_delta([1, 2], [5, 6]) == -1.0


# ---------------------------------------------------------------------------------------------
# Multiple comparisons, proportions, planning
# ---------------------------------------------------------------------------------------------
def test_holm_and_bh_match_reference():
    p = [0.01, 0.04, 0.03, 0.005, 0.20]
    assert st.holm_bonferroni(p) == pytest.approx([0.04, 0.09, 0.09, 0.025, 0.2], rel=1e-12)
    assert st.benjamini_hochberg(p) == pytest.approx([0.025, 0.05, 0.05, 0.025, 0.2], rel=1e-12)
    assert st.adjust_p_values(p, "none") == p
    assert st.holm_bonferroni([]) == []
    assert st.holm_bonferroni([0.6, 0.7]) == [1.0, 1.0]  # capped at 1
    with pytest.raises(st.StatisticsError):
        st.holm_bonferroni([0.1, 1.2])


def test_wilson_interval_matches_reference():
    low, high = st.wilson_interval(7, 20)
    assert close(low, 0.18119182410108203, rel=1e-12) and close(high, 0.5671457233147638, rel=1e-12)
    assert st.wilson_interval(0, 10) == (0.0, pytest.approx(0.27753279986288926, rel=1e-12))
    assert st.wilson_interval(10, 10) == (pytest.approx(0.7224672001371106, rel=1e-12), 1.0)
    assert st.wilson_interval(0, 0) == (0.0, 1.0)


def test_required_seeds_estimate_matches_power_tables():
    # Exact noncentral-t sample sizes (G*Power): d = 0.5 → 64, d = 0.8 → 26, d = 0.2 → 394 per group.
    assert st.required_seeds_estimate(0.5) == 64
    assert st.required_seeds_estimate(0.8) == 26
    assert st.required_seeds_estimate(0.2) == 394
    assert st.required_seeds_estimate(0.5, small_sample_correction=False) == 63  # plain normal approximation
    assert st.required_seeds_estimate(0.5, paired=True) < st.required_seeds_estimate(0.5)
    assert st.required_seeds_estimate(5.0) == 2
    with pytest.raises(st.StatisticsError):
        st.required_seeds_estimate(0.0)
