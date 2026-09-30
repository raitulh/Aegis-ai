"""Strategy promotion and rollback — benchmark-evidence gate, governance policy and human approval.

Promotion of a version is allowed only when **all** of these hold:

1. the :class:`~engines.lab.evolution.promotion.PromotionGate` finds it *eligible* against the incumbent (≥ 3
   seeds per checked objective, every safety constraint satisfied, a primary-objective improvement whose
   bootstrap CI excludes zero, no regression, and internal benchmark comparisons showing an improvement over the
   incumbent — improvement is never claimed without benchmark evidence);
2. governance allows it: humans are evaluated against ``strategy.promote`` (baseline: requires approval);
   automated actors against ``strategy.auto_promote`` (baseline: **denied**). An automated actor can at most
   request a human approval — evolution never promotes by itself;
3. a human holding ``strategy:promote`` performs it (directly when policy allows, or by approving the request —
   separation of duties applies to approvals).

Promotion and rollback serialize per strategy with ``advisory_xact_lock("strategy-promotion:<id>")`` and
optimistic locking on every touched version, so concurrent promotions cannot leave two PROMOTED versions.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import ConcurrentModification, PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.governance.approvals import get_approval, is_approved, register_approval_hook, request_approval
from aegis_api.lab.governance.policies import evaluate_policy
from aegis_api.lab.models import Approval, EvolutionRun, Mission, Strategy, StrategyEvaluation, StrategyVersion
from aegis_api.lab.strategies import schemas
from aegis_api.lab.strategies.benchmarks import latest_comparisons
from aegis_api.lab.strategies.service import (
    check_strategy_access,
    incumbent_version,
    load_strategy,
    load_version,
    set_version_status,
    version_out,
)
from engines.lab.evolution.promotion import PromotionDecision, PromotionGate, PromotionPolicy
from engines.lab.evolution.rollback import RollbackError, RollbackManager
from engines.lab.states import ApprovalStatus, StrategyStatus, assert_transition

log = structlog.get_logger("aegis.lab.strategies")

PROMOTION_ACTION = "strategy.promote"
AUTO_PROMOTION_ACTION = "strategy.auto_promote"
SUBJECT_TYPE = "strategy_version"
MIN_SEEDS = 3
PROMOTABLE: frozenset[str] = frozenset({StrategyStatus.SURVIVING, StrategyStatus.EXPERIMENTAL})


def promotion_policy() -> PromotionPolicy:
    """The gate's policy: ≥3 seeds, CI-backed improvement, no safety/reproducibility regression, benchmarks."""
    return PromotionPolicy(
        min_seeds=MIN_SEEDS,
        primary_objective="scientific_performance",
        no_regression_objectives=("safety", "reproducibility"),
        require_benchmark=True,
        seed=0,
    )


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    """JSON-safe copy (UUIDs/datetimes as strings, non-finite floats as strings)."""
    return json.loads(json.dumps(_finite(value), default=str, allow_nan=False))


_finite_json = _jsonable


@dataclass
class GateResult:
    decision: PromotionDecision
    incumbent: StrategyVersion | None
    suites: list[str]
    evidence: list[dict[str, Any]]
    candidate_evaluation_ids: list[str] = field(default_factory=list)
    incumbent_evaluation_ids: list[str] = field(default_factory=list)

    def to_out(self) -> schemas.PromotionDecisionOut:
        return schemas.PromotionDecisionOut(
            eligible=self.decision.eligible,
            reasons=list(self.decision.reasons),
            checks=[
                schemas.PromotionCheckOut(name=c.name, passed=c.passed, detail=c.detail) for c in self.decision.checks
            ],
            statistics=_finite_json(self.decision.statistics) or {},
            incumbent_version_id=str(self.incumbent.id) if self.incumbent is not None else None,
            benchmark_suites=self.suites,
            requires_human_approval=True,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "eligible": self.decision.eligible,
            "reasons": list(self.decision.reasons),
            "failed_checks": self.decision.failed_checks(),
            "incumbent_version_id": str(self.incumbent.id) if self.incumbent is not None else None,
            "benchmark_suites": self.suites,
            "benchmark_evidence": self.evidence,
            "candidate_evaluations": self.candidate_evaluation_ids,
            "incumbent_evaluations": self.incumbent_evaluation_ids,
        }


