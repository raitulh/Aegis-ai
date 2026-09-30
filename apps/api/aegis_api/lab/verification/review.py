"""Scientific review: the rule-based reviewer (``engines.lab.review``) over stored research records, plus an
optional agent review stored alongside it.

``run_review`` gathers the review input for a subject — hypotheses (novelty rationale), literature consulted,
experiment specifications and their validation reports, comparisons, claims with evidence counts,
reproductions and report text — and persists the deterministic result as a ``ScientificReview`` with
``reviewer_type="rule"``. A ScientificReviewerAgent opinion is stored as a separate review
(``reviewer_type="agent"``). Reviews never approve, verify or publish anything.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.models import (
    AgentRun,
    ClaimEvidence,
    Discovery,
    Experiment,
    ExperimentComparison,
    ExperimentVersion,
    Hypothesis,
    Mission,
    MissionReport,
    Reproduction,
    ResearchSource,
    ResearchTask,
    ScientificClaim,
    ScientificReview,
)
from aegis_api.lab.verification.common import anchor, clean_text, comparison_result, get_many, jsonable, parse_spec
from aegis_api.lab.verification.schemas import ReviewOut
from aegis_api.schemas.common import Page, PageParams
from engines.lab.design_validator import ValidationReport
from engines.lab.review import (
    REVIEW_ENGINE_VERSION,
    ClaimSummary,
    HypothesisSummary,
    LabelledComparison,
    LabelledReport,
    LabelledSpec,
    ReproductionSummary,
    ReviewInput,
    ReviewResult,
    review,
)
from engines.lab.states import RunState

SUBJECT_TYPES: tuple[str, ...] = ("mission", "experiment", "claim", "discovery", "report")
REPRODUCTION_VERDICTS = frozenset({"reproduced", "partially_reproduced", "not_reproduced", "inconclusive"})
MAX_REPORT_TEXT = 1_500_000


def review_out(row: ScientificReview) -> ReviewOut:
    return ReviewOut.model_validate(row)


def report_text_from_content(content: Mapping[str, Any] | None) -> str:
    """Markdown of the report's sections (deterministic sections first, labelled model sections after)."""
    document = (content or {}).get("document") or {}
    parts = [f"# {document.get('title', '')}"]
    for section in document.get("sections") or []:
        parts.append(f"## {section.get('title', '')}\n\n{section.get('markdown', '')}")
    for section in (content or {}).get("model_generated_sections") or []:
        parts.append(f"## {section.get('title', 'Model-generated summary')}\n\n{section.get('text', '')}")
    return "\n\n".join(parts)[:MAX_REPORT_TEXT]


# ---------------------------------------------------------------------------------------------
# Scope collection
# ---------------------------------------------------------------------------------------------
@dataclass
class ReviewScope:
    subject_type: str
    subject_id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    mission_id: uuid.UUID | None
    experiments: list[Experiment] = field(default_factory=list)
    comparisons: list[ExperimentComparison] = field(default_factory=list)
    claims: list[ScientificClaim] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    reproductions: list[Reproduction] = field(default_factory=list)
    literature_refs: int = 0
    report_text: str = ""
    label: str = ""


def _mission_literature(db: Session, mission_id: uuid.UUID | None) -> int:
    if mission_id is None:
        return 0
    return int(
        db.scalar(
            select(func.count(ResearchSource.id))
            .join(ResearchTask, ResearchTask.id == ResearchSource.research_task_id)
            .where(ResearchTask.mission_id == mission_id)
        )
        or 0
    )


def _latest_report(db: Session, mission_id: uuid.UUID) -> MissionReport | None:
    return db.scalar(
        select(MissionReport).where(MissionReport.mission_id == mission_id).order_by(MissionReport.version.desc()).limit(1)
    )


