"""Research reports.

Factual sections are assembled deterministically from stored records, and every number cites the evidence
record it comes from as ``[EV:<evidence-id>]``. A ReportAgent may contribute narrative; it is accepted only if
every citation resolves to known evidence, no numeric sentence is uncited and no overclaiming language is
used — otherwise the narrative is rejected and the deterministic summary is used. Reports never claim legal
compliance, certification or guarantees.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound
from aegis_api.models import Evidence
from aegis_api.models.lab import (
    Discovery,
    EvaluationRun,
    Experiment,
    Failure,
    Hypothesis,
    Mission,
    ResearchReport,
    ResearchSource,
    ScientificClaim,
    Verification,
)
from aegis_api.security.context import Principal
from aegis_api.services.lab import artifacts, events, usage
from aegis_api.services.lab.access import accessible_project_ids
from aegis_api.services.lab.common import Actor, sha256_bytes
from engines.lab.enums import LabEventType
from engines.lab.reports import render_markdown, validate_citations

DISCLAIMER = (
    "This report summarizes machine-assisted research results with their evidence. It is not a certification, "
    "compliance attestation or guarantee; conclusions are limited to the tested configurations."
)


def _cite(evidence_id: uuid.UUID | str | None) -> str:
    return f" [EV:{evidence_id}]" if evidence_id else ""


def facts(organization_id: uuid.UUID, mission_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        mission = db.get(Mission, mission_id)
        if mission is None:
            raise NotFound("Mission not found")
        known: set[str] = {
            str(e) for e in db.scalars(select(Evidence.id).where(Evidence.chain_scope == f"mission:{mission.id}")).all()
        }
        hyps = db.scalars(
            select(Hypothesis).where(Hypothesis.mission_id == mission.id).order_by(Hypothesis.created_at)
        ).all()
        exps = db.scalars(
            select(Experiment).where(Experiment.mission_id == mission.id).order_by(Experiment.created_at)
        ).all()
        claims = db.scalars(
            select(ScientificClaim).where(ScientificClaim.mission_id == mission.id).order_by(ScientificClaim.created_at)
        ).all()
        failures = db.scalars(
            select(Failure).where(Failure.mission_id == mission.id).order_by(Failure.detected_at)
        ).all()
        discoveries = db.scalars(select(Discovery).where(Discovery.mission_id == mission.id)).all()
        sources = db.scalars(
            select(ResearchSource)
            .where(ResearchSource.project_id == mission.project_id)
            .order_by(ResearchSource.created_at)
            .limit(30)
        ).all()
        sections: dict[str, str] = {}
        sections["Research Question"] = f"{mission.objective}\n\nConstraints: " + (
            "; ".join(mission.constraints) if mission.constraints else "none recorded"
        )
        sections["Methodology"] = (
            f"Autonomy level {mission.autonomy_level}; {len(exps)} experiment(s) executed in isolated sandboxes "
            "(no network, non-root, resource-limited). Metrics were measured by platform-owned evaluation harnesses "
            "unless flagged as self-reported. Claims were verified against configured criteria (reproduction, "
            "independent re-evaluation, provenance, statistics)."
        )
        sections["Literature"] = (
            "\n".join(
                f"- {s.title or s.url} ({s.source_type}{', ' + s.publication_date if s.publication_date else ''})"
                + (f" doi:{s.doi}" if s.doi else "")
                for s in sources
            )
            or "_No literature sources were recorded._"
        )
        sections["Hypotheses"] = (
            "\n".join(
                f"- **{h.status}** — {h.statement}"
                + (
                    f" _(critique score {h.critique_score:.2f}, model-assessed)_"
                    if h.critique_score is not None
                    else ""
                )
                for h in hyps
            )
            or "_No hypotheses._"
        )
        exp_lines: list[str] = []
        result_lines: list[str] = []
        for x in exps:
            exp_lines.append(f"- {x.title} — status {x.status}; outcome {(x.outcome or {}).get('evaluation', 'n/a')}")
            evals = db.scalars(select(EvaluationRun).where(EvaluationRun.experiment_id == x.id)).all()
            for e in evals:
                if str(e.evidence_id) in known and e.evaluator_key in ("benchmark", "statistical"):
                    metrics = ", ".join(f"{k}={v:.4g}" for k, v in list((e.metrics or {}).items())[:6])
                    result_lines.append(
                        f"- {x.title}: {e.evaluator_key} evaluator verdict {e.verdict} ({metrics}).{_cite(e.evidence_id)}"
                    )
        sections["Experiments"] = "\n".join(exp_lines) or "_No experiments._"
        sections["Results"] = "\n".join(result_lines) or "_No evaluated results._"
        sections["Failures"] = (
            "\n".join(
                f"- {f.failure_type} ({f.rule_id}): {f.root_cause}; recurrence {f.recurrence_count}.{_cite(f.evidence_id)}"
                for f in failures
                if str(f.evidence_id) in known or f.evidence_id is None
            )
            or "_No failures recorded._"
        )
        ver_lines = []
        for c in claims:
            v = db.scalar(
                select(Verification).where(Verification.claim_id == c.id).order_by(Verification.created_at.desc())
            )
            ver_lines.append(
                f"- **{c.status}** (confidence {c.confidence:.2f}): {c.statement}"
                + (_cite(v.evidence_id) if v and str(v.evidence_id) in known else "")
            )
        sections["Verification"] = "\n".join(ver_lines) or "_No claims were verified._"
        sections["Limitations"] = "\n".join(
            [
                "- Results apply only to the tested parameters, dataset versions, environments and seeds.",
                "- Hypothesis scores and agent confidences are model self-assessments, not measurements.",
                *[
                    f"- Discovery '{d.title[:120]}' is {d.status}; limitations: {'; '.join(d.limitations)}"
                    for d in discoveries
                ],
            ]
        )
        spend = usage.summary(db, organization_id, mission_id=mission.id)["spend"]
        sections["Reproducibility"] = (
            "Every run stores a reproducibility manifest (code checksum, environment image digest, dataset checksum, "
            "seeds, parameters, hardware, outputs). Evidence is hash-chained per mission "
            f"(head {mission.evidence_head_hash or 'n/a'}, {mission.evidence_seq} records). "
            f"Recorded spend: LLM {spend['llm_tokens']} tokens, compute {spend['compute_seconds']} s."
        )
        sections["Evidence"] = f"{len(known)} evidence record(s) in the mission's hash chain."
        verified = [c for c in claims if c.status == "verified"]
        summary = (
            f"Mission '{mission.title}' ran {len(exps)} experiment(s) over {len(hyps)} hypothesis(es). "
            f"{len(verified)} claim(s) reached VERIFIED status, {len(failures)} failure(s) were analyzed."
        )
        return {
            "title": f"Research report: {mission.title}",
            "sections": sections,
            "deterministic_summary": summary,
            "known_evidence_ids": sorted(known),
            "project_id": str(mission.project_id),
        }


def assemble(
    organization_id: uuid.UUID,
    mission_id: uuid.UUID,
    report_facts: dict[str, Any],
    *,
    narrative: dict[str, Any] | None,
    narrative_run_id: uuid.UUID | None,
    actor: Actor,
) -> dict[str, Any]:
    sections = dict(report_facts["sections"])
    known = report_facts["known_evidence_ids"]
    check_summary: dict[str, Any] = {"narrative": "not_requested"}
    summary = report_facts["deterministic_summary"]
    if narrative:
        text = "\n\n".join([narrative.get("executive_summary", ""), *narrative.get("sections", {}).values()])
        check = validate_citations(text, known)
        accepted = not check.unknown and not check.overclaiming and check.uncited_numeric_sentences == 0
        check_summary = {
            "narrative": "accepted" if accepted else "rejected",
            "cited": check.cited,
            "unknown": check.unknown,
            "uncited_numeric_sentences": check.uncited_numeric_sentences,
            "overclaiming": check.overclaiming,
        }
        if accepted:
            summary = validate_citations(narrative.get("executive_summary", ""), known).text
            for name, body in (narrative.get("sections") or {}).items():
                if name in ("Executive Summary", "Research Question", "Results", "Verification"):
                    continue  # factual sections stay deterministic
                sections[f"{name}"] = validate_citations(body, known).text
    sections["Executive Summary"] = summary + "\n\n" + DISCLAIMER
    title = report_facts["title"]
    markdown = render_markdown(
        title,
        sections,
        metadata={
            "mission_id": str(mission_id),
            "generated_by": actor.label,
            "citation_check": json.dumps(check_summary),
        },
    )
    project_id = uuid.UUID(report_facts["project_id"])
    artifact_id, _, digest = artifacts.store(
        organization_id,
        data=markdown.encode(),
        name=f"report-{mission_id}.md",
        kind="report",
        content_type="text/markdown",
        project_id=project_id,
        mission_id=mission_id,
        retention_class="evidence",
        created_by=actor.label,
    )
    with session_scope(organization_id) as db:
        report = ResearchReport(
            organization_id=organization_id,
            project_id=project_id,
            mission_id=mission_id,
            title=title[:300],
            status="ready",
            sections=sections,
            markdown_artifact_id=artifact_id,
            content_hash=sha256_bytes(markdown.encode()),
            cited_evidence_ids=sorted({c for c in known if f"[EV:{c}]" in markdown}),
            citation_check=check_summary,
            narrative_run_id=narrative_run_id,
            generated_by=actor.label[:160],
        )
        db.add(report)
        db.flush()
        events.emit(
            db,
            organization_id=organization_id,
            mission_id=mission_id,
            project_id=project_id,
            event_type=LabEventType.REPORT_GENERATED,
            message=f"Report generated ({len(report.cited_evidence_ids)} evidence citations; narrative {check_summary['narrative']})",
            data={"report_id": str(report.id), "artifact_id": str(artifact_id), "sha256": digest},
            actor=actor,
        )
        return {"report_id": str(report.id), "artifact_id": str(artifact_id), "citation_check": check_summary}


def list_reports(db: Session, principal: Principal, *, mission_id: uuid.UUID | None = None) -> Select[ResearchReport]:
    stmt = select(ResearchReport).where(ResearchReport.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(ResearchReport.project_id.in_(visible))
    if mission_id:
        stmt = stmt.where(ResearchReport.mission_id == mission_id)
    return stmt.order_by(ResearchReport.created_at.desc())