def suite_signature(evaluation: StrategyEvaluation) -> tuple[str, ...]:
    raw = evaluation.raw_metrics or {}
    suites = raw.get("suites") if isinstance(raw, Mapping) else None
    return tuple(sorted(str(s) for s in suites)) if isinstance(suites, list) else ()


def pooled_samples(evaluations: Sequence[StrategyEvaluation]) -> dict[str, list[float]]:
    """Per-seed objective samples of several evaluations, pooled."""
    pooled: dict[str, list[float]] = {}
    for evaluation in evaluations:
        samples = (evaluation.raw_metrics or {}).get("samples")
        if not isinstance(samples, Mapping):
            continue
        for name, values in samples.items():
            if isinstance(values, list):
                pooled.setdefault(str(name), []).extend(
                    float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)
                )
    return pooled


def _evaluations(db: Session, version_id: uuid.UUID) -> list[StrategyEvaluation]:
    return list(
        db.scalars(
            select(StrategyEvaluation)
            .where(StrategyEvaluation.strategy_version_id == version_id)
            .order_by(StrategyEvaluation.created_at.desc(), StrategyEvaluation.id)
        ).all()
    )


def _first_promotion_decision(gate: PromotionGate, candidate: Mapping[str, Sequence[float]]) -> PromotionDecision:
    """No incumbent exists for the kind at all: sample size and safety feasibility are required."""
    reference = gate.evaluate(candidate, candidate, [])
    checks = [c for c in reference.checks if c.name in ("sample_size", "feasibility")]
    eligible = all(c.passed for c in checks)
    reasons = (
        [
            "no incumbent exists for this kind; sample size and safety feasibility hold — governance and human "
            "approval still apply"
        ]
        if eligible
        else [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    )
    return PromotionDecision(eligible=eligible, reasons=reasons, statistics=reference.statistics, checks=checks)


def evaluate_gate(db: Session, version: StrategyVersion, strategy: Strategy) -> GateResult:
    """Evidence-based eligibility of ``version`` against the incumbent (pure gate over stored evaluations)."""
    gate = PromotionGate(promotion_policy())
    incumbent = incumbent_version(db, strategy)
    evaluations = _evaluations(db, version.id)
    signature = suite_signature(evaluations[0]) if evaluations else ()
    candidate = [e for e in evaluations if suite_signature(e) == signature] if evaluations else []
    if incumbent is not None and incumbent.id == version.id:
        decision = gate.evaluate({}, {}, [])
        decision = decision.model_copy(
            update={"eligible": False, "reasons": ["the version is already the incumbent for its kind and scope"]}
        )
        return GateResult(decision, incumbent, list(signature), [], [str(e.id) for e in candidate])
    incumbent_evals = (
        [e for e in _evaluations(db, incumbent.id) if suite_signature(e) == signature] if incumbent is not None else []
    )
    candidate_samples = pooled_samples(candidate)
    if incumbent is None:
        decision = _first_promotion_decision(gate, candidate_samples)
        evidence: list[dict[str, Any]] = []
    else:
        evidence = latest_comparisons(db, strategy.organization_id, version.id, list(signature), incumbent.id)
        decision = gate.evaluate(pooled_samples(incumbent_evals), candidate_samples, evidence)
    return GateResult(
        decision=decision,
        incumbent=incumbent,
        suites=list(signature),
        evidence=evidence,
        candidate_evaluation_ids=[str(e.id) for e in candidate],
        incumbent_evaluation_ids=[str(e.id) for e in incumbent_evals],
    )


def gate_for_version(db: Session, actor: Actor, version_id: uuid.UUID | str) -> GateResult:
    version, strategy = load_version(db, actor, version_id, "strategy:read")
    return evaluate_gate(db, version, strategy)


# ---------------------------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------------------------
@dataclass
class PromotionOutcome:
    status: Literal["promoted", "pending_approval"]
    version: StrategyVersion
    gate: GateResult
    approval: Approval | None = None
    retired_version_ids: list[uuid.UUID] = field(default_factory=list)

    def to_out(self) -> schemas.PromotionOut:
        return schemas.PromotionOut(
            status=self.status,
            version=version_out(self.version),
            approval_id=str(self.approval.id) if self.approval is not None else None,
            retired_version_ids=[str(v) for v in self.retired_version_ids],
            decision=self.gate.to_out(),
        )


def _clean_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not 3 <= len(text) <= 2000:
        raise ValidationFailed("reason must be 3-2000 characters")
    return text


def _locked(db: Session, version_id: uuid.UUID, strategy_id: uuid.UUID) -> tuple[StrategyVersion, Strategy]:
    """Re-read the rows after taking the promotion lock (another transaction may have just committed)."""
    version = db.execute(
        select(StrategyVersion).where(StrategyVersion.id == version_id).execution_options(populate_existing=True)
    ).scalar_one()
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id).execution_options(populate_existing=True)
    ).scalar_one()
    return version, strategy


