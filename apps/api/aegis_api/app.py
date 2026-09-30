"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aegis_api.config import get_settings
from aegis_api.errors import install_error_handlers
from aegis_api.logging import configure_logging
from aegis_api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecureHeadersMiddleware
from aegis_api.routers import (
    audits,
    auth,
    demo,
    evidence,
    findings,
    health,
    identity,
    lab_governance,
    lab_missions,
    lab_org,
    lab_science,
    lab_verification,
    operations,
    policies,
    system,
    systems,
    workspace,
)

log = structlog.get_logger("aegis.app")

DESCRIPTION = """
**Aegis AI** — Continuous AI Assurance & Governance Platform.

Continuously test AI systems and agents for fairness, hallucination, safety, privacy, security and policy
compliance — with evidence-backed findings and automatic re-testing.

Authentication: session cookie, session bearer token, or an API key (`Authorization: Bearer aeg_live_…`).
All data is scoped to the authenticated workspace.
"""

TAGS_METADATA = [
    {
        "name": "Auth",
        "description": "Sign up, sign in, sessions, JWT tokens with refresh rotation, password reset, SSO.",
    },
    {
        "name": "Identity",
        "description": "Roles, custom roles, service accounts, key rotation, SSO connections, quotas, flags.",
    },
    {"name": "Systems", "description": "Register AI systems and configure model providers."},
    {"name": "Audits", "description": "Run audits and stream live progress; inspect results, evidence and reports."},
    {"name": "Policies", "description": "Upload and compile policies into executable controls; framework mappings."},
    {"name": "Findings", "description": "Findings, remediation and regression re-testing."},
    {"name": "Evidence", "description": "Immutable, hash-chained audit evidence and reports."},
    {"name": "Operations", "description": "Agent traces, red team, monitoring and alerts."},
    {"name": "Workspace", "description": "Overview, search, team, API keys, integrations and notifications."},
    {"name": "Demo", "description": "Public demo endpoints (no authentication)."},
    {"name": "Organization", "description": "Organization, users, workspaces, projects and teams."},
    {
        "name": "Missions",
        "description": "Scientist Lab missions, live events (SSE), workflow runs, agents and prompts.",
    },
    {"name": "Science", "description": "Research, knowledge, memory, hypotheses, experiments, datasets and artifacts."},
    {
        "name": "Verification",
        "description": "Claims and lineage, verification, discoveries, failures, reports, strategies.",
    },
    {
        "name": "Governance",
        "description": "Approvals, lab policies, tools/MCP, models, usage, billing, webhooks, benchmarks.",
    },
    {"name": "Health", "description": "Liveness and readiness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log.info(
        "startup",
        environment=settings.environment,
        job_backend=settings.effective_job_backend,
        dev_secrets=settings.uses_dev_secrets,
    )
    if settings.uses_dev_secrets and settings.is_production:
        raise RuntimeError("Refusing to start in production without SECRETS_ENCRYPTION_KEY and API_KEY_PEPPER")
    _seed_reference_data()
    try:
        from aegis_api.db.session import get_engine
        from aegis_api.infrastructure.observability.telemetry import instrument_engine_metrics

        instrument_engine_metrics(get_engine())
    except Exception:
        log.warning("db_metrics_instrumentation_skipped", exc_info=True)
    yield


def _seed_reference_data() -> None:
    """Load global framework reference packs (idempotent)."""
    try:
        from aegis_api.db.session import session_factory
        from aegis_api.services import policy_service, rbac_service
        from aegis_api.services.lab import reference as lab_reference

        session = session_factory(admin=True)()
        try:
            policy_service.seed_frameworks(session)
            rbac_service.seed_catalogue(session)
            lab_reference.seed(session)
            session.commit()
        finally:
            session.close()
    except Exception:
        log.warning("framework_seed_skipped", exc_info=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "Aegis AI"},
        license_info={"name": "Apache-2.0"},
    )
    # Middleware (outermost first): context/logging → secure headers → body limit → CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecureHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    install_error_handlers(app)
    from aegis_api.infrastructure.observability.telemetry import configure_tracing

    configure_tracing(app)

    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(identity.router)
    app.include_router(systems.router)
    app.include_router(audits.router)
    app.include_router(policies.router)
    app.include_router(findings.router)
    app.include_router(evidence.router)
    app.include_router(operations.router)
    app.include_router(workspace.router)
    app.include_router(demo.router)
    app.include_router(lab_org.router)
    app.include_router(lab_missions.router)
    app.include_router(lab_science.router)
    app.include_router(lab_verification.router)
    app.include_router(lab_governance.router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()
