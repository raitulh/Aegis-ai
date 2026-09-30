"""Agent registry: configured agents, immutable versions, default (system) agents, run/step/message queries.

An agent's configuration (model policy, prompt, tools, permission ceiling, budget, timeout, autonomy ceiling)
lives in immutable ``agent_versions``; changing an agent always creates a new version and moves
``current_version_id``. Permission ceilings may never contain human-only permissions, tools must exist in the
tool registry, the autonomy ceiling may not exceed the organization's and the prompt key must resolve.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.agents.roles import RoleSpec, get_role, load_roles
from aegis_api.lab.agents.schemas import (
    AgentBudget,
    AgentCreate,
    AgentMessageOut,
    AgentOut,
    AgentRunOut,
    AgentStepOut,
    AgentVersionCreate,
    AgentVersionOut,
    ModelPolicy,
    ModelProvenance,
)
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import get_org_settings
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_by_id, paginate_keyset
from aegis_api.lab.models import Agent, AgentMessage, AgentRun, AgentStep, AgentVersion
from aegis_api.lab.tools.registry import is_known_tool_name
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.rbac import ALL_LAB_PERMISSIONS, HUMAN_ONLY_PERMISSIONS
from engines.lab import autonomy as autonomy_engine
from engines.lab.states import AUTONOMY_RANK, AgentRole, AgentRunStatus, AutonomyLevel, StrategyKind

log = structlog.get_logger("aegis.lab.agents")

DEFAULT_MAX_COST_USD = 2.0
DEFAULT_MAX_TOOL_CALLS = 20
AGENT_PERMISSION_CEILING = ALL_LAB_PERMISSIONS - HUMAN_ONLY_PERMISSIONS

# Which strategy family steers each role (recorded on runs for reproducibility).
ROLE_STRATEGY_KIND: dict[str, str] = {
    AgentRole.LITERATURE: StrategyKind.SEARCH,
    AgentRole.KNOWLEDGE: StrategyKind.SEARCH,
    AgentRole.HYPOTHESIS: StrategyKind.HYPOTHESIS,
    AgentRole.HYPOTHESIS_CRITIC: StrategyKind.HYPOTHESIS,
    AgentRole.EXPERIMENT_DESIGNER: StrategyKind.EXPERIMENT,
    AgentRole.CODING: StrategyKind.EXPERIMENT,
    AgentRole.SIMULATION: StrategyKind.EXPERIMENT,
    AgentRole.PLANNER: StrategyKind.AGENT_TOPOLOGY,
}


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------
_CONFIG_FIELDS = (
    "model_policy",
    "prompt_key",
    "prompt_version",
    "strategy_kind",
    "tools",
    "memory_scope",
    "permissions",
    "budget",
    "rate_limit_per_min",
    "timeout_seconds",
    "max_autonomy_level",
    "evaluation_policy",
)


def config_hash(config: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of the version configuration (tools/permissions order-insensitive)."""
    canonical = {k: config.get(k) for k in _CONFIG_FIELDS}
    canonical["tools"] = sorted(canonical.get("tools") or [])
    canonical["permissions"] = sorted(canonical.get("permissions") or [])
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _lower_level(a: str, b: str) -> str:
    return min((a, b), key=lambda level: AUTONOMY_RANK.get(level, 0))