def _fill_mission(db: Session, scope: ReviewScope, mission: Mission, *, include_report: bool) -> None:
    scope.experiments = list(
        db.scalars(select(Experiment).where(Experiment.mission_id == mission.id).order_by(Experiment.created_at)).all()
    )
    experiment_ids = [e.id for e in scope.experiments]
    conditions = [ExperimentComparison.mission_id == mission.id]
    if experiment_ids:
        conditions.append(ExperimentComparison.candidate_experiment_id.in_(experiment_ids))
    scope.comparisons = list(
        db.scalars(select(ExperimentComparison).where(or_(*conditions)).order_by(ExperimentComparison.created_at)).all()
    )
    scope.claims = list(
        db.scalars(
            select(ScientificClaim).where(ScientificClaim.mission_id == mission.id).order_by(ScientificClaim.created_at)
        ).all()
    )
    scope.hypotheses = list(
        db.scalars(select(Hypothesis).where(Hypothesis.mission_id == mission.id).order_by(Hypothesis.created_at)).all()
    )
    repro_conditions = [Reproduction.mission_id == mission.id]
    if experiment_ids:
        repro_conditions.append(Reproduction.experiment_id.in_(experiment_ids))
    scope.reproductions = list(
        db.scalars(select(Reproduction).where(or_(*repro_conditions)).order_by(Reproduction.created_at)).all()
    )
    scope.literature_refs = _mission_literature(db, mission.id)
    if include_report and (report := _latest_report(db, mission.id)) is not None:
        scope.report_text = report_text_from_content(report.content)


def _fill_claims(db: Session, scope: ReviewScope, claims: Sequence[ScientificClaim]) -> None:
    from aegis_api.lab.verification.verification import claim_context

    scope.claims = list(claims)
    experiments: dict[uuid.UUID, Experiment] = {}
    comparisons: dict[uuid.UUID, ExperimentComparison] = {}
    for claim in claims:
        ctx = claim_context(db, claim)
        if ctx.comparison is not None:
            comparisons[ctx.comparison.id] = ctx.comparison
        for exp in (ctx.candidate, ctx.baseline):
            if exp is not None:
                experiments[exp.id] = exp
    scope.experiments = list(experiments.values())
    scope.comparisons = list(comparisons.values())
    hypothesis_ids = {e.hypothesis_id for e in scope.experiments if e.hypothesis_id}
    scope.hypotheses = list(get_many(db, Hypothesis, hypothesis_ids, scope.organization_id).values())
    candidate_ids = [c.candidate_experiment_id for c in scope.comparisons] or [e.id for e in scope.experiments]
    scope.reproductions = (
        list(db.scalars(select(Reproduction).where(Reproduction.experiment_id.in_(candidate_ids))).all())
        if candidate_ids
        else []
    )
    scope.literature_refs = _mission_literature(db, scope.mission_id)