def _check_promotable(version: StrategyVersion) -> None:
    if version.status == StrategyStatus.PROMOTED:
        raise Conflict("This version is already promoted", code="strategy_already_promoted")
    if version.status == StrategyStatus.RETIRED:
        raise Conflict("A retired version returns to service only through a rollback", code="invalid_state_transition")
    if version.status not in PROMOTABLE:
        assert_transition("strategy", version.status, StrategyStatus.PROMOTED)  # raises InvalidTransitionError


def _apply_promotion(
    db: Session,
    actor: Actor,
    version: StrategyVersion,
    strategy: Strategy,
    *,
    promoted_by_id: uuid.UUID | None,
    reason: str,
    gate: GateResult,
    approval_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Candidate → PROMOTED, the strategy's previous PROMOTED version(s) → RETIRED (caller holds the lock)."""
    now = utcnow()
    previous = db.scalars(
        select(StrategyVersion)
        .where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.status == StrategyStatus.PROMOTED,
            StrategyVersion.id != version.id,
        )
        .execution_options(populate_existing=True)
    ).all()
    retired: list[uuid.UUID] = []
    for prior in previous:
        set_version_status(prior, StrategyStatus.RETIRED)
        prior.retired_at = now
        retired.append(prior.id)
    if version.status == StrategyStatus.EXPERIMENTAL:
        set_version_status(version, StrategyStatus.SURVIVING)
    set_version_status(version, StrategyStatus.PROMOTED)
    version.promoted_at = now
    version.promoted_by_id = promoted_by_id
    previous_current = strategy.current_version_id
    strategy.current_version_id = version.id
    db.flush()
    payload = {
        "strategy_id": str(strategy.id),
        "kind": strategy.kind,
        "version_id": str(version.id),
        "version": version.version,
        "retired_version_ids": [str(r) for r in retired],
        "incumbent_version_id": str(gate.incumbent.id) if gate.incumbent is not None else None,
        "approval_id": str(approval_id) if approval_id else None,
        "benchmark_suites": gate.suites,
    }
    emit(
        db,
        organization_id=strategy.organization_id,
        type=EventType.STRATEGY_PROMOTED,
        payload=payload,
        project_id=strategy.project_id,
        workspace_id=strategy.workspace_id,
        subject_type="strategy_version",
        subject_id=version.id,
        actor=actor,
    )
    audit(
        db,
        actor,
        AuditAction.STRATEGY_PROMOTED,
        "strategy_version",
        version.id,
        before={"current_version_id": previous_current, "retired": [str(r) for r in retired]},
        after={**payload, "reason": reason, "promoted_by_id": promoted_by_id},
    )
    append_evidence(
        db,
        organization_id=strategy.organization_id,
        kind="strategy_promotion",
        title=f"Promoted {strategy.name} v{version.version}"[:300],
        content=_finite_json(
            {
                **payload,
                "reason": reason,
                "promoted_by_id": promoted_by_id,
                "content_hash": version.content_hash,
                "gate": {**gate.summary(), "statistics": gate.decision.statistics},
            }
        ),
    )
    log.info("strategy_promoted", strategy_id=str(strategy.id), version_id=str(version.id), retired=len(retired))
    return retired