def default_config(spec: RoleSpec, *, org_ceiling: str) -> dict[str, Any]:
    """Version-1 configuration derived from a role's defaults (human-only permissions never granted)."""
    settings = get_settings()
    tools = [t for t in spec.default_tools if is_known_tool_name(t)]
    unknown = sorted(set(spec.default_tools) - set(tools))
    if unknown:
        log.warning("role_default_tools_unknown", role=str(spec.role), tools=unknown)
    budget = AgentBudget(
        max_steps=max(1, min(int(spec.max_steps or settings.agent_default_max_steps), 50)),
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        max_cost_usd=DEFAULT_MAX_COST_USD,
    )
    return {
        "model_policy": ModelPolicy(task_type=spec.task_type, tier=spec.tier, temperature=spec.temperature).model_dump(
            exclude_none=True
        ),
        "prompt_key": spec.prompt_key,
        "prompt_version": None,
        "strategy_kind": ROLE_STRATEGY_KIND.get(str(spec.role)),
        "tools": tools,
        "memory_scope": {},
        "permissions": sorted(set(spec.default_permissions) & AGENT_PERMISSION_CEILING),
        "budget": budget.model_dump(exclude_none=True),
        "rate_limit_per_min": 30,
        "timeout_seconds": int(settings.agent_default_timeout_seconds),
        "max_autonomy_level": org_ceiling,
        "evaluation_policy": {},
    }


def _merge(base: dict[str, Any], data: AgentVersionCreate) -> dict[str, Any]:
    merged = dict(base)
    fields = data.model_fields_set
    if data.model_policy is not None:
        merged["model_policy"] = data.model_policy.model_dump(exclude_none=True)
    if data.prompt_key is not None:
        merged["prompt_key"] = data.prompt_key
    if "prompt_version" in fields:
        merged["prompt_version"] = data.prompt_version
    if data.tools is not None:
        merged["tools"] = list(data.tools)
    if data.permissions is not None:
        merged["permissions"] = sorted(set(data.permissions))
    if data.budget is not None:
        merged["budget"] = data.budget.model_dump(exclude_none=True)
    if data.timeout_seconds is not None:
        merged["timeout_seconds"] = data.timeout_seconds
    if data.max_autonomy_level is not None:
        merged["max_autonomy_level"] = str(data.max_autonomy_level)
    if data.memory_scope is not None:
        merged["memory_scope"] = dict(data.memory_scope)
    if data.rate_limit_per_min is not None:
        merged["rate_limit_per_min"] = data.rate_limit_per_min
    if data.evaluation_policy is not None:
        merged["evaluation_policy"] = dict(data.evaluation_policy)
    if "strategy_kind" in fields:
        merged["strategy_kind"] = data.strategy_kind
    return merged


def validate_config(db: Session, organization_id: uuid.UUID, config: dict[str, Any]) -> None:
    """Raise ``ValidationFailed`` listing every problem with an agent version configuration."""
    from aegis_api.lab.llm.schemas import TaskType
    from aegis_api.lab.prompts.registry import resolve_template

    problems: list[str] = []
    permissions = set(config.get("permissions") or [])
    human_only = sorted(permissions & HUMAN_ONLY_PERMISSIONS)
    if human_only:
        problems.append(f"human-only permissions cannot be granted to agents: {', '.join(human_only)}")
    unknown_perms = sorted(permissions - ALL_LAB_PERMISSIONS - HUMAN_ONLY_PERMISSIONS)
    if unknown_perms:
        problems.append(f"unknown permissions: {', '.join(unknown_perms)}")
    unknown_tools = sorted(t for t in config.get("tools") or [] if not is_known_tool_name(t))
    if unknown_tools:
        problems.append(f"unknown tools: {', '.join(unknown_tools)}")
    org = get_org_settings(db, organization_id)
    ceiling = autonomy_engine.normalize_level(org.max_autonomy_level or AutonomyLevel.L0_ASSISTED)
    level = config.get("max_autonomy_level")
    try:
        normalized = autonomy_engine.normalize_level(str(level))
    except ValueError:
        problems.append(f"unknown autonomy level '{level}'")
    else:
        config["max_autonomy_level"] = normalized
        if AUTONOMY_RANK[normalized] > AUTONOMY_RANK[ceiling]:
            problems.append(f"max_autonomy_level {normalized} exceeds the organization ceiling {ceiling}")
    task_type = (config.get("model_policy") or {}).get("task_type")
    if task_type is not None:
        try:
            TaskType(task_type)
        except ValueError:
            problems.append(f"unknown model_policy.task_type '{task_type}'")
    kind = config.get("strategy_kind")
    if kind is not None and kind not in {k.value for k in StrategyKind}:
        problems.append(f"unknown strategy_kind '{kind}'")
    try:
        AgentBudget.model_validate(config.get("budget") or {})
    except ValueError as exc:
        problems.append(f"invalid budget: {exc}")
    key = config.get("prompt_key")
    if not key:
        problems.append("prompt_key is required")
    else:
        try:
            resolve_template(db, organization_id, str(key), version=config.get("prompt_version"))
        except NotFound:
            suffix = f" version {config.get('prompt_version')}" if config.get("prompt_version") else ""
            problems.append(f"prompt template '{key}'{suffix} does not exist")
    if problems:
        raise ValidationFailed("Invalid agent configuration", details={"problems": problems})


