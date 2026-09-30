"""Claims (with machine-readable lineage), verifications, discoveries, failures, lessons, evaluators, reports,
strategies and evolution runs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, rate_limited, require
from aegis_api.errors import InvalidState
from aegis_api.idempotency import Idempotency, idempotency
from aegis_api.models.lab import (
    Discovery,
    EvolutionRun,
    Failure,
    Mission,
    ResearchReport,
    ScientificClaim,
    Strategy,
    StrategyVersion,
    Verification,
)
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.lab import (
    Accepted,
    ClaimIn,
    ClaimOut,
    DecisionIn,
    DiscoveryOut,
    EvolutionIn,
    FailureOut,
    LessonOut,
    PromoteIn,
    ReasonIn,
    ReportOut,
    StrategyIn,
    StrategyOut,
    StrategyVersionIn,
    TransitionIn,
    VerifyIn,
)
from aegis_api.security.context import Principal
from aegis_api.services.lab import discoveries, evolution, failures, strategies, verification
from aegis_api.services.lab import reports as report_service
from aegis_api.services.lab.access import get_scoped
from aegis_api.services.lab.common import Actor, parse_uuid
from aegis_api.workflows import client as workflow_client
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY

router = APIRouter(prefix="/api/v1", tags=["Verification"])


def _accept(idem: Idempotency, db: Session, body: Accepted) -> dict[str, Any]:
    payload = body.model_dump()
    idem.complete(db, 202, payload)
    return payload


# --- claims -----------------------------------------------------------------------------------------------------


@router.get("/claims", response_model=Page[ClaimOut])
def list_claims(
    params: PageParams = Depends(),
    mission_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=24),
    principal: Principal = Depends(require("verification:read")),
    db: Session = Depends(get_db),
) -> Page[ClaimOut]:
    stmt = verification.list_claims(db, principal, mission_id=mission_id, status=status, project_id=project_id)
    return paginate(db, stmt, params, ClaimOut.model_validate)


@router.post("/claims", response_model=ClaimOut, status_code=201)
def create_claim(
    body: ClaimIn, principal: Principal = Depends(require("verification:run")), db: Session = Depends(get_db)
) -> ClaimOut:
    data = body.model_dump()
    for key in ("mission_id", "experiment_id"):
        data[key] = parse_uuid(data[key], key) if data.get(key) else None
    return ClaimOut.model_validate(verification.create_manual_claim(db, principal, data))


@router.get("/claims/{claim_id}", response_model=ClaimOut)
def get_claim(
    claim_id: uuid.UUID, principal: Principal = Depends(require("verification:read")), db: Session = Depends(get_db)
) -> ClaimOut:
    return ClaimOut.model_validate(get_scoped(db, principal, ScientificClaim, claim_id, label="Claim"))


@router.get("/claims/{claim_id}/lineage")
def claim_lineage(
    claim_id: uuid.UUID, principal: Principal = Depends(require("verification:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Machine-readable provenance: Claim → Evidence → Verification → Evaluation → Experiment → ExperimentRun →
    CodeSnapshot → DatasetVersion → ModelVersion → Environment → RawArtifact."""
    return verification.lineage(db, get_scoped(db, principal, ScientificClaim, claim_id, label="Claim"))


