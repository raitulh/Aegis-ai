"""Mission report builder: section order, evidence citations, derived limitations, determinism."""

from __future__ import annotations

import re
from typing import Any

from engines.lab.comparison import compare_samples
from engines.lab.experiment_spec import StatisticalPlan, spec_hash
from engines.lab.reports import NO_EVIDENCE, REPORT_ENGINE_VERSION, SECTIONS, build_report, render_markdown
from engines.lab.review import review, scan_overclaiming
from tests.lab.unit.test_experiment_spec import clean_spec
from tests.lab.unit.test_review import clean_input

EXPECTED_SECTIONS = [
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
]
PLAN = StatisticalPlan(n_seeds=3)
IMPROVED = compare_samples([0.80, 0.81, 0.79], [0.86, 0.87, 0.85], metric="accuracy", direction="maximize", plan=PLAN)
INSUFFICIENT = compare_samples([120.0], [100.0], metric="latency_ms", direction="minimize", plan=PLAN)


def report_input(**changes: Any) -> dict[str, Any]:
    spec = clean_spec()
    data: dict[str, Any] = {
        "title": "Distillation efficiency study",
        "research_question": "Does attention distillation improve accuracy at equal latency?",
        "mission_id": "mission-1",
        "objective": "Quantify the accuracy change of the distilled model.",
        "literature": [
            {"id": "lit-1", "title": "Distilling attention", "year": 2025, "evidence_ids": ["ev-lit-1"]},
            {
                "id": "lit-2",
                "title": "A preprint on pruning",
                "is_preprint": True,
                "url": "https://arxiv.org/abs/2501.00001",
                "evidence_ids": ["ev-lit-2"],
            },
        ],
        "hypotheses": [
            {
                "id": "hyp-1",
                "statement": "Distillation improves accuracy.",
                "status": "SUPPORTED",
                "evidence_ids": ["ev-h1"],
            },
            {
                "id": "hyp-2",
                "statement": "Distillation reduces latency.",
                "status": "INCONCLUSIVE",
                "evidence_ids": ["ev-h2"],
            },
        ],
        "experiments": [
            {
                "id": "exp-1",
                "title": "Distilled vs baseline",
                "kind": "candidate",
                "status": "COMPLETED",
                "spec": spec.model_dump(),
                "n_runs": 3,
                "seeds": [0, 1, 2],
                "metric_sources": {"accuracy": "evaluator", "latency_ms": "platform"},
                "evidence_ids": ["ev-exp-1"],
            },
            {
                "id": "exp-2",
                "title": "Self-reported pilot",
                "status": "COMPLETED",
                "metric_sources": {"accuracy": "self_reported"},
                "evidence_ids": [],
            },
        ],
        "results": [
            {
                "id": "res-1",
                "comparison": IMPROVED.model_dump(),
                "experiment_id": "exp-1",
                "metric_source": "evaluator",
                "evidence_ids": ["ev-cmp-1"],
            },
            {
                "id": "res-2",
                "comparison": INSUFFICIENT.model_dump(),
                "experiment_id": "exp-1",
                "metric_source": "self_reported",
                "evidence_ids": ["ev-cmp-2"],
            },
        ],
        "failures": [
            {
                "id": "fail-1",
                "failure_type": "RESOURCE_FAILURE",
                "summary": "Seed 3 ran out of memory.",
                "status": "RESOLVED",
                "lesson": "Request 2 GB.",
                "evidence_ids": ["ev-f1"],
            }
        ],
        "evolution": [
            {
                "id": "sv-1",
                "strategy": "experiment-designer",
                "version": 2,
                "status": "SURVIVING",
                "summary": "Adds a validation split.",
                "fitness": {"quality": 0.71},
                "evidence_ids": ["ev-evo"],
            }
        ],
        "claims": [
            {
                "id": "claim-1",
                "statement": "Distillation improved accuracy by 6 points.",
                "status": "PARTIALLY_VERIFIED",
                "confidence": 0.62,
                "profile": "ml_benchmark",
                "satisfied": ["statistically_supported", "evaluator_passed"],
                "missing": ["replicated"],
                "failed": [],
                "evidence_ids": ["ev-cmp-1", "ev-claim-1"],
            },
            {
                "id": "claim-2",
                "statement": "Latency is unchanged.",
                "status": "CONTESTED",
                "confidence": 0.3,
                "contradicting_evidence_count": 1,
                "evidence_ids": [],
            },
        ],
        "reproductions": [
            {"id": "rep-1", "verdict": "partially_reproduced", "experiment_id": "exp-1", "evidence_ids": ["ev-rep-1"]}
        ],
        "evidence": [
            {"id": "ev-cmp-1", "kind": "lab.comparison", "title": "Comparison accuracy"},
            {"id": "ev-orphan", "kind": "lab.note", "title": "Uncited note"},
        ],
        "limitations": ["Single dataset."],
        "generated_at": "2026-09-30T12:00:00Z",
        "engine_versions": {"verification": "verification-1.0.0"},
    }
    data.update(changes)
    return data