def _new_version(
    db: Session, agent: Agent, config: dict[str, Any], number: int, created_by_id: uuid.UUID | None
) -> AgentVersion:
    version = AgentVersion(
        organization_id=agent.organization_id,
        agent_id=agent.id,
        version=number,
        model_policy=dict(config.get("model_policy") or {}),
        prompt_key=str(config["prompt_key"]),
        prompt_version=config.get("prompt_version"),
        strategy_kind=config.get("strategy_kind"),
        strategy_version_id=None,
        tools=list(config.get("tools") or []),
        memory_scope=dict(config.get("memory_scope") or {}),
        permissions=sorted(set(config.get("permissions") or [])),
        budget=dict(config.get("budget") or {}),
        rate_limit_per_min=int(config.get("rate_limit_per_min") or 30),
        timeout_seconds=int(config.get("timeout_seconds") or get_settings().agent_default_timeout_seconds),
        max_autonomy_level=str(config.get("max_autonomy_level") or AutonomyLevel.L1_RESEARCH_AUTOMATION),
        evaluation_policy=dict(config.get("evaluation_policy") or {}),
        config_hash=config_hash(config),
        created_by_id=created_by_id,
    )
    db.add(version)
    db.flush()
    agent.current_version_id = version.id
    db.flush()
    return version


def version_config(version: AgentVersion) -> dict[str, Any]:
    return {field: getattr(version, field) for field in _CONFIG_FIELDS}


# ---------------------------------------------------------------------------------------------
# Default agents
# ---------------------------------------------------------------------------------------------
def ensure_default_agents(db: Session, organization_id: uuid.UUID) -> list[Agent]:
    """Create (once) a system agent + version 1 for every registered role. Idempotent; returns system agents."""
    roles = load_roles(strict=False)
    advisory_xact_lock(db, f"agents:defaults:{organization_id}")
    org = get_org_settings(db, organization_id)
    ceiling = org.max_autonomy_level or AutonomyLevel.L1_RESEARCH_AUTOMATION
    existing = {
        a.role: a
        for a in db.scalars(
            select(Agent).where(Agent.organization_id == organization_id, Agent.is_system.is_(True))
        ).all()
    }
    taken = set(db.scalars(select(Agent.name).where(Agent.organization_id == organization_id)).all())
    system_actor = Actor.system(organization_id, "system:agent-defaults")
    agents: list[Agent] = []
    for role_name in sorted(roles):
        spec = roles[role_name]
        agent = existing.get(str(spec.role))
        if agent is not None and agent.current_version_id is not None:
            agents.append(agent)
            continue
        config = default_config(spec, org_ceiling=ceiling)
        if agent is None:
            name = str(spec.role) if str(spec.role) not in taken else f"{spec.role} (system)"
            agent = Agent(
                organization_id=organization_id,
                role=str(spec.role),
                name=name[:120],
                description=spec.description or f"Built-in {spec.role}",
                status="active",
                is_system=True,
            )
            db.add(agent)
            db.flush()
            taken.add(name)
        version = _new_version(db, agent, config, 1, None)
        audit(
            db,
            system_actor,
            AuditAction.AGENT_CONFIGURED,
            "agent",
            agent.id,
            after={"operation": "default_created", "role": agent.role, "version": 1, "config_hash": version.config_hash},
        )
        agents.append(agent)
    return agents


