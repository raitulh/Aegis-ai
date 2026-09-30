"""Strategy registry: strategies, immutable versions, lifecycle, active-version resolution and lineage.

* Every version — human, agent or evolution — passes :class:`engines.lab.evolution.guardrails.StrategyGuardrails`
  against the strategy's ``parameter_schema`` (and its parent): unknown or out-of-bounds parameters, undeclared
  definition keys and authority-bearing keys (permissions, secrets, tool allow-lists, network, policy, …) are
  rejected with 422. Evolution changes behaviour, never authority.
* ``definition``/``parameters``/lineage of a version are immutable (database trigger); only lifecycle columns
  change, always through :func:`engines.lab.states.assert_transition`.
* ``PROMOTED``/``ROLLED_BACK`` are reachable only through :mod:`aegis_api.lab.strategies.promotion`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate, sort_clause
from aegis_api.lab.models import Mission, Strategy, StrategyEvaluation, StrategyMutation, StrategyVersion
from aegis_api.lab.strategies import schemas
from aegis_api.lab.strategies.seeds import ensure_default_strategies, version_content_hash
from aegis_api.schemas.common import Page, PageParams
from engines.lab.evolution.guardrails import GuardrailReport, StrategyGuardrails
from engines.lab.evolution.types import ParameterSchema, SchemaError
from engines.lab.states import StrategyKind, StrategyStatus, assert_transition

log = structlog.get_logger("aegis.lab.strategies")

GUARDRAILS = StrategyGuardrails()
LIVE_STATUSES: tuple[str, ...] = (StrategyStatus.CANDIDATE, StrategyStatus.EXPERIMENTAL, StrategyStatus.SURVIVING)
MANUAL_TARGETS: frozenset[str] = frozenset(
    {StrategyStatus.EXPERIMENTAL, StrategyStatus.SURVIVING, StrategyStatus.RETIRED}
)
MAX_LINEAGE_DEPTH = 200
CreatedBy = Literal["human", "evolution", "agent", "seed"]


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def parse_uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise NotFound(f"{label} not found") from exc


def actor_origin(actor: Actor) -> CreatedBy:
    """``created_by`` for versions created directly by ``actor``."""
    if actor.kind == "agent":
        return "agent"
    if actor.kind in ("workflow", "system"):
        return "evolution"
    return "human"


def check_strategy_access(db: Session, actor: Actor, strategy: Strategy, *permissions: str) -> None:
    """Project visibility + permissions (project roles apply to project-scoped strategies)."""
    if strategy.project_id is not None:
        load_project(db, actor, strategy.project_id, *permissions)
    elif permissions:
        actor.require(*permissions)


def load_strategy(db: Session, actor: Actor, strategy_id: uuid.UUID | str, *permissions: str) -> Strategy:
    strategy = get_owned(db, Strategy, strategy_id, actor, label="Strategy")
    try:
        check_strategy_access(db, actor, strategy, *permissions)
    except NotFound as exc:
        raise NotFound("Strategy not found") from exc
    return strategy


def load_version(
    db: Session, actor: Actor, version_id: uuid.UUID | str, *permissions: str
) -> tuple[StrategyVersion, Strategy]:
    version = get_owned(db, StrategyVersion, version_id, actor, label="Strategy version")
    strategy = db.get(Strategy, version.strategy_id)
    if strategy is None or strategy.organization_id != actor.organization_id:
        raise NotFound("Strategy version not found")
    try:
        check_strategy_access(db, actor, strategy, *permissions)
    except NotFound as exc:
        raise NotFound("Strategy version not found") from exc
    return version, strategy


def parse_schema(document: Mapping[str, Any]) -> ParameterSchema:
    """Parse and guardrail-check a parameter schema (422 on malformed or authority-bearing schemas)."""
    try:
        schema = ParameterSchema.from_dict(document)
    except SchemaError as exc:
        raise ValidationFailed("Invalid parameter schema", details={"error": str(exc)[:2000]}) from exc
    report = GUARDRAILS.validate_schema(schema)
    if not report.ok:
        raise ValidationFailed(
            "The parameter schema declares authority-bearing parameters; strategies change behaviour, never authority",
            code="strategy_guardrail_violation",
            details=report.as_dict(),
        )
    return schema


def guardrail_report(
    definition: Mapping[str, Any] | None,
    parameters: Mapping[str, Any] | None,
    schema: Mapping[str, Any] | ParameterSchema,
    parent: StrategyVersion | None = None,
) -> GuardrailReport:
    parent_doc = None if parent is None else {"definition": parent.definition, "parameters": parent.parameters}
    return GUARDRAILS.validate(definition, parameters, schema, parent_doc)


def enforce_guardrails(
    definition: Mapping[str, Any] | None,
    parameters: Mapping[str, Any] | None,
    schema: Mapping[str, Any] | ParameterSchema,
    parent: StrategyVersion | None = None,
) -> None:
    report = guardrail_report(definition, parameters, schema, parent)
    if not report.ok:
        raise ValidationFailed(
            "Strategy guardrail violation: evolution changes behaviour, never authority",
            code="strategy_guardrail_violation",
            details=report.as_dict(),
        )


def strategy_out(strategy: Strategy) -> schemas.StrategyOut:
    return schemas.StrategyOut.model_validate(strategy)


def version_out(version: StrategyVersion) -> schemas.StrategyVersionOut:
    return schemas.StrategyVersionOut.model_validate(version)


def _event(
    db: Session,
    actor: Actor,
    strategy: Strategy,
    type_: str,
    payload: dict[str, Any],
    *,
    mission_id: uuid.UUID | None = None,
    subject_type: str = "strategy",
    subject_id: uuid.UUID | None = None,
) -> None:
    emit(
        db,
        organization_id=strategy.organization_id,
        type=type_,
        payload=payload,
        mission_id=mission_id,
        project_id=strategy.project_id,
        workspace_id=strategy.workspace_id,
        subject_type=subject_type,
        subject_id=subject_id or strategy.id,
        actor=actor,
    )


# ---------------------------------------------------------------------------------------------
# Version creation (shared by humans, agents and evolution)
# ---------------------------------------------------------------------------------------------
def next_version_number(db: Session, strategy_id: uuid.UUID) -> int:
    advisory_xact_lock(db, f"strategy-versions:{strategy_id}")
    current = db.scalar(select(func.max(StrategyVersion.version)).where(StrategyVersion.strategy_id == strategy_id))
    return int(current or 0) + 1


def find_by_content(db: Session, strategy_id: uuid.UUID, content_hash: str) -> StrategyVersion | None:
    return db.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id, StrategyVersion.content_hash == content_hash)
        .order_by(StrategyVersion.version)
        .limit(1)
    )


def insert_version(
    db: Session,
    strategy: Strategy,
    *,
    definition: Mapping[str, Any],
    parameters: Mapping[str, Any],
    parent: StrategyVersion | None,
    created_by: CreatedBy,
    evolution_run_id: uuid.UUID | None = None,
    generation: int | None = None,
    agent_run_id: uuid.UUID | None = None,
    mutation_history: list[Any] | None = None,
) -> StrategyVersion:
    """Persist a new CANDIDATE version after re-checking the guardrails (defence in depth)."""
    schema = ParameterSchema.from_dict(strategy.parameter_schema)
    complete = schema.with_defaults(parameters)
    enforce_guardrails(definition, complete, schema, parent)
    version = StrategyVersion(
        id=uuid.uuid4(),
        organization_id=strategy.organization_id,
        strategy_id=strategy.id,
        version=next_version_number(db, strategy.id),
        parent_version_id=parent.id if parent is not None else None,
        definition=dict(definition),
        parameters=complete,
        content_hash=version_content_hash(dict(definition), complete),
        status=StrategyStatus.CANDIDATE,
        evolution_run_id=evolution_run_id,
        generation=generation,
        fitness={},
        mutation_history=list(mutation_history or []),
        created_by=created_by,
        created_by_agent_run_id=agent_run_id,
    )
    db.add(version)
    db.flush()
    return version


def record_mutation(
    db: Session,
    strategy: Strategy,
    child: StrategyVersion,
    *,
    operator: str,
    diff: dict[str, Any],
    parent_version_id: uuid.UUID | None,
    second_parent_version_id: uuid.UUID | None = None,
    seed: int | None = None,
    rationale: str | None = None,
    generated_by: Literal["engine", "agent", "human"] = "engine",
    agent_run_id: uuid.UUID | None = None,
    evolution_run_id: uuid.UUID | None = None,
) -> StrategyMutation:
    mutation = StrategyMutation(
        organization_id=strategy.organization_id,
        strategy_id=strategy.id,
        parent_version_id=parent_version_id,
        second_parent_version_id=second_parent_version_id,
        child_version_id=child.id,
        operator=operator[:48],
        diff=diff,
        rationale=rationale[:4000] if rationale else None,
        seed=seed,
        generated_by=generated_by,
        agent_run_id=agent_run_id,
        evolution_run_id=evolution_run_id,
    )
    db.add(mutation)
    db.flush()
    return mutation


def parameter_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {
        "before": {k: before.get(k) for k in changed},
        "after": {k: after.get(k) for k in changed},
        "paths": [f"parameters.{k}" for k in changed],
    }


# ---------------------------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------------------------
def create_strategy(db: Session, actor: Actor, data: schemas.StrategyCreate) -> Strategy:
    """Create a strategy and its version 1 (CANDIDATE) after guardrail validation."""
    project = None
    if data.project_id:
        project = load_project(db, actor, data.project_id, "strategy:create")
    else:
        actor.require("strategy:create")
    if data.kind not in {k.value for k in StrategyKind}:
        raise ValidationFailed(f"Unknown strategy kind '{data.kind}'")
    schema = parse_schema(data.parameter_schema)
    parameters = schema.with_defaults(data.parameters)
    enforce_guardrails(data.definition, parameters, schema)

    strategy = Strategy(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id if project is not None else None,
        project_id=project.id if project is not None else None,
        kind=data.kind,
        name=data.name.strip(),
        description=data.description,
        parameter_schema=schema.to_dict(),
        status="active",
        created_by_id=actor.user_id,
    )
    try:
        with db.begin_nested():
            db.add(strategy)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"A strategy named '{data.name}' already exists", code="strategy_name_exists") from exc
    version = insert_version(
        db,
        strategy,
        definition=data.definition,
        parameters=parameters,
        parent=None,
        created_by=actor_origin(actor),
        agent_run_id=actor.agent_run_id,
    )
    _event(
        db,
        actor,
        strategy,
        EventType.STRATEGY_CREATED,
        {"strategy_id": str(strategy.id), "kind": strategy.kind, "name": strategy.name, "version_id": str(version.id)},
    )
    audit(
        db,
        actor,
        AuditAction.STRATEGY_CREATED,
        "strategy",
        strategy.id,
        after={"kind": strategy.kind, "name": strategy.name, "project_id": strategy.project_id, "version": 1},
    )
    return strategy


def get_strategy_detail(db: Session, actor: Actor, strategy_id: uuid.UUID | str) -> schemas.StrategyDetailOut:
    strategy = load_strategy(db, actor, strategy_id, "strategy:read")
    current = db.get(StrategyVersion, strategy.current_version_id) if strategy.current_version_id else None
    count = db.scalar(select(func.count(StrategyVersion.id)).where(StrategyVersion.strategy_id == strategy.id)) or 0
    base = strategy_out(strategy).model_dump()
    return schemas.StrategyDetailOut(
        **base, current_version=version_out(current) if current is not None else None, version_count=int(count)
    )


def list_strategies(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    kind: str | None = None,
    project_id: uuid.UUID | str | None = None,
    status: str | None = None,
    sort: str | None = None,
) -> Page[schemas.StrategyOut]:
    actor.require("strategy:read")
    ensure_default_strategies(db, actor.organization_id)
    stmt = select(Strategy).where(Strategy.organization_id == actor.organization_id)
    if kind:
        if kind not in {k.value for k in StrategyKind}:
            raise ValidationFailed(f"Unknown strategy kind '{kind}'")
        stmt = stmt.where(Strategy.kind == kind)
    if project_id:
        stmt = stmt.where(Strategy.project_id == load_project(db, actor, project_id).id)
    if status:
        stmt = stmt.where(Strategy.status == status)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(Strategy.project_id.is_(None), Strategy.project_id.in_(visible)))
    stmt = stmt.order_by(sort_clause(Strategy, sort, ("created_at", "name", "kind"), default="name"), Strategy.id)
    return paginate(db, stmt, params, strategy_out)


# ---------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------
def latest_version(db: Session, strategy_id: uuid.UUID) -> StrategyVersion | None:
    return db.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version.desc())
        .limit(1)
    )


def create_version(
    db: Session, actor: Actor, strategy_id: uuid.UUID | str, data: schemas.StrategyVersionCreate
) -> StrategyVersion:
    """Create an immutable CANDIDATE version (guardrails checked against the schema and the parent)."""
    strategy = load_strategy(db, actor, strategy_id, "strategy:create")
    if strategy.status != "active":
        raise Conflict("The strategy is not active", code="strategy_inactive")
    parent: StrategyVersion | None
    if data.parent_version_id:
        parent = get_owned(db, StrategyVersion, data.parent_version_id, actor, label="Parent version")
        if parent.strategy_id != strategy.id:
            raise ValidationFailed("parent_version_id belongs to another strategy")
    else:
        parent = (
            db.get(StrategyVersion, strategy.current_version_id) if strategy.current_version_id else None
        ) or latest_version(db, strategy.id)
    definition = dict(data.definition) if data.definition is not None else dict(parent.definition if parent else {})
    schema = ParameterSchema.from_dict(strategy.parameter_schema)
    parameters = schema.with_defaults(data.parameters)
    enforce_guardrails(definition, parameters, schema, parent)
    existing = find_by_content(db, strategy.id, version_content_hash(definition, parameters))
    if existing is not None:
        raise Conflict(
            f"Version {existing.version} already has exactly this definition and parameters",
            code="strategy_version_exists",
            details={"version_id": str(existing.id)},
        )
    diff = parameter_diff(parent.parameters if parent else {}, parameters)
    origin = actor_origin(actor)
    history = [
        {
            "operator": "human_edit" if origin == "human" else f"{origin}_edit",
            "parents": [str(parent.id)] if parent else [],
            "changed_paths": diff["paths"],
            "rationale": data.rationale,
        }
    ]
    version = insert_version(
        db,
        strategy,
        definition=definition,
        parameters=parameters,
        parent=parent,
        created_by=origin,
        agent_run_id=actor.agent_run_id,
        mutation_history=history,
    )
    record_mutation(
        db,
        strategy,
        version,
        operator="human_edit" if origin == "human" else f"{origin}_edit",
        diff=diff,
        parent_version_id=parent.id if parent else None,
        rationale=data.rationale,
        generated_by="agent" if origin == "agent" else "human",
        agent_run_id=actor.agent_run_id,
    )
    _event(
        db,
        actor,
        strategy,
        EventType.STRATEGY_MUTATED,
        {
            "strategy_id": str(strategy.id),
            "version_ids": [str(version.id)],
            "operator": history[0]["operator"],
            "created_by": origin,
        },
        subject_type="strategy_version",
        subject_id=version.id,
    )
    audit(
        db,
        actor,
        "STRATEGY_VERSION_CREATED",
        "strategy_version",
        version.id,
        after={"strategy_id": strategy.id, "version": version.version, "changed": diff["paths"]},
    )
    return version


def get_version(db: Session, actor: Actor, version_id: uuid.UUID | str) -> StrategyVersion:
    version, _strategy = load_version(db, actor, version_id, "strategy:read")
    return version


def list_versions(
    db: Session,
    actor: Actor,
    strategy_id: uuid.UUID | str,
    params: PageParams,
    *,
    status: str | None = None,
    evolution_run_id: uuid.UUID | str | None = None,
) -> Page[schemas.StrategyVersionOut]:
    strategy = load_strategy(db, actor, strategy_id, "strategy:read")
    stmt = select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id)
    if status:
        if status.upper() not in StrategyStatus.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(StrategyStatus.__members__)}")
        stmt = stmt.where(StrategyVersion.status == status.upper())
    if evolution_run_id:
        stmt = stmt.where(StrategyVersion.evolution_run_id == parse_uuid(evolution_run_id, "Evolution run"))
    stmt = stmt.order_by(StrategyVersion.version.desc())
    return paginate(db, stmt, params, version_out)


def set_version_status(version: StrategyVersion, target: str) -> None:
    """Apply one lifecycle transition (validated) and its timestamps."""
    assert_transition("strategy", version.status, target)
    version.status = target
    if target == StrategyStatus.RETIRED:
        version.retired_at = utcnow()


def transition_version(
    db: Session, actor: Actor, version_id: uuid.UUID | str, target: str, *, reason: str
) -> StrategyVersion:
    """Manual lifecycle change (EXPERIMENTAL / SURVIVING / RETIRED). Promotion and rollback have their own paths."""
    version, strategy = load_version(db, actor, version_id, "strategy:evolve")
    target = target.upper()
    if target not in MANUAL_TARGETS:
        raise ValidationFailed(
            "Only EXPERIMENTAL, SURVIVING and RETIRED can be set directly; use promote/rollback for the others"
        )
    if version.status == StrategyStatus.PROMOTED:
        raise Conflict(
            "A PROMOTED version changes only through promotion of another version or a rollback",
            code="invalid_state_transition",
        )
    if target in (StrategyStatus.EXPERIMENTAL, StrategyStatus.SURVIVING):
        evaluated = db.scalar(
            select(func.count(StrategyEvaluation.id)).where(StrategyEvaluation.strategy_version_id == version.id)
        )
        if not evaluated:
            raise Conflict(
                "The version has no benchmark evaluation yet; evaluate it before advancing it",
                code="strategy_not_evaluated",
            )
    before = version.status
    set_version_status(version, target)
    db.flush()
    audit(
        db,
        actor,
        "STRATEGY_VERSION_TRANSITIONED",
        "strategy_version",
        version.id,
        before={"status": before},
        after={"status": target, "reason": reason, "strategy_id": strategy.id},
    )
    return version


def list_evaluations(
    db: Session, actor: Actor, version_id: uuid.UUID | str, params: PageParams
) -> Page[schemas.StrategyEvaluationOut]:
    version, _ = load_version(db, actor, version_id, "strategy:read")
    stmt = (
        select(StrategyEvaluation)
        .where(StrategyEvaluation.strategy_version_id == version.id)
        .order_by(StrategyEvaluation.created_at.desc(), StrategyEvaluation.id)
    )
    return paginate(db, stmt, params, schemas.StrategyEvaluationOut.model_validate)


def list_mutations(
    db: Session, actor: Actor, version_id: uuid.UUID | str, params: PageParams
) -> Page[schemas.StrategyMutationOut]:
    """Mutations that produced this version and mutations that used it as a parent."""
    version, _ = load_version(db, actor, version_id, "strategy:read")
    stmt = (
        select(StrategyMutation)
        .where(
            or_(
                StrategyMutation.child_version_id == version.id,
                StrategyMutation.parent_version_id == version.id,
                StrategyMutation.second_parent_version_id == version.id,
            )
        )
        .order_by(StrategyMutation.created_at, StrategyMutation.id)
    )
    return paginate(db, stmt, params, schemas.StrategyMutationOut.model_validate)


def _node(version: StrategyVersion) -> schemas.LineageNodeOut:
    return schemas.LineageNodeOut(
        id=str(version.id),
        version=version.version,
        status=version.status,
        parent_version_id=str(version.parent_version_id) if version.parent_version_id else None,
        created_by=version.created_by,
        generation=version.generation,
        evolution_run_id=str(version.evolution_run_id) if version.evolution_run_id else None,
    )


def version_lineage(db: Session, actor: Actor, version_id: uuid.UUID | str) -> schemas.StrategyLineageOut:
    version, _ = load_version(db, actor, version_id, "strategy:read")
    ancestors: list[schemas.LineageNodeOut] = []
    seen = {version.id}
    cursor = version.parent_version_id
    while cursor is not None and len(ancestors) < MAX_LINEAGE_DEPTH and cursor not in seen:
        parent = db.get(StrategyVersion, cursor)
        if parent is None or parent.organization_id != actor.organization_id:
            break
        ancestors.append(_node(parent))
        seen.add(parent.id)
        cursor = parent.parent_version_id
    children = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.parent_version_id == version.id)
        .order_by(StrategyVersion.version)
        .limit(500)
    ).all()
    mutations = db.scalars(
        select(StrategyMutation)
        .where(StrategyMutation.child_version_id == version.id)
        .order_by(StrategyMutation.created_at, StrategyMutation.id)
    ).all()
    return schemas.StrategyLineageOut(
        version_id=str(version.id),
        ancestors=ancestors,
        children=[_node(c) for c in children],
        mutations=[schemas.StrategyMutationOut.model_validate(m) for m in mutations],
    )


# ---------------------------------------------------------------------------------------------
# Active version resolution
# ---------------------------------------------------------------------------------------------
ActiveScope = Literal["mission", "project", "organization", "none"]


def _promoted_for(
    db: Session,
    organization_id: uuid.UUID,
    kind: str,
    *,
    project_id: uuid.UUID | None,
    exclude_strategy_id: uuid.UUID | None = None,
) -> StrategyVersion | None:
    scope = Strategy.project_id == project_id if project_id is not None else Strategy.project_id.is_(None)
    stmt = (
        select(StrategyVersion)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(
            Strategy.organization_id == organization_id,
            Strategy.kind == kind,
            Strategy.status == "active",
            scope,
            StrategyVersion.status == StrategyStatus.PROMOTED,
        )
    )
    if exclude_strategy_id is not None:
        stmt = stmt.where(Strategy.id != exclude_strategy_id)
    return db.scalar(stmt.order_by(StrategyVersion.promoted_at.desc().nulls_last(), StrategyVersion.id).limit(1))


def resolve_active_strategy_version(
    db: Session,
    organization_id: uuid.UUID,
    kind: str,
    *,
    project_id: uuid.UUID | str | None = None,
    mission: Mission | None = None,
    exclude_strategy_id: uuid.UUID | None = None,
) -> tuple[StrategyVersion | None, ActiveScope]:
    """The version in force for ``kind``: mission pin > project-scoped promoted > organization promoted."""
    if kind not in {k.value for k in StrategyKind}:
        raise ValidationFailed(f"Unknown strategy kind '{kind}'")
    if mission is not None and mission.organization_id != organization_id:
        raise NotFound("Mission not found")
    if mission is not None and exclude_strategy_id is None:
        pinned = (mission.strategy_pins or {}).get(kind)
        if pinned:
            try:
                pinned_id = uuid.UUID(str(pinned))
            except ValueError:
                pinned_id = None
            version = db.get(StrategyVersion, pinned_id) if pinned_id else None
            if (
                version is not None
                and version.organization_id == organization_id
                and version.status != StrategyStatus.ROLLED_BACK
            ):
                strategy = db.get(Strategy, version.strategy_id)
                if strategy is not None and strategy.kind == kind:
                    return version, "mission"
    pid: uuid.UUID | None = None
    if project_id is not None:
        pid = parse_uuid(project_id, "Project")
    elif mission is not None:
        pid = mission.project_id

    if pid is not None:
        found = _promoted_for(db, organization_id, kind, project_id=pid, exclude_strategy_id=exclude_strategy_id)
        if found is not None:
            return found, "project"
    found = _promoted_for(db, organization_id, kind, project_id=None, exclude_strategy_id=exclude_strategy_id)
    if found is None and ensure_default_strategies(db, organization_id):
        found = _promoted_for(db, organization_id, kind, project_id=None, exclude_strategy_id=exclude_strategy_id)
    return (found, "organization") if found is not None else (None, "none")


def incumbent_version(db: Session, strategy: Strategy) -> StrategyVersion | None:
    """The version a candidate of ``strategy`` must beat: the strategy's PROMOTED version, or — before its
    first promotion — the version currently in force for its kind and scope (another strategy)."""
    if strategy.current_version_id is not None:
        current = db.get(StrategyVersion, strategy.current_version_id)
        if current is not None and current.status == StrategyStatus.PROMOTED:
            return current
    promoted = db.scalar(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id, StrategyVersion.status == StrategyStatus.PROMOTED)
        .order_by(StrategyVersion.promoted_at.desc().nulls_last())
        .limit(1)
    )
    if promoted is not None:
        return promoted
    version, _scope = resolve_active_strategy_version(
        db, strategy.organization_id, strategy.kind, project_id=strategy.project_id, exclude_strategy_id=strategy.id
    )
    return version


def get_active_strategy_version(
    db: Session,
    organization_id: uuid.UUID,
    kind: str,
    *,
    project_id: uuid.UUID | str | None = None,
    mission: Mission | None = None,
) -> StrategyVersion | None:
    """Mission pin > project-scoped promoted > organization promoted > ``None`` (seeds org defaults lazily)."""
    version, _scope = resolve_active_strategy_version(db, organization_id, kind, project_id=project_id, mission=mission)
    return version


def active_strategy(
    db: Session,
    actor: Actor,
    kind: str,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
) -> schemas.ActiveStrategyOut:
    actor.require("strategy:read")
    mission = None
    if mission_id:
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        load_project(db, actor, mission.project_id)
    if project_id:
        load_project(db, actor, project_id)
    version, scope = resolve_active_strategy_version(
        db, actor.organization_id, kind, project_id=project_id, mission=mission
    )
    return schemas.ActiveStrategyOut(
        kind=kind,
        version=version_out(version) if version is not None else None,
        strategy_id=str(version.strategy_id) if version is not None else None,
        scope=scope,
    )