def test_report_has_exactly_the_fourteen_sections_in_order():
    doc = build_report(report_input())
    assert [(s.id, s.title) for s in doc.sections] == EXPECTED_SECTIONS == list(SECTIONS)
    assert doc.engine_version == REPORT_ENGINE_VERSION
    markdown = render_markdown(doc)
    headings = re.findall(r"^## (.+)$", markdown, flags=re.MULTILINE)
    assert headings == [title for _, title in EXPECTED_SECTIONS]
    assert markdown.startswith("# Distillation efficiency study\n")


def test_every_claim_and_result_line_cites_evidence():
    doc = build_report(report_input())
    for section_id in ("results", "verification", "hypotheses", "experiments", "literature"):
        lines = [line for line in doc.section(section_id).markdown.splitlines() if line.startswith("- ")]
        assert lines, section_id
        for line in lines:
            assert "[evidence:" in line or NO_EVIDENCE in line, line
    results = doc.section("results").markdown
    assert "[evidence:ev-cmp-1]" in results and "[evidence:ev-cmp-2]" in results
    assert "[evidence:ev-claim-1]" in doc.section("verification").markdown
    assert NO_EVIDENCE in doc.section("verification").markdown  # claim-2 has none, and says so
    assert "ev-cmp-1" in doc.section("results").evidence_ids and "ev-cmp-1" in doc.evidence_ids


def test_uncertainty_is_never_collapsed_to_binary():
    doc = build_report(report_input())
    results = doc.section("results").markdown
    assert "95% CI [" in results and "p = " in results and "hedges_g = " in results and "n = 3 vs 3" in results
    assert "insufficient data" in results
    verification = doc.section("verification").markdown
    assert (
        "confidence 0.62" in verification
        and "partially verified" in verification
        and "missing: replicated" in verification
    )
    summary = doc.section("executive_summary").markdown
    assert "improved" in summary and "95% CI" in summary and "contested" in summary


def test_limitations_are_derived_automatically():
    doc = build_report(report_input())
    text = "\n".join(doc.limitations)
    expected_fragments = [
        "Reproduction rep-1 of experiment exp-1 was partially reproduced",  # failed/partial reproduction
        "Hypothesis hyp-2 was inconclusive",  # inconclusive hypothesis
        "Claim claim-1 is partially verified",  # unverified claim
        "Claim claim-2 has contradicting evidence",  # contradicting evidence
        "rests on small samples",  # small n
        "insufficient data",  # insufficient comparison
        "self-reported",  # self-reported-only metrics
        "is a preprint",  # literature quality
        "have no recorded evidence",  # missing evidence
        "Single dataset.",  # provided limitation
    ]
    for fragment in expected_fragments:
        assert fragment in text, fragment
    assert doc.section("limitations").markdown.count("\n- ") + 1 == len(doc.limitations)


def test_evidence_register_and_review_in_appendix():
    result = review(clean_input())
    doc = build_report(report_input(review=result.model_dump()))
    evidence = doc.section("evidence")
    assert "[evidence:ev-cmp-1] lab.comparison: Comparison accuracy" in evidence.markdown
    assert "[evidence:ev-h1] — not in the evidence register" in evidence.markdown
    assert "Uncited note (not cited above)" in evidence.markdown
    appendix = doc.section("appendix").markdown
    assert "comparison-1.0.0" in appendix and "verification-1.0.0" in appendix and "Automated review" in appendix
    assert "Generated at: 2026-09-30T12:00:00Z" in appendix


def test_reproducibility_section_records_hashes_images_and_seeds():
    doc = build_report(report_input())
    item = doc.section("reproducibility").items[0]
    assert item["spec_hash"] == spec_hash(clean_spec())
    assert item["image_digest"].startswith("sha256:") and item["seeds"] == [0, 1, 2]
    assert "Reproduction outcomes: partially reproduced: 1." in doc.section("reproducibility").markdown


def test_report_is_deterministic():
    a = build_report(report_input())
    b = build_report(report_input())
    assert a == b and a.content_hash == b.content_hash and render_markdown(a) == render_markdown(b)
    c = build_report(report_input(title="Another title"))
    assert c.content_hash != a.content_hash


def test_untrusted_text_is_escaped_and_template_does_not_overclaim():
    evil = report_input(
        hypotheses=[
            {
                "id": "h",
                "statement": "<script>alert(1)</script> [click](javascript:alert(1))",
                "status": "GENERATED",
                "evidence_ids": ["e]x"],
            }
        ]
    )
    doc = build_report(evil)
    hypotheses = doc.section("hypotheses").markdown
    assert "<script>" not in hypotheses and "\\<script\\>" in hypotheses
    assert re.search(r"(?<!\\)\]\(", hypotheses) is None  # no unescaped "](" → no live markdown link
    assert re.search(r"(?<!\\)\[click", hypotheses) is None
    assert "[evidence:e_x]" in hypotheses  # ids are reduced to a markdown-inert form
    neutral = build_report(report_input())
    assert scan_overclaiming(render_markdown(neutral)) == []


def test_minimal_report_still_has_every_section():
    doc = build_report({"title": "Empty mission", "research_question": "Anything?"})
    assert [s.id for s in doc.sections] == [sid for sid, _ in EXPECTED_SECTIONS]
    assert "No specific limitation was detected automatically" in doc.section("limitations").markdown
    assert "No baseline comparisons were completed." in doc.section("results").markdown
    assert doc.evidence_ids == []
