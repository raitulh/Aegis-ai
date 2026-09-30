"""Mission report builder: structured, evidence-cited, deterministic.

:func:`build_report` turns a :class:`ReportInput` (assembled by the verification/reporting service from stored
rows) into a :class:`ReportDocument` with exactly these sections, in this order::

    executive_summary, research_question, methodology, literature, hypotheses, experiments, results,
    failures, evolution, verification, limitations, evidence, reproducibility, appendix

Rules the builder enforces:

* every claim/result/hypothesis/experiment line cites its internal evidence ids as ``[evidence:<id>]``; a line
  without evidence says so explicitly ("no evidence recorded") and produces a limitation;
* uncertainty is never collapsed to a binary: results always carry the verdict *with* effect size, confidence
  interval, (adjusted) p-value and n; claims carry their status (six states) and numeric confidence;
* limitations are derived automatically — failed/partial reproductions, inconclusive or rejected hypotheses,
  claims that are not verified, missing evidence, contradicting evidence, small samples, insufficient data,
  self-reported-only metrics, retracted or preprint literature, review concerns — plus any provided ones;
* output is deterministic (no clocks, no randomness): identical input → identical document and ``content_hash``;
* the text avoids certainty language (it never says a result is "proven" or "guaranteed").
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.comparison import COMPARISON_ENGINE_VERSION, ComparisonResult
from engines.lab.experiment_spec import ExperimentSpec, canonical_json, spec_hash
from engines.lab.review import ReviewResult

REPORT_ENGINE_VERSION = "report-1.0.0"
SECTIONS: tuple[tuple[str, str], ...] = (
    ("executive_summary", "Executive Summary"),
    ("research_question", "Research Question"),
    ("methodology", "Methodology"),
    ("literature", "Literature"),
    ("hypotheses", "Hypotheses"),
    ("experiments", "Experiments"),
    ("results", "Results"),
    ("failures", "Failures"),
    ("evolution", "Evolution"),
    ("verification", "Verification"),
    ("limitations", "Limitations"),
    ("evidence", "Evidence"),
    ("reproducibility", "Reproducibility"),
    ("appendix", "Appendix"),
)
SMALL_N_THRESHOLD = 5
NO_EVIDENCE = "_(no evidence recorded)_"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiteratureItem(_In):
    id: str
    title: str
    url: str | None = None
    year: int | None = None
    venue: str | None = None
    is_preprint: bool = False
    is_retracted: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class HypothesisItem(_In):
    id: str
    statement: str
    status: str
    rationale: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ExperimentItem(_In):
    id: str
    title: str
    kind: str = "candidate"
    status: str
    spec: ExperimentSpec | None = None
    spec_hash: str | None = None
    n_runs: int = Field(default=0, ge=0)
    seeds: list[int] = Field(default_factory=list)
    metric_sources: dict[str, str] = Field(default_factory=dict)
    image: str | None = None
    image_digest: str | None = None
    code_snapshot_id: str | None = None
    dataset_version_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ResultItem(_In):
    id: str
    comparison: ComparisonResult
    experiment_id: str | None = None
    baseline_experiment_id: str | None = None
    metric_source: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class FailureItem(_In):
    id: str
    failure_type: str
    summary: str
    status: str
    lesson: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class EvolutionItem(_In):
    id: str
    strategy: str
    status: str
    summary: str
    version: int | None = None
    fitness: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimItem(_In):
    id: str
    statement: str
    status: str
    confidence: float = Field(ge=0, le=1)
    profile: str | None = None
    satisfied: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    contradicting_evidence_count: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)


class ReproductionItem(_In):
    id: str
    verdict: str
    experiment_id: str | None = None
    metric_deltas: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceItem(_In):
    id: str
    kind: str
    title: str


class ReportInput(_In):
    title: str = Field(min_length=1, max_length=300)
    research_question: str = Field(min_length=1)
    mission_id: str | None = None
    objective: str | None = None
    methodology: str | None = None
    literature: list[LiteratureItem] = Field(default_factory=list)
    hypotheses: list[HypothesisItem] = Field(default_factory=list)
    experiments: list[ExperimentItem] = Field(default_factory=list)
    results: list[ResultItem] = Field(default_factory=list)
    failures: list[FailureItem] = Field(default_factory=list)
    evolution: list[EvolutionItem] = Field(default_factory=list)
    claims: list[ClaimItem] = Field(default_factory=list)
    reproductions: list[ReproductionItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    review: ReviewResult | None = None
    generated_at: str | None = None
    engine_versions: dict[str, str] = Field(default_factory=dict)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    markdown: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    sections: list[ReportSection]
    limitations: list[str]
    evidence_ids: list[str]
    engine_version: str = REPORT_ENGINE_VERSION
    content_hash: str

    def section(self, section_id: str) -> ReportSection:
        return next(s for s in self.sections if s.id == section_id)


# ---------------------------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------------------------
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _safe_id(evidence_id: str) -> str:
    """Evidence ids are internal (UUIDs); anything else is reduced to a markdown-inert form."""
    return evidence_id if _SAFE_ID.match(evidence_id) else re.sub(r"[^A-Za-z0-9_.:-]", "_", evidence_id)[:128]


def cite(evidence_ids: Iterable[str]) -> str:
    """``[evidence:a] [evidence:b]`` (deduplicated, input order) or an explicit no-evidence marker."""
    ids = list(dict.fromkeys(_safe_id(i) for i in evidence_ids if i))
    return " ".join(f"[evidence:{i}]" for i in ids) if ids else NO_EVIDENCE


def _num(value: float | None, spec: str = ".4g") -> str:
    return "n/a" if value is None else format(value, spec)


def _escape(text: str) -> str:
    """Neutralise markdown/HTML control characters in untrusted text (one line)."""
    cleaned = " ".join(str(text).split())
    for ch in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "|"):
        cleaned = cleaned.replace(ch, "\\" + ch)
    return cleaned


def _counts(values: Iterable[str]) -> str:
    counter = Counter(values)
    return ", ".join(f"{k.lower().replace('_', ' ')}: {counter[k]}" for k in sorted(counter)) or "none"


def describe_comparison(c: ComparisonResult) -> str:
    """One line with the verdict *and* its uncertainty (never a bare yes/no)."""
    s = c.statistics
    parts = [
        f"**{_escape(c.metric)}** ({c.direction}): {c.verdict.replace('_', ' ')}",
        f"candidate {_num(s.mean_candidate)} vs baseline {_num(s.mean_baseline)} (delta {_num(s.delta, '+.4g')})",
    ]
    if s.ci_low is not None and s.ci_high is not None:
        parts.append(f"{s.ci_level:.0%} CI [{s.ci_low:.4g}, {s.ci_high:.4g}]")
    else:
        parts.append("CI unavailable")
    p_text = f"p = {_num(s.p_value, '.3g')}"
    if s.family_size > 1:
        p_text += f", {s.correction}-adjusted p = {_num(s.p_value_adjusted, '.3g')} ({s.family_size} comparisons)"
    parts.append(p_text)
    parts.append(f"{s.effect_size_kind} = {_num(s.effect_size, '.3g')}")
    parts.append(f"n = {s.n_candidate} vs {s.n_baseline}; test {s.test}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------------------------
class _Builder:
    def __init__(self, data: ReportInput) -> None:
        self.data = data
        self.missing_evidence: Counter[str] = Counter()

    def line(self, text: str, evidence_ids: Sequence[str], section: str) -> str:
        if not evidence_ids:
            self.missing_evidence[section] += 1
        return f"- {text} {cite(evidence_ids)}"

    # Sections ----------------------------------------------------------------------------------
    def executive_summary(self) -> ReportSection:
        d = self.data
        lines = [f"This report summarises the mission on: {_escape(d.research_question)}", ""]
        lines.append(
            f"- Hypotheses: {len(d.hypotheses)} ({_counts(h.status for h in d.hypotheses)}); "
            f"experiments: {len(d.experiments)}; comparisons: {len(d.results)} "
            f"({_counts(r.comparison.verdict for r in d.results)})."
        )
        lines.append(
            f"- Claims: {len(d.claims)} ({_counts(c.status for c in d.claims)}); "
            f"reproductions: {len(d.reproductions)} ({_counts(r.verdict for r in d.reproductions)}); "
            f"recorded failures: {len(d.failures)}."
        )
        if d.review is not None:
            lines.append(f"- Automated scientific review: {d.review.verdict} (score {d.review.score:.2f}).")
        evidence: list[str] = []
        key_results = [r for r in d.results if r.comparison.verdict in ("improved", "regressed")]
        if key_results:
            lines += ["", "Key results (with uncertainty):"]
            for r in key_results:
                lines.append(self.line(describe_comparison(r.comparison), r.evidence_ids, "executive_summary"))
                evidence += r.evidence_ids
        key_claims = [c for c in d.claims if c.status in ("VERIFIED", "PARTIALLY_VERIFIED", "CONTESTED")]
        if key_claims:
            lines += ["", "Claims under verification:"]
            for c in key_claims:
                text = (
                    f"{_escape(c.statement)} — status {c.status.replace('_', ' ').lower()}, "
                    f"confidence {c.confidence:.2f}"
                )
                lines.append(self.line(text, c.evidence_ids, "executive_summary"))
                evidence += c.evidence_ids
        if not key_results and not key_claims:
            lines += [
                "",
                "No comparison reached a significant, practically relevant difference and no claim is verified.",
            ]
        lines += ["", "See Limitations for the caveats that apply to every statement above."]
        return self._section("executive_summary", lines, [], evidence)

    def research_question(self) -> ReportSection:
        d = self.data
        lines = [f"**Question:** {_escape(d.research_question)}"]
        if d.objective:
            lines += ["", f"**Objective:** {_escape(d.objective)}"]
        if d.mission_id:
            lines += ["", f"Mission: `{_escape(d.mission_id)}`"]
        return self._section("research_question", lines, [], [])

    def methodology(self) -> ReportSection:
        d = self.data
        lines: list[str] = []
        if d.methodology:
            lines += [_escape(d.methodology), ""]
        items = []
        for e in d.experiments:
            if e.spec is None:
                continue
            plan = e.spec.statistical_plan
            text = (
                f"{_escape(e.title)} ({e.kind}): {_escape(e.spec.method)[:500]} — pre-registered {plan.test} test, "
                f"alpha {plan.alpha:g}, {plan.correction} correction, {plan.n_seeds} seed(s), CI level {plan.ci_level:g}"
                + (f", minimum effect size {plan.min_effect_size:g}" if plan.min_effect_size is not None else "")
            )
            lines.append(self.line(text, e.evidence_ids, "methodology"))
            items.append({"experiment_id": e.id, "test": plan.test, "alpha": plan.alpha, "correction": plan.correction})
        if not items:
            lines.append("No structured experiment specifications were provided.")
        lines += [
            "",
            "Comparisons use the pre-registered plan (two-sided tests; direction-aware verdicts; multiple-comparison "
            f"correction across metrics) computed by `{COMPARISON_ENGINE_VERSION}`. Claims are assessed against "
            "explicit verification criteria; model judgments are advisory only.",
        ]
        return self._section("methodology", lines, items, [i for e in d.experiments if e.spec for i in e.evidence_ids])

    def literature(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for item in self.data.literature:
            flags = [f for f, on in (("preprint", item.is_preprint), ("RETRACTED", item.is_retracted)) if on]
            text = _escape(item.title) + (f" ({item.year})" if item.year else "")
            if item.venue:
                text += f", {_escape(item.venue)}"
            if item.url:
                text += " `" + " ".join(item.url.replace("`", "").split())[:500] + "`"
            if flags:
                text += f" — {', '.join(flags)}"
            lines.append(self.line(text, item.evidence_ids, "literature"))
            items.append(item.model_dump())
            evidence += item.evidence_ids
        return self._section("literature", lines or ["No literature sources were recorded."], items, evidence)

    def hypotheses(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for h in self.data.hypotheses:
            text = f"**{_escape(h.id)}** — {_escape(h.statement)} (status: {h.status.lower().replace('_', ' ')})"
            if h.rationale:
                text += f". Rationale: {_escape(h.rationale)}"
            lines.append(self.line(text, h.evidence_ids, "hypotheses"))
            items.append(h.model_dump())
            evidence += h.evidence_ids
        return self._section("hypotheses", lines or ["No hypotheses were recorded."], items, evidence)

    def experiments(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for e in self.data.experiments:
            text = f"**{_escape(e.title)}** ({e.kind}, status {e.status.lower()}): {e.n_runs} run(s)" + (
                f", seeds {e.seeds}" if e.seeds else ""
            )
            if e.metric_sources:
                text += "; metric sources: " + ", ".join(
                    f"{_escape(k)}={v}" for k, v in sorted(e.metric_sources.items())
                )
            lines.append(self.line(text, e.evidence_ids, "experiments"))
            items.append({"id": e.id, "title": e.title, "kind": e.kind, "status": e.status, "n_runs": e.n_runs})
            evidence += e.evidence_ids
        return self._section("experiments", lines or ["No experiments were run."], items, evidence)

    def results(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for r in self.data.results:
            text = describe_comparison(r.comparison)
            if r.metric_source == "self_reported":
                text += " (metric self-reported by the experiment code)"
            lines.append(self.line(text, r.evidence_ids, "results"))
            items.append(
                {
                    "id": r.id,
                    "metric": r.comparison.metric,
                    "verdict": r.comparison.verdict,
                    "statistics": r.comparison.statistics.model_dump(),
                    "rationale": r.comparison.rationale,
                }
            )
            evidence += r.evidence_ids
        return self._section("results", lines or ["No baseline comparisons were completed."], items, evidence)

    def failures(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for f in self.data.failures:
            text = f"{f.failure_type} ({f.status.lower()}): {_escape(f.summary)}"
            if f.lesson:
                text += f". Lesson: {_escape(f.lesson)}"
            lines.append(self.line(text, f.evidence_ids, "failures"))
            items.append(f.model_dump())
            evidence += f.evidence_ids
        return self._section("failures", lines or ["No failures were recorded."], items, evidence)

    def evolution(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for e in self.data.evolution:
            version = f" v{e.version}" if e.version is not None else ""
            fitness = ", ".join(f"{_escape(k)}={v:.4g}" for k, v in sorted(e.fitness.items()))
            text = f"{_escape(e.strategy)}{version} ({e.status.lower()}): {_escape(e.summary)}"
            if fitness:
                text += f" [fitness: {fitness}]"
            lines.append(self.line(text, e.evidence_ids, "evolution"))
            items.append(e.model_dump())
            evidence += e.evidence_ids
        return self._section("evolution", lines or ["No strategy evolution took place."], items, evidence)

    def verification(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for c in self.data.claims:
            text = (
                f"**{_escape(c.id)}** — {_escape(c.statement)}: status {c.status.replace('_', ' ').lower()}, "
                f"confidence {c.confidence:.2f}"
                + (f" (profile {c.profile})" if c.profile else "")
                + f"; satisfied: {', '.join(c.satisfied) or 'none'}; missing: {', '.join(c.missing) or 'none'}; "
                f"failed: {', '.join(c.failed) or 'none'}"
            )
            lines.append(self.line(text, c.evidence_ids, "verification"))
            items.append(c.model_dump())
            evidence += c.evidence_ids
        if self.data.reproductions:
            lines += ["", "Reproductions:"]
            for rep in self.data.reproductions:
                text = f"reproduction {_escape(rep.id)}" + (
                    f" of {_escape(rep.experiment_id)}" if rep.experiment_id else ""
                )
                text += f": {rep.verdict.replace('_', ' ')}"
                lines.append(self.line(text, rep.evidence_ids, "verification"))
                items.append(rep.model_dump())
                evidence += rep.evidence_ids
        return self._section("verification", lines or ["No claims were submitted for verification."], items, evidence)

    def limitations(self) -> tuple[ReportSection, list[str]]:
        d = self.data
        found: list[str] = []
        for rep in d.reproductions:
            if rep.verdict in ("not_reproduced", "partially_reproduced", "inconclusive"):
                target = f" of experiment {rep.experiment_id}" if rep.experiment_id else ""
                found.append(f"Reproduction {rep.id}{target} was {rep.verdict.replace('_', ' ')}.")
        if (d.results or d.claims) and not d.reproductions:
            found.append("No independent reproduction was attempted; results have not been replicated.")
        for h in d.hypotheses:
            if h.status in ("INCONCLUSIVE", "REJECTED"):
                found.append(f"Hypothesis {h.id} was {h.status.lower()}.")
        for c in d.claims:
            if c.status != "VERIFIED":
                missing = f"; missing criteria: {', '.join(c.missing)}" if c.missing else ""
                failed = f"; failed criteria: {', '.join(c.failed)}" if c.failed else ""
                found.append(
                    f"Claim {c.id} is {c.status.replace('_', ' ').lower()} (confidence {c.confidence:.2f}){missing}{failed}."
                )
            if c.contradicting_evidence_count > 0 or c.status == "CONTESTED":
                found.append(f"Claim {c.id} has contradicting evidence ({c.contradicting_evidence_count} item(s)).")
        for r in d.results:
            s = r.comparison.statistics
            if r.comparison.verdict == "insufficient_data":
                found.append(f"Comparison {r.id} on {r.comparison.metric} had insufficient data for a conclusion.")
            elif min(s.n_baseline, s.n_candidate) < SMALL_N_THRESHOLD:
                found.append(
                    f"Comparison {r.id} on {r.comparison.metric} rests on small samples "
                    f"(n = {s.n_candidate} vs {s.n_baseline}); estimates are imprecise."
                )
            if s.n_excluded_baseline or s.n_excluded_candidate:
                found.append(
                    f"Comparison {r.id} excluded {s.n_excluded_baseline + s.n_excluded_candidate} non-finite observation(s)."
                )
            if r.metric_source == "self_reported":
                found.append(
                    f"Result {r.id} ({r.comparison.metric}) relies on metrics self-reported by the experiment code."
                )
        for e in d.experiments:
            if e.metric_sources and all(v == "self_reported" for v in e.metric_sources.values()):
                found.append(f"Experiment {e.id} has only self-reported metrics (no independent measurement).")
        for item in d.literature:
            if item.is_retracted:
                found.append(f"Literature source {item.id} has been retracted.")
            elif item.is_preprint:
                found.append(f"Literature source {item.id} is a preprint (not peer reviewed).")
        if d.review is not None:
            for check, status in d.review.checks.items():
                if status != "pass":
                    found.append(f"Automated review check '{check}' reported: {status}.")
        for section, count in sorted(self.missing_evidence.items()):
            found.append(f"{count} statement(s) in section '{section}' have no recorded evidence.")
        found.extend(_escape(x) for x in d.limitations)
        found = list(dict.fromkeys(found))
        lines = [f"- {x}" for x in found] or [
            "- No specific limitation was detected automatically; this does not imply that none exist."
        ]
        return self._section("limitations", lines, [{"limitation": x} for x in found], []), found

    def evidence(self, cited: list[str]) -> ReportSection:
        register = {e.id: e for e in self.data.evidence}
        lines, items = [], []
        for eid in cited:
            entry = register.get(eid)
            if entry is None:
                lines.append(f"- [evidence:{_safe_id(eid)}] — not in the evidence register")
                items.append({"id": eid, "registered": False})
            else:
                lines.append(f"- [evidence:{_safe_id(eid)}] {_escape(entry.kind)}: {_escape(entry.title)}")
                items.append({"id": eid, "kind": entry.kind, "title": entry.title, "registered": True})
        for eid, entry in register.items():
            if eid not in cited:
                lines.append(
                    f"- [evidence:{_safe_id(eid)}] {_escape(entry.kind)}: {_escape(entry.title)} (not cited above)"
                )
                items.append({"id": eid, "kind": entry.kind, "title": entry.title, "registered": True, "cited": False})
        all_ids = cited + [eid for eid in register if eid not in cited]
        return self._section("evidence", lines or ["No evidence was recorded."], items, all_ids)

    def reproducibility(self) -> ReportSection:
        lines, items, evidence = [], [], []
        for e in self.data.experiments:
            digest = e.image_digest or (e.spec.environment.image_digest if e.spec else None)
            image = e.image or (e.spec.environment.image if e.spec else None)
            hash_ = e.spec_hash or (spec_hash(e.spec) if e.spec else None)
            seeds = e.seeds or (e.spec.seeds if e.spec else [])
            datasets = e.dataset_version_ids or ([u.dataset_version_id for u in e.spec.datasets] if e.spec else [])
            snapshot = e.code_snapshot_id or (e.spec.code.code_snapshot_id if e.spec else None)
            text = (
                f"{_escape(e.title)}: spec hash `{hash_ or 'n/a'}`, image `{_escape(image or 'n/a')}`"
                + (f" pinned to `{digest}`" if digest else " (not digest-pinned)")
                + f", code snapshot `{snapshot or 'n/a'}`, seeds {seeds or 'n/a'}, datasets {datasets or 'none'}"
            )
            lines.append(self.line(text, e.evidence_ids, "reproducibility"))
            items.append(
                {
                    "experiment_id": e.id,
                    "spec_hash": hash_,
                    "image": image,
                    "image_digest": digest,
                    "code_snapshot_id": snapshot,
                    "seeds": seeds,
                    "dataset_version_ids": datasets,
                }
            )
            evidence += e.evidence_ids
        verdicts = _counts(r.verdict for r in self.data.reproductions)
        lines += ["", f"Reproduction outcomes: {verdicts}."]
        return self._section("reproducibility", lines, items, evidence)

    def appendix(self) -> ReportSection:
        d = self.data
        versions = {"report": REPORT_ENGINE_VERSION, "comparison": COMPARISON_ENGINE_VERSION, **d.engine_versions}
        lines = ["Engine versions:"] + [f"- {_escape(k)}: `{_escape(v)}`" for k, v in sorted(versions.items())]
        if d.review is not None:
            lines += [
                "",
                f"Automated review ({d.review.engine_version}): verdict {d.review.verdict}, score {d.review.score:.2f}",
            ]
            lines += [f"- {check}: {status}" for check, status in d.review.checks.items()]
        if d.generated_at:
            lines += ["", f"Generated at: {_escape(d.generated_at)}"]
        return self._section("appendix", lines, [{"engine_versions": versions}], [])

    # -------------------------------------------------------------------------------------------
    @staticmethod
    def _section(section_id: str, lines: list[str], items: list[dict[str, Any]], evidence: list[str]) -> ReportSection:
        title = dict(SECTIONS)[section_id]
        return ReportSection(
            id=section_id,
            title=title,
            markdown="\n".join(lines).strip() + "\n",
            items=items,
            evidence_ids=list(dict.fromkeys(e for e in evidence if e)),
        )


def build_report(data: ReportInput | dict[str, Any]) -> ReportDocument:
    """Build the structured, evidence-cited mission report (deterministic)."""
    item = data if isinstance(data, ReportInput) else ReportInput.model_validate(data)
    b = _Builder(item)
    built: dict[str, ReportSection] = {}
    for section_id in (
        "executive_summary",
        "research_question",
        "methodology",
        "literature",
        "hypotheses",
        "experiments",
        "results",
        "failures",
        "evolution",
        "verification",
        "reproducibility",
    ):
        built[section_id] = getattr(b, section_id)()
    limitations_section, limitations = b.limitations()
    built["limitations"] = limitations_section
    cited = list(dict.fromkeys(eid for section in built.values() for eid in section.evidence_ids))
    built["evidence"] = b.evidence(cited)
    built["appendix"] = b.appendix()
    sections = [built[section_id] for section_id, _ in SECTIONS]
    all_ids = list(dict.fromkeys(eid for s in sections for eid in s.evidence_ids))
    payload = {"title": item.title, "sections": [s.model_dump() for s in sections]}
    content_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ReportDocument(
        title=item.title,
        sections=sections,
        limitations=limitations,
        evidence_ids=all_ids,
        content_hash=content_hash,
    )


def render_markdown(doc: ReportDocument) -> str:
    """Render the document as Markdown (``# title`` then one ``##`` heading per section, in order)."""
    parts = [f"# {_escape(doc.title)}", ""]
    for section in doc.sections:
        parts += [f"## {section.title}", "", section.markdown.rstrip(), ""]
    parts.append(f"_Report engine {doc.engine_version}; content hash {doc.content_hash}._")
    return "\n".join(parts) + "\n"
