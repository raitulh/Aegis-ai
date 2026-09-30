"""Prompt registry API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aegis_api.lab.llm.schemas import TaskType
from aegis_api.schemas.common import ORMModel

KEY_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$"
VARIABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"


class PromptTemplateCreate(BaseModel):
    """A new organization version of a prompt. The data-handling policy is appended to ``system`` when absent."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=2, max_length=120, pattern=KEY_PATTERN)
    task_type: TaskType
    description: str | None = Field(default=None, max_length=2000)
    system: str = Field(min_length=1, max_length=50_000)
    user: str = Field(min_length=1, max_length=50_000)
    variables: list[str] = Field(default_factory=list, max_length=64)
    output_schema: dict[str, Any] | None = None
    status: Literal["active", "draft"] = "active"


class PromptStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "deprecated"]


class PromptTemplateOut(ORMModel):
    id: str
    organization_id: str | None
    scope: Literal["system", "organization"]
    key: str
    version: int
    task_type: str
    description: str | None
    system_template: str
    user_template: str
    variables: list[Any]
    output_schema: dict[str, Any] | None
    status: str
    content_hash: str
    created_by_id: str | None
    created_at: datetime
    updated_at: datetime | None = None


class PromptKeyOut(BaseModel):
    key: str
    task_type: str
    description: str | None
    effective_template_id: str | None = Field(description="Template render_prompt uses when no version is pinned")
    effective_version: int | None
    effective_scope: Literal["system", "organization"] | None
    versions: list[dict[str, Any]] = Field(description="[{version, scope, status, template_id}] newest first")
