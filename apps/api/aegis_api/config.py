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

from pydantic import AliasChoices, Field, field_validator, model_validator
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
    environment: Environment = Field(default="development", validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV"))
    app_name: str = "Aegis AI"
    app_version: str = "1.0.0"
    api_base_url: str = Field(default="http://localhost:8000", validation_alias=AliasChoices("API_BASE_URL", "APP_URL"))
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
    gemini_model: str = "gemini-flash-latest"
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

    # =========================================================================================
    # AI Scientist Evolution Lab
    # =========================================================================================
    # --- tokens (JWT access + rotating refresh) ------------------------------------------------
    # JWT_SECRET is the HS256 signing key (required in production). For zero-downtime rotation set
    # JWT_SECRETS_JSON={"kid-2026-09":"<secret>", "kid-2026-06":"<old secret>"} and JWT_ACTIVE_KID.
    jwt_secret: str | None = None
    jwt_secrets_json: str = "{}"
    jwt_active_kid: str = "primary"
    jwt_issuer: str = "aegis-lab"
    jwt_audience: str = "aegis-api"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_days: int = 30
    oidc_redirect_base_url: str | None = None  # defaults to API_BASE_URL

    # --- workflows (Temporal) ------------------------------------------------------------------
    temporal_address: str | None = None  # e.g. localhost:7233 — unset → local durable workflow engine
    temporal_namespace: str = "default"
    temporal_task_queue: str = "aegis-lab"
    temporal_execution_task_queue: str = "aegis-lab-execution"
    temporal_tls: bool = False
    temporal_api_key: str | None = None
    temporal_connect_timeout_seconds: float = 5.0
    workflow_engine: Literal["temporal", "local"] | None = None
    workflow_stale_after_seconds: int = 300  # local engine: resume RUNNING runs with no heartbeat

    # --- object storage ------------------------------------------------------------------------
    object_storage_backend: Literal["local", "s3"] = "local"
    object_storage_endpoint: str | None = None  # S3-compatible endpoint (MinIO, GCS interop, R2…)
    object_storage_bucket: str = "aegis-lab"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None
    object_storage_region: str = "us-east-1"
    object_storage_force_path_style: bool = True
    object_storage_local_dir: str = "var/objects"
    signed_url_ttl_seconds: int = 900
    max_artifact_bytes: int = 5 * 1024 * 1024 * 1024
    max_lab_upload_bytes: int = 200 * 1024 * 1024
    malware_scanner: Literal["none", "clamav"] = "none"
    clamav_host: str = "localhost"
    clamav_port: int = 3310

    # --- LLM gateway (provider-agnostic; Gemini is one provider) --------------------------------
    llm_default_provider: Literal["gemini", "openai", "anthropic", "ollama"] = "gemini"
    llm_max_retries: int = 3
    llm_timeout_seconds: float = 120.0
    llm_max_output_tokens: int = 8192
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Model ids are configuration, never hard-coded in business logic. Stable aliases are the defaults;
    # the concrete ``modelVersion`` returned by the provider is recorded on every call for reproducibility.
    gemini_default_model: str | None = None  # falls back to GEMINI_MODEL
    gemini_reasoning_model: str | None = "gemini-pro-latest"
    gemini_fast_model: str | None = "gemini-flash-lite-latest"
    gemini_deep_research_agent: str = "deep-research-preview-04-2026"  # Interactions API agent id
    gemini_timeout_seconds: float = 120.0
    gemini_max_retries: int = 3
    openai_reasoning_model: str | None = None
    anthropic_reasoning_model: str | None = None

    # --- execution fabric (sandboxed experiments) ------------------------------------------------
    execution_backend: Literal["local_docker", "kubernetes", "disabled"] = "local_docker"
    docker_host: str = "unix:///var/run/docker.sock"
    execution_default_image: str = "python:3.12-slim"
    # Comma-separated image allowlist (exact refs or prefixes ending with '*'). Pin digests in production.
    execution_allowed_images: str = "python:3.12-slim,python:3.11-slim"
    execution_user: str = "65534:65534"
    execution_max_cpu: float = 4.0
    execution_max_memory_mb: int = 8192
    execution_max_disk_mb: int = 10240
    execution_max_timeout_seconds: int = 6 * 3600
    execution_default_timeout_seconds: int = 900
    execution_max_output_bytes: int = 512 * 1024 * 1024
    execution_max_output_files: int = 2000
    execution_max_log_bytes: int = 10 * 1024 * 1024
    execution_pids_limit: int = 256
    execution_tmpfs_mb: int = 256
    execution_gpu_enabled: bool = False
    execution_cpu_price_per_hour_usd: float = 0.0
    execution_memory_gb_price_per_hour_usd: float = 0.0
    execution_gpu_price_per_hour_json: str = "{}"  # {"nvidia-a100": 3.2}
    # Optional egress for allowlisted sandbox networking: containers join an internal network whose only
    # route out is an allowlisting HTTP(S) proxy. Without these, sandboxes only support network mode "none".
    execution_egress_network: str | None = None
    execution_egress_proxy_url: str | None = None
    k8s_fetcher_image: str = "curlimages/curl:8.10.1"
    k8s_api_url: str = "https://kubernetes.default.svc"
    k8s_namespace: str = "aegis-sandbox"
    k8s_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105 - a file path
    k8s_ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    k8s_runtime_class: str | None = None  # e.g. gvisor
    k8s_image_pull_secret: str | None = None

    # --- research / tools / MCP -------------------------------------------------------------------
    paper_search_sources: str = "openalex,arxiv"
    openalex_mailto: str | None = None
    tool_default_timeout_seconds: float = 30.0
    url_fetch_max_bytes: int = 2 * 1024 * 1024
    # Hosts research tools may reach (comma-separated; exact host or leading-dot suffix).
    tool_egress_allowlist: str = "api.openalex.org,export.arxiv.org,arxiv.org,api.crossref.org,.wikipedia.org"
    mcp_timeout_seconds: float = 30.0
    mcp_max_response_bytes: int = 1024 * 1024
    # Dual-era MCP client: try the stateless revision first, fall back to the initialize handshake.
    mcp_protocol_version: str = "2026-07-28"
    mcp_legacy_protocol_version: str = "2025-11-25"

    # --- agents -----------------------------------------------------------------------------------
    agent_default_max_steps: int = 8
    agent_default_timeout_seconds: int = 600
    agent_max_tool_output_chars: int = 20000

    # --- events / idempotency / rate limits -------------------------------------------------------
    event_bus_backend: Literal["redis", "memory"] | None = None  # default: redis when REDIS_URL set
    sse_heartbeat_seconds: float = 15.0
    sse_max_stream_seconds: int = 3600
    idempotency_ttl_hours: int = 24
    rate_limit_research_per_min: int = 10
    rate_limit_execution_per_min: int = 20
    rate_limit_model_per_min: int = 120
    rate_limit_download_per_min: int = 120

    # --- observability ------------------------------------------------------------------------------
    metrics_enabled: bool = True
    metrics_token: str | None = None  # when set, /metrics requires `Authorization: Bearer <token>`
    otel_exporter_otlp_endpoint: str | None = None  # e.g. http://otel-collector:4318
    otel_service_name: str = "aegis-api"
    otel_traces_sampler_ratio: float = 1.0

    # --- billing ------------------------------------------------------------------------------------
    billing_provider: Literal["none"] = "none"
    billing_currency: str = "USD"

    # --- lab feature flags (defaults; per-org overrides in feature_flags) ---------------------------
    feature_evolution: bool = True
    feature_deep_research: bool = True
    feature_mcp: bool = True
    feature_gpu_execution: bool = False
    feature_enterprise_sso: bool = False
    feature_verification: bool = True
    feature_graph_memory: bool = True
    feature_billing: bool = False

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
            missing = [
                name
                for name, value in (
                    ("SECRETS_ENCRYPTION_KEY", self.secrets_encryption_key),
                    ("API_KEY_PEPPER", self.api_key_pepper),
                    ("JWT_SECRET", self.jwt_secret or self.jwt_secret_map),
                )
                if not value
            ]
            if self.object_storage_backend == "s3" and not (
                self.object_storage_access_key and self.object_storage_secret_key
            ):
                missing.append("OBJECT_STORAGE_ACCESS_KEY/OBJECT_STORAGE_SECRET_KEY")
            if missing:
                raise ValueError(f"Missing required production settings: {', '.join(missing)}")
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
            **self.lab_feature_defaults(),
        }

    # --- lab derived values ------------------------------------------------------------------
    @property
    def jwt_secret_map(self) -> dict[str, str]:
        """kid → secret. JWT_SECRET is published under JWT_ACTIVE_KID; JWT_SECRETS_JSON adds rotated keys."""
        try:
            data = json.loads(self.jwt_secrets_json or "{}")
        except json.JSONDecodeError:
            data = {}
        keys = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        if self.jwt_secret:
            keys[self.jwt_active_kid] = self.jwt_secret
        return keys

    @property
    def effective_jwt_keys(self) -> dict[str, str]:
        keys = self.jwt_secret_map
        if not keys:
            digest = hashlib.sha256(f"{_DEV_ONLY_SEED}:jwt".encode()).hexdigest()
            keys = {self.jwt_active_kid: digest}
        return keys

    @property
    def effective_workflow_engine(self) -> str:
        if self.workflow_engine:
            return self.workflow_engine
        return "temporal" if self.temporal_address else "local"

    @property
    def effective_event_bus(self) -> str:
        if self.event_bus_backend:
            return self.event_bus_backend
        return "redis" if self.redis_url else "memory"

    @property
    def effective_gemini_default_model(self) -> str:
        return self.gemini_default_model or self.gemini_model

    @property
    def execution_allowed_image_list(self) -> list[str]:
        return [i.strip() for i in self.execution_allowed_images.split(",") if i.strip()]

    @property
    def tool_egress_allowlist_hosts(self) -> list[str]:
        return [h.strip().lower() for h in self.tool_egress_allowlist.split(",") if h.strip()]

    @property
    def paper_search_source_list(self) -> list[str]:
        return [s.strip().lower() for s in self.paper_search_sources.split(",") if s.strip()]

    @property
    def gpu_prices(self) -> dict[str, float]:
        try:
            data = json.loads(self.execution_gpu_price_per_hour_json or "{}")
        except json.JSONDecodeError:
            return {}
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}

    @property
    def agent_message_key(self) -> bytes:
        """HMAC key for signing inter-agent message envelopes (derived; never exposed)."""
        return hashlib.sha256(self.effective_api_key_pepper + b":agent-messages").digest()

    @property
    def signed_url_key(self) -> bytes:
        return hashlib.sha256(self.effective_api_key_pepper + b":signed-urls").digest()

    def lab_feature_defaults(self) -> dict[str, bool]:
        return {
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