def resolve_agent(
    db: Session, actor: Actor, role: str, *, agent_id: uuid.UUID | str | None = None
) -> tuple[Agent, AgentVersion]:
    """The agent (explicit id, or the role's system agent — created on demand) and its current version."""
    if agent_id is not None:
        agent = get_owned(db, Agent, agent_id, actor, label="Agent")
        if agent.role != role:
            raise ValidationFailed(f"Agent {agent.id} has role {agent.role}, not {role}")
    else:
        agent_or_none = db.scalar(
            select(Agent).where(
                Agent.organization_id == actor.organization_id, Agent.role == role, Agent.is_system.is_(True)
            )
        )
        if agent_or_none is None or agent_or_none.current_version_id is None:
            ensure_default_agents(db, actor.organization_id)
            agent_or_none = db.scalar(
                select(Agent).where(
                    Agent.organization_id == actor.organization_id, Agent.role == role, Agent.is_system.is_(True)
                )
            )
        if agent_or_none is None:
            raise ValidationFailed(f"No agent is configured for role {role}")
        agent = agent_or_none
    if agent.status != "active":
        raise Conflict(f"Agent '{agent.name}' is {agent.status}", code="agent_inactive")
    version = db.get(AgentVersion, agent.current_version_id) if agent.current_version_id else None
    if version is None:
        raise Conflict(f"Agent '{agent.name}' has no configuration version", code="agent_unconfigured")
    return agent, version


# ---------------------------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------------------------
def _role_spec(role: str) -> RoleSpec:
    try:
        return get_role(role)
    except KeyError as exc:
        raise ValidationFailed(f"Agent role '{role}' is not available in this deployment") from exc


def create_agent(db: Session, actor: Actor, data: AgentCreate) -> Agent:
    """Create a configured agent (version 1). Requires ``agent:manage``; counts against ``max_agents``."""
    actor.require("agent:manage")
    from aegis_api.lab.governance.quotas import check_quota

    spec = _role_spec(str(data.role))
    check_quota(db, actor.organization_id, "max_agents")
    exists = db.scalar(select(Agent.id).where(Agent.organization_id == actor.organization_id, Agent.name == data.name))
    if exists is not None:
        raise Conflict(f"An agent named '{data.name}' already exists", code="agent_name_taken")
    org = get_org_settings(db, actor.organization_id)
    base = default_config(spec, org_ceiling=org.max_autonomy_level or AutonomyLevel.L1_RESEARCH_AUTOMATION)
    config = _merge(base, data.config)
    validate_config(db, actor.organization_id, config)
    agent = Agent(
        organization_id=actor.organization_id,
        role=str(data.role),
        name=data.name,
        description=data.description,
        status="active",
        is_system=False,
        created_by_id=actor.user_id,
    )
    db.add(agent)
    db.flush()
    version = _new_version(db, agent, config, 1, actor.user_id)
    audit(
        db,
        actor,
        AuditAction.AGENT_CONFIGURED,
        "agent",
        agent.id,
        after={"operation": "created", "role": agent.role, "name": agent.name, **_audit_config(version)},
    )
    return agent


def _audit_config(version: AgentVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "config_hash": version.config_hash,
        "tools": list(version.tools or []),
        "permissions": list(version.permissions or []),
        "max_autonomy_level": version.max_autonomy_level,
        "prompt_key": version.prompt_key,
    }


