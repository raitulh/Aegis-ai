"""Deterministic scientific review checks (run before, and independently of, the ScientificReviewerAgent)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.experiments.validator import ValidationReport
from engines.lab.verification.claims import overclaiming_terms

Verdict = Literal["pass", "concern", "fail"]


@dataclass
class ReviewFinding:
    check: str
    verdict: Verdict
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewInput:
    spec: ExperimentSpec
    validation: ValidationReport
    hypothesis_novelty_notes: str = ""
    literature_source_count: int = 0
    statistical_rows: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)  # {"statement", "evidence_ids"}
    reproduction_verdicts: list[str] = field(default_factory=list)  # pass|fail|inconclusive
    contradicting_evidence_count: int = 0
    manifest_completeness: float | None = None
    self_reported_metrics: bool = False
    report_text: str = ""


def review(inp: ReviewInput) -> list[ReviewFinding]:
    out: list[ReviewFinding] = []
    spec = inp.spec

    if not inp.hypothesis_novelty_notes.strip():
        out.append(ReviewFinding("novelty_reasoning", "concern", "No novelty reasoning recorded for the hypothesis"))
    elif inp.literature_source_count == 0:
        out.append(ReviewFinding("novelty_reasoning", "concern", "Novelty is asserted without linked literature"))
    else:
        out.append(
            ReviewFinding(
                "novelty_reasoning", "pass", f"Novelty reasoning linked to {inp.literature_source_count} source(s)"
            )
        )

    if spec.baseline is None:
        out.append(ReviewFinding("baseline_quality", "fail", "No baseline"))
    elif not spec.baseline.description and not spec.baseline.parameters:
        out.append(ReviewFinding("baseline_quality", "concern", "Baseline is under-specified"))
    else:
        out.append(ReviewFinding("baseline_quality", "pass", f"Baseline '{spec.baseline.name}' specified"))

    design_errors = [i.code for i in inp.validation.errors]
    out.append(
        ReviewFinding(
            "experiment_design",
            "fail" if design_errors else ("concern" if inp.validation.warnings else "pass"),
            "Design errors: " + ", ".join(sorted(set(design_errors)))
            if design_errors
            else f"{len(inp.validation.warnings)} design warning(s)",
        )
    )

    rows = [r for r in inp.statistical_rows if not r.get("insufficient")]
    if not inp.statistical_rows:
        out.append(ReviewFinding("statistical_validity", "fail", "No statistical test recorded"))
    elif len(rows) < len(inp.statistical_rows):
        out.append(
            ReviewFinding("statistical_validity", "concern", "Some metrics lacked the samples required by the plan")
        )
    elif spec.statistical_plan.correction == "none" and len(rows) > 1:
        out.append(ReviewFinding("statistical_validity", "concern", "Multiple metrics tested without correction"))
    else:
        out.append(
            ReviewFinding(
                "statistical_validity",
                "pass",
                f"{len(rows)} test(s) with {spec.statistical_plan.correction} correction",
            )
        )

    leakage = [i for i in inp.validation.issues if i.code == "data_leakage"]
    out.append(
        ReviewFinding(
            "leakage",
            "fail" if any(i.severity == "error" for i in leakage) else ("concern" if leakage else "pass"),
            "; ".join(i.message for i in leakage) or "No leakage indicators",
        )
    )

    if not inp.reproduction_verdicts:
        repro_verdict: Verdict = "concern"
        repro_msg = "Not yet reproduced"
    elif "fail" in inp.reproduction_verdicts:
        repro_verdict, repro_msg = "fail", "A reproduction failed"
    else:
        repro_verdict, repro_msg = "pass", f"{inp.reproduction_verdicts.count('pass')} successful reproduction(s)"
    if inp.manifest_completeness is not None and inp.manifest_completeness < 1.0 and repro_verdict == "pass":
        repro_verdict, repro_msg = "concern", repro_msg + f"; manifest {inp.manifest_completeness:.0%} complete"
    out.append(ReviewFinding("reproducibility", repro_verdict, repro_msg))

    unsupported = [c["statement"][:120] for c in inp.claims if not c.get("evidence_ids")]
    out.append(
        ReviewFinding(
            "unsupported_claims",
            "fail" if unsupported else "pass",
            f"{len(unsupported)} claim(s) without evidence" if unsupported else "All claims link evidence",
            {"claims": unsupported},
        )
    )

    independent = [v for v in spec.variables if v.kind == "independent"]
    controls = [v.name for v in spec.variables if v.kind == "control"] + list(spec.controls)
    out.append(
        ReviewFinding(
            "missing_controls",
            "concern" if independent and not controls else "pass",
            "Independent variables without controls" if independent and not controls else "Controls declared",
        )
    )

    out.append(
        ReviewFinding(
            "contradictory_evidence",
            "concern" if inp.contradicting_evidence_count else "pass",
            f"{inp.contradicting_evidence_count} contradicting source(s) recorded"
            if inp.contradicting_evidence_count
            else "None recorded",
        )
    )

    texts: Sequence[str] = [c.get("statement", "") for c in inp.claims] + [inp.report_text]
    flagged = sorted({t for text in texts for t in overclaiming_terms(text)})
    out.append(
        ReviewFinding(
            "overclaiming",
            "fail" if flagged else "pass",
            ("Overclaiming language: " + ", ".join(flagged)) if flagged else "No overclaiming language",
            {"terms": flagged},
        )
    )
    if inp.self_reported_metrics:
        out.append(ReviewFinding("measurement_independence", "concern", "Metrics were self-reported by generated code"))
    else:
        out.append(ReviewFinding("measurement_independence", "pass", "Metrics measured by a platform harness"))
    return out


def overall(findings: Sequence[ReviewFinding]) -> Verdict:
    if any(f.verdict == "fail" for f in findings):
        return "fail"
    if any(f.verdict == "concern" for f in findings):
        return "concern"
    return "pass"
