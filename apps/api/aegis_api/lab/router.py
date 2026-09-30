"""Aggregates every lab context router. Included by ``aegis_api.app`` before the core routers."""

from __future__ import annotations

from fastapi import APIRouter

from aegis_api.lab.admin.router import router as admin_router
from aegis_api.lab.agents.router import router as agents_router
from aegis_api.lab.data.router import router as data_router
from aegis_api.lab.evaluation.router import router as evaluation_router
from aegis_api.lab.events.router import router as events_router
from aegis_api.lab.execution.router import router as execution_router
from aegis_api.lab.experiments.router import router as experiments_router
from aegis_api.lab.failures.router import router as failures_router
from aegis_api.lab.governance.router import router as governance_router
from aegis_api.lab.hypotheses.router import router as hypotheses_router
from aegis_api.lab.identity.router import router as identity_router
from aegis_api.lab.knowledge.router import router as knowledge_router
from aegis_api.lab.llm.router import router as llm_router
from aegis_api.lab.missions.router import router as missions_router
from aegis_api.lab.observability.router import router as system_router
from aegis_api.lab.research.router import router as research_router
from aegis_api.lab.strategies.router import router as strategies_router
from aegis_api.lab.tools.router import router as tools_router
from aegis_api.lab.usage.router import router as usage_router
from aegis_api.lab.verification.router import router as verification_router

api_router = APIRouter()
for _router in (
    system_router,
    identity_router,
    admin_router,
    missions_router,
    agents_router,
    llm_router,
    research_router,
    knowledge_router,
    hypotheses_router,
    experiments_router,
    data_router,
    execution_router,
    evaluation_router,
    failures_router,
    strategies_router,
    verification_router,
    governance_router,
    tools_router,
    usage_router,
    events_router,
):
    api_router.include_router(_router)

LAB_TAGS_METADATA = [
    {"name": "System", "description": "Liveness, readiness, metrics and platform info."},
    {
        "name": "Organizations",
        "description": "Organizations, workspaces, projects, teams, service accounts, tokens, SSO.",
    },
    {"name": "Admin", "description": "Platform administration (platform admins only)."},
    {"name": "Missions", "description": "Research missions: define, plan, approve, run and observe."},
    {"name": "Agents", "description": "AI scientist agents, versions, runs, steps and prompt templates."},
    {"name": "Models", "description": "Model routing configuration, provider catalog and model usage."},
    {"name": "Research", "description": "Literature search, Gemini Deep Research tasks, sources and papers."},
    {
        "name": "Knowledge",
        "description": "Scientific memory, document ingestion, hybrid search and the knowledge graph.",
    },
    {"name": "Hypotheses", "description": "Hypothesis generation, critique, selection and evidence."},
    {
        "name": "Experiments",
        "description": "Experiment design, validation, runs, metrics, comparisons, reproducibility.",
    },
    {"name": "Data", "description": "Datasets and artifacts (immutable versions, secure upload/download)."},
    {"name": "Execution", "description": "Sandboxed compute jobs."},
    {"name": "Evaluation", "description": "Evaluators and evaluation runs."},
    {"name": "Failures", "description": "Failure intelligence and lessons learned."},
    {"name": "Strategies", "description": "Strategy registry, evolution runs, promotion/rollback, benchmarks."},
    {
        "name": "Verification",
        "description": "Claims, lineage, reproduction, verification, discoveries, reviews, reports.",
    },
    {"name": "Governance", "description": "Governance policies, approvals, budgets and quotas."},
    {"name": "Tools", "description": "Tool broker, tool invocations and MCP servers/tools."},
    {"name": "Usage", "description": "Usage, cost aggregation and billing."},
    {"name": "Events", "description": "Event log, live SSE streams and webhooks."},
]