def create_agent_version(
    db: Session, actor: Actor, agent_id: uuid.UUID | str, data: AgentVersionCreate
) -> AgentVersion:
    """Append an immutable version (fields not given are copied from the current version). A configuration
    identical to the current version returns the current version."""
    actor.require("agent:manage")
    agent = get_owned(db, Agent, agent_id, actor, label="Agent")
    if agent.status == "archived":
        raise Conflict("Archived agents cannot be reconfigured", code="agent_archived")
    advisory_xact_lock(db, f"agent-version:{agent.id}")
    current = db.get(AgentVersion, agent.current_version_id) if agent.current_version_id else None
    if current is not None:
        base = version_config(current)
    else:
        org = get_org_settings(db, actor.organization_id)
        base = default_config(
            _role_spec(agent.role), org_ceiling=org.max_autonomy_level or AutonomyLevel.L1_RESEARCH_AUTOMATION
        )
    config = _merge(base, data)
    validate_config(db, actor.organization_id, config)
    if current is not None and config_hash(config) == current.config_hash:
        return current
    latest = db.scalar(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id))
    version = _new_version(db, agent, config, int(latest or 0) + 1, actor.user_id)
    audit(
        db,
        actor,
        AuditAction.AGENT_CONFIGURED,
        "agent",
        agent.id,
        before=_audit_config(current) if current is not None else None,
        after={"operation": "version_created", **_audit_config(version)},
    )
    return version


# ---------------------------------------------------------------------------------------------
# Queries & mappers
# ---------------------------------------------------------------------------------------------
def version_out(version: AgentVersion) -> AgentVersionOut:
    return AgentVersionOut.model_validate(version)


def agent_out(agent: Agent, version: AgentVersion | None = None) -> AgentOut:
    return AgentOut(
        id=str(agent.id),
        role=agent.role,
        name=agent.name,
        description=agent.description,
        status=agent.status,
        is_system=agent.is_system,
        current_version_id=str(agent.current_version_id) if agent.current_version_id else None,
        current_version=version_out(version) if version is not None else None,
        created_by_id=str(agent.created_by_id) if agent.created_by_id else None,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def get_agent(db: Session, actor: Actor, agent_id: uuid.UUID | str) -> tuple[Agent, AgentVersion | None]:
    actor.require("agent:read")
    agent = get_owned(db, Agent, agent_id, actor, label="Agent")
    version = db.get(AgentVersion, agent.current_version_id) if agent.current_version_id else None
    return agent, version


def list_agents(
    db: Session, actor: Actor, params: PageParams, *, role: str | None = None, include_archived: bool = False
) -> Page[AgentOut]:
    actor.require("agent:read")
    stmt = select(Agent).where(Agent.organization_id == actor.organization_id)
    if role:
        stmt = stmt.where(Agent.role == role)
    if not include_archived:
        stmt = stmt.where(Agent.status != "archived")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Agent.is_system.desc(), Agent.role, Agent.name).limit(params.page_size).offset(params.offset)
    ).all()
    version_ids = [a.current_version_id for a in rows if a.current_version_id]
    versions = (
        {v.id: v for v in db.scalars(select(AgentVersion).where(AgentVersion.id.in_(version_ids))).all()}
        if version_ids
        else {}
    )
    items = [agent_out(a, versions.get(a.current_version_id) if a.current_version_id else None) for a in rows]
    return Page.build(items, int(total), params)


def list_versions(db: Session, actor: Actor, agent_id: uuid.UUID | str, params: PageParams) -> Page[AgentVersionOut]:
    actor.require("agent:read")
    agent = get_owned(db, Agent, agent_id, actor, label="Agent")
    base = select(AgentVersion).where(AgentVersion.agent_id == agent.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(AgentVersion.version.desc()).limit(params.page_size).offset(params.offset)
    ).all()
    return Page.build([version_out(v) for v in rows], int(total), params)


