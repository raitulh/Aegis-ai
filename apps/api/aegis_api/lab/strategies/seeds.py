"""Built-in default strategies — one per :class:`~engines.lab.states.StrategyKind`.

Each default is an organization-level strategy (``name = "default-<kind>"``) whose version 1 is PROMOTED and
``created_by = "seed"``. Its ``parameter_schema`` is the guardrail for everything evolution or agents may
later propose: only the declared parameters, only within their declared bounds/choices. Nothing here names a
permission, tool allow-list, secret, network or policy setting — strategies change behaviour, never authority.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.models import Strategy, StrategyVersion
from engines.lab.evolution import StrategyGuardrails
from engines.lab.evolution.types import canonical_hash
from engines.lab.states import StrategyKind, StrategyStatus

log = structlog.get_logger("aegis.lab.strategies")

DEFAULT_NAME_PREFIX = "default-"
MODEL_ROUTING_TASKS: tuple[str, ...] = (
    "hypothesis_generation",
    "hypothesis_critique",
    "experiment_design",
    "coding",
    "result_analysis",
    "failure_analysis",
    "scientific_review",
    "report_generation",
    "summarization",
)
TIERS: tuple[str, ...] = ("fast", "default", "reasoning")
RESEARCH_STEPS: tuple[str, ...] = ("memory_search", "literature_search", "deep_research", "web_fetch")
PROMPT_VARIANTS: tuple[str, ...] = tuple(
    f"persona:{persona}/format:{fmt}"
    for persona in ("rigorous", "skeptic", "creative")
    for fmt in ("structured", "narrative")
)
_TIER_DEFAULTS: dict[str, str] = {
    "hypothesis_generation": "reasoning",
    "hypothesis_critique": "reasoning",
    "experiment_design": "reasoning",
    "coding": "default",
    "result_analysis": "default",
    "failure_analysis": "default",
    "scientific_review": "reasoning",
    "report_generation": "default",
    "summarization": "fast",
}


def _float(lo: float, hi: float, default: float, description: str, scale: float = 0.1) -> dict[str, Any]:
    return {"type": "float", "min": lo, "max": hi, "default": default, "mutation_scale": scale, "description": description}


def _int(lo: int, hi: int, default: int, description: str) -> dict[str, Any]:
    return {"type": "int", "min": lo, "max": hi, "step": 1, "default": default, "description": description}


def _choice(choices: tuple[str, ...], default: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "choice", "choices": list(choices), "default": default, "description": description, **extra}


def _bool(default: bool, description: str) -> dict[str, Any]:
    return {"type": "bool", "default": default, "description": description}


def _ordered(choices: tuple[str, ...], lo: int, hi: int, default: list[str], description: str) -> dict[str, Any]:
    return {
        "type": "ordered_list",
        "choices": list(choices),
        "min": lo,
        "max": hi,
        "default": default,
        "description": description,
    }


DEFINITION_KEYS: list[str] = ["component", "description", "prompt_key"]

DEFAULT_STRATEGIES: dict[str, dict[str, Any]] = {
    StrategyKind.SEARCH.value: {
        "description": "Literature search: sources, result budget, recency weighting and lexical ranking.",
        "definition": {"component": "research.literature_search", "description": "Hybrid literature retrieval."},
        "parameters": {
            "sources": _ordered(("openalex", "arxiv"), 1, 2, ["openalex", "arxiv"], "Sources queried, in order."),
            "max_results": _int(5, 50, 20, "Results kept per query."),
            "recency_half_life_days": _int(30, 3650, 730, "Half-life of the recency weight in days."),
            "title_boost": _float(0.0, 3.0, 1.0, "Weight of query terms matched in the title."),
            "stemming": _bool(False, "Match word stems instead of exact tokens."),
        },
    },
    StrategyKind.HYPOTHESIS.value: {
        "description": "Hypothesis generation: candidate count, ranking weights, critique rounds, temperature.",
        "definition": {"component": "hypotheses.generate", "prompt_key": "hypothesis.generate"},
        "parameters": {
            "n_candidates": _int(3, 15, 6, "Hypotheses generated per round."),
            "novelty_weight": _float(0.0, 1.0, 0.5, "Ranking weight of novelty."),
            "feasibility_weight": _float(0.0, 1.0, 0.6, "Ranking weight of feasibility."),
            "testability_weight": _float(
                0.0, 1.0, 0.7, "Ranking weight of testability; also the falsifiability evidence required."
            ),
            "evidence_weight": _float(
                0.0, 1.0, 0.6, "Ranking weight of supporting evidence; also the measurability evidence required."
            ),
            "risk_weight": _float(0.0, 1.0, 0.4, "Penalty weight of risk."),
            "critique_rounds": _int(1, 3, 1, "Critique rounds before selection."),
            "temperature": _float(0.0, 1.0, 0.7, "Sampling temperature of the generation step."),
        },
    },
    StrategyKind.EXPERIMENT.value: {
        "description": "Experiment design: replicates, ablation depth and baseline policy.",
        "definition": {"component": "experiments.design", "prompt_key": "experiment.design"},
        "parameters": {
            "n_seeds": _int(3, 10, 5, "Independent seeds (replicates) per arm."),
            "ablation_depth": _int(0, 3, 1, "Levels of ablation studies added to a candidate design."),
            "baseline_policy": _choice(
                ("required", "when_comparative", "optional"),
                "required",
                "When a design must include a control/baseline arm.",
            ),
        },
    },
    StrategyKind.OPTIMIZATION.value: {
        "description": "Optimization loop: search method, evaluation budget and variation operators.",
        "definition": {"component": "evolution.optimizer"},
        "parameters": {
            "search_method": _choice(("random", "grid", "evolutionary"), "evolutionary", "Search method."),
            "budget_fraction": _float(0.1, 1.0, 1.0, "Fraction of the evaluation budget used."),
            "mutation_scale": _float(0.02, 0.5, 0.1, "Relative step size of numeric mutations.", scale=0.2),
            "crossover_rate": _float(0.5, 1.0, 0.9, "Probability of crossover per child."),
        },
    },
    StrategyKind.PROMPT.value: {
        "description": "Prompt variant used by agents (persona and output format).",
        "definition": {"component": "agents.prompts"},
        "parameters": {
            "variant": _choice(
                PROMPT_VARIANTS, PROMPT_VARIANTS[0], "Prompt variant (persona/format segments).", prompt_variant=True
            ),
        },
    },
    StrategyKind.AGENT_TOPOLOGY.value: {
        "description": "Agent topology: whether critic and reviewer agents take part in a cycle.",
        "definition": {"component": "missions.topology"},
        "parameters": {
            "critic_enabled": _bool(True, "Run the hypothesis critic."),
            "reviewer_enabled": _bool(True, "Run the scientific reviewer before reporting."),
        },
    },
    StrategyKind.TOOL_SEQUENCING.value: {
        "description": "Order in which research steps are attempted (authority stays with policy and mission).",
        "definition": {"component": "research.sequencing"},
        "parameters": {
            "order": _ordered(
                RESEARCH_STEPS, 1, 4, ["memory_search", "literature_search", "web_fetch"], "Research step order."
            ),
        },
    },
    StrategyKind.MODEL_ROUTING.value: {
        "description": "Preferred model tier per task type (models and prices come from configuration).",
        "definition": {"component": "llm.routing"},
        "parameters": {
            f"tier_{task}": _choice(TIERS, _TIER_DEFAULTS[task], f"Model tier for {task.replace('_', ' ')}.")
            for task in MODEL_ROUTING_TASKS
        },
    },
}


def default_strategy_name(kind: str) -> str:
    return f"{DEFAULT_NAME_PREFIX}{kind}"


def default_schema(kind: str) -> dict[str, Any]:
    spec = DEFAULT_STRATEGIES[kind]
    return {"parameters": dict(spec["parameters"]), "definition_keys": list(DEFINITION_KEYS)}


def default_parameters(kind: str) -> dict[str, Any]:
    return {name: p["default"] for name, p in DEFAULT_STRATEGIES[kind]["parameters"].items()}


def default_definition(kind: str) -> dict[str, Any]:
    return dict(DEFAULT_STRATEGIES[kind]["definition"])


def version_content_hash(definition: dict[str, Any], parameters: dict[str, Any]) -> str:
    """Content hash of a version (definition + parameters), canonical JSON."""
    return canonical_hash({"definition": definition, "parameters": parameters})


def validate_defaults() -> None:
    """Raise if a built-in default violates its own guardrails (checked by tests and at seeding)."""
    guardrails = StrategyGuardrails()
    for kind in DEFAULT_STRATEGIES:
        report = guardrails.validate(default_definition(kind), default_parameters(kind), default_schema(kind))
        if not report.ok:
            raise ValueError(f"default {kind} strategy violates its guardrails: {report.as_dict()}")


def ensure_default_strategies(db: Session, organization_id: uuid.UUID) -> list[Strategy]:
    """Create the missing built-in default strategies of an organization (idempotent, concurrency-safe).

    Returns the strategies created by this call (empty when every default already exists).
    """
    names = {default_strategy_name(kind): kind for kind in DEFAULT_STRATEGIES}
    existing = set(
        db.scalars(
            select(Strategy.name).where(Strategy.organization_id == organization_id, Strategy.name.in_(list(names)))
        ).all()
    )
    if len(existing) == len(names):
        return []
    advisory_xact_lock(db, f"strategy-defaults:{organization_id}")
    existing = set(
        db.scalars(
            select(Strategy.name).where(Strategy.organization_id == organization_id, Strategy.name.in_(list(names)))
        ).all()
    )
    created: list[Strategy] = []
    now = utcnow()
    for name, kind in names.items():
        if name in existing:
            continue
        definition, parameters = default_definition(kind), default_parameters(kind)
        strategy = Strategy(
            id=uuid.uuid4(),
            organization_id=organization_id,
            kind=kind,
            name=name,
            description=DEFAULT_STRATEGIES[kind]["description"],
            parameter_schema=default_schema(kind),
            status="active",
        )
        db.add(strategy)
        db.flush()
        version = StrategyVersion(
            id=uuid.uuid4(),
            organization_id=organization_id,
            strategy_id=strategy.id,
            version=1,
            definition=definition,
            parameters=parameters,
            content_hash=version_content_hash(definition, parameters),
            status=StrategyStatus.PROMOTED,
            created_by="seed",
            mutation_history=[],
            promoted_at=now,
        )
        db.add(version)
        db.flush()
        strategy.current_version_id = version.id
        db.flush()
        created.append(strategy)
    if created:
        log.info("default_strategies_seeded", organization_id=str(organization_id), kinds=[s.kind for s in created])
    return created