def promote_version(db: Session, actor: Actor, version_id: uuid.UUID | str, *, reason: str) -> PromotionOutcome:
    """Promote a SURVIVING (or evaluated EXPERIMENTAL) version — or request the human approval it needs.

    Raises 409 ``promotion_ineligible`` when the benchmark-evidence gate fails, ``PolicyDenied`` when governance
    denies (always for automated auto-promotion under the baseline policy).
    """
    clean = _clean_reason(reason)
    version, strategy = load_version(db, actor, version_id, "strategy:read")
    if actor.is_human:
        check_strategy_access(db, actor, strategy, "strategy:promote")
    else:
        check_strategy_access(db, actor, strategy, "strategy:evolve")
    advisory_xact_lock(db, f"strategy-promotion:{strategy.id}")
    version, strategy = _locked(db, version.id, strategy.id)
    _check_promotable(version)

    mission = None
    if actor.kind in ("agent", "workflow") and version.evolution_run_id is not None:
        run = db.get(EvolutionRun, version.evolution_run_id)
        mission = db.get(Mission, run.mission_id) if run is not None and run.mission_id else None
    context = {"risk_level": "MEDIUM", "x_strategy_kind": strategy.kind}
    decision = None
    if not actor.is_human:
        decision = evaluate_policy(
            db, actor, AUTO_PROMOTION_ACTION, context, project_id=strategy.project_id, mission=mission
        )
        if decision.denied:
            raise PolicyDenied(
                "Strategy auto-promotion is denied: "
                + ("; ".join(decision.reasons) or "automated actors cannot promote strategies"),
                details=_jsonable(decision.to_dict()),
            )

    gate = evaluate_gate(db, version, strategy)
    if not gate.decision.eligible:
        raise Conflict(
            "The version is not eligible for promotion",
            code="promotion_ineligible",
            details={
                "reasons": list(gate.decision.reasons),
                "failed_checks": gate.decision.failed_checks(),
                "incumbent_version_id": str(gate.incumbent.id) if gate.incumbent is not None else None,
                "benchmark_suites": gate.suites,
            },
        )

    if actor.is_human:
        decision = evaluate_policy(db, actor, PROMOTION_ACTION, context, project_id=strategy.project_id)
        if decision.denied:
            raise PolicyDenied(
                "Strategy promotion is denied by policy: " + "; ".join(decision.reasons),
                details=_jsonable(decision.to_dict()),
            )
        if decision.allowed or is_approved(db, actor.organization_id, PROMOTION_ACTION, SUBJECT_TYPE, version.id):
            retired = _apply_promotion(
                db, actor, version, strategy, promoted_by_id=actor.user_id, reason=clean, gate=gate, approval_id=None
            )
            return PromotionOutcome("promoted", version, gate, None, retired)

    approval = request_approval(
        db,
        actor,
        action=PROMOTION_ACTION,
        subject_type=SUBJECT_TYPE,
        subject_id=version.id,
        title=f"Promote strategy {strategy.name} to version {version.version}",
        payload=_finite_json(
            {
                "strategy_id": str(strategy.id),
                "kind": strategy.kind,
                "version": version.version,
                "reason": clean,
                "gate": gate.summary(),
            }
        ),
        risk_level="MEDIUM",
        decision=decision,
        project_id=strategy.project_id,
        mission_id=mission.id if mission is not None else None,
        workflow_run_id=actor.workflow_run_id if actor.kind == "workflow" else None,
        required_permission="strategy:promote",
    )
    audit(
        db,
        actor,
        "STRATEGY_PROMOTION_REQUESTED",
        "strategy_version",
        version.id,
        after={"approval_id": approval.id, "strategy_id": strategy.id, "reason": clean},
    )
    return PromotionOutcome("pending_approval", version, gate, approval, [])


def apply_approved_promotion(db: Session, actor: Actor, approval_id: uuid.UUID | str) -> StrategyVersion:
    """Carry out a promotion a human APPROVED (approval hook and workflows; idempotent)."""
    approval = get_approval(db, actor, approval_id)
    if approval.action != PROMOTION_ACTION or approval.subject_type != SUBJECT_TYPE:
        raise ValidationFailed("The approval is not a strategy promotion request")
    if approval.status != ApprovalStatus.APPROVED:
        raise Conflict(f"The promotion request is {approval.status}", code="approval_not_approved")
    version, strategy = load_version(db, actor, approval.subject_id, "strategy:read")
    advisory_xact_lock(db, f"strategy-promotion:{strategy.id}")
    version, strategy = _locked(db, version.id, strategy.id)
    if version.status == StrategyStatus.PROMOTED:
        return version
    _check_promotable(version)
    gate = evaluate_gate(db, version, strategy)
    if not gate.decision.eligible:
        raise Conflict(
            "The approved version is no longer eligible (the incumbent or the evidence changed)",
            code="promotion_ineligible",
            details={"reasons": list(gate.decision.reasons), "failed_checks": gate.decision.failed_checks()},
        )
    _apply_promotion(
        db,
        actor,
        version,
        strategy,
        promoted_by_id=approval.decided_by_id,
        reason=approval.decision_reason or "approved",
        gate=gate,
        approval_id=approval.id,
    )
    return version


