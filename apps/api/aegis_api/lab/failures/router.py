"""HTTP API for failure intelligence (tag "Failures"): failures, diagnoses, recoveries and lessons.

Failures are classified by deterministic rules (reproducible, evidence-backed); model and human diagnoses are
recorded alongside the rule classification, never instead of it. Tracebacks and logs are redacted before storage
and are untrusted program output — render them as text only.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit, require_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.failures import recovery, service
from aegis_api.lab.failures.schemas import (
    DiagnosisOverride,
    FailureCreate,
    FailureDetailOut,
    FailureOut,
    FailureTransitionIn,
    LessonDecisionIn,
    LessonOut,
    RecoveryApplyIn,
    RecoveryApplyOut,
    RecoveryOutcomeIn,
)
from aegis_api.lab.models import Failure
from aegis_api.schemas.common import Page, PageParams
from engines.lab.states import FailureStatus, FailureType

router = APIRouter(prefix="/api/v1", tags=["Failures"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
_READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E}


def failure_detail(db: Session, actor: Actor, failure: Failure) -> FailureDetailOut:
    out = FailureDetailOut.model_validate(failure)
    lesson = service.lesson_for(db, actor, failure)
    return out.model_copy(
        update={
            "similar": [FailureOut.model_validate(f) for f in service.similar_failures(db, actor, failure)],
            "lesson": LessonOut.model_validate(lesson) if lesson is not None else None,
        }
    )


# -- failures --------------------------------------------------------------------------------------
@router.get(
    "/failures",
    response_model=CursorPage[FailureOut],
    status_code=200,
    summary="List failures",
    description="Failures in projects you can see, newest first (cursor pagination). Filters: `project_id`, "
    "`mission_id`, `experiment_id`, `failure_type`, `status`, `signature`. Requires `failure:read`.",
    responses=_READ_ERRORS,
)
def list_failures(
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    experiment_id: uuid.UUID | None = Query(default=None),
    failure_type: FailureType | None = Query(default=None, alias="type"),
    status: FailureStatus | None = Query(default=None),
    signature: str | None = Query(default=None, max_length=64),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_failures(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        failure_type=failure_type.value if failure_type else None,
        status=status.value if status else None,
        signature=signature,
        mapper=FailureOut.model_validate,
    )


@router.get(
    "/failures/{failure_id}",
    response_model=FailureDetailOut,
    status_code=200,
    summary="Get a failure",
    description="The failure with its redacted traceback/log excerpt, the deterministic classification (and any "
    "model or human diagnosis recorded alongside it), the recovery proposal, similar failures and the linked "
    "lesson. Requires `failure:read`.",
    responses=_READ_ERRORS,
)
def get_failure(failure_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return failure_detail(db, actor, service.get_failure(db, actor, failure_id))


@router.post(
    "/failures",
    response_model=FailureDetailOut,
    status_code=201,
    summary="Record a failure",
    description="Records a failure observed outside the automated pipeline. Signals are redacted, classified by the "
    "deterministic rule engine, linked to recurring/similar failures and a lesson, and given a bounded recovery "
    "proposal. Requires `failure:write` in the project. Supports `Idempotency-Key`.",
    responses=_WRITE_ERRORS,
)
def create_failure(
    body: FailureCreate,
    idem: Idempotency = Depends(idempotency),
    actor: Actor = Depends(require_actor("failure:write")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    failure = service.record_failure(db, actor, body)
    return idem.remember(201, failure_detail(db, actor, failure))


@router.post(
    "/failures/{failure_id}/diagnosis",
    response_model=FailureDetailOut,
    status_code=200,
    summary="Record a human diagnosis",
    description="Overrides the displayed root cause with a human diagnosis (`root_cause_source: human`), optionally "
    "re-labelling the failure type (the recovery proposal is recomputed). The rule classification stays recorded. "
    "Signed-in humans with `failure:write` only.",
    responses=_WRITE_ERRORS,
)
def override_diagnosis(
    failure_id: uuid.UUID,
    body: DiagnosisOverride,
    actor: Actor = Depends(require_actor("failure:write")),
    db: Session = Depends(get_db),
) -> Any:
    failure = service.override_diagnosis(db, actor, failure_id, body)
    return failure_detail(db, actor, failure)


@router.post(
    "/failures/{failure_id}/recovery/apply",
    response_model=RecoveryApplyOut,
    status_code=200,
    summary="Apply the recovery",
    description="Applies the recovery patch (rule proposal by default, or the guardrail-accepted model proposal) as a "
    "NEW validated experiment version; the failure becomes `RECOVERING`. Proposals that need approval return "
    "`status: approval_required` with the `approval_id` instead. Never applied to policy failures or negative "
    "results (409). Humans need `failure:write`; automated callers also need mission autonomy ≥ L3. Supports "
    "`Idempotency-Key`.",
    responses={**_WRITE_ERRORS, 429: _E, 503: {"description": "The experiments service is unavailable"}},
)
def apply_recovery(
    failure_id: uuid.UUID,
    body: RecoveryApplyIn | None = None,
    idem: Idempotency = Depends(idempotency),
    _rate: None = Depends(lab_rate_limit("execution")),
    actor: Actor = Depends(require_actor("failure:write")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    result = recovery.apply_recovery(db, actor, failure_id, source=(body or RecoveryApplyIn()).source)
    out = RecoveryApplyOut(
        status=result.status,
        failure=FailureOut.model_validate(result.failure),
        experiment_id=str(result.experiment_id) if result.experiment_id else None,
        new_version_id=str(result.new_version_id) if result.new_version_id else None,
        approval_id=str(result.approval_id) if result.approval_id else None,
    )
    return idem.remember(200, out)


@router.post(
    "/failures/{failure_id}/recovery/outcome",
    response_model=FailureDetailOut,
    status_code=200,
    summary="Record the recovery outcome",
    description="Closes the recovery test: `RESOLVED` when it succeeded, back to `DIAGNOSED` when it failed. Omit "
    "`succeeded` to assess the outcome from the runs of the recovery version (409 while they are missing or "
    "running). The lesson's success count and confidence are updated; a proposed lesson becomes ACTIVE after two "
    "successful recoveries. Requires `failure:write`.",
    responses=_WRITE_ERRORS,
)
def recovery_outcome(
    failure_id: uuid.UUID,
    body: RecoveryOutcomeIn,
    actor: Actor = Depends(require_actor("failure:write")),
    db: Session = Depends(get_db),
) -> Any:
    if body.succeeded is None:
        failure = recovery.assess_recovery(db, actor, failure_id, reason=body.reason)
    else:
        failure = recovery.record_recovery_outcome(db, actor, failure_id, body.succeeded, reason=body.reason)
    return failure_detail(db, actor, failure)


@router.post(
    "/failures/{failure_id}/transition",
    response_model=FailureDetailOut,
    status_code=200,
    summary="Change a failure's status",
    description="Manual lifecycle change with a reason: `WONT_FIX`, `RESOLVED`, `DIAGNOSED` or `OPEN` (reopen). "
    "`RECOVERING` is only entered by applying a recovery. Illegal transitions return 409. Requires `failure:write`.",
    responses=_WRITE_ERRORS,
)
def transition_failure(
    failure_id: uuid.UUID,
    body: FailureTransitionIn,
    actor: Actor = Depends(require_actor("failure:write")),
    db: Session = Depends(get_db),
) -> Any:
    failure = service.transition_failure(db, actor, failure_id, body.status.value, body.reason)
    return failure_detail(db, actor, failure)


# -- lessons ---------------------------------------------------------------------------------------
@router.get(
    "/lessons",
    response_model=Page[LessonOut],
    status_code=200,
    summary="List lessons",
    description="Lessons learned from failures, newest first. Filters: `project_id`, `failure_type`, `status` "
    "(PROPOSED|ACTIVE|RETIRED), `signature`. Requires `failure:read`.",
    responses=_READ_ERRORS,
)
def list_lessons(
    project_id: uuid.UUID | None = Query(default=None),
    failure_type: FailureType | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(PROPOSED|ACTIVE|RETIRED)$"),
    signature: str | None = Query(default=None, max_length=64),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_lessons(
        db,
        actor,
        params,
        project_id=project_id,
        failure_type=failure_type.value if failure_type else None,
        status=status,
        signature=signature,
        mapper=LessonOut.model_validate,
    )


@router.get(
    "/lessons/{lesson_id}",
    response_model=LessonOut,
    status_code=200,
    summary="Get a lesson",
    description="One lesson: statement, recommendation (remedy + patch), evidence, confidence and how often it was "
    "applied and succeeded. Requires `failure:read`.",
    responses=_READ_ERRORS,
)
def get_lesson(lesson_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return LessonOut.model_validate(service.get_lesson(db, actor, lesson_id))


@router.post(
    "/lessons/{lesson_id}/approve",
    response_model=LessonOut,
    status_code=200,
    summary="Approve a lesson",
    description="A human reviewer activates a PROPOSED lesson (`memory:review`, signed-in humans only).",
    responses=_WRITE_ERRORS,
)
def approve_lesson(
    lesson_id: uuid.UUID,
    body: LessonDecisionIn,
    actor: Actor = Depends(require_actor("memory:review")),
    db: Session = Depends(get_db),
) -> Any:
    return LessonOut.model_validate(service.decide_lesson(db, actor, lesson_id, approve=True, reason=body.reason))


@router.post(
    "/lessons/{lesson_id}/retire",
    response_model=LessonOut,
    status_code=200,
    summary="Retire a lesson",
    description="A human reviewer retires a lesson so it is no longer reused (`memory:review`, humans only).",
    responses=_WRITE_ERRORS,
)
def retire_lesson(
    lesson_id: uuid.UUID,
    body: LessonDecisionIn,
    actor: Actor = Depends(require_actor("memory:review")),
    db: Session = Depends(get_db),
) -> Any:
    return LessonOut.model_validate(service.decide_lesson(db, actor, lesson_id, approve=False, reason=body.reason))
