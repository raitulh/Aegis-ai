"""Agent role registry — the contract between the agent runtime and the domain contexts.

A role declares *what* an agent of that role is for (LLM task type, prompt, structured output schema,
tools, permission ceiling) and *how its output is applied* (``apply``) through the owning context's
services. Agents only ever PROPOSE: ``apply`` persists proposals (hypotheses as ``GENERATED``,
experiment designs through the design validator, diagnoses paired with deterministic classification…);
the platform — not the model — decides permissions, execution, measurement, evaluation, verification
and promotion.

Each domain package registers its roles in ``aegis_api.lab.<context>.agent_roles``::

    register_role(RoleSpec(
        role=AgentRole.HYPOTHESIS, task_type="hypothesis_generation", prompt_key="hypothesis.generate",
        output_model=HypothesisProposalBatch, default_tools=("paper_search", "memory_search"),
        default_permissions=frozenset({"hypothesis:create", "memory:read", "research:read"}),
        build_context=_context, apply=_apply,
    ))
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aegis_api.lab.core.actor import Actor
from aegis_api.lab.models import AgentRun
from engines.lab.states import AgentRole, AutonomyLevel

log = structlog.get_logger("aegis.lab.agents")

# (db, actor, run) -> variables for prompt rendering. Untrusted content (papers, tool output, memory)
# must be returned under keys listed in ``untrusted_context_keys`` so the prompt assembler wraps it as data.
ContextBuilder = Callable[[Session, Actor, AgentRun], dict[str, Any]]
# (db, actor, run, validated_output) -> summary dict of what was persisted (ids etc.)
OutputApplier = Callable[[Session, Actor, AgentRun, BaseModel], dict[str, Any]]


@dataclass(frozen=True)
class RoleSpec:
    role: AgentRole
    task_type: str
    prompt_key: str
    output_model: type[BaseModel]
    build_context: ContextBuilder
    apply: OutputApplier
    description: str = ""
    default_tools: tuple[str, ...] = ()
    default_permissions: frozenset[str] = field(default_factory=frozenset)
    # Minimum mission autonomy for this role to run WITHOUT a human explicitly starting it.
    min_autonomy_level: str = AutonomyLevel.L1_RESEARCH_AUTOMATION
    untrusted_context_keys: tuple[str, ...] = ()
    tier: str = "default"  # fast|default|reasoning
    temperature: float = 0.2
    max_steps: int = 6


ROLE_REGISTRY: dict[str, RoleSpec] = {}


def register_role(spec: RoleSpec) -> RoleSpec:
    existing = ROLE_REGISTRY.get(spec.role)
    if existing is not None and existing is not spec:
        raise ValueError(f"Role {spec.role} is already registered")
    ROLE_REGISTRY[spec.role] = spec
    return spec


ROLE_MODULES: tuple[str, ...] = (
    "aegis_api.lab.missions.agent_roles",
    "aegis_api.lab.research.agent_roles",
    "aegis_api.lab.knowledge.agent_roles",
    "aegis_api.lab.hypotheses.agent_roles",
    "aegis_api.lab.experiments.agent_roles",
    "aegis_api.lab.failures.agent_roles",
    "aegis_api.lab.strategies.agent_roles",
    "aegis_api.lab.verification.agent_roles",
)

# Which package owns which role (documentation + completeness test).
ROLE_OWNERS: dict[AgentRole, str] = {
    AgentRole.QUEST: "missions",
    AgentRole.PLANNER: "missions",
    AgentRole.LITERATURE: "research",
    AgentRole.KNOWLEDGE: "knowledge",
    AgentRole.HYPOTHESIS: "hypotheses",
    AgentRole.HYPOTHESIS_CRITIC: "hypotheses",
    AgentRole.EXPERIMENT_DESIGNER: "experiments",
    AgentRole.CODING: "experiments",
    AgentRole.SIMULATION: "experiments",
    AgentRole.DATA_ANALYST: "experiments",
    AgentRole.STATISTICAL_ANALYST: "experiments",
    AgentRole.FAILURE_ANALYZER: "failures",
    AgentRole.EVOLUTION: "strategies",
    AgentRole.REPRODUCTION: "verification",
    AgentRole.VERIFIER: "verification",
    AgentRole.SCIENTIFIC_REVIEWER: "verification",
    AgentRole.REPORT: "verification",
}


def load_roles(strict: bool = True) -> dict[str, RoleSpec]:
    for module in ROLE_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module or strict:
                raise
            log.warning("role_module_missing", module=module)
    return ROLE_REGISTRY


def get_role(role: str) -> RoleSpec:
    if role not in ROLE_REGISTRY:
        load_roles(strict=False)
    try:
        return ROLE_REGISTRY[role]
    except KeyError as exc:
        raise KeyError(f"Agent role '{role}' is not registered") from exc