def collect_scope(db: Session, actor: Actor, subject_type: str, subject_id: uuid.UUID | str, *permissions: str) -> ReviewScope:
    """Load and authorize the review subject and everything the reviewer needs about it."""
    if subject_type not in SUBJECT_TYPES:
        raise ValidationFailed(f"subject_type must be one of {', '.join(SUBJECT_TYPES)}")
    org = actor.organization_id

    def scope_for(row: Any, project_id: uuid.UUID, mission_id: uuid.UUID | None, label: str) -> ReviewScope:
        try:
            project = load_project(db, actor, project_id, *permissions)
        except NotFound as exc:
            raise NotFound(f"{subject_type.capitalize()} not found") from exc
        return ReviewScope(
            subject_type=subject_type,
            subject_id=row.id,
            organization_id=org,
            project_id=project.id,
            workspace_id=project.workspace_id,
            mission_id=mission_id,
            label=label,
        )

    if subject_type == "mission":
        mission = get_owned(db, Mission, subject_id, actor, label="Mission")
        scope = scope_for(mission, mission.project_id, mission.id, mission.title)
        _fill_mission(db, scope, mission, include_report=True)
        return scope
    if subject_type == "report":
        report = get_owned(db, MissionReport, subject_id, actor, label="Report")
        mission = db.get(Mission, report.mission_id)
        if mission is None:
            raise NotFound("Report not found")
        scope = scope_for(report, report.project_id, report.mission_id, f"{mission.title} report v{report.version}")
        _fill_mission(db, scope, mission, include_report=False)
        scope.report_text = report_text_from_content(report.content)
        return scope
    if subject_type == "experiment":
        experiment = get_owned(db, Experiment, subject_id, actor, label="Experiment")
        scope = scope_for(experiment, experiment.project_id, experiment.mission_id, experiment.title)
        experiments = {experiment.id: experiment}
        if experiment.baseline_experiment_id and (base := db.get(Experiment, experiment.baseline_experiment_id)):
            experiments[base.id] = base
        scope.experiments = list(experiments.values())
        scope.comparisons = list(
            db.scalars(
                select(ExperimentComparison)
                .where(ExperimentComparison.candidate_experiment_id == experiment.id)
                .order_by(ExperimentComparison.created_at)
            ).all()
        )
        comparison_ids = [c.id for c in scope.comparisons]
        scope.claims = (
            list(
                db.scalars(
                    select(ScientificClaim).where(
                        ScientificClaim.source_type == "experiment_comparison",
                        ScientificClaim.source_id.in_(comparison_ids),
                    )
                ).all()
            )
            if comparison_ids
            else []
        )
        if experiment.hypothesis_id and (hypothesis := db.get(Hypothesis, experiment.hypothesis_id)):
            scope.hypotheses = [hypothesis]
        scope.reproductions = list(
            db.scalars(select(Reproduction).where(Reproduction.experiment_id == experiment.id)).all()
        )
        scope.literature_refs = _mission_literature(db, experiment.mission_id)
        return scope
    if subject_type == "claim":
        claim = get_owned(db, ScientificClaim, subject_id, actor, label="Claim")
        scope = scope_for(claim, claim.project_id, claim.mission_id, clean_text(claim.statement, 120))
        _fill_claims(db, scope, [claim])
        return scope
    discovery = get_owned(db, Discovery, subject_id, actor, label="Discovery")
    claim = db.get(ScientificClaim, discovery.claim_id)
    if claim is None:
        raise NotFound("Discovery not found")
    scope = scope_for(discovery, discovery.project_id, discovery.mission_id, discovery.title)
    _fill_claims(db, scope, [claim])
    scope.report_text = "\n\n".join(t for t in (discovery.title, discovery.summary or "") if t)
    return scope


def build_review_input(db: Session, scope: ReviewScope) -> tuple[ReviewInput, list[str]]:
    """Scope → the engine's ``ReviewInput`` (+ notes about records that could not be interpreted)."""
    notes: list[str] = []
    versions = get_many(
        db, ExperimentVersion, [e.current_version_id for e in scope.experiments if e.current_version_id], scope.organization_id
    )
    specs: list[LabelledSpec] = []
    reports: list[LabelledReport] = []
    for experiment in scope.experiments:
        version = versions.get(experiment.current_version_id) if experiment.current_version_id else None
        if version is None:
            continue
        spec = parse_spec(version.spec)
        if spec is not None:
            specs.append(LabelledSpec(id=str(experiment.id), spec=spec))
        else:
            notes.append(f"experiment {experiment.id}: the stored specification could not be parsed")
        if version.validation_report:
            try:
                reports.append(
                    LabelledReport(id=str(version.id), report=ValidationReport.model_validate(version.validation_report))
                )
            except ValidationError:
                notes.append(f"experiment version {version.id}: the validation report could not be parsed")
    comparisons: list[LabelledComparison] = []
    for comparison in scope.comparisons:
        result = comparison_result(comparison)
        if result is None:
            notes.append(f"comparison {comparison.id}: statistics could not be interpreted")
            continue
        comparisons.append(LabelledComparison(id=str(comparison.id), comparison=result))
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"supports": 0, "contradicts": 0})
    evidence_ids: dict[uuid.UUID, list[str]] = defaultdict(list)
    claim_ids = [c.id for c in scope.claims]
    if claim_ids:
        for claim_id, relation, evidence_id in db.execute(
            select(ClaimEvidence.claim_id, ClaimEvidence.relation, ClaimEvidence.evidence_id).where(
                ClaimEvidence.claim_id.in_(claim_ids)
            )
        ).all():
            if relation in ("supports", "contradicts"):
                counts[claim_id][relation] += 1
            if evidence_id is not None and relation == "supports":
                evidence_ids[claim_id].append(str(evidence_id))
    data = ReviewInput(
        hypotheses=[
            HypothesisSummary(id=str(h.id), statement=h.statement, novelty_rationale=h.novelty_notes, status=h.status)
            for h in scope.hypotheses
        ],
        literature_refs=scope.literature_refs,
        specs=specs,
        validation_reports=reports,
        comparisons=comparisons,
        claims=[
            ClaimSummary(
                id=str(c.id),
                text=c.statement,
                status=c.status,
                supporting_evidence_count=counts[c.id]["supports"],
                contradicting_evidence_count=counts[c.id]["contradicts"],
                evidence_ids=evidence_ids[c.id][:200],
            )
            for c in scope.claims
        ],
        reproductions=[
            ReproductionSummary(id=str(r.id), verdict=r.verdict, experiment_id=str(r.experiment_id))  # type: ignore[arg-type]
            for r in scope.reproductions
            if r.status == RunState.COMPLETED and r.verdict in REPRODUCTION_VERDICTS
        ],
        report_text=scope.report_text[:MAX_REPORT_TEXT],
    )
    return data, notes


