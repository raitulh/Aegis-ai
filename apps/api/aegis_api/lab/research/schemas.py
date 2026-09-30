"""API contract of the research context (tasks, plan review, events, sources, papers)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_api.lab.research.literature import SUPPORTED_SOURCES
from aegis_api.schemas.common import ORMModel

ResearchKind = Literal["literature_search", "deep_research", "web_research"]
SourceKind = Literal["paper", "preprint", "web_page", "dataset", "book", "other"]
LiteratureSource = Literal["openalex", "arxiv"]
_AGENT_PATTERN = r"^deep-research[a-z0-9.\-]{0,100}$"


class ResearchParameters(BaseModel):
    """Execution parameters of a research task (validated; unknown keys are rejected)."""

    model_config = ConfigDict(extra="forbid")

    sources: list[LiteratureSource] | None = Field(
        default=None, max_length=len(SUPPORTED_SOURCES), description="Literature sources (default from settings)"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of sources to keep")
    collaborative_planning: bool = Field(
        default=False, description="Deep Research: review and approve the research plan before it runs"
    )
    agent: str | None = Field(
        default=None,
        pattern=_AGENT_PATTERN,
        description="Deep Research agent id (defaults to GEMINI_DEEP_RESEARCH_AGENT)",
    )
    estimated_cost_usd: float | None = Field(
        default=None, ge=0, le=10_000, description="Caller's cost estimate, evaluated by governance policy"
    )

    @field_validator("sources")
    @classmethod
    def _unique_sources(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value is not None else None


class ResearchTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    mission_id: str | None = None
    kind: ResearchKind = "literature_search"
    title: str = Field(min_length=1, max_length=300)
    query: str = Field(min_length=1, max_length=4000)
    parameters: ResearchParameters = Field(default_factory=ResearchParameters)

    @field_validator("title", "query")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ResearchTaskOut(ORMModel):
    id: str
    project_id: str
    workspace_id: str
    mission_id: str | None = None
    kind: str
    title: str
    query: str
    parameters: dict[str, Any]
    status: str
    plan_status: str
    plan: dict[str, Any] | None = None
    provider: str | None = None
    provider_agent: str | None = None
    provider_interaction_id: str | None = None
    provider_status: str | None = None
    summary: str | None = None
    source_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    report_artifact_id: str | None = Field(
        default=None, description="Id of the report's immutable artifact version (deep/web research)"
    )
    approval_id: str | None = None
    workflow_run_id: str | None = None
    agent_run_id: str | None = None
    error: str | None = None
    poll_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_polled_at: datetime | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("plan", mode="before")
    @classmethod
    def _public_plan(cls, value: Any) -> Any:
        """Internal control state (pending provider actions and claims) is not part of the contract."""
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if not k.startswith("_")}
        return value


class ResearchTaskDetailOut(ResearchTaskOut):
    report: str | None = Field(default=None, description="Provider report text (untrusted content)")


class ResearchEventOut(ORMModel):
    id: str
    research_task_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class PlanApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=4000, description="Optional guidance for the run")


class PlanReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1, max_length=4000, description="What the revised plan should change")


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class SourceCreate(BaseModel):
    """A manually registered scientific source."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    source_type: SourceKind = "paper"
    title: str = Field(min_length=1, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)
    doi: str | None = Field(default=None, max_length=255)
    authors: list[str] = Field(default_factory=list, max_length=100)
    abstract: str | None = Field(default=None, max_length=20_000)
    publication_date: date | None = None
    venue: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=300)
    citation: str | None = Field(default=None, max_length=2000)
    external_ids: dict[str, str] = Field(default_factory=dict, max_length=10)
    is_peer_reviewed: bool | None = None
    is_retracted: bool = False
    work_type: str | None = Field(default=None, max_length=40, description="e.g. journal-article, proceedings")

    @field_validator("authors")
    @classmethod
    def _authors(cls, value: list[str]) -> list[str]:
        cleaned = [a.strip()[:200] for a in value if a and a.strip()]
        return cleaned

    @field_validator("external_ids")
    @classmethod
    def _external_ids(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, item in value.items():
            k = key.strip()[:40]
            if k and item and str(item).strip():
                out[k] = str(item).strip()[:200]
        return out


class SourceOut(ORMModel):
    id: str
    project_id: str
    research_task_id: str | None = None
    source_type: str
    title: str
    url: str | None = None
    canonical_url: str | None = None
    doi: str | None = None
    external_ids: dict[str, Any]
    publisher: str | None = None
    authors: list[Any]
    publication_date: date | None = None
    retrieved_at: datetime | None = None
    citation: str | None = None
    abstract: str | None = Field(default=None, description="Untrusted bibliographic text")
    checksum: str | None = None
    trust_metadata: dict[str, Any] = Field(description="Deterministic source-quality assessment (rules + inputs)")
    status: str
    discovered_by: str
    content_artifact_id: str | None = None
    created_at: datetime
    updated_at: datetime


PaperOut = SourceOut
