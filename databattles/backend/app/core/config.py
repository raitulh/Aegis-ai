"""Application configuration.

All configuration comes from environment variables (optionally a local `.env`).
Settings are validated at startup; in production the process refuses to start
when security-critical values are missing or left at their development defaults.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-insecure-secret-change-me-please-0123456789"  # noqa: S105 — dev default; production startup refuses it


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: Literal["development", "test", "production"] = "development"
    APP_NAME: str = "DataBattles"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    DATABASE_URL: str = "postgresql+psycopg://databattles:databattles@localhost:5432/databattles"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_SLOW_QUERY_MS: int = 500

    SECRET_KEY: str = _DEV_SECRET
    ENCRYPTION_KEY: str | None = None  # Fernet key for integration tokens; derived from SECRET_KEY if unset

    WEB_BASE_URL: str = "http://localhost:3000"
    API_BASE_URL: str = "http://localhost:8000"
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    COOKIE_SECURE: bool = False
    COOKIE_DOMAIN: str | None = None
    SESSION_TTL_DAYS: int = 30

    # Object storage
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_ROOT: str = "./var/storage"
    S3_BUCKET: str | None = None
    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    SIGNED_URL_TTL_SECONDS: int = 300

    # Upload limits (MB)
    MAX_DATASET_FILE_MB: int = 200
    MAX_SUBMISSION_MB: int = 50
    MAX_IMAGE_MB: int = 5
    MAX_JSON_BODY_KB: int = 1024
    DATASET_QUOTA_MB_PER_OWNER: int = 2048

    # Email
    EMAIL_BACKEND: Literal["console", "smtp"] = "console"
    EMAIL_FROM: str = "DataBattles <no-reply@databattles.local>"
    EMAIL_STORE_BODIES: bool = True  # dev mailbox; set false in production
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_STARTTLS: bool = True

    # OAuth / integrations (all optional)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    GITHUB_WEBHOOK_SECRET: str | None = None
    GITHUB_API_TOKEN: str | None = None  # optional app token for public metadata reads
    GITHUB_API_BASE: str = "https://api.github.com"

    # Evaluation sandbox
    EVALUATOR_SANDBOX: Literal["subprocess", "docker"] = "subprocess"
    EVALUATOR_TIMEOUT_SECONDS: int = 120
    EVALUATOR_CPU_SECONDS: int = 90
    EVALUATOR_MEMORY_MB: int = 1024
    EVALUATOR_DOCKER_IMAGE: str = "databattles-evaluator:latest"
    EVALUATOR_MAX_ROWS: int = 2_000_000
    # Parent directory for per-job working dirs. With the docker sandbox and a containerized worker, mount a host
    # directory at the SAME path in the worker (e.g. /var/lib/databattles/eval) so `docker run -v` can see it.
    EVALUATOR_WORKDIR_ROOT: str | None = None

    # Jobs
    EMBEDDED_WORKER: bool = False  # run the job worker inside the API process (tiny single-node deployments)
    WORKER_POLL_SECONDS: float = 1.0
    WORKER_ID: str = "worker-1"

    RATE_LIMIT_ENABLED: bool = True
    DEMO_MODE: bool = True  # frontend shows a "demo data" marker on seeded content

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.ENV == "production":
            problems: list[str] = []
            if self.SECRET_KEY == _DEV_SECRET or len(self.SECRET_KEY) < 32:
                problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
            if not self.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be true in production")
            if not self.WEB_BASE_URL.startswith("https://"):
                problems.append("WEB_BASE_URL must use https in production")
            if self.GITHUB_CLIENT_ID and not self.GITHUB_WEBHOOK_SECRET:
                problems.append("GITHUB_WEBHOOK_SECRET is required when GitHub integration is enabled")
            if self.STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
                problems.append("S3_BUCKET is required when STORAGE_BACKEND=s3")
            if self.EMAIL_BACKEND == "smtp" and not self.SMTP_HOST:
                problems.append("SMTP_HOST is required when EMAIL_BACKEND=smtp")
            if problems:
                raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def fernet_key(self) -> bytes:
        if self.ENCRYPTION_KEY:
            return self.ENCRYPTION_KEY.encode()
        digest = hashlib.sha256(("fernet:" + self.SECRET_KEY).encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def github_oauth_enabled(self) -> bool:
        return bool(self.GITHUB_CLIENT_ID and self.GITHUB_CLIENT_SECRET)

    @property
    def allowed_origins(self) -> set[str]:
        return {self.WEB_BASE_URL.rstrip("/"), self.API_BASE_URL.rstrip("/"), *[o.rstrip("/") for o in self.CORS_ORIGINS]}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