# ---------------------------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------------------------
def run_review(db: Session, actor: Actor, subject_type: str, subject_id: uuid.UUID | str) -> ScientificReview:
    """Deterministic rule-based review of a subject (``reviewer_type="rule"``)."""
    scope = collect_scope(db, actor, subject_type, subject_id, "verification:run")
    data, notes = build_review_input(db, scope)
    result: ReviewResult = review(data)
    row = ScientificReview(
        id=uuid.uuid4(),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        mission_id=scope.mission_id,
        subject_type=subject_type,
        subject_id=scope.subject_id,
        reviewer_type="rule",
        reviewer_user_id=None,
        checks=jsonable(
            {
                **result.checks,
                "engine_version": result.engine_version,
                "input_summary": {
                    "hypotheses": len(data.hypotheses),
                    "literature_refs": data.literature_refs,
                    "specs": len(data.specs),
                    "validation_reports": len(data.validation_reports),
                    "comparisons": len(data.comparisons),
                    "claims": len(data.claims),
                    "reproductions": len(data.reproductions),
                    "report_chars": len(data.report_text),
                },
                "notes": notes,
                "requested_by": actor.as_dict(),
            }
        ),
        findings=jsonable([f.model_dump() for f in result.findings]),
        verdict=result.verdict,
        score=result.score,
    )
    db.add(row)
    db.flush()
    anchor(
        db,
        scope.organization_id,
        "scientific_review",
        f"Scientific review of {subject_type} {scope.subject_id}: {result.verdict}",
        {
            "review_id": row.id,
            "subject_type": subject_type,
            "subject_id": scope.subject_id,
            "reviewer_type": "rule",
            "verdict": result.verdict,
            "score": result.score,
            "checks": result.checks,
            "engine_version": result.engine_version,
        },
    )
    return row


AGENT_VERDICTS = {"accept": "pass", "minor_revision": "concerns", "major_revision": "concerns", "reject": "fail"}


