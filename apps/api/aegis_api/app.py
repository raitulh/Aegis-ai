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
    operations,
    policies,
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
    yield


def _seed_reference_data() -> None:
    """Load global framework reference packs (idempotent)."""
    try:
        from aegis_api.db.session import session_factory
        from aegis_api.services import policy_service, rbac_service

        session = session_factory(admin=True)()
        try:
            policy_service.seed_frameworks(session)
            rbac_service.seed_catalogue(session)
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

    app.include_router(health.router)
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

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()