def on_approval_decided(db: Session, actor: Actor, approval: Approval) -> None:
    """Approval hook: a human approved a promotion request → promote (rejections change nothing)."""
    if approval.action != PROMOTION_ACTION or approval.status != ApprovalStatus.APPROVED:
        return
    apply_approved_promotion(db, actor, approval.id)


register_approval_hook(PROMOTION_ACTION, on_approval_decided)


# ---------------------------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------------------------
@dataclass
class RollbackOutcome:
    strategy: Strategy
    rolled_back: StrategyVersion
    restored: StrategyVersion
    reason: str

    def to_out(self) -> schemas.RollbackOut:
        return schemas.RollbackOut(
            strategy_id=str(self.strategy.id),
            rolled_back_version_id=str(self.rolled_back.id),
            restored_version_id=str(self.restored.id),
            reason=self.reason,
        )


def rollback(
    db: Session,
    actor: Actor,
    strategy_id: uuid.UUID | str,
    *,
    reason: str,
    target_version_id: uuid.UUID | str | None = None,
) -> RollbackOutcome:
    """Human-only: current PROMOTED → ROLLED_BACK and the previously promoted version → PROMOTED."""
    actor.require_human("rolling back a strategy")
    clean = _clean_reason(reason)
    strategy = load_strategy(db, actor, strategy_id, "strategy:rollback")
    advisory_xact_lock(db, f"strategy-promotion:{strategy.id}")
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy.id).execution_options(populate_existing=True)
    ).scalar_one()
    versions = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .execution_options(populate_existing=True)
    ).all()
    history = [{"version_id": str(v.id), "status": v.status, "promoted_at": v.promoted_at} for v in versions]
    try:
        plan = RollbackManager().plan(
            history, target_version_id=str(target_version_id) if target_version_id else None, reason=clean
        )
    except RollbackError as exc:
        raise Conflict(str(exc), code="rollback_unavailable") from exc
    by_id = {str(v.id): v for v in versions}
    now = utcnow()
    for change in plan.changes:
        version = by_id[change.version_id]
        if version.status != change.from_status:
            raise ConcurrentModification("The strategy changed concurrently; reload and retry")
        set_version_status(version, change.to_status)
        if change.to_status == StrategyStatus.PROMOTED:
            version.promoted_at = now
            version.promoted_by_id = actor.user_id
        elif change.to_status == StrategyStatus.ROLLED_BACK:
            version.retired_at = now
    current = by_id[plan.current_version_id]
    target = by_id[plan.target_version_id]
    strategy.current_version_id = target.id
    db.flush()
    payload = {
        "strategy_id": str(strategy.id),
        "kind": strategy.kind,
        "rolled_back_version_id": str(current.id),
        "restored_version_id": str(target.id),
        "restored_version": target.version,
        "reason": clean,
    }
    emit(
        db,
        organization_id=strategy.organization_id,
        type=EventType.STRATEGY_ROLLED_BACK,
        payload=payload,
        project_id=strategy.project_id,
        workspace_id=strategy.workspace_id,
        subject_type="strategy",
        subject_id=strategy.id,
        actor=actor,
    )
    audit(
        db,
        actor,
        AuditAction.STRATEGY_ROLLED_BACK,
        "strategy",
        strategy.id,
        before={"current_version_id": current.id},
        after=payload,
    )
    append_evidence(
        db,
        organization_id=strategy.organization_id,
        kind="strategy_rollback",
        title=f"Rolled back {strategy.name} to v{target.version}"[:300],
        content={**payload, "changes": [c.model_dump() for c in plan.changes], "by": actor.as_dict()},
    )
    return RollbackOutcome(strategy=strategy, rolled_back=current, restored=target, reason=clean)