def record_agent_review(
    db: Session,
    actor: Actor,
    agent_run: AgentRun,
    *,
    subject_type: str,
    subject_id: uuid.UUID | str,
    findings: Sequence[Mapping[str, Any]],
    scores: Mapping[str, int] | None,
    recommendation: str | None,
    summary: str | None,
) -> ScientificReview:
    """Store a ScientificReviewerAgent review next to the rule review (never replaces it, approves nothing)."""
    scope = collect_scope(db, actor, subject_type, subject_id, "verification:run")
    existing = db.scalar(
        select(ScientificReview).where(
            ScientificReview.agent_run_id == agent_run.id,
            ScientificReview.subject_type == subject_type,
            ScientificReview.subject_id == scope.subject_id,
        )
    )
    if existing is not None:
        return existing
    severities = {str(f.get("severity")) for f in findings}
    verdict = AGENT_VERDICTS.get(recommendation or "", "")
    if not verdict:
        verdict = "fail" if "error" in severities else "concerns" if "warning" in severities else "pass"
    elif verdict == "pass" and "error" in severities:
        verdict = "concerns"
    score = None
    if scores:
        values = [int(v) for v in scores.values() if isinstance(v, int) and not isinstance(v, bool)]
        score = round(sum(values) / (5 * len(values)), 6) if values else None
    row = ScientificReview(
        id=uuid.uuid4(),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        mission_id=scope.mission_id,
        subject_type=subject_type,
        subject_id=scope.subject_id,
        reviewer_type="agent",
        agent_run_id=agent_run.id,
        checks=jsonable(
            {
                "scores": dict(scores or {}),
                "recommendation": recommendation,
                "summary": (summary or "")[:4000] or None,
                "advisory": True,
                "provenance": {
                    "agent_run_id": agent_run.id,
                    "agent_version_id": agent_run.agent_version_id,
                    "provider": agent_run.provider,
                    "model": agent_run.model,
                    "model_version": agent_run.model_version,
                    "prompt_key": agent_run.prompt_key,
                    "prompt_version": agent_run.prompt_version,
                    "prompt_hash": agent_run.prompt_hash,
                    "strategy_version_id": agent_run.strategy_version_id,
                },
            }
        ),
        findings=jsonable([dict(f) for f in findings]),
        verdict=verdict,
        score=score,
    )
    db.add(row)
    db.flush()
    return row


def get_review(db: Session, actor: Actor, review_id: uuid.UUID | str) -> ScientificReview:
    row = get_owned(db, ScientificReview, review_id, actor, label="Review")
    if row.project_id is not None:
        try:
            load_project(db, actor, row.project_id, "verification:read")
        except NotFound as exc:
            raise NotFound("Review not found") from exc
    else:
        actor.require("verification:read")
    return row


def list_reviews(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    subject_type: str | None = None,
    subject_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    reviewer_type: str | None = None,
) -> Page[ReviewOut]:
    stmt = select(ScientificReview).where(ScientificReview.organization_id == actor.organization_id)
    if subject_type:
        if subject_type not in SUBJECT_TYPES:
            raise ValidationFailed(f"subject_type must be one of {', '.join(SUBJECT_TYPES)}")
        stmt = stmt.where(ScientificReview.subject_type == subject_type)
    if subject_id:
        try:
            stmt = stmt.where(ScientificReview.subject_id == uuid.UUID(str(subject_id)))
        except ValueError as exc:
            raise ValidationFailed("Invalid subject_id") from exc
    if mission_id:
        stmt = stmt.where(ScientificReview.mission_id == get_owned(db, Mission, mission_id, actor, label="Mission").id)
    if reviewer_type:
        stmt = stmt.where(ScientificReview.reviewer_type == reviewer_type)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(ScientificReview.project_id.is_(None), ScientificReview.project_id.in_(visible)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ScientificReview.created_at.desc(), ScientificReview.id.desc())
        .limit(params.page_size)
        .offset(params.offset)
    ).all()
    return Page.build([review_out(r) for r in rows], int(total), params)


def latest_rule_review(db: Session, subject_type: str, subject_id: uuid.UUID) -> ReviewResult | None:
    """The newest rule review of a subject as the engine model (for reports)."""
    row = db.scalar(
        select(ScientificReview)
        .where(
            ScientificReview.subject_type == subject_type,
            ScientificReview.subject_id == subject_id,
            ScientificReview.reviewer_type == "rule",
        )
        .order_by(ScientificReview.created_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    checks = {k: v for k, v in (row.checks or {}).items() if k in REVIEW_CHECK_KEYS}
    try:
        return ReviewResult.model_validate(
            {
                "findings": row.findings or [],
                "checks": checks,
                "verdict": row.verdict,
                "score": row.score if row.score is not None else 0.0,
                "engine_version": (row.checks or {}).get("engine_version", REVIEW_ENGINE_VERSION),
            }
        )
    except ValidationError:
        return None


REVIEW_CHECK_KEYS = frozenset(
    {
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
    }
)
