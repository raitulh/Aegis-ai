"""Rule-based scientific review of a mission's designs, analyses, claims and report text.

Ten checks, each ``pass`` | ``concern`` | ``fail``; every problem is a finding ``{check, severity (info |
warning | error), message, refs}``:

``novelty_reasoning``       hypotheses state why they are new, and literature was consulted
``baseline_quality``        comparative designs have a (justified) baseline, baselines are not under-sampled
``experiment_design``       designs were validated and passed (errors → fail, warnings → concern)
``statistical_validity``    comparisons have enough data, corrections are applied, verdicts agree with CIs
``leakage``                 no ``DATA_LEAKAGE`` issue in any validation report
``reproducibility``         reproductions exist and succeeded; no ``MISSING_REPRODUCIBILITY`` errors
``unsupported_claims``      every claim has supporting evidence; significance language is backed by a test
``missing_controls``        designs that manipulate variables declare controls; no ``INVALID_CONTROL``
``contradictory_evidence``  contradictions are acknowledged; verified claims are not contradicted
``overclaiming``            regex scan for certainty language — "proves/proven", "guarantee(s|d)",
                            "definitively", "conclusively", universal "always/never" claims about outcomes,
                            "state-of-the-art"/"SOTA", "breakthrough", "revolutionary", "100%" — with negation
                            handling ("does not prove" is appropriate hedging). Matches in claims are errors,
                            in the report text warnings.

``verdict``: ``fail`` if any check fails, ``concerns`` if any has a concern, else ``pass``.
``score`` = mean over checks of (pass = 1, concern = 0.5, fail = 0).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.comparison import ComparisonResult
from engines.lab.design_validator import ValidationReport
from engines.lab.experiment_spec import ExperimentSpec

REVIEW_ENGINE_VERSION = "review-1.0.0"
CHECKS: tuple[str, ...] = (
    "novelty_reasoning",
    "baseline_quality",
    "experiment_design",
    "statistical_validity",
    "leakage",
    "reproducibility",
    "unsupported_claims",
    "missing_controls",
    "contradictory_evidence",
    "overclaiming",
)
MIN_NOVELTY_RATIONALE_CHARS = 40
SMALL_N = 3

Severity = Literal["info", "warning", "error"]
CheckStatus = Literal["pass", "concern", "fail"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HypothesisSummary(_In):
    id: str
    statement: str
    novelty_rationale: str | None = None
    status: str | None = None


class ClaimSummary(_In):
    id: str
    text: str
    status: str | None = None
    supporting_evidence_count: int = Field(default=0, ge=0)
    contradicting_evidence_count: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)


class ReproductionSummary(_In):
    id: str
    verdict: Literal["reproduced", "partially_reproduced", "not_reproduced", "inconclusive"]
    experiment_id: str | None = None


class LabelledSpec(_In):
    id: str
    spec: ExperimentSpec


class LabelledReport(_In):
    id: str
    report: ValidationReport


class LabelledComparison(_In):
    id: str
    comparison: ComparisonResult


class ReviewInput(_In):
    hypotheses: list[HypothesisSummary] = Field(default_factory=list)
    literature_refs: int = Field(default=0, ge=0)
    specs: list[LabelledSpec] = Field(default_factory=list)
    validation_reports: list[LabelledReport] = Field(default_factory=list)
    comparisons: list[LabelledComparison] = Field(default_factory=list)
    claims: list[ClaimSummary] = Field(default_factory=list)
    reproductions: list[ReproductionSummary] = Field(default_factory=list)
    report_text: str = Field(default="", max_length=2_000_000)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str
    severity: Severity
    message: str
    refs: list[dict[str, Any]] = Field(default_factory=list)


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]
    checks: dict[str, CheckStatus]
    verdict: Literal["pass", "concerns", "fail"]
    score: float = Field(ge=0, le=1)
    engine_version: str = REVIEW_ENGINE_VERSION


# ---------------------------------------------------------------------------------------------
# Overclaiming scanner
# ---------------------------------------------------------------------------------------------
_OUTCOME_WORDS = (
    r"outperform\w*|improv\w*|better|worse|beat\w*|fail\w*|succeed\w*|works?|generali[sz]\w*|higher|lower|"
    r"superior|inferior|correct|accurate|converge\w*|wins?|reduc\w*|increas\w*|holds?"
)
OVERCLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    # (label, pattern, negation-sensitive)
    ("proof language", re.compile(r"\bprov(?:e|es|en|ed|ing)\b", re.IGNORECASE), True),
    ("guarantee language", re.compile(r"\bguarantee(?:s|d)?\b", re.IGNORECASE), True),
    (
        "certainty language",
        re.compile(r"\b(?:definitively|conclusively|unequivocally|undeniabl[ey])\b", re.IGNORECASE),
        True,
    ),
    (
        "universal claim",
        re.compile(rf"\b(?:always|never)\b[^.!?\n]{{0,60}}?\b(?:{_OUTCOME_WORDS})\b", re.IGNORECASE),
        False,
    ),
    ("state-of-the-art claim", re.compile(r"\bstate[-\s]of[-\s]the[-\s]art\b|\bSOTA\b", re.IGNORECASE), True),
    ("hype language", re.compile(r"\b(?:breakthrough|revolutionary|game[-\s]chang\w*)\b", re.IGNORECASE), True),
    ("absolute percentage", re.compile(r"\b100\s?(?:%|percent\b)", re.IGNORECASE), True),
)
_NEGATION = re.compile(
    r"(?:\b(?:not|no|cannot|never|nor|without|neither|unable\s+to)|n't)\s+(?:\w+\s+){0,2}$", re.IGNORECASE
)


def _verdict_contradicts_ci(c: ComparisonResult) -> bool:
    """A directional verdict whose CI (of candidate - baseline) still straddles zero."""
    s = c.statistics
    if c.verdict not in ("improved", "regressed") or s.ci_low is None or s.ci_high is None:
        return False
    return s.ci_low <= 0 <= s.ci_high


def scan_overclaiming(text: str) -> list[dict[str, Any]]:
    """Return overclaiming matches ``{label, match, offset, excerpt}`` in ``text`` (negated hedges skipped)."""
    hits: list[dict[str, Any]] = []
    if not text:
        return hits
    for label, pattern, negation_sensitive in OVERCLAIM_PATTERNS:
        for m in pattern.finditer(text):
            before = text[max(0, m.start() - 40) : m.start()]
            if negation_sensitive and _NEGATION.search(before):
                continue
            start, end = max(0, m.start() - 60), min(len(text), m.end() + 60)
            hits.append(
                {
                    "label": label,
                    "match": m.group(0),
                    "offset": m.start(),
                    "excerpt": " ".join(text[start:end].split()),
                }
            )
    hits.sort(key=lambda h: h["offset"])
    return hits


# ---------------------------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------------------------
class _Review:
    def __init__(self, data: ReviewInput) -> None:
        self.data = data
        self.findings: list[Finding] = []

    def add(self, check: str, severity: Severity, message: str, refs: Iterable[dict[str, Any]] = ()) -> None:
        self.findings.append(Finding(check=check, severity=severity, message=message, refs=list(refs)))

    def status(self, check: str) -> CheckStatus:
        severities = {f.severity for f in self.findings if f.check == check}
        if "error" in severities:
            return "fail"
        if "warning" in severities:
            return "concern"
        return "pass"

    # -------------------------------------------------------------------------------------------
    def novelty_reasoning(self) -> None:
        d = self.data
        if not d.hypotheses:
            self.add("novelty_reasoning", "info", "no hypotheses to review for novelty")
        for h in d.hypotheses:
            rationale = (h.novelty_rationale or "").strip()
            if not rationale:
                self.add(
                    "novelty_reasoning",
                    "warning",
                    f"hypothesis {h.id} gives no novelty rationale",
                    [{"hypothesis": h.id}],
                )
            elif len(rationale) < MIN_NOVELTY_RATIONALE_CHARS:
                self.add(
                    "novelty_reasoning",
                    "warning",
                    f"hypothesis {h.id} has a very short novelty rationale",
                    [{"hypothesis": h.id}],
                )
        if d.hypotheses and d.literature_refs == 0:
            self.add("novelty_reasoning", "warning", "novelty is asserted without any literature consulted")

    def baseline_quality(self) -> None:
        for labelled_spec in self.data.specs:
            spec = labelled_spec.spec
            if spec.kind in ("candidate", "ablation", "sensitivity") and spec.baseline.kind == "none":
                self.add(
                    "baseline_quality",
                    "error",
                    f"experiment {labelled_spec.id} ({spec.kind}) has no baseline",
                    [{"experiment": labelled_spec.id}],
                )
            elif spec.baseline.kind == "reference_values" and not spec.baseline.justification:
                self.add(
                    "baseline_quality",
                    "warning",
                    f"experiment {labelled_spec.id} compares against unjustified reference values",
                    [{"experiment": labelled_spec.id}],
                )
        for labelled_cmp in self.data.comparisons:
            s = labelled_cmp.comparison.statistics
            if s.n_baseline < s.n_candidate:
                self.add(
                    "baseline_quality",
                    "warning",
                    f"comparison {labelled_cmp.id}: baseline has fewer runs ({s.n_baseline}) than the candidate ({s.n_candidate})",
                    [{"comparison": labelled_cmp.id}],
                )

    def experiment_design(self) -> None:
        d = self.data
        if d.specs and not d.validation_reports:
            self.add("experiment_design", "warning", "experiment designs were not validated")
        for labelled_report in d.validation_reports:
            report = labelled_report.report
            errors = [i for i in report.issues if i.severity == "error" and i.code != "DATA_LEAKAGE"]
            warnings = [i for i in report.issues if i.severity == "warning" and i.code != "DATA_LEAKAGE"]
            if errors:
                self.add(
                    "experiment_design",
                    "error",
                    f"design {labelled_report.id} has {len(errors)} validation error(s): {', '.join(sorted({i.code for i in errors}))}",
                    [{"validation_report": labelled_report.id, "codes": sorted({i.code for i in errors})}],
                )
            elif warnings:
                self.add(
                    "experiment_design",
                    "warning",
                    f"design {labelled_report.id} has validation warning(s): {', '.join(sorted({i.code for i in warnings}))}",
                    [{"validation_report": labelled_report.id}],
                )

    def statistical_validity(self) -> None:
        d = self.data
        for labelled_cmp in d.comparisons:
            c = labelled_cmp.comparison
            s = c.statistics
            ref = [{"comparison": labelled_cmp.id, "metric": c.metric}]
            if c.verdict == "insufficient_data":
                self.add(
                    "statistical_validity",
                    "warning",
                    f"comparison {labelled_cmp.id} on {c.metric}: insufficient data",
                    ref,
                )
                continue
            if min(s.n_baseline, s.n_candidate) < SMALL_N:
                self.add(
                    "statistical_validity",
                    "warning",
                    f"comparison {labelled_cmp.id}: only {min(s.n_baseline, s.n_candidate)} runs per arm",
                    ref,
                )
            if s.family_size > 1 and s.correction == "none":
                self.add(
                    "statistical_validity",
                    "warning",
                    f"comparison {labelled_cmp.id}: {s.family_size} comparisons without multiple-comparison correction",
                    ref,
                )
            if _verdict_contradicts_ci(c):
                self.add(
                    "statistical_validity",
                    "warning",
                    f"comparison {labelled_cmp.id}: verdict {c.verdict!r} but the confidence interval includes 0",
                    ref,
                )
        for labelled_spec in d.specs:
            if labelled_spec.spec.statistical_plan.alpha > 0.1:
                self.add(
                    "statistical_validity",
                    "warning",
                    f"experiment {labelled_spec.id} uses alpha > 0.1",
                    [{"experiment": labelled_spec.id}],
                )
        significance = re.compile(r"\bsignificant(?:ly)?\b|\bp\s*[<=]", re.IGNORECASE)
        for claim in d.claims:
            if significance.search(claim.text) and not d.comparisons:
                self.add(
                    "statistical_validity",
                    "error",
                    f"claim {claim.id} asserts significance but no statistical comparison was run",
                    [{"claim": claim.id}],
                )

    def leakage(self) -> None:
        for labelled_report in self.data.validation_reports:
            leaks = [i for i in labelled_report.report.issues if i.code == "DATA_LEAKAGE"]
            for issue in leaks:
                self.add(
                    "leakage",
                    "error" if issue.severity == "error" else "warning",
                    f"design {labelled_report.id}: {issue.message}",
                    [{"validation_report": labelled_report.id, "field": issue.field}],
                )

    def reproducibility(self) -> None:
        d = self.data
        if (d.claims or d.comparisons) and not d.reproductions:
            self.add("reproducibility", "warning", "no reproduction has been attempted")
        for r in d.reproductions:
            if r.verdict == "not_reproduced":
                self.add(
                    "reproducibility",
                    "error",
                    f"reproduction {r.id} did not reproduce the result",
                    [{"reproduction": r.id}],
                )
            elif r.verdict in ("partially_reproduced", "inconclusive"):
                self.add(
                    "reproducibility",
                    "warning",
                    f"reproduction {r.id} was {r.verdict.replace('_', ' ')}",
                    [{"reproduction": r.id}],
                )
        for labelled_report in d.validation_reports:
            if any(
                i.code == "MISSING_REPRODUCIBILITY" and i.severity == "error" for i in labelled_report.report.issues
            ):
                self.add(
                    "reproducibility",
                    "error",
                    f"design {labelled_report.id} is not reproducible as specified (seeds, code or environment missing)",
                    [{"validation_report": labelled_report.id}],
                )

    def unsupported_claims(self) -> None:
        for claim in self.data.claims:
            if claim.supporting_evidence_count == 0 and not claim.evidence_ids:
                self.add(
                    "unsupported_claims", "error", f"claim {claim.id} has no supporting evidence", [{"claim": claim.id}]
                )
            elif claim.status in ("VERIFIED",) and claim.supporting_evidence_count < 2:
                self.add(
                    "unsupported_claims",
                    "warning",
                    f"claim {claim.id} is marked verified on a single piece of evidence",
                    [{"claim": claim.id}],
                )

    def missing_controls(self) -> None:
        for labelled_spec in self.data.specs:
            spec = labelled_spec.spec
            manipulates = any(v.role == "independent" for v in spec.variables)
            has_controls = bool(spec.controls) or any(v.role == "control" for v in spec.variables)
            if (manipulates or spec.kind in ("candidate", "ablation")) and not has_controls:
                self.add(
                    "missing_controls",
                    "warning",
                    f"experiment {labelled_spec.id} declares no controlled variables",
                    [{"experiment": labelled_spec.id}],
                )
        for labelled_report in self.data.validation_reports:
            for issue in labelled_report.report.issues:
                if issue.code == "INVALID_CONTROL":
                    self.add(
                        "missing_controls",
                        "error",
                        f"design {labelled_report.id}: {issue.message}",
                        [{"validation_report": labelled_report.id, "field": issue.field}],
                    )

    def contradictory_evidence(self) -> None:
        d = self.data
        for claim in d.claims:
            if claim.contradicting_evidence_count > 0:
                severity: Severity = "error" if claim.status == "VERIFIED" else "warning"
                self.add(
                    "contradictory_evidence",
                    severity,
                    f"claim {claim.id} has {claim.contradicting_evidence_count} contradicting evidence item(s)"
                    + (" but is marked verified" if severity == "error" else ""),
                    [{"claim": claim.id}],
                )
        verdicts: dict[str, set[str]] = {}
        for labelled_cmp in d.comparisons:
            verdicts.setdefault(labelled_cmp.comparison.metric, set()).add(labelled_cmp.comparison.verdict)
        for metric, found in sorted(verdicts.items()):
            if {"improved", "regressed"} <= found:
                self.add(
                    "contradictory_evidence",
                    "warning",
                    f"comparisons on {metric!r} disagree (both improved and regressed)",
                    [{"metric": metric}],
                )

    def overclaiming(self) -> None:
        for claim in self.data.claims:
            for hit in scan_overclaiming(claim.text):
                self.add(
                    "overclaiming",
                    "error",
                    f"claim {claim.id}: {hit['label']} ({hit['match']!r})",
                    [{"source": f"claim:{claim.id}", **hit}],
                )
        hits = scan_overclaiming(self.data.report_text)
        for hit in hits:
            self.add(
                "overclaiming", "warning", f"report: {hit['label']} ({hit['match']!r})", [{"source": "report", **hit}]
            )


def review(data: ReviewInput | dict[str, Any]) -> ReviewResult:
    """Run all checks over ``data`` and return findings, per-check status, verdict and score."""
    item = data if isinstance(data, ReviewInput) else ReviewInput.model_validate(data)
    run = _Review(item)
    for check in CHECKS:
        getattr(run, check)()
    checks: dict[str, CheckStatus] = {check: run.status(check) for check in CHECKS}
    values = {"pass": 1.0, "concern": 0.5, "fail": 0.0}
    score = sum(values[s] for s in checks.values()) / len(checks)
    if "fail" in checks.values():
        verdict: Literal["pass", "concerns", "fail"] = "fail"
    elif "concern" in checks.values():
        verdict = "concerns"
    else:
        verdict = "pass"
    return ReviewResult(findings=run.findings, checks=checks, verdict=verdict, score=round(score, 6))