@router.post("/claims/{claim_id}/verify", response_model=Accepted, status_code=202)
def verify_claim(
    claim_id: uuid.UUID,
    body: VerifyIn,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("verification:run")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("execution")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    v = verification.start(db, principal, claim_id, criteria_overrides=body.criteria_overrides)
    return _accept(idem, db, Accepted(id=str(v.id), status=v.status, workflow_run_id=str(v.workflow_run_id)))


@router.get("/verifications")
def list_verifications(
    claim_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("verification:read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(Verification).where(Verification.organization_id == principal.organization_id)
    if claim_id:
        get_scoped(db, principal, ScientificClaim, claim_id, label="Claim")
        stmt = stmt.where(Verification.claim_id == claim_id)
    return [
        verification.verification_detail(db, v)
        for v in db.scalars(stmt.order_by(Verification.created_at.desc()).limit(200)).all()
    ]


@router.get("/verifications/{verification_id}")
def get_verification(
    verification_id: uuid.UUID,
    principal: Principal = Depends(require("verification:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return verification.verification_detail(
        db, get_scoped(db, principal, Verification, verification_id, label="Verification")
    )


# --- discoveries --------------------------------------------------------------------------------------------------


@router.get("/discoveries", response_model=Page[DiscoveryOut])
def list_discoveries(
    params: PageParams = Depends(),
    status: str | None = Query(default=None, max_length=24),
    mission_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("discovery:read")),
    db: Session = Depends(get_db),
) -> Page[DiscoveryOut]:
    return paginate(
        db,
        discoveries.list_discoveries(db, principal, status=status, mission_id=mission_id),
        params,
        DiscoveryOut.model_validate,
    )


@router.get("/discoveries/{discovery_id}")
def get_discovery(
    discovery_id: uuid.UUID, principal: Principal = Depends(require("discovery:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    return {
        **DiscoveryOut.model_validate(d).model_dump(),
        "history": [
            {
                "version": h.version,
                "status": h.status,
                "reason": h.reason,
                "actor": h.actor,
                "at": h.created_at.isoformat(),
            }
            for h in discoveries.history(db, d)
        ],
    }


@router.post("/discoveries/{discovery_id}/review", response_model=DiscoveryOut)
def review_discovery(
    discovery_id: uuid.UUID,
    body: DecisionIn,
    principal: Principal = Depends(require("discovery:approve")),
    db: Session = Depends(get_db),
) -> DiscoveryOut:
    return DiscoveryOut.model_validate(
        discoveries.review(db, principal, discovery_id, approve=body.approve, reason=body.reason)
    )


@router.post("/discoveries/{discovery_id}/contest", response_model=DiscoveryOut)
def contest_discovery(
    discovery_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require("verification:run")),
    db: Session = Depends(get_db),
) -> DiscoveryOut:
    return DiscoveryOut.model_validate(discoveries.contest(db, principal, discovery_id, body.reason or "contested"))


@router.post("/discoveries/{discovery_id}/publication", status_code=201)
def request_publication(
    discovery_id: uuid.UUID, principal: Principal = Depends(require("discovery:publish")), db: Session = Depends(get_db)
) -> dict[str, str]:
    approval = discoveries.request_publication(db, principal, discovery_id)
    return {"approval_id": str(approval.id), "status": approval.status}


# --- failures / lessons / evaluators --------------------------------------------------------------------------------


@router.get("/failures", response_model=Page[FailureOut])
def list_failures(
    params: PageParams = Depends(),
    mission_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    failure_type: str | None = Query(default=None, max_length=40),
    principal: Principal = Depends(require("failure:read")),
    db: Session = Depends(get_db),
) -> Page[FailureOut]:
    stmt = failures.list_failures(
        db, principal, mission_id=mission_id, failure_type=failure_type, project_id=project_id
    )
    return paginate(db, stmt, params, FailureOut.model_validate)


@router.get("/failures/{failure_id}", response_model=FailureOut)
def get_failure(
    failure_id: uuid.UUID, principal: Principal = Depends(require("failure:read")), db: Session = Depends(get_db)
) -> FailureOut:
    return FailureOut.model_validate(get_scoped(db, principal, Failure, failure_id, label="Failure"))


@router.post("/failures/{failure_id}/status", response_model=FailureOut)
def set_failure_status(
    failure_id: uuid.UUID,
    body: TransitionIn,
    principal: Principal = Depends(require("experiment:create")),
    db: Session = Depends(get_db),
) -> FailureOut:
    return FailureOut.model_validate(failures.set_recovery_status(db, principal, failure_id, body.status))


@router.get("/lessons", response_model=Page[LessonOut])
def list_lessons(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("failure:read")),
    db: Session = Depends(get_db),
) -> Page[LessonOut]:
    return paginate(db, failures.list_lessons(db, principal, project_id=project_id), params, LessonOut.model_validate)


@router.get("/evaluations/evaluators")
def evaluator_catalog(_: Principal = Depends(require("evaluation:read"))) -> list[dict[str, Any]]:
    return DEFAULT_REGISTRY.catalog()


# --- reports ------------------------------------------------------------------------------------------------------


@router.get("/research-reports", response_model=Page[ReportOut])
def list_reports(
    params: PageParams = Depends(),
    mission_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("report:read")),
    db: Session = Depends(get_db),
) -> Page[ReportOut]:
    return paginate(
        db, report_service.list_reports(db, principal, mission_id=mission_id), params, ReportOut.model_validate
    )


@router.get("/research-reports/{report_id}")
def get_report(
    report_id: uuid.UUID, principal: Principal = Depends(require("report:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    report = get_scoped(db, principal, ResearchReport, report_id, label="Report")
    return {**ReportOut.model_validate(report).model_dump(), "sections": report.sections}


@router.post("/missions/{mission_id}/reports", response_model=Accepted, status_code=202)
def generate_report(
    mission_id: uuid.UUID,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("report:generate")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("model")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    mission = get_scoped(db, principal, Mission, mission_id, label="Mission")
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="report",
        business_key=f"report:{mission.id}:{uuid.uuid4().hex[:8]}",
        payload={"mission_id": str(mission.id)},
        principal=principal,
    )
    return _accept(idem, db, Accepted(id=str(mission.id), status="generating", workflow_run_id=str(run.id)))


# --- strategies / evolution -----------------------------------------------------------------------------------------


@router.get("/strategies", response_model=Page[StrategyOut])
def list_strategies(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("strategy:read")),
    db: Session = Depends(get_db),
) -> Page[StrategyOut]:
    return paginate(
        db, strategies.list_strategies(db, principal, project_id=project_id), params, StrategyOut.model_validate
    )


@router.post("/strategies", response_model=StrategyOut, status_code=201)
def create_strategy(
    body: StrategyIn, principal: Principal = Depends(require("strategy:write")), db: Session = Depends(get_db)
) -> StrategyOut:
    strategy, _ = strategies.create(
        db,
        principal,
        name=body.name,
        description=body.description,
        definition=body.definition,
        project_id=parse_uuid(body.project_id, "Project") if body.project_id else None,
    )
    return StrategyOut.model_validate(strategy)


@router.get("/strategies/{strategy_id}")
def get_strategy(
    strategy_id: uuid.UUID, principal: Principal = Depends(require("strategy:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    strategy = get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    return {
        **StrategyOut.model_validate(strategy).model_dump(),
        "versions": [
            strategies.version_dict(v, active_id=strategy.promoted_version_id)
            for v in strategies.versions(db, strategy)
        ],
    }


@router.post("/strategies/{strategy_id}/versions", status_code=201)
def create_strategy_version(
    strategy_id: uuid.UUID,
    body: StrategyVersionIn,
    principal: Principal = Depends(require("strategy:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    v = strategies.new_version(
        db,
        principal,
        strategy_id,
        definition=body.definition,
        parent_version_id=parse_uuid(body.parent_version_id, "Version") if body.parent_version_id else None,
    )
    return strategies.version_dict(v)


@router.get("/strategy-versions/{version_id}/lineage")
def strategy_lineage(
    version_id: uuid.UUID, principal: Principal = Depends(require("strategy:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return strategies.lineage(db, get_scoped(db, principal, StrategyVersion, version_id, label="Strategy version"))


@router.get("/strategies/{strategy_id}/promotion-check")
def promotion_check(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID = Query(...),
    principal: Principal = Depends(require("strategy:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    strategy = get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    version = get_scoped(db, principal, StrategyVersion, version_id, label="Strategy version")
    if version.strategy_id != strategy.id:
        raise InvalidState("version belongs to another strategy")
    return strategies.promotion_decision(db, strategy, version)


@router.post("/strategies/{strategy_id}/promote")
def promote_strategy(
    strategy_id: uuid.UUID,
    body: PromoteIn,
    principal: Principal = Depends(require("strategy:promote")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    principal.require_human("strategy promotion")
    get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    return strategies.promote(
        db,
        strategy_id=strategy_id,
        version_id=parse_uuid(body.version_id, "Strategy version"),
        actor=Actor.of(principal),
        organization_id=principal.organization_id,
        reason=body.reason,
        principal=principal,
        override_gate=body.override_gate,
    )


@router.post("/strategies/{strategy_id}/rollback")
def rollback_strategy(
    strategy_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require("strategy:rollback")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return strategies.rollback(db, principal, strategy_id, body.reason or "rollback")


@router.post("/evolution-runs", response_model=Accepted, status_code=202)
def start_evolution(
    body: EvolutionIn,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("strategy:evolve")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("execution")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    run = evolution.start(
        db,
        principal,
        strategy_id=body.strategy_id,
        config=body.config,
        max_generations=body.max_generations,
        mode=body.mode,
        experiment_template_id=parse_uuid(body.experiment_template_id, "Experiment")
        if body.experiment_template_id
        else None,
        benchmark=body.benchmark,
        mission_id=parse_uuid(body.mission_id, "Mission") if body.mission_id else None,
    )
    return _accept(idem, db, Accepted(id=str(run.id), status=run.status, workflow_run_id=str(run.workflow_run_id)))


@router.get("/evolution-runs")
def list_evolution_runs(
    strategy_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("strategy:read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(EvolutionRun).where(EvolutionRun.organization_id == principal.organization_id)
    if strategy_id:
        stmt = stmt.where(EvolutionRun.strategy_id == strategy_id)
    return [evolution.run_dict(r) for r in db.scalars(stmt.order_by(EvolutionRun.created_at.desc()).limit(100)).all()]


@router.get("/evolution-runs/{run_id}")
def get_evolution_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("strategy:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return evolution.run_dict(get_scoped(db, principal, EvolutionRun, run_id, label="Evolution run"))
