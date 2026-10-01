"""Application configuration.

All configuration is read from environment variables (optionally a ``.env`` file). Secrets are never
hard-coded; development-only fallbacks are generated deterministically and flagged loudly at startup.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

_DEV_ONLY_SEED = "aegis-development-only-do-not-use-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------------------------
    environment: Environment = "development"
    app_name: str = "Aegis AI"
    app_version: str = "1.0.0"
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = False

    # --- database ----------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"
    # Owner/admin connection used for migrations, seeding and cross-tenant maintenance jobs.
    database_admin_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- queue -------------------------------------------------------------------------------
    redis_url: str | None = None
    job_backend: Literal["celery", "inline"] | None = None
    inline_job_workers: int = 2

    # --- authentication ----------------------------------------------------------------------
    local_auth_enabled: bool = True
    session_ttl_hours: int = 24 * 7
    guest_session_ttl_hours: int = 12
    supabase_url: str | None = Field(default=None, validation_alias="SUPABASE_URL")
    next_public_supabase_url: str | None = None
    supabase_jwt_secret: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str | None = None

    # --- secrets -----------------------------------------------------------------------------
    secrets_encryption_key: str | None = None
    api_key_pepper: str | None = None

    # --- http hardening ----------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"
    max_request_bytes: int = 2 * 1024 * 1024
    max_upload_bytes: int = 10 * 1024 * 1024
    rate_limit_public_per_min: int = 60
    rate_limit_auth_per_min: int = 600
    rate_limit_expensive_per_min: int = 20
    rate_limit_enabled: bool = True
    # Per-account login throttling (independent of IP, defeats distributed credential stuffing).
    login_max_failures: int = 10
    login_failure_window_seconds: int = 900
    # Number of reverse proxies in front of the API whose X-Forwarded-For entries are trusted.
    # 0 (default) ignores X-Forwarded-For entirely, so clients cannot spoof their address.
    trusted_proxy_hops: int = 0
    # Outbound calls to private/loopback networks. Unset → allowed outside production only.
    allow_private_network_targets: bool | None = None
    # Extra origins allowed to send cookie-authenticated unsafe requests (CSRF guard). CORS origins and
    # WEB_BASE_URL are always trusted.
    csrf_trusted_origins: str = ""

    # --- jobs / reliability ------------------------------------------------------------------
    # A running audit whose worker has not heartbeated for this long is considered lost.
    job_lease_timeout_seconds: int = 900
    audit_max_attempts: int = 2
    job_max_attempts: int = 3
    # Periodic maintenance (webhook delivery, stale-run recovery, sandbox purge, retention,
    # continuous-assurance schedules). With Celery it runs under `celery beat`; inline it runs on a
    # background thread in the API process when enabled.
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30
    sse_heartbeat_seconds: int = 15
    idempotency_ttl_hours: int = 24

    # --- evidence ------------------------------------------------------------------------------
    # Ed25519 private key (urlsafe base64 of the 32-byte seed) used to sign evidence export manifests.
    # Generate: python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    evidence_signing_key: str | None = None

    # --- runtime guard -------------------------------------------------------------------------
    runtime_max_batch: int = 500
    runtime_default_mode: Literal["observe", "audit", "enforce"] = "observe"

    # --- usage & billing -----------------------------------------------------------------------
    billing_provider: Literal["none", "stripe"] = "none"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Optional JSON overriding plan limits/features (see aegis_api.billing.plans). Prices are never
    # hard-coded: {"pro": {"price_display": "$49 / month"}} is shown only when configured.
    plans_json: str = "{}"

    # --- retention defaults (days; workspaces can override within plan limits) -----------------
    retention_runtime_events_days: int = 30
    retention_audit_events_days: int = 180
    retention_sandbox_hours_grace: int = 1

    # --- observability -------------------------------------------------------------------------
    metrics_enabled: bool = True
    # When set, /metrics requires `Authorization: Bearer <token>`. Required in production.
    metrics_token: str | None = None

    # --- storage -----------------------------------------------------------------------------
    storage_backend: Literal["local", "supabase"] = "local"
    storage_local_dir: str = "var/storage"
    storage_bucket: str = "aegis-evidence"

    # --- AI providers ------------------------------------------------------------------------
    ollama_base_url: str | None = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"
    ollama_embed_model: str = "nomic-embed-text"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    embedding_provider: Literal["hash", "ollama", "gemini", "openai"] = "hash"
    embedding_dim: int = 768
    # JSON: {"provider:model": {"input_per_mtok": 0.1, "output_per_mtok": 0.4}}. Costs are only
    # reported when a price is configured here; otherwise cost is left empty (never invented).
    model_pricing_json: str = "{}"
    model_timeout_seconds: float = 60.0

    # --- email -------------------------------------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str = "Aegis AI <no-reply@aegis.local>"

    # --- demo --------------------------------------------------------------------------------
    demo_enabled: bool = True
    demo_reference_org_slug: str = "aegis-demo"

    # --- feature flags (defaults; per-org overrides live in the database) --------------------
    feature_adaptive_redteam: bool = True
    feature_agent_audit: bool = True
    feature_continuous_monitoring: bool = True
    feature_external_verification: bool = False
    feature_enterprise_controls: bool = False
    feature_runtime_enforcement: bool = True
    feature_ai_assistant: bool = False
    feature_marketplace: bool = False
    feature_sso: bool = False

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _normalise_driver(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("postgres://"):
            value = "postgresql://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.environment != "production":
            return self
        problems = [
            f"{name} is required"
            for name, value in (
                ("SECRETS_ENCRYPTION_KEY", self.secrets_encryption_key),
                ("API_KEY_PEPPER", self.api_key_pepper),
                ("EVIDENCE_SIGNING_KEY", self.evidence_signing_key),
            )
            if not value
        ]
        if any(o == "*" for o in self.cors_origin_list):
            problems.append("CORS_ORIGINS must not contain '*' (credentials are enabled)")
        if not self.web_base_url.startswith("https://"):
            problems.append("WEB_BASE_URL must use https")
        if self.metrics_enabled and not self.metrics_token:
            problems.append("METRICS_TOKEN is required when METRICS_ENABLED=true")
        if self.billing_provider == "stripe" and not (self.stripe_secret_key and self.stripe_webhook_secret):
            problems.append("STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are required for BILLING_PROVIDER=stripe")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    # --- derived values ------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def effective_job_backend(self) -> str:
        if self.job_backend:
            return self.job_backend
        return "celery" if self.redis_url else "inline"

    @property
    def effective_supabase_url(self) -> str | None:
        url = self.supabase_url or self.next_public_supabase_url
        return url.rstrip("/") if url else None

    @property
    def admin_database_url(self) -> str:
        return self.database_admin_url or self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_origins(self) -> set[str]:
        extra = [o.strip() for o in self.csrf_trusted_origins.split(",") if o.strip()]
        return {o.rstrip("/") for o in [*self.cors_origin_list, self.web_base_url, *extra] if o and o != "*"}

    @property
    def effective_allow_private(self) -> bool:
        if self.allow_private_network_targets is not None:
            return self.allow_private_network_targets
        return not self.is_production

    @property
    def fernet_key(self) -> bytes:
        if self.secrets_encryption_key:
            return self.secrets_encryption_key.encode()
        digest = hashlib.sha256(f"{_DEV_ONLY_SEED}:fernet".encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def uses_dev_secrets(self) -> bool:
        return not (self.secrets_encryption_key and self.api_key_pepper)

    @property
    def effective_api_key_pepper(self) -> bytes:
        return (self.api_key_pepper or f"{_DEV_ONLY_SEED}:pepper").encode()

    @property
    def model_pricing(self) -> dict[str, dict[str, float]]:
        try:
            data = json.loads(self.model_pricing_json or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def feature_defaults(self) -> dict[str, bool]:
        return {
            "adaptive_redteam": self.feature_adaptive_redteam,
            "agent_audit": self.feature_agent_audit,
            "continuous_monitoring": self.feature_continuous_monitoring,
            "external_verification": self.feature_external_verification,
            "enterprise_controls": self.feature_enterprise_controls,
            "runtime_enforcement": self.feature_runtime_enforcement,
            "ai_assistant": self.feature_ai_assistant,
            "marketplace": self.feature_marketplace,
            "sso": self.feature_sso,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
