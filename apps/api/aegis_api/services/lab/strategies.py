"""Strategy registry: versioned strategy definitions, evaluations, promotion and rollback.

Guardrails:
* A new version may never be more privileged than its parent (tools, network, egress hosts, secrets,
  permissions, autonomy, model tiers, production access) — checked by ``check_escalation`` and denied by the
  policy engine (``evolution-cannot-escalate``).
* Promotion requires the statistical ``PromotionGate`` (enough evaluations, reproduction, non-dominated,
  significantly better than the incumbent) and a policy decision. Automated promotion additionally needs
  mission autonomy ≥ L4 *and* the organization's explicit opt-in; otherwise a human approves.
* Promotions/rollbacks are serialized per strategy (advisory lock + optimistic locking).
* ``Strategy.promoted_version_id`` designates the active version; earlier promoted versions keep their
  promotion record so rollback can restore them.
"""

from __future__ import annotations

import statistics
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, InvalidState, PolicyDenied, ValidationFailed
from aegis_api.infrastructure.locks import advisory_xact_lock
from aegis_api.models.lab import Approval, Mission, Strategy, StrategyEvaluation, StrategyMutation, StrategyVersion
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import events, graph
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from aegis_api.services.lab.common import Actor, sha256_json
from engines.lab.enums import ApprovalStatus, LabEventType, StrategyStatus
from engines.lab.evolution.fitness import FitnessEngine, FitnessVector
from engines.lab.evolution.genome import StrategyDefinition, check_escalation
from engines.lab.evolution.promotion import (
    CandidateStats,
    PromotionCriteria,
    PromotionGate,
    RollbackManager,
    VersionRecord,
)
from engines.lab.state_machines import STRATEGY


def parse_definition(raw: dict[str, Any]) -> StrategyDefinition:
    try:
        return StrategyDefinition.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailed(f"invalid strategy definition: {exc.errors()[:3]}") from exc


def _add_version(
    db: Session,
    strategy: Strategy,
    definition: StrategyDefinition,
    *,
    parent: StrategyVersion | None,
    origin: str,
    generation: int = 0,
    evolution_run_id: uuid.UUID | None = None,
    created_by_id: uuid.UUID | None = None,
    status: str = StrategyStatus.CANDIDATE,
) -> StrategyVersion:
    number = 1 + int(
        db.scalar(
            select(func.coalesce(func.max(StrategyVersion.version), 0)).where(
                StrategyVersion.strategy_id == strategy.id
            )
        )
        or 0
    )
    version = StrategyVersion(
        organization_id=strategy.organization_id,
        strategy_id=strategy.id,
        version=number,
        parent_version_id=parent.id if parent else None,
        definition=definition.model_dump(mode="json"),
        definition_sha256=definition.fingerprint(),
        parameter_hash=definition.parameter_hash()[:32],
        status=status,
        generation=generation,
        evolution_run_id=evolution_run_id,
        origin=origin,
        created_by_id=created_by_id,
    )
    db.add(version)
    db.flush()
    strategy.version_count = number
    return version


def escalation_problems(parent: StrategyVersion | None, child: StrategyDefinition) -> list[str]:
    if parent is None:
        return []
    parent_def = StrategyDefinition.model_validate(parent.definition)
    problems = check_escalation(parent_def.governance, child.governance)
    if parent_def.kind != child.kind:
        problems.append("changes strategy kind")
    return problems


