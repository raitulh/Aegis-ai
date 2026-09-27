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
                )
                if not value
            ]
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
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
