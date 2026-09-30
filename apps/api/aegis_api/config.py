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
    allow_private_network_targets: bool = False

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
    openai_embed_model: str = "text-embedding-3-small"
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

    # --- tokens (JWT access + rotating refresh) ------------------------------------------------
    # HS256 signing key for access tokens (>= 32 chars; required in production).
    jwt_signing_key: str | None = None
    jwt_issuer: str = "aegis-lab"
    jwt_audience: str = "aegis-api"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    password_reset_ttl_minutes: int = 30
    email_verification_ttl_hours: int = 48
    email_backend: Literal["smtp", "outbox", "memory"] | None = None

    # --- LLM gateway / Gemini ------------------------------------------------------------------
    llm_default_provider: Literal["gemini", "openai", "anthropic", "ollama"] = "gemini"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_revision: str = "2026-05-20"
    # Model IDs always come from configuration; they fall back to GEMINI_MODEL when unset.
    gemini_default_model: str | None = None
    gemini_reasoning_model: str | None = None
    gemini_fast_model: str | None = None
    gemini_deep_research_agent: str | None = None
    gemini_timeout_seconds: float = 120.0
    gemini_max_retries: int = 3
    # JSON list of extra/override catalogue entries:
    # [{"provider": "gemini", "model": "...", "tier": "fast", "input_per_mtok": 0.1, "output_per_mtok": 0.4}]
    model_catalog_json: str = "[]"
    llm_max_output_tokens: int = 8192
    agent_max_steps: int = 6

    # --- durable workflows -----------------------------------------------------------------------
    workflow_engine: Literal["temporal", "inline"] | None = None
    temporal_address: str | None = None
    temporal_namespace: str = "default"
    temporal_task_queue: str = "aegis-lab"
    temporal_tls: bool = False
    temporal_api_key: str | None = None
    workflow_lease_seconds: int = 300
    inline_workflow_poll_seconds: float = 2.0

    # --- object storage ---------------------------------------------------------------------------
    object_storage_backend: Literal["local", "s3"] = "local"
    object_storage_endpoint: str | None = None
    object_storage_public_endpoint: str | None = None
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "aegis-lab"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None
    object_storage_local_dir: str = "var/objects"
    object_storage_presign_ttl_seconds: int = 600
    max_artifact_upload_bytes: int = 200 * 1024 * 1024
    malware_scanner: Literal["none", "clamav"] = "none"
    clamav_host: str | None = None
    clamav_port: int = 3310

    # --- execution fabric -------------------------------------------------------------------------
    execution_backend: Literal["docker", "kubernetes", "disabled"] = "docker"
    docker_host: str | None = None
    execution_default_image: str = "python:3.12-alpine"
    execution_workdir_root: str = "var/sandbox"
    execution_user: str = "65534:65534"
    execution_pids_limit: int = 256
    execution_max_cpu: float = 4.0
    execution_max_memory_mb: int = 8192
    execution_max_timeout_seconds: int = 3600
    execution_max_output_bytes: int = 50 * 1024 * 1024
    execution_max_log_bytes: int = 2 * 1024 * 1024
    execution_tmpfs_mb: int = 256
    execution_require_digest_pinned_images: bool = False
    execution_max_gpu_count: int = 0
    execution_allowed_gpu_types: str = ""
    kubernetes_api_url: str | None = None
    kubernetes_namespace: str = "aegis-sandbox"
    kubernetes_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105 - file path
    kubernetes_ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    kubernetes_runtime_class: str | None = "gvisor"
    # JSON: {"cpu_core_hour": 0.04, "memory_gb_hour": 0.005, "gpu_hour": {"nvidia-l4": 0.8}, "storage_gb_month": 0.023}
    compute_pricing_json: str = "{}"

    # --- research integrations --------------------------------------------------------------------
    research_search_providers: str = "arxiv,crossref"
    research_fetch_max_bytes: int = 5 * 1024 * 1024
    research_user_agent: str = "AegisLab/1.0 (+https://github.com/aegis-ai)"

    # --- lab governance ---------------------------------------------------------------------------
    lab_platform_max_autonomy: str = "L4_CLOSED_LOOP_EVOLUTION"
    agent_message_signing_key: str | None = None
    idempotency_ttl_hours: int = 24
    event_stream_heartbeat_seconds: float = 15.0
    event_stream_max_seconds: float = 3600.0

    # --- observability ----------------------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "aegis-api"
    otel_traces_sampler_ratio: float = 1.0
    metrics_enabled: bool = True
    metrics_token: str | None = None
    sentry_dsn: str | None = None

    # --- billing ----------------------------------------------------------------------------------
    billing_provider: Literal["none"] = "none"

    # --- demo --------------------------------------------------------------------------------
    demo_enabled: bool = True
    demo_reference_org_slug: str = "aegis-demo"

    # --- feature flags (defaults; per-org overrides live in the database) --------------------
    feature_adaptive_redteam: bool = True
    feature_agent_audit: bool = True
    feature_continuous_monitoring: bool = True
    feature_external_verification: bool = False
    feature_enterprise_controls: bool = False
    feature_evolution: bool = True
    feature_deep_research: bool = True
    feature_mcp: bool = True
    feature_gpu_execution: bool = False
    feature_enterprise_sso: bool = False
    feature_verification: bool = True
    feature_graph_memory: bool = True
    feature_billing: bool = False

    # --- rate limit tiers for expensive lab operations -----------------------------------------
    rate_limit_research_per_min: int = 6
    rate_limit_execution_per_min: int = 30
    rate_limit_model_per_min: int = 120
    rate_limit_download_per_min: int = 120

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
        if self.environment == "production":
            required: list[tuple[str, object]] = [
                ("SECRETS_ENCRYPTION_KEY", self.secrets_encryption_key),
                ("API_KEY_PEPPER", self.api_key_pepper),
                ("JWT_SIGNING_KEY", self.jwt_signing_key),
            ]
            if self.effective_workflow_engine == "temporal":
                required.append(("TEMPORAL_ADDRESS", self.temporal_address))
            if self.object_storage_backend == "s3":
                required.append(("OBJECT_STORAGE_BUCKET", self.object_storage_bucket))
            missing = [name for name, value in required if not value]
            if missing:
                raise ValueError(f"Missing required production settings: {', '.join(missing)}")
            if self.jwt_signing_key and len(self.jwt_signing_key) < 32:
                raise ValueError("JWT_SIGNING_KEY must be at least 32 characters in production")
            if self.object_storage_backend == "local":
                raise ValueError("OBJECT_STORAGE_BACKEND=local is not supported in production; use s3")
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
    def fernet_key(self) -> bytes:
        if self.secrets_encryption_key:
            return self.secrets_encryption_key.encode()
        digest = hashlib.sha256(f"{_DEV_ONLY_SEED}:fernet".encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def uses_dev_secrets(self) -> bool:
        return not (self.secrets_encryption_key and self.api_key_pepper and self.jwt_signing_key)

    @property
    def effective_jwt_key(self) -> bytes:
        return (self.jwt_signing_key or f"{_DEV_ONLY_SEED}:jwt").encode()

    @property
    def effective_agent_message_key(self) -> bytes:
        if self.agent_message_signing_key:
            return self.agent_message_signing_key.encode()
        base = self.secrets_encryption_key or _DEV_ONLY_SEED
        return hashlib.sha256(f"{base}:agent-messages".encode()).digest()

    @property
    def effective_workflow_engine(self) -> str:
        if self.workflow_engine:
            return self.workflow_engine
        return "temporal" if self.temporal_address else "inline"

    @property
    def effective_email_backend(self) -> str:
        if self.email_backend:
            return self.email_backend
        if self.smtp_host:
            return "smtp"
        return "memory" if self.environment == "test" else "outbox"

    @property
    def gemini_models(self) -> dict[str, str]:
        """Tier → model id, falling back to GEMINI_MODEL (single place where a default model id lives)."""
        default = self.gemini_default_model or self.gemini_model
        return {
            "fast": self.gemini_fast_model or default,
            "default": default,
            "reasoning": self.gemini_reasoning_model or default,
        }

    @property
    def model_catalog(self) -> list[dict[str, object]]:
        try:
            data = json.loads(self.model_catalog_json or "[]")
        except json.JSONDecodeError:
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    @property
    def compute_pricing(self) -> dict[str, object]:
        try:
            data = json.loads(self.compute_pricing_json or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def search_providers(self) -> list[str]:
        return [p.strip() for p in self.research_search_providers.split(",") if p.strip()]

    @property
    def allowed_gpu_types(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.execution_allowed_gpu_types.split(",") if t.strip())

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
            "evolution": self.feature_evolution,
            "deep_research": self.feature_deep_research,
            "mcp": self.feature_mcp,
            "gpu_execution": self.feature_gpu_execution,
            "enterprise_sso": self.feature_enterprise_sso,
            "verification": self.feature_verification,
            "graph_memory": self.feature_graph_memory,
            "billing": self.feature_billing,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