def create(
    db: Session,
    principal: Principal,
    *,
    name: str,
    description: str | None,
    definition: dict[str, Any],
    project_id: uuid.UUID | None,
    is_demo: bool = False,
) -> tuple[Strategy, StrategyVersion]:
    principal.require("strategy:write")
    if project_id:
        get_project(db, principal, project_id)
    parsed = parse_definition(definition)
    strategy = Strategy(
        organization_id=principal.organization_id,
        project_id=project_id,
        name=name[:160],
        kind=parsed.kind.value,
        description=description,
        status="active",
        is_demo=is_demo,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(strategy)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("A strategy with this name already exists") from exc
    version = _add_version(
        db,
        strategy,
        parsed,
        parent=None,
        origin="human",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.strategy.created",
        resource_type="strategy",
        resource_id=strategy.id,
        principal=principal,
        after={"name": strategy.name, "kind": strategy.kind, "definition_sha256": version.definition_sha256},
    )
    return strategy, version


def new_version(
    db: Session,
    principal: Principal,
    strategy_id: uuid.UUID | str,
    *,
    definition: dict[str, Any],
    parent_version_id: uuid.UUID | None,
) -> StrategyVersion:
    principal.require("strategy:write")
    strategy = get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    parsed = parse_definition(definition)
    parent = (
        db.get(StrategyVersion, parent_version_id)
        if parent_version_id
        else (db.get(StrategyVersion, strategy.promoted_version_id) if strategy.promoted_version_id else None)
    )
    if parent is not None and parent.strategy_id != strategy.id:
        raise ValidationFailed("parent version belongs to another strategy")
    problems = escalation_problems(parent, parsed)
    result = lab_policy.evaluate(
        db,
        organization_id=principal.organization_id,
        action="strategy.create",
        facts=lab_policy.base_facts(
            db,
            principal.organization_id,
            Actor.of(principal),
            extra={"strategy": {"governance_escalation": bool(problems), "escalations": problems}},
        ),
        actor=Actor.of(principal),
        resource_type="strategy",
        resource_id=str(strategy.id),
    )
    if result.denied:
        raise PolicyDenied("; ".join(result.reasons + problems), details={"escalations": problems})
    version = _add_version(
        db,
        strategy,
        parsed,
        parent=parent,
        origin="human",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    audit_log.record(
        db,
        organization_id=strategy.organization_id,
        action="lab.strategy.version_created",
        resource_type="strategy",
        resource_id=strategy.id,
        principal=principal,
        after={"version": version.version, "definition_sha256": version.definition_sha256},
    )
    return version


def fitness_engine(definition: StrategyDefinition | None = None) -> FitnessEngine:
    return FitnessEngine()


def record_evaluation(
    db: Session,
    *,
    version: StrategyVersion,
    source: str,
    metrics: dict[str, float | None],
    primary_samples: list[float],
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
    benchmark_run_id: uuid.UUID | None = None,
    reproduced: bool | None = None,
) -> StrategyEvaluation:
    engine = fitness_engine()
    fitness = engine.compute(metrics)
    row = StrategyEvaluation(
        organization_id=version.organization_id,
        strategy_version_id=version.id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        benchmark_run_id=benchmark_run_id,
        source=source,
        metrics={k: v for k, v in metrics.items() if v is not None},
        primary_samples=primary_samples,
        fitness=fitness.to_dict(),
        feasible=fitness.feasible,
        reproduced=reproduced,
    )
    db.add(row)
    db.flush()
    aggregate(db, version)
    return row


def aggregate(db: Session, version: StrategyVersion) -> None:
    rows = list(
        db.scalars(select(StrategyEvaluation).where(StrategyEvaluation.strategy_version_id == version.id)).all()
    )
    if not rows:
        return
    keys = sorted({k for r in rows for k in (r.metrics or {})})
    means: dict[str, float] = {}
    for k in keys:
        vals = [float(r.metrics[k]) for r in rows if isinstance((r.metrics or {}).get(k), int | float)]
        if vals:
            means[k] = statistics.fmean(vals)
    reproduced = [r.reproduced for r in rows if r.reproduced is not None]
    if reproduced:
        means["reproducibility"] = sum(1.0 for x in reproduced if x) / len(reproduced)
    version.metrics = means
    version.fitness = fitness_engine().compute(means).to_dict()
    version.evaluations = len(rows)


def _stats(db: Session, version: StrategyVersion) -> CandidateStats:
    rows = list(
        db.scalars(select(StrategyEvaluation).where(StrategyEvaluation.strategy_version_id == version.id)).all()
    )
    samples = [float(x) for r in rows for x in (r.primary_samples or [])]
    fitness = fitness_engine().compute(version.metrics or {})
    reproduced_flags = [r.reproduced for r in rows if r.reproduced is not None]
    return CandidateStats(
        individual_id=str(version.id),
        fitness=fitness,
        primary_samples=samples,
        evaluations=len(rows),
        reproduced=(all(reproduced_flags) if reproduced_flags else None),
    )


def promotion_decision(
    db: Session, strategy: Strategy, candidate: StrategyVersion, criteria: PromotionCriteria | None = None
) -> dict[str, Any]:
    incumbent = db.get(StrategyVersion, strategy.promoted_version_id) if strategy.promoted_version_id else None
    gate = PromotionGate(fitness_engine().keys, criteria)
    decision = gate.evaluate(
        _stats(db, candidate), _stats(db, incumbent) if incumbent and incumbent.id != candidate.id else None
    )
    return decision.to_dict()


def promote(
    db: Session,
    *,
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    actor: Actor,
    organization_id: uuid.UUID,
    reason: str,
    principal: Principal | None = None,
    mission: Mission | None = None,
    override_gate: bool = False,
    approval_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Promote a version. Humans need ``strategy:promote``; automated promotion is policy-gated and, when the
    policy requires it, only proceeds with an APPROVED approval for this exact version."""
    advisory_xact_lock(db, f"strategy-promotion:{strategy_id}")
    strategy = db.get(Strategy, strategy_id)
    version = db.get(StrategyVersion, version_id)
    if (
        strategy is None
        or version is None
        or version.strategy_id != strategy.id
        or strategy.organization_id != organization_id
    ):
        raise ValidationFailed("strategy version not found")
    if strategy.promoted_version_id == version.id:
        return {"promoted": True, "already": True, "version_id": str(version.id)}
    decision = promotion_decision(db, strategy, version)
    if not decision["eligible"] and not (override_gate and actor.is_human):
        return {"promoted": False, "gate": decision}
    definition = StrategyDefinition.model_validate(version.definition)
    parent = db.get(StrategyVersion, version.parent_version_id) if version.parent_version_id else None
    problems = escalation_problems(parent, definition)
    result = lab_policy.evaluate(
        db,
        organization_id=organization_id,
        action="strategy.promote",
        facts=lab_policy.base_facts(
            db,
            organization_id,
            actor,
            mission=mission,
            extra={"strategy": {"governance_escalation": bool(problems), "escalations": problems}},
        ),
        actor=actor,
        resource_type="strategy_version",
        resource_id=str(version.id),
    )
    if result.denied:
        raise PolicyDenied("; ".join(result.reasons), details=result.to_dict())
    if result.needs_approval and not actor.is_human:
        approval = db.get(Approval, approval_id) if approval_id else None
        if not (
            approval is not None
            and approval.organization_id == organization_id
            and approval.resource_type == "strategy_version"
            and approval.resource_id == str(version.id)
            and approval.status == ApprovalStatus.APPROVED
        ):
            return {"promoted": False, "approval_required": True, "policy": result.to_dict(), "gate": decision}
    # walk the state machine to PROMOTED
    for target in (StrategyStatus.EXPERIMENTAL, StrategyStatus.SURVIVING, StrategyStatus.PROMOTED):
        if version.status != StrategyStatus.PROMOTED and STRATEGY.can(version.status, target):
            version.status = target
    if version.status != StrategyStatus.PROMOTED:
        raise InvalidState(f"version in status '{version.status}' cannot be promoted")
    previous = strategy.promoted_version_id
    version.promoted_at = utcnow()
    strategy.promoted_version_id = version.id
    audit_log.record(
        db,
        organization_id=organization_id,
        action="lab.strategy.promoted",
        resource_type="strategy",
        resource_id=strategy.id,
        principal=principal,
        actor_label=actor.label,
        actor_type=actor.type,
        before={"promoted_version_id": str(previous) if previous else None},
        after={"promoted_version_id": str(version.id), "reason": reason, "gate": decision, "override": override_gate},
    )
    if mission is not None or strategy.project_id:
        events.emit(
            db,
            organization_id=organization_id,
            mission_id=mission.id if mission else None,
            project_id=strategy.project_id,
            event_type=LabEventType.STRATEGY_PROMOTED,
            message=f"Strategy '{strategy.name}' v{version.version} promoted",
            data={"strategy_id": str(strategy.id), "version_id": str(version.id), "gate": decision},
            actor=actor,
        )
    return {
        "promoted": True,
        "version_id": str(version.id),
        "gate": decision,
        "previous": str(previous) if previous else None,
    }


def rollback(db: Session, principal: Principal, strategy_id: uuid.UUID | str, reason: str) -> dict[str, Any]:
    principal.require("strategy:rollback")
    strategy = get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    advisory_xact_lock(db, f"strategy-promotion:{strategy.id}")
    if strategy.promoted_version_id is None:
        raise InvalidState("strategy has no promoted version")
    history = [
        VersionRecord(
            version_id=str(v.id), status=v.status, promoted_at=v.promoted_at.isoformat() if v.promoted_at else None
        )
        for v in db.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id)).all()
    ]
    target = RollbackManager.rollback_target(history, str(strategy.promoted_version_id))
    current = db.get(StrategyVersion, strategy.promoted_version_id)
    assert current is not None
    current.status = STRATEGY.ensure(current.status, StrategyStatus.ROLLED_BACK)
    current.retired_at = utcnow()
    strategy.promoted_version_id = uuid.UUID(target) if target else None
    audit_log.record(
        db,
        organization_id=strategy.organization_id,
        action="lab.strategy.rolled_back",
        resource_type="strategy",
        resource_id=strategy.id,
        principal=principal,
        before={"promoted_version_id": str(current.id)},
        after={"promoted_version_id": target, "reason": reason},
    )
    return {"rolled_back": str(current.id), "active": target}


def lineage(db: Session, version: StrategyVersion) -> dict[str, Any]:
    chain: list[dict[str, Any]] = []
    current: StrategyVersion | None = version
    guard = 0
    while current is not None and guard < 200:
        mutation = db.scalar(select(StrategyMutation).where(StrategyMutation.child_version_id == current.id))
        chain.append(
            {
                "version_id": str(current.id),
                "version": current.version,
                "generation": current.generation,
                "origin": current.origin,
                "status": current.status,
                "parameter_hash": current.parameter_hash,
                "metrics": current.metrics,
                "mutation": {
                    "operator": mutation.operator,
                    "changes": mutation.changes,
                    "seed": mutation.seed,
                    "second_parent": str(mutation.second_parent_version_id)
                    if mutation.second_parent_version_id
                    else None,
                }
                if mutation
                else None,
            }
        )
        current = db.get(StrategyVersion, current.parent_version_id) if current.parent_version_id else None
        guard += 1
    return {"version_id": str(version.id), "ancestry": chain}


def list_strategies(db: Session, principal: Principal, *, project_id: uuid.UUID | None = None) -> Select[Strategy]:
    stmt = select(Strategy).where(Strategy.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where((Strategy.project_id.is_(None)) | (Strategy.project_id.in_(visible)))
    if project_id:
        stmt = stmt.where(Strategy.project_id == project_id)
    return stmt.order_by(Strategy.created_at.desc())


def versions(db: Session, strategy: Strategy) -> list[StrategyVersion]:
    return list(
        db.scalars(
            select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id).order_by(StrategyVersion.version)
        ).all()
    )


def version_dict(v: StrategyVersion, *, active_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "strategy_id": str(v.strategy_id),
        "version": v.version,
        "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
        "status": v.status,
        "active": active_id == v.id,
        "generation": v.generation,
        "origin": v.origin,
        "definition": v.definition,
        "definition_sha256": v.definition_sha256,
        "parameter_hash": v.parameter_hash,
        "metrics": v.metrics,
        "fitness": v.fitness,
        "evaluations": v.evaluations,
        "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
    }


def link_to_graph(
    db: Session, version: StrategyVersion, parent: StrategyVersion | None, project_id: uuid.UUID | None
) -> None:
    if parent is None:
        return
    graph.link_refs(
        db,
        organization_id=version.organization_id,
        project_id=project_id,
        source=("strategy", str(version.id), f"strategy v{version.version}"),
        target=("strategy", str(parent.id), f"strategy v{parent.version}"),
        relation="evolved_from",
    )


def fitness_vector(raw: dict[str, Any]) -> FitnessVector:
    return fitness_engine().compute({k: v for k, v in raw.items() if isinstance(v, int | float)})


def definition_fingerprint(definition: dict[str, Any]) -> str:
    return sha256_json(definition)
