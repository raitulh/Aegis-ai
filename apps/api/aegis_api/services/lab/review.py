"""Scientific review: deterministic reviewer checks (design, baseline, statistics, leakage, reproducibility,
overclaiming, self-reported metrics), optionally complemented by a ScientificReviewer agent whose notes are
stored as model-assisted and never override a deterministic 'fail'."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound
from aegis_api.models.lab import (
    ClaimEvidence,
    EvaluationRun,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    HypothesisEvidence,
    ScientificClaim,
)
from aegis_api.services.lab import evidence
from aegis_api.services.lab.common import Actor
from aegis_api.services.lab.reproducibility import manifest_report
from engines.lab.enums import RunKind
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.experiments.validator import ValidationIssue, ValidationReport
from engines.lab.review import ReviewInput, overall, review


def _validation(stored: dict[str, Any]) -> ValidationReport:
    return ValidationReport(
        issues=[
            ValidationIssue(i.get("code", ""), i.get("severity", "error"), i.get("field", ""), i.get("message", ""))
            for i in stored.get("issues", [])
        ]
    )


def review_experiment(
    organization_id: uuid.UUID,
    experiment_id: uuid.UUID,
    *,
    actor: Actor,
    model_review: dict[str, Any] | None = None,
    report_text: str = "",
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = ExperimentSpec.model_validate(version.spec)
        hypothesis = db.get(Hypothesis, experiment.hypothesis_id) if experiment.hypothesis_id else None
        sources = (
            int(
                db.scalar(
                    select(func.count(HypothesisEvidence.id)).where(HypothesisEvidence.hypothesis_id == hypothesis.id)
                )
                or 0
            )
            if hypothesis
            else 0
        )
        stat = db.scalar(
            select(EvaluationRun).where(
                EvaluationRun.experiment_version_id == version.id, EvaluationRun.evaluator_key == "statistical"
            )
        )
        runs = list(db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_version_id == version.id)).all())
        claims = db.scalars(select(ScientificClaim).where(ScientificClaim.experiment_id == experiment.id)).all()
        claim_rows = [
            {
                "statement": c.statement,
                "evidence_ids": [
                    str(e)
                    for e in db.scalars(select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == c.id)).all()
                ],
            }
            for c in claims
        ]
        repro = db.scalar(
            select(EvaluationRun).where(
                EvaluationRun.experiment_version_id == version.id, EvaluationRun.evaluator_key == "reproduction"
            )
        )
        contradicting = int(
            db.scalar(
                select(func.count(ClaimEvidence.id)).where(
                    ClaimEvidence.claim_id.in_([c.id for c in claims]), ClaimEvidence.relation == "contradicts"
                )
            )
            or 0
        )
        completeness = [
            manifest_report(r)["completeness"] for r in runs if r.manifest and r.run_kind == RunKind.CANDIDATE
        ]
        inp = ReviewInput(
            spec=spec,
            validation=_validation(version.validation or {}),
            hypothesis_novelty_notes=(hypothesis.novelty_notes or "") if hypothesis else "",
            literature_source_count=sources,
            statistical_rows=list((stat.details or {}).get("rows") or []) if stat else [],
            claims=claim_rows,
            reproduction_verdicts=[repro.verdict] if repro else [],
            contradicting_evidence_count=contradicting,
            manifest_completeness=min(completeness) if completeness else None,
            self_reported_metrics=any(r.self_reported for r in runs),
            report_text=report_text,
        )
        findings = review(inp)
        verdict = overall(findings)
        payload = {
            "experiment_id": str(experiment.id),
            "overall": verdict,
            "findings": [f.__dict__ for f in findings],
            "model_assisted": ({"source": "model_assisted", **model_review} if model_review else None),
        }
        record = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=experiment.mission_id,
            project_id=experiment.project_id,
            kind="scientific_review",
            title=f"Scientific review ({verdict}): {experiment.title}"[:300],
            content=payload,
            confidence=0.8,
        )
        experiment.outcome = {
            **(experiment.outcome or {}),
            "review": {"overall": verdict, "evidence_id": str(record.id)},
        }
        return {**payload, "evidence_id": str(record.id)}