def run_out(run: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        id=str(run.id),
        agent_id=str(run.agent_id),
        agent_version_id=str(run.agent_version_id),
        role=run.role,
        project_id=str(run.project_id) if run.project_id else None,
        mission_id=str(run.mission_id) if run.mission_id else None,
        parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
        workflow_run_id=str(run.workflow_run_id) if run.workflow_run_id else None,
        status=run.status,
        status_reason=run.status_reason,
        triggered_by=run.triggered_by,
        input=dict(run.input or {}),
        output=run.output,
        error_code=run.error_code,
        error=run.error,
        granted_permissions=list(run.granted_permissions or []),
        autonomy_level=run.autonomy_level,
        tools_used=list(run.tools_used or []),
        input_tokens=run.input_tokens or 0,
        output_tokens=run.output_tokens or 0,
        cost_usd=run.cost_usd or 0,
        step_count=run.step_count or 0,
        tool_call_count=run.tool_call_count or 0,
        deadline_at=run.deadline_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        trace_id=run.trace_id,
        created_by_id=str(run.created_by_id) if run.created_by_id else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
        provenance=ModelProvenance(
            prompt_template_id=str(run.prompt_template_id) if run.prompt_template_id else None,
            prompt_key=run.prompt_key,
            prompt_version=run.prompt_version,
            prompt_hash=run.prompt_hash,
            provider=run.provider,
            model=run.model,
            model_version=run.model_version,
            model_config_snapshot=dict(run.model_config_snapshot or {}),
            strategy_version_id=str(run.strategy_version_id) if run.strategy_version_id else None,
            agent_version_id=str(run.agent_version_id),
        ),
    )


def get_run(db: Session, actor: Actor, run_id: uuid.UUID | str, *, permission: str = "agent:read") -> AgentRun:
    run = get_owned(db, AgentRun, run_id, actor, label="Agent run")
    if run.project_id is not None:
        load_project(db, actor, run.project_id, permission)
    else:
        actor.require(permission)
    return run


def list_runs(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    mission_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    role: str | None = None,
    status: str | None = None,
    parent_run_id: uuid.UUID | None = None,
) -> CursorPage[AgentRunOut]:
    actor.require("agent:read")
    stmt = select(AgentRun).where(AgentRun.organization_id == actor.organization_id)
    if project_id is not None:
        load_project(db, actor, project_id, "agent:read")
        stmt = stmt.where(AgentRun.project_id == project_id)
    else:
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(or_(AgentRun.project_id.is_(None), AgentRun.project_id.in_(visible)))
    if mission_id is not None:
        stmt = stmt.where(AgentRun.mission_id == mission_id)
    if role:
        stmt = stmt.where(AgentRun.role == role)
    if status:
        if status.upper() not in {s.value for s in AgentRunStatus}:
            raise ValidationFailed(f"Unknown agent run status '{status}'")
        stmt = stmt.where(AgentRun.status == status.upper())
    if parent_run_id is not None:
        stmt = stmt.where(AgentRun.parent_run_id == parent_run_id)
    return paginate_keyset(db, stmt, params, time_col=AgentRun.created_at, id_col=AgentRun.id, mapper=run_out)


def step_out(step: AgentStep) -> AgentStepOut:
    return AgentStepOut.model_validate(step)


def list_steps(db: Session, actor: Actor, run_id: uuid.UUID | str, params: CursorParams) -> CursorPage[AgentStepOut]:
    run = get_run(db, actor, run_id)
    stmt = select(AgentStep).where(AgentStep.agent_run_id == run.id)
    return paginate_by_id(db, stmt, params, id_col=AgentStep.seq, mapper=step_out)


def message_out(message: AgentMessage) -> AgentMessageOut:
    return AgentMessageOut.model_validate(message)


def list_messages(
    db: Session, actor: Actor, run_id: uuid.UUID | str, params: CursorParams
) -> CursorPage[AgentMessageOut]:
    run = get_run(db, actor, run_id)
    stmt = select(AgentMessage).where(
        AgentMessage.organization_id == actor.organization_id,
        or_(AgentMessage.sender_run_id == run.id, AgentMessage.receiver_run_id == run.id),
    )
    return paginate_keyset(
        db, stmt, params, time_col=AgentMessage.created_at, id_col=AgentMessage.id, mapper=message_out,
        descending=False,
    )


def lower_autonomy(a: str, b: str) -> str:
    return _lower_level(a, b)
