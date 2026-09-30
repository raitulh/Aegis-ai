"""Hypothesis lifecycle: creation, critique, deterministic selection, evidence links and conclusion.

Rules enforced here (never delegated to a model):

* A hypothesis is only accepted when it is **falsifiable**: it carries a measurable prediction (metric,
  comparator, threshold, relative_to, direction). Vague hypotheses are rejected with guidance (422).
* Critiques must be independent of the generator: the agent run that proposed a hypothesis (or any
  ``HypothesisAgent``) can never critique it. Critique scores are aggregated into ``hypothesis.scores``.
* Selection is deterministic (:mod:`aegis_api.lab.hypotheses.scoring`) and never selects a hypothesis without a
  measurable prediction.
* ``SUPPORTED`` / ``REJECTED`` / ``INCONCLUSIVE`` are reached **only** through :func:`conclude_hypothesis`, which
  decides from recorded experiment comparisons (verdict, pre-registered prediction, statistical power) — never
  because a person or a model asserted it. Every status change goes through ``assert_transition``.

Services never commit; callers own the transaction.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate, sort_clause
from aegis_api.lab.hypotheses import scoring
from aegis_api.lab.hypotheses.schemas import (
    PREDICTION_EXAMPLE,
    PREDICTION_GUIDANCE,
    CritiqueInput,
    EvidenceCreate,
    EvidenceRef,
    HypothesisCreate,
    HypothesisUpdate,
    MeasurablePrediction,
    parse_prediction,
)
from aegis_api.lab.models import (
    ArtifactVersion,
    DatasetVersion,
    EvaluationRun,
    Experiment,
    ExperimentComparison,
    ExperimentRun,
    Hypothesis,
    HypothesisCritique,
    HypothesisEvidence,
    Memory,
    Mission,
    Project,
    ResearchSource,
    ScientificClaim,
    SourceDocument,
    Strategy,
    StrategyVersion,
)
from aegis_api.schemas.common import Page, PageParams
from engines.lab.experiment_spec import check_criterion
from engines.lab.statistics import StatisticsError, required_seeds_estimate
from engines.lab.states import (
    MISSION_TERMINAL,
    AgentRole,
    HypothesisStatus,
    StrategyStatus,
    assert_transition,
    can_transition,
)

log = structlog.get_logger("aegis.lab.hypotheses")

H = HypothesisStatus
EDITABLE_STATUSES = frozenset({H.GENERATED, H.CRITIQUED})
CONCLUSION_STATUSES = frozenset({H.SUPPORTED, H.REJECTED, H.INCONCLUSIVE})
CONCLUSION_RULES_VERSION = "hypothesis-conclusion-1.0.0"
ASSUMED_POWER = 0.8
MIN_STATEMENT_WORDS = 4
SORTABLE = ("created_at", "updated_at", "status", "selection_rank", "confidence", "feasibility")

REF_MODELS: dict[str, type[Any]] = {
    "research_source": ResearchSource,
    "source_document": SourceDocument,
    "memory": Memory,
    "experiment": Experiment,
    "experiment_run": ExperimentRun,
    "experiment_comparison": ExperimentComparison,
    "evaluation_run": EvaluationRun,
    "dataset_version": DatasetVersion,
    "artifact_version": ArtifactVersion,
    "claim": ScientificClaim,
    "hypothesis": Hypothesis,
}


# =============================================================================================
# Loading
# =============================================================================================
def _load(db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, *permissions: str) -> Hypothesis:
    hypothesis = get_owned(db, Hypothesis, hypothesis_id, actor, label="Hypothesis")
    load_project(db, actor, hypothesis.project_id, *permissions)
    return hypothesis


def get_hypothesis(db: Session, actor: Actor, hypothesis_id: uuid.UUID | str) -> Hypothesis:
    return _load(db, actor, hypothesis_id, "hypothesis:read")


def list_evidence(db: Session, hypothesis: Hypothesis) -> list[HypothesisEvidence]:
    return list(
        db.scalars(
            select(HypothesisEvidence)
            .where(HypothesisEvidence.hypothesis_id == hypothesis.id)
            .order_by(HypothesisEvidence.created_at, HypothesisEvidence.id)
        )
    )


def list_critiques(db: Session, hypothesis: Hypothesis) -> list[HypothesisCritique]:
    return list(
        db.scalars(
            select(HypothesisCritique)
            .where(HypothesisCritique.hypothesis_id == hypothesis.id)
            .order_by(HypothesisCritique.created_at, HypothesisCritique.id)
        )
    )


def _status_filter(statuses: Sequence[str] | None) -> list[str] | None:
    if not statuses:
        return None
    out: list[str] = []
    for status in statuses:
        try:
            out.append(HypothesisStatus(str(status).upper()).value)
        except ValueError as exc:
            raise ValidationFailed(f"Unknown hypothesis status {status!r}") from exc
    return out


def _scoped_query(
    db: Session,
    actor: Actor,
    *,
    project_id: uuid.UUID | str | None,
    mission_id: uuid.UUID | str | None,
    statuses: Sequence[str] | None,
) -> Select[tuple[Hypothesis]]:
    stmt = select(Hypothesis).where(Hypothesis.organization_id == actor.organization_id)
    if mission_id is not None:
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        load_project(db, actor, mission.project_id, "hypothesis:read")
        stmt = stmt.where(Hypothesis.mission_id == mission.id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "hypothesis:read")
        stmt = stmt.where(Hypothesis.project_id == project.id)
    if mission_id is None and project_id is None:
        actor.require("hypothesis:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(Hypothesis.project_id.in_(visible))
    wanted = _status_filter(statuses)
    if wanted:
        stmt = stmt.where(Hypothesis.status.in_(wanted))
    return stmt


def list_hypotheses(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    status: str | None = None,
    sort: str | None = None,
    mapper: Callable[[Hypothesis], Any] | None = None,
) -> Page[Any]:
    stmt = _scoped_query(
        db, actor, project_id=project_id, mission_id=mission_id, statuses=[status] if status else None
    )
    order = sort_clause(Hypothesis, sort, SORTABLE)
    stmt = stmt.order_by(order.nulls_last(), Hypothesis.id)
    return paginate(db, stmt, params, mapper or (lambda h: h))


def list_for_mission(
    db: Session, actor: Actor, mission_id: uuid.UUID | str, statuses: Sequence[str] | None = None
) -> list[Hypothesis]:
    stmt = _scoped_query(db, actor, project_id=None, mission_id=mission_id, statuses=statuses)
    return list(db.scalars(stmt.order_by(Hypothesis.created_at, Hypothesis.id)))


# =============================================================================================
# Creation
# =============================================================================================
def _mission_in_project(db: Session, actor: Actor, mission_id: uuid.UUID | str, project: Project) -> Mission:
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    if mission.project_id != project.id:
        raise ValidationFailed("mission_id belongs to a different project")
    if mission.status in MISSION_TERMINAL:
        raise Conflict(f"Mission is {mission.status}; no new hypotheses can be added", code="mission_not_active")
    return mission


def require_falsifiable(statement: str, prediction: MeasurablePrediction | None) -> MeasurablePrediction:
    """Reject vague hypotheses with actionable guidance (HTTP 422)."""
    words = [w for w in statement.split() if w.strip()]
    problems: list[str] = []
    if len(words) < MIN_STATEMENT_WORDS:
        problems.append(f"the statement is too short to be a testable hypothesis (at least {MIN_STATEMENT_WORDS} words)")
    if prediction is None:
        problems.append("measurable_prediction is missing")
    if problems:
        raise ValidationFailed(
            "The hypothesis is not falsifiable: " + "; ".join(problems),
            code="hypothesis_not_falsifiable",
            details={"problems": problems, "guidance": PREDICTION_GUIDANCE, "example": PREDICTION_EXAMPLE},
        )
    assert prediction is not None
    return prediction


def _normalised_statement(statement: str) -> str:
    return " ".join(statement.split()).lower()


def _duplicate(db: Session, project: Project, mission_id: uuid.UUID | None, statement: str) -> Hypothesis | None:
    norm = _normalised_statement(statement)
    stmt = select(Hypothesis).where(
        Hypothesis.project_id == project.id,
        Hypothesis.status != H.ARCHIVED,
        func.lower(func.regexp_replace(func.btrim(Hypothesis.statement), r"\s+", " ", "g")) == norm,
    )
    stmt = stmt.where(Hypothesis.mission_id == mission_id if mission_id else Hypothesis.mission_id.is_(None))
    return db.scalars(stmt.limit(1)).first()


def resolve_evidence_ref(
    db: Session, actor: Actor, project_id: uuid.UUID, ref_type: str, ref_id: uuid.UUID | str
) -> Any:
    """Load an evidence target through the tenant session; it must belong to the same project (or be
    organization-level). Foreign ids surface as 404."""
    model = REF_MODELS.get(ref_type)
    if model is None:
        raise ValidationFailed(f"Unsupported evidence ref_type {ref_type!r}")
    row = get_owned(db, model, ref_id, actor, label=ref_type.replace("_", " ").capitalize())
    row_project = getattr(row, "project_id", None)
    if row_project is not None and row_project != project_id:
        raise ValidationFailed(f"{ref_type} {ref_id} belongs to a different project")
    return row


def _link_evidence(
    db: Session,
    hypothesis: Hypothesis,
    *,
    relation: str,
    ref_type: str,
    ref_id: uuid.UUID,
    note: str | None,
    confidence: float,
) -> tuple[HypothesisEvidence, bool]:
    existing = db.scalar(
        select(HypothesisEvidence).where(
            HypothesisEvidence.hypothesis_id == hypothesis.id,
            HypothesisEvidence.ref_type == ref_type,
            HypothesisEvidence.ref_id == ref_id,
            HypothesisEvidence.relation == relation,
        )
    )
    if existing is not None:
        return existing, False
    row = HypothesisEvidence(
        organization_id=hypothesis.organization_id,
        hypothesis_id=hypothesis.id,
        relation=relation,
        ref_type=ref_type,
        ref_id=ref_id,
        note=note,
        confidence=confidence,
    )
    db.add(row)
    db.flush()
    return row, True


def _evidence_json(refs: Sequence[EvidenceRef]) -> list[dict[str, Any]]:
    return [{"ref_type": r.ref_type, "ref_id": str(r.ref_id), "note": r.note} for r in refs]


def _record_graph_node(db: Session, actor: Actor, hypothesis: Hypothesis) -> None:
    """Best effort: mirror the hypothesis into the knowledge graph (secondary index; never blocks creation)."""
    try:
        from aegis_api.lab.knowledge import graph as knowledge_graph
    except ImportError:
        return
    upsert = getattr(knowledge_graph, "upsert_node", None)
    if upsert is None:
        return
    try:
        with db.begin_nested():
            upsert(
                db,
                actor,
                node_type="Hypothesis",
                key=f"hypothesis:{hypothesis.id}",
                label=hypothesis.statement[:1000],
                ref_type="hypothesis",
                ref_id=hypothesis.id,
                project_id=hypothesis.project_id,
                properties={
                    "status": hypothesis.status,
                    "metric": (hypothesis.measurable_prediction or {}).get("metric"),
                    "mission_id": str(hypothesis.mission_id) if hypothesis.mission_id else None,
                },
            )
    except Exception:
        log.warning("hypothesis_graph_node_failed", hypothesis_id=str(hypothesis.id), exc_info=True)


def _emit(db: Session, actor: Actor, hypothesis: Hypothesis, event_type: str, payload: dict[str, Any]) -> None:
    emit(
        db,
        organization_id=hypothesis.organization_id,
        type=event_type,
        payload={"hypothesis_id": str(hypothesis.id), "status": hypothesis.status, **payload},
        mission_id=hypothesis.mission_id,
        project_id=hypothesis.project_id,
        workspace_id=hypothesis.workspace_id,
        subject_type="hypothesis",
        subject_id=hypothesis.id,
        actor=actor,
    )


def create_hypothesis(
    db: Session,
    actor: Actor,
    data: HypothesisCreate,
    *,
    agent_run_id: uuid.UUID | None = None,
    provenance: dict[str, Any] | None = None,
) -> Hypothesis:
    """Create a ``GENERATED`` hypothesis (``hypothesis:create``). Evidence refs are ownership-checked."""
    project = load_project(db, actor, data.project_id, "hypothesis:create")
    mission = _mission_in_project(db, actor, data.mission_id, project) if data.mission_id else None
    statement = data.statement.strip()
    prediction = require_falsifiable(statement, data.measurable_prediction)
    parent: Hypothesis | None = None
    if data.parent_hypothesis_id is not None:
        parent = get_owned(db, Hypothesis, data.parent_hypothesis_id, actor, label="Parent hypothesis")
        if parent.project_id != project.id:
            raise ValidationFailed("parent_hypothesis_id belongs to a different project")
    duplicate = _duplicate(db, project, mission.id if mission else None, statement)
    if duplicate is not None:
        raise Conflict(
            "An identical hypothesis already exists",
            code="duplicate_hypothesis",
            details={"existing_id": str(duplicate.id)},
        )
    refs: list[tuple[str, EvidenceRef]] = [("supports", r) for r in data.supporting_evidence]
    refs += [("contradicts", r) for r in data.contradicting_evidence]
    for _relation, ref in refs:
        resolve_evidence_ref(db, actor, project.id, ref.ref_type, ref.ref_id)
    hypothesis = Hypothesis(
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission else None,
        statement=statement,
        rationale=data.rationale,
        expected_outcome=data.expected_outcome,
        measurable_prediction=prediction.model_dump(mode="json"),
        assumptions=list(data.assumptions),
        novelty_notes=data.novelty_notes,
        feasibility=data.feasibility,
        estimated_cost_usd=data.estimated_cost_usd,
        confidence=data.confidence,
        status=H.GENERATED,
        parent_hypothesis_id=parent.id if parent else None,
        generation=(parent.generation + 1) if parent else 0,
        scores={},
        provenance=dict(provenance or {}),
        supporting_evidence=_evidence_json(data.supporting_evidence),
        contradicting_evidence=_evidence_json(data.contradicting_evidence),
        created_by_id=actor.user_id,
        created_by_agent_run_id=agent_run_id or actor.agent_run_id,
    )
    db.add(hypothesis)
    db.flush()
    for relation, ref in refs:
        _link_evidence(
            db, hypothesis, relation=relation, ref_type=ref.ref_type, ref_id=ref.ref_id, note=ref.note, confidence=0.5
        )
    _emit(
        db,
        actor,
        hypothesis,
        EventType.HYPOTHESIS_CREATED,
        {
            "statement": statement[:300],
            "metric": prediction.metric,
            "agent_run_id": str(hypothesis.created_by_agent_run_id) if hypothesis.created_by_agent_run_id else None,
        },
    )
    _record_graph_node(db, actor, hypothesis)
    db.flush()
    return hypothesis


def update_hypothesis(
    db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, data: HypothesisUpdate
) -> Hypothesis:
    """Edit a hypothesis that has not been selected yet (``GENERATED``/``CRITIQUED``)."""
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    if data.lock_version is not None and data.lock_version != hypothesis.lock_version:
        raise Conflict("The hypothesis was modified concurrently; reload and retry", code="concurrent_modification")
    if hypothesis.status not in EDITABLE_STATUSES:
        raise Conflict(
            f"A {hypothesis.status} hypothesis can no longer be edited (only GENERATED or CRITIQUED)",
            code="hypothesis_not_editable",
        )
    changes = data.model_dump(exclude_unset=True, exclude={"lock_version"})
    if not changes:
        return hypothesis
    if "statement" in changes and changes["statement"] is not None:
        changes["statement"] = changes["statement"].strip()
    new_statement = changes.get("statement") or hypothesis.statement
    if "measurable_prediction" in changes:
        prediction = require_falsifiable(new_statement, data.measurable_prediction)
        changes["measurable_prediction"] = prediction.model_dump(mode="json")
    elif "statement" in changes:
        require_falsifiable(new_statement, parse_prediction(hypothesis.measurable_prediction))
    for key in ("statement",):
        if key in changes and changes[key] is None:
            raise ValidationFailed(f"{key} cannot be null")
    if "assumptions" in changes and changes["assumptions"] is None:
        changes["assumptions"] = []
    if "estimated_cost_usd" in changes and changes["estimated_cost_usd"] is None:
        del changes["estimated_cost_usd"]
    for key, value in changes.items():
        setattr(hypothesis, key, value)
    provenance = dict(hypothesis.provenance or {})
    edits = list(provenance.get("edits") or [])[-49:]
    edits.append({"at": utcnow().isoformat(), "by": actor.as_dict(), "fields": sorted(changes)})
    provenance["edits"] = edits
    hypothesis.provenance = provenance
    if hypothesis.status == H.CRITIQUED and {"statement", "measurable_prediction"} & set(changes):
        hypothesis.scores = {**(hypothesis.scores or {}), "critiques_stale": True}
    db.flush()
    _emit(db, actor, hypothesis, EventType.HYPOTHESIS_UPDATED, {"fields": sorted(changes)})
    return hypothesis


def add_evidence(db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, data: EvidenceCreate) -> HypothesisEvidence:
    """Link supporting/contradicting/context evidence (idempotent per (ref, relation))."""
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    if hypothesis.status == H.ARCHIVED:
        raise Conflict("The hypothesis is archived", code="hypothesis_archived")
    if data.ref_type == "hypothesis" and str(data.ref_id) == str(hypothesis.id):
        raise ValidationFailed("A hypothesis cannot be evidence for itself")
    resolve_evidence_ref(db, actor, hypothesis.project_id, data.ref_type, data.ref_id)
    row, created = _link_evidence(
        db,
        hypothesis,
        relation=data.relation,
        ref_type=data.ref_type,
        ref_id=data.ref_id,
        note=data.note,
        confidence=data.confidence,
    )
    if created and data.relation in ("supports", "contradicts"):
        key = "supporting_evidence" if data.relation == "supports" else "contradicting_evidence"
        current = list(getattr(hypothesis, key) or [])
        current.append({"ref_type": data.ref_type, "ref_id": str(data.ref_id), "note": data.note})
        setattr(hypothesis, key, current)
        db.flush()
    if created:
        _emit(
            db,
            actor,
            hypothesis,
            EventType.HYPOTHESIS_UPDATED,
            {"evidence": {"relation": data.relation, "ref_type": data.ref_type, "ref_id": str(data.ref_id)}},
        )
    return row


# =============================================================================================
# Critique
# =============================================================================================
def _critique_dicts(critiques: Sequence[HypothesisCritique]) -> list[dict[str, Any]]:
    return [{"scores": c.scores or {}, "recommendation": c.recommendation} for c in critiques]


def record_critique(
    db: Session,
    actor: Actor,
    hypothesis_id: uuid.UUID | str,
    data: CritiqueInput,
    *,
    agent_run_id: uuid.UUID | None = None,
    provenance: dict[str, Any] | None = None,
) -> HypothesisCritique:
    """Record an independent critique; the hypothesis becomes ``CRITIQUED`` and its scores are re-aggregated."""
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    critic_run = agent_run_id or actor.agent_run_id
    if critic_run is not None and critic_run == hypothesis.created_by_agent_run_id:
        raise Forbidden("A hypothesis cannot be critiqued by the agent run that generated it (independence)")
    if actor.kind == "agent" and actor.agent_role == AgentRole.HYPOTHESIS:
        raise Forbidden("The hypothesis generator role cannot critique hypotheses (independence)")
    assert_transition("hypothesis", hypothesis.status, H.CRITIQUED)
    if critic_run is not None or actor.kind == "agent":
        critic_type = "agent"
    elif actor.is_human:
        critic_type = "human"
    else:
        critic_type = "rule"
    critique = HypothesisCritique(
        organization_id=hypothesis.organization_id,
        hypothesis_id=hypothesis.id,
        critic_type=critic_type,
        agent_run_id=critic_run,
        critic_user_id=actor.user_id if critic_type == "human" else None,
        scores=data.scores.model_dump(),
        issues=list(data.issues),
        recommendation=data.recommendation,
        rationale=data.rationale,
        provenance=dict(provenance or {}),
    )
    db.add(critique)
    db.flush()
    aggregate = scoring.aggregate_critiques(_critique_dicts(list_critiques(db, hypothesis)))
    previous = dict(hypothesis.scores or {})
    hypothesis.scores = {
        **{k: v for k, v in previous.items() if k in ("selection",)},
        **aggregate["means"],
        "n_critiques": aggregate["n_critiques"],
        "recommendations": aggregate["recommendations"],
        "latest_recommendation": aggregate["latest_recommendation"],
        "critiques_stale": False,
        "aggregated_at": utcnow().isoformat(),
    }
    hypothesis.status = H.CRITIQUED
    db.flush()
    _emit(
        db,
        actor,
        hypothesis,
        EventType.HYPOTHESIS_UPDATED,
        {"critique_id": str(critique.id), "recommendation": data.recommendation, "critic_type": critic_type},
    )
    return critique


# =============================================================================================
# Transitions
# =============================================================================================
def _parse_status(status: str) -> HypothesisStatus:
    try:
        return HypothesisStatus(str(status).upper())
    except ValueError as exc:
        raise ValidationFailed(f"Unknown hypothesis status {status!r}") from exc


def transition_hypothesis(
    db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, status: str, reason: str
) -> Hypothesis:
    """Human/workflow lifecycle change. Conclusions (SUPPORTED/REJECTED/INCONCLUSIVE) are refused here: they are
    only reached through :func:`conclude_hypothesis` from recorded evidence."""
    target = _parse_status(status)
    if target in CONCLUSION_STATUSES:
        raise ValidationFailed(
            f"{target} can only be reached by concluding the hypothesis from experiment comparisons "
            "(POST /hypotheses/{id}/conclude)",
            code="conclusion_requires_evidence",
        )
    if actor.kind == "agent":
        raise Forbidden("Agents cannot change hypothesis status directly")
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    assert_transition("hypothesis", hypothesis.status, target)
    before = hypothesis.status
    hypothesis.status = target
    hypothesis.status_reason = reason.strip()[:2000]
    db.flush()
    _emit(db, actor, hypothesis, EventType.HYPOTHESIS_UPDATED, {"from": before, "reason": hypothesis.status_reason})
    return hypothesis


def platform_advance(db: Session, actor: Actor, hypothesis: Hypothesis, target: str, reason: str) -> bool:
    """Status change that is a *platform consequence* of an action the caller already authorised in the same
    project (e.g. a validated experiment design → ``EXPERIMENT_DESIGNED``). Still guarded by the state machine."""
    return _advance(db, actor, hypothesis, target, reason)


def _advance(db: Session, actor: Actor, hypothesis: Hypothesis, target: str, reason: str) -> bool:
    if hypothesis.status == target:
        return False
    assert_transition("hypothesis", hypothesis.status, target)
    before = hypothesis.status
    hypothesis.status = target
    hypothesis.status_reason = reason[:2000]
    db.flush()
    _emit(db, actor, hypothesis, EventType.HYPOTHESIS_UPDATED, {"from": before, "reason": reason[:300]})
    return True


def mark_designed(
    db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, *, experiment_id: uuid.UUID | None = None
) -> Hypothesis:
    """``SELECTED``/``INCONCLUSIVE`` → ``EXPERIMENT_DESIGNED`` (idempotent once designed or under test)."""
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    if hypothesis.status in (H.EXPERIMENT_DESIGNED, H.TESTING):
        return hypothesis
    suffix = f" (experiment {experiment_id})" if experiment_id else ""
    _advance(db, actor, hypothesis, H.EXPERIMENT_DESIGNED, f"validated experiment design{suffix}")
    return hypothesis


def mark_testing(
    db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, *, experiment_id: uuid.UUID | None = None
) -> Hypothesis:
    """``EXPERIMENT_DESIGNED``/``INCONCLUSIVE`` → ``TESTING`` when experiment runs are scheduled (no-op otherwise)."""
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    if hypothesis.status in (H.EXPERIMENT_DESIGNED, H.INCONCLUSIVE) and can_transition(
        "hypothesis", hypothesis.status, H.TESTING
    ):
        suffix = f" (experiment {experiment_id})" if experiment_id else ""
        _advance(db, actor, hypothesis, H.TESTING, f"experiment runs scheduled{suffix}")
    return hypothesis


# =============================================================================================
# Selection
# =============================================================================================
def active_strategy_version(
    db: Session,
    organization_id: uuid.UUID,
    kind: str,
    *,
    project_id: uuid.UUID | None = None,
    mission: Mission | None = None,
) -> StrategyVersion | None:
    """The strategy version in force for ``kind``: the mission's pin, else the promoted version of the most
    specific active strategy (project-scoped before organization-wide)."""
    pinned = (mission.strategy_pins or {}).get(kind) if mission is not None else None
    if pinned:
        try:
            version = db.get(StrategyVersion, uuid.UUID(str(pinned)))
        except ValueError:
            version = None
        if version is not None and version.organization_id == organization_id:
            strategy = db.get(Strategy, version.strategy_id)
            if strategy is not None and strategy.kind == kind:
                return version
    stmt = (
        select(StrategyVersion)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(
            Strategy.organization_id == organization_id,
            Strategy.kind == kind,
            Strategy.status == "active",
            StrategyVersion.status == StrategyStatus.PROMOTED,
        )
    )
    if project_id is not None:
        stmt = stmt.where(or_(Strategy.project_id == project_id, Strategy.project_id.is_(None)))
    else:
        stmt = stmt.where(Strategy.project_id.is_(None))
    stmt = stmt.order_by(
        Strategy.project_id.is_(None).asc(),
        StrategyVersion.promoted_at.desc().nulls_last(),
        StrategyVersion.version.desc(),
    )
    return db.scalars(stmt.limit(1)).first()


@dataclass
class HypothesisSelection:
    mission_id: uuid.UUID
    top_k: int
    result: scoring.SelectionResult
    weight_notes: list[str] = field(default_factory=list)
    strategy_version_id: uuid.UUID | None = None


def _candidate(hypothesis: Hypothesis) -> scoring.SelectionCandidate:
    scores = hypothesis.scores or {}
    means = {dim: float(scores[dim]) for dim in scoring.DIMENSIONS if isinstance(scores.get(dim), int | float)}
    return scoring.SelectionCandidate(
        id=str(hypothesis.id),
        created_at=hypothesis.created_at,
        status=hypothesis.status,
        has_measurable_prediction=parse_prediction(hypothesis.measurable_prediction) is not None,
        critique_scores=means,
        critique_count=int(scores.get("n_critiques") or 0),
        latest_recommendation=scores.get("latest_recommendation"),
        feasibility_estimate=hypothesis.feasibility,
        estimated_cost_usd=float(hypothesis.estimated_cost_usd or 0),
    )


def select_hypotheses(db: Session, actor: Actor, mission_id: uuid.UUID | str, top_k: int) -> HypothesisSelection:
    """Deterministically select up to ``top_k`` hypotheses of a mission (``hypothesis:update``).

    Already ``SELECTED`` hypotheses keep their selection and occupy slots, so repeating the call is idempotent.
    """
    if top_k < 1 or top_k > 50:
        raise ValidationFailed("top_k must be between 1 and 50")
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    load_project(db, actor, mission.project_id, "hypothesis:update")
    advisory_xact_lock(db, f"hypothesis-select:{mission.id}")
    strategy = active_strategy_version(db, actor.organization_id, "hypothesis", project_id=mission.project_id, mission=mission)
    weights, notes = scoring.ScoringWeights.from_parameters(strategy.parameters if strategy is not None else None)
    rows = list(
        db.scalars(
            select(Hypothesis)
            .where(
                Hypothesis.mission_id == mission.id,
                Hypothesis.status.in_([H.GENERATED, H.CRITIQUED, H.SELECTED]),
            )
            .order_by(Hypothesis.created_at, Hypothesis.id)
        )
    )
    result = scoring.select_top([_candidate(h) for h in rows], top_k, weights)
    by_id = {str(h.id): h for h in rows}
    ranked_at = utcnow().isoformat()
    for entry in result.ranking:
        hypothesis = by_id[entry.id]
        selection_info = {
            "score": entry.score,
            "rank": entry.rank,
            "components": entry.components,
            "eligible": entry.eligible,
            "reason": entry.reason,
            "weights": weights.as_dict(),
            "scoring_version": result.scoring_version,
            "strategy_version_id": str(strategy.id) if strategy is not None else None,
            "ranked_at": ranked_at,
        }
        hypothesis.scores = {**(hypothesis.scores or {}), "selection": selection_info}
        if entry.newly_selected:
            assert_transition("hypothesis", hypothesis.status, H.SELECTED)
            before = hypothesis.status
            hypothesis.status = H.SELECTED
            hypothesis.selection_rank = entry.rank
            hypothesis.status_reason = f"selected with score {entry.score:.4f} (rank {entry.rank})"
            _emit(
                db,
                actor,
                hypothesis,
                EventType.HYPOTHESIS_UPDATED,
                {"from": before, "selection_rank": entry.rank, "score": entry.score},
            )
    db.flush()
    return HypothesisSelection(
        mission_id=mission.id,
        top_k=top_k,
        result=result,
        weight_notes=notes,
        strategy_version_id=strategy.id if strategy is not None else None,
    )


# =============================================================================================
# Conclusion
# =============================================================================================
def _power(statistics: dict[str, Any]) -> tuple[bool, str]:
    """Whether a comparison was adequately powered (recorded by the comparison service, else recomputed)."""
    power = statistics.get("power")
    if isinstance(power, dict) and isinstance(power.get("adequately_powered"), bool):
        return bool(power["adequately_powered"]), str(power.get("reason") or "")
    min_effect = statistics.get("min_effect")
    alpha = statistics.get("alpha") or 0.05
    n = min(int(statistics.get("n_baseline") or 0), int(statistics.get("n_candidate") or 0))
    if not isinstance(min_effect, int | float) or min_effect <= 0:
        return False, "no pre-registered minimum effect size"
    try:
        required = required_seeds_estimate(
            float(min_effect), float(alpha), ASSUMED_POWER, paired=statistics.get("test") == "paired_t"
        )
    except StatisticsError:
        return False, "power could not be computed"
    return n >= required, f"n={n} per arm, {required} required for effect {min_effect:g} at power {ASSUMED_POWER}"


def assess_comparison(prediction: MeasurablePrediction, comparison: ExperimentComparison) -> dict[str, Any]:
    """Classify one comparison as ``supports`` / ``contradicts`` / ``inconclusive`` / ``not_applicable`` for the
    prediction (deterministic; see :func:`conclude_hypothesis`)."""
    base: dict[str, Any] = {
        "comparison_id": str(comparison.id),
        "metric": comparison.metric,
        "verdict": comparison.verdict,
        "comparison_type": comparison.comparison_type,
    }
    if comparison.metric != prediction.metric:
        return {**base, "outcome": "not_applicable", "reason": f"measures {comparison.metric!r}, not the predicted metric"}
    if comparison.comparison_type != "baseline":
        return {
            **base,
            "outcome": "not_applicable",
            "reason": f"{comparison.comparison_type} comparisons characterise components, they do not test the prediction",
        }
    if comparison.direction != prediction.direction:
        return {
            **base,
            "outcome": "inconclusive",
            "reason": f"metric direction {comparison.direction} differs from the prediction ({prediction.direction})",
        }
    stats = dict(comparison.statistics or {})
    powered, power_reason = _power(stats)
    mean_c = stats.get("mean_candidate")
    mean_b = stats.get("mean_baseline")
    check = check_criterion(
        prediction.to_criterion(),
        float(mean_c) if isinstance(mean_c, int | float) and math.isfinite(mean_c) else None,
        float(mean_b) if isinstance(mean_b, int | float) and math.isfinite(mean_b) else None,
        prediction.direction,
    )
    base.update(
        {
            "criterion": check.model_dump(),
            "adequately_powered": powered,
            "power": power_reason,
            "metric_source": stats.get("metric_source"),
        }
    )
    verdict = comparison.verdict
    if verdict == "insufficient_data":
        return {**base, "outcome": "inconclusive", "reason": "insufficient data for the pre-registered test"}
    if verdict == "regressed":
        return {**base, "outcome": "contradicts", "reason": "statistically significant change against the prediction"}
    if verdict == "improved":
        if check.satisfied is True:
            return {**base, "outcome": "supports", "reason": f"significant improvement meeting the threshold ({check.reason})"}
        if check.satisfied is False:
            if powered:
                return {
                    **base,
                    "outcome": "contradicts",
                    "reason": f"significant improvement but below the predicted threshold ({check.reason})",
                }
            return {
                **base,
                "outcome": "inconclusive",
                "reason": f"improvement below the predicted threshold and the study is not adequately powered ({power_reason})",
            }
        return {**base, "outcome": "inconclusive", "reason": f"the prediction could not be evaluated ({check.reason})"}
    if powered:
        return {
            **base,
            "outcome": "contradicts",
            "reason": f"no significant difference in an adequately powered comparison ({power_reason})",
        }
    return {
        **base,
        "outcome": "inconclusive",
        "reason": f"no significant difference, but the comparison is not adequately powered ({power_reason})",
    }


def decide_conclusion(assessments: Sequence[dict[str, Any]]) -> tuple[str, str]:
    """Combine per-comparison assessments into ``(status, rationale)``."""
    supports = [a for a in assessments if a["outcome"] == "supports"]
    contradicts = [a for a in assessments if a["outcome"] == "contradicts"]
    applicable = [a for a in assessments if a["outcome"] != "not_applicable"]
    if not applicable:
        return H.INCONCLUSIVE, "no baseline comparison measured the predicted metric"
    if supports and contradicts:
        return H.INCONCLUSIVE, (
            f"conflicting evidence: {len(supports)} comparison(s) support and {len(contradicts)} contradict the prediction"
        )
    if supports:
        return H.SUPPORTED, "; ".join(f"{a['comparison_id']}: {a['reason']}" for a in supports)
    if contradicts:
        return H.REJECTED, "; ".join(f"{a['comparison_id']}: {a['reason']}" for a in contradicts)
    return H.INCONCLUSIVE, "; ".join(f"{a['comparison_id']}: {a['reason']}" for a in applicable)


def load_comparisons(
    db: Session, actor: Actor, comparison_ids: Sequence[uuid.UUID | str]
) -> list[ExperimentComparison]:
    out: list[ExperimentComparison] = []
    seen: set[uuid.UUID] = set()
    for cid in comparison_ids:
        comparison = get_owned(db, ExperimentComparison, cid, actor, label="Comparison")
        if comparison.id not in seen:
            seen.add(comparison.id)
            out.append(comparison)
    return out


def conclude_hypothesis(
    db: Session, actor: Actor, hypothesis_id: uuid.UUID | str, comparisons: Sequence[ExperimentComparison]
) -> Hypothesis:
    """Conclude from recorded comparisons → ``SUPPORTED`` / ``REJECTED`` / ``INCONCLUSIVE``.

    * SUPPORTED — a baseline comparison of the predicted metric is ``improved`` (significant, practically
      significant, direction-consistent) and the observed means meet the predicted threshold;
    * REJECTED — a comparison ``regressed``, or an adequately powered comparison shows no (sufficient)
      improvement;
    * INCONCLUSIVE — insufficient data, an under-powered null result, conflicting evidence or no comparison of
      the predicted metric.

    The rationale (with comparison ids) is stored in ``status_reason`` and ``provenance.conclusions``; each used
    comparison is linked as evidence and the conclusion is appended to the hash-chained evidence log.
    """
    if actor.kind == "agent":
        raise Forbidden("Agents cannot conclude hypotheses; conclusions are derived by the platform from evidence")
    hypothesis = _load(db, actor, hypothesis_id, "hypothesis:update")
    if not comparisons:
        raise ValidationFailed("At least one comparison is required to conclude a hypothesis")
    prediction = parse_prediction(hypothesis.measurable_prediction)
    if prediction is None:
        raise ValidationFailed("The hypothesis has no valid measurable prediction", code="hypothesis_not_falsifiable")
    for comparison in comparisons:
        if comparison.organization_id != hypothesis.organization_id:
            raise NotFound("Comparison not found")
        candidate = db.get(Experiment, comparison.candidate_experiment_id)
        if candidate is None or candidate.hypothesis_id != hypothesis.id:
            raise ValidationFailed(
                f"Comparison {comparison.id} does not test this hypothesis (its candidate experiment is not linked to it)"
            )
    ordered = sorted(comparisons, key=lambda c: (c.created_at, str(c.id)))
    assessments = [assess_comparison(prediction, c) for c in ordered]
    status, rationale = decide_conclusion(assessments)
    comparison_ids = [str(c.id) for c in ordered]
    conclusions = list((hypothesis.provenance or {}).get("conclusions") or [])
    last = conclusions[-1] if conclusions else None
    if (
        last is not None
        and hypothesis.status == status
        and last.get("status") == status
        and last.get("comparison_ids") == comparison_ids
    ):
        return hypothesis  # idempotent replay
    if hypothesis.status != H.TESTING:
        _advance(db, actor, hypothesis, H.TESTING, "concluding from experiment comparisons")
    assert_transition("hypothesis", hypothesis.status, status)
    before = hypothesis.status
    hypothesis.status = status
    hypothesis.status_reason = (
        f"{status} ({CONCLUSION_RULES_VERSION}; comparisons: {', '.join(comparison_ids)}): {rationale}"
    )[:8000]
    entry = {
        "status": status,
        "rationale": rationale,
        "comparison_ids": comparison_ids,
        "assessments": assessments,
        "rules_version": CONCLUSION_RULES_VERSION,
        "decided_at": utcnow().isoformat(),
        "decided_by": actor.as_dict(),
    }
    hypothesis.provenance = {**(hypothesis.provenance or {}), "conclusions": [*conclusions[-19:], entry]}
    relation_by_outcome = {"supports": "supports", "contradicts": "contradicts"}
    for assessment in assessments:
        _link_evidence(
            db,
            hypothesis,
            relation=relation_by_outcome.get(assessment["outcome"], "context"),
            ref_type="experiment_comparison",
            ref_id=uuid.UUID(assessment["comparison_id"]),
            note=str(assessment["reason"])[:2000],
            confidence=1.0 if assessment["outcome"] in relation_by_outcome else 0.5,
        )
    db.flush()
    append_evidence(
        db,
        organization_id=hypothesis.organization_id,
        kind="hypothesis_conclusion",
        title=f"Hypothesis {hypothesis.id} concluded {status}",
        content={
            "hypothesis_id": str(hypothesis.id),
            "prediction": prediction.model_dump(mode="json"),
            "status": status,
            "rationale": rationale,
            "comparison_ids": comparison_ids,
            "assessments": assessments,
            "rules_version": CONCLUSION_RULES_VERSION,
        },
    )
    _emit(
        db,
        actor,
        hypothesis,
        EventType.HYPOTHESIS_UPDATED,
        {"from": before, "conclusion": status, "comparison_ids": comparison_ids},
    )
    return hypothesis


def latest_conclusion(hypothesis: Hypothesis) -> dict[str, Any] | None:
    conclusions = (hypothesis.provenance or {}).get("conclusions") or []
    return conclusions[-1] if conclusions else None
