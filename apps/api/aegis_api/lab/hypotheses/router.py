"""HTTP API for hypotheses (tag "Hypotheses"): create, list, read, edit, critique, select, evidence, conclude.

Hypotheses are created ``GENERATED`` by humans or by the HypothesisAgent; critiques come from an independent
critic; selection is deterministic; ``SUPPORTED``/``REJECTED``/``INCONCLUSIVE`` are only reachable by concluding
from recorded experiment comparisons.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.hypotheses import service
from aegis_api.lab.hypotheses.schemas import (
    ConcludeRequest,
    ConclusionOut,
    CritiqueInput,
    EvidenceCreate,
    HypothesisCreate,
    HypothesisCritiqueOut,
    HypothesisDetailOut,
    HypothesisEvidenceOut,
    HypothesisOut,
    HypothesisUpdate,
    RankingEntryOut,
    SelectionOut,
    SelectRequest,
    TransitionRequest,
)
from aegis_api.lab.models import Hypothesis
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Hypotheses"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E}


def _detail(db: Session, hypothesis: Hypothesis) -> HypothesisDetailOut:
    out = HypothesisDetailOut.model_validate(hypothesis)
    out.evidence = [HypothesisEvidenceOut.model_validate(e) for e in service.list_evidence(db, hypothesis)]
    out.critiques = [HypothesisCritiqueOut.model_validate(c) for c in service.list_critiques(db, hypothesis)]
    return out


@router.post(
    "/hypotheses",
    response_model=HypothesisOut,
    status_code=201,
    summary="Create a hypothesis",
    description=(
        "Creates a `GENERATED` hypothesis. The hypothesis must be falsifiable: `measurable_prediction` (metric, "
        "comparator, threshold, relative_to, direction) is required — vague hypotheses are rejected with guidance "
        "(422 `hypothesis_not_falsifiable`). Supporting/contradicting evidence refs are ownership-checked. "
        "Supports `Idempotency-Key`. Requires `hypothesis:create`."
    ),
    responses={**WRITE_ERRORS, 409: {"description": "Mission not active or duplicate hypothesis"}},
)
def create_hypothesis(
    body: HypothesisCreate,
    idem: Idempotency = Depends(idempotency),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    hypothesis = service.create_hypothesis(db, actor, body)
    return idem.remember(201, HypothesisOut.model_validate(hypothesis))


@router.get(
    "/hypotheses",
    response_model=Page[HypothesisOut],
    status_code=200,
    summary="List hypotheses",
    description=(
        "Hypotheses of projects you can see. Filter by `mission_id`, `project_id`, `status`; `sort` is one of "
        "created_at, updated_at, status, selection_rank, confidence, feasibility (prefix `-` for descending). "
        "Requires `hypothesis:read`."
    ),
    responses=READ_ERRORS,
)
def list_hypotheses(
    mission_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=24),
    sort: str | None = Query(default=None, max_length=40, description="e.g. -created_at or selection_rank"),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_hypotheses(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        status=status,
        sort=sort,
        mapper=HypothesisOut.model_validate,
    )


@router.get(
    "/hypotheses/{hypothesis_id}",
    response_model=HypothesisDetailOut,
    status_code=200,
    summary="Get a hypothesis",
    description="The hypothesis with its evidence links and critiques. Requires `hypothesis:read`.",
    responses=READ_ERRORS,
)
def get_hypothesis(
    hypothesis_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> HypothesisDetailOut:
    return _detail(db, service.get_hypothesis(db, actor, hypothesis_id))


@router.patch(
    "/hypotheses/{hypothesis_id}",
    response_model=HypothesisOut,
    status_code=200,
    summary="Edit a hypothesis",
    description=(
        "Edits statement, rationale, prediction and estimates while the hypothesis is `GENERATED` or `CRITIQUED` "
        "(409 otherwise). Pass `lock_version` for optimistic concurrency. Requires `hypothesis:update`."
    ),
    responses=WRITE_ERRORS,
)
def update_hypothesis(
    hypothesis_id: uuid.UUID,
    body: HypothesisUpdate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> HypothesisOut:
    return HypothesisOut.model_validate(service.update_hypothesis(db, actor, hypothesis_id, body))


@router.post(
    "/hypotheses/{hypothesis_id}/critiques",
    response_model=HypothesisCritiqueOut,
    status_code=201,
    summary="Critique a hypothesis",
    description=(
        "Records an independent critique (scores 0..1 for novelty, feasibility, testability, evidence strength and "
        "risk; issues; recommendation select|revise|reject). The hypothesis becomes `CRITIQUED` and its scores are "
        "re-aggregated. The generating agent run can never critique its own hypothesis (403). Requires "
        "`hypothesis:update`."
    ),
    responses=WRITE_ERRORS,
)
def critique_hypothesis(
    hypothesis_id: uuid.UUID,
    body: CritiqueInput,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> HypothesisCritiqueOut:
    return HypothesisCritiqueOut.model_validate(service.record_critique(db, actor, hypothesis_id, body))


@router.post(
    "/hypotheses/{hypothesis_id}/transition",
    response_model=HypothesisOut,
    status_code=200,
    summary="Change a hypothesis status",
    description=(
        "Human/workflow lifecycle change validated by the hypothesis state machine (409 when illegal). "
        "`SUPPORTED`, `REJECTED` and `INCONCLUSIVE` are refused (422): they are only reached through "
        "`POST /hypotheses/{id}/conclude` from experiment evidence. Requires `hypothesis:update`."
    ),
    responses=WRITE_ERRORS,
)
def transition_hypothesis(
    hypothesis_id: uuid.UUID,
    body: TransitionRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> HypothesisOut:
    return HypothesisOut.model_validate(
        service.transition_hypothesis(db, actor, hypothesis_id, body.status, body.reason)
    )


@router.post(
    "/missions/{mission_id}/hypotheses/select",
    response_model=SelectionOut,
    status_code=200,
    summary="Select hypotheses deterministically",
    description=(
        "Ranks the mission's GENERATED/CRITIQUED hypotheses with the documented deterministic score (weights from "
        "the mission's hypothesis strategy when present) and selects up to `top_k`. Hypotheses without a "
        "measurable prediction are never selected; already selected ones keep their slot (idempotent). Requires "
        "`hypothesis:update`."
    ),
    responses=WRITE_ERRORS,
)
def select_hypotheses(
    mission_id: uuid.UUID,
    body: SelectRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> SelectionOut:
    selection = service.select_hypotheses(db, actor, mission_id, body.top_k)
    return SelectionOut(
        mission_id=str(selection.mission_id),
        top_k=selection.top_k,
        selected_ids=list(selection.result.selected_ids),
        newly_selected_ids=list(selection.result.newly_selected_ids),
        ranking=[RankingEntryOut(**entry.as_dict()) for entry in selection.result.ranking],
        weights=selection.result.weights.as_dict(),
        weight_notes=selection.weight_notes,
        strategy_version_id=str(selection.strategy_version_id) if selection.strategy_version_id else None,
        scoring_version=selection.result.scoring_version,
    )


@router.post(
    "/hypotheses/{hypothesis_id}/evidence",
    response_model=HypothesisEvidenceOut,
    status_code=201,
    summary="Link evidence to a hypothesis",
    description=(
        "Links a research source, document, memory, experiment, run, comparison, evaluation, dataset/artifact "
        "version, claim or hypothesis of the same project as supporting, contradicting or context evidence "
        "(idempotent per reference and relation). Requires `hypothesis:update`."
    ),
    responses=WRITE_ERRORS,
)
def add_evidence(
    hypothesis_id: uuid.UUID,
    body: EvidenceCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> HypothesisEvidenceOut:
    return HypothesisEvidenceOut.model_validate(service.add_evidence(db, actor, hypothesis_id, body))


@router.post(
    "/hypotheses/{hypothesis_id}/conclude",
    response_model=ConclusionOut,
    status_code=200,
    summary="Conclude a hypothesis from comparisons",
    description=(
        "Derives SUPPORTED / REJECTED / INCONCLUSIVE deterministically from recorded experiment comparisons of the "
        "hypothesis's experiments: SUPPORTED needs a significant, direction-consistent improvement meeting the "
        "predicted threshold; REJECTED needs a regression or an adequately powered null result; everything else is "
        "INCONCLUSIVE. The rationale and comparison ids are stored. Never based on a model's claim. Requires "
        "`hypothesis:update` (not available to agents)."
    ),
    responses=WRITE_ERRORS,
)
def conclude_hypothesis(
    hypothesis_id: uuid.UUID,
    body: ConcludeRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ConclusionOut:
    comparisons = service.load_comparisons(db, actor, body.comparison_ids)
    hypothesis = service.conclude_hypothesis(db, actor, hypothesis_id, comparisons)
    conclusion = service.latest_conclusion(hypothesis) or {}
    return ConclusionOut(
        hypothesis=HypothesisOut.model_validate(hypothesis),
        status=hypothesis.status,
        rationale=str(conclusion.get("rationale") or hypothesis.status_reason or ""),
        comparison_ids=list(conclusion.get("comparison_ids") or []),
        assessments=list(conclusion.get("assessments") or []),
    )
