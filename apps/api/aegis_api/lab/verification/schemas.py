"""API contract of the verification context: claims, evidence links, lineage, verifications, reproductions,
discoveries, scientific reviews and mission reports."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_api.schemas.common import ORMModel

ClaimType = Literal["quantitative", "comparative", "causal", "qualitative"]
ClaimDirection = Literal["increase", "decrease"]
EvidenceRelation = Literal["supports", "contradicts", "context"]
EvidenceType = Literal[
    "experiment_run",
    "experiment_comparison",
    "experiment",
    "experiment_version",
    "artifact_version",
    "evaluation_run",
    "dataset_version",
    "code_snapshot",
    "environment",
    "research_source",
    "memory",
    "reproduction",
    "verification",
    "agent_run",
]
DiscoveryStatusName = Literal[
    "CANDIDATE",
    "VERIFICATION_PENDING",
    "VERIFIED",
    "HUMAN_REVIEW",
    "APPROVED",
    "REJECTED",
    "CONTESTED",
    "PUBLISHED",
]
ReviewSubjectType = Literal["mission", "experiment", "claim", "discovery", "report"]

MAX_CONDITIONS_BYTES = 16 * 1024
MAX_SEED = 2**31 - 1


def _finite(value: float | None, name: str) -> float | None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------------------------
class ClaimCreate(_Strict):
    project_id: str = Field(description="Project the claim belongs to")
    mission_id: str | None = Field(default=None, description="Mission that produced the claim (same project)")
    statement: str = Field(min_length=3, max_length=4000)
    claim_type: ClaimType = "comparative"
    domain: str = Field(default="general", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\- ]+$")
    metric: str | None = Field(default=None, max_length=120)
    direction: ClaimDirection | None = Field(default=None, description="Claimed direction of the effect")
    value: float | None = Field(default=None, description="Point estimate of the claimed effect")
    ci_low: float | None = None
    ci_high: float | None = None
    units: str | None = Field(default=None, max_length=48)
    conditions: dict[str, Any] = Field(default_factory=dict, description="Scope qualifiers (dataset, setting…)")
    criteria_profile: str = Field(default="default", min_length=1, max_length=48)

    @field_validator("statement")
    @classmethod
    def _statement(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("statement is too short")
        return cleaned

    @field_validator("conditions")
    @classmethod
    def _conditions(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_CONDITIONS_BYTES:
            raise ValueError(f"conditions must serialize to at most {MAX_CONDITIONS_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def _interval(self) -> ClaimCreate:
        for name in ("value", "ci_low", "ci_high"):
            _finite(getattr(self, name), name)
        if (self.ci_low is None) != (self.ci_high is None):
            raise ValueError("ci_low and ci_high must be given together")
        if self.ci_low is not None and self.ci_high is not None and self.ci_low > self.ci_high:
            raise ValueError("ci_low must not exceed ci_high")
        return self


class ClaimOut(ORMModel):
    id: str
    project_id: str
    workspace_id: str
    mission_id: str | None = None
    statement: str
    claim_type: str
    domain: str
    metric: str | None = None
    direction: str | None = None
    value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    units: str | None = None
    conditions: dict[str, Any]
    source_type: str
    source_id: str | None = None
    extracted_by: str
    extractor_agent_run_id: str | None = None
    status: str
    confidence: float
    uncertainty: dict[str, Any]
    criteria_profile: str
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ClaimEvidenceOut(ORMModel):
    id: str
    claim_id: str
    evidence_type: str
    ref_id: str
    relation: str
    weight: float
    note: str | None = None
    evidence_id: str | None = Field(default=None, description="Anchor in the hash-chained evidence store")
    content_hash: str | None = None
    created_at: datetime


class ClaimDetailOut(ClaimOut):
    evidence: list[ClaimEvidenceOut]
    verification_ids: list[str] = Field(default_factory=list)


class EvidenceLinkCreate(_Strict):
    evidence_type: EvidenceType
    ref_id: str
    relation: EvidenceRelation = "supports"
    note: str | None = Field(default=None, max_length=4000)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class ClaimExtractRequest(_Strict):
    comparison_id: str
    criteria_profile: str | None = Field(default=None, max_length=48)


class ClaimExtractionOut(BaseModel):
    items: list[ClaimOut]
    created: bool
    skipped_reason: str | None = None


# ---------------------------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------------------------
class LineageNodeOut(BaseModel):
    id: str
    type: str
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class LineageEdgeOut(BaseModel):
    source: str
    target: str
    relation: str


class LineageCompletenessOut(BaseModel):
    required_chain: list[str]
    present: list[str]
    missing: list[str]
    missing_links: list[dict[str, Any]]
    complete: bool


class LineageOut(BaseModel):
    schema_version: str
    claim_id: str
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]
    completeness: LineageCompletenessOut
    digest: str = Field(description="SHA-256 of the canonical graph (anchorable in the evidence chain)")
    not_applicable: list[str] = Field(
        default_factory=list, description="Chain types that do not apply to this claim (e.g. no model involvement)"
    )


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------
class VerifyRequest(_Strict):
    profile: str | None = Field(default=None, max_length=48, description="Criteria profile (default: the claim's)")


class VerificationRunOut(ORMModel):
    id: str
    verification_id: str
    check_type: str
    status: str
    passed: bool | None = None
    result: dict[str, Any]
    evaluator_key: str | None = None
    evaluator_version: str | None = None
    agent_run_id: str | None = None
    reproduction_id: str | None = None
    evaluation_run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime


class VerificationOut(ORMModel):
    id: str
    claim_id: str
    project_id: str
    mission_id: str | None = None
    discovery_id: str | None = None
    criteria_profile: str
    criteria: dict[str, Any]
    status: str
    verdict: str | None = None
    confidence: float | None = None
    checks: dict[str, Any]
    generating_agent_version_ids: list[str]
    requested_by_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    workflow_run_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class VerificationDetailOut(VerificationOut):
    runs: list[VerificationRunOut]


# ---------------------------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------------------------
class MetricTolerance(_Strict):
    abs_tol: float | None = Field(
        default=None, ge=0.0, le=1e12, validation_alias=AliasChoices("abs_tol", "abs"), serialization_alias="abs_tol"
    )
    rel_tol: float | None = Field(
        default=None, ge=0.0, le=1.0, validation_alias=AliasChoices("rel_tol", "rel"), serialization_alias="rel_tol"
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)


class ReproductionTolerance(_Strict):
    """Within-tolerance rule: ``|reproduced - original| <= max(abs_tol, rel_tol × |original|)``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)

    abs_tol: float = Field(default=0.0, ge=0.0, le=1e12, validation_alias=AliasChoices("abs_tol", "abs"))
    rel_tol: float = Field(default=0.05, ge=0.0, le=1.0, validation_alias=AliasChoices("rel_tol", "rel"))
    per_metric: dict[str, MetricTolerance] = Field(default_factory=dict, max_length=50)
    one_sided: bool = Field(default=False, description="A better-than-original reproduction also counts")

    @field_validator("per_metric")
    @classmethod
    def _metric_names(cls, value: dict[str, MetricTolerance]) -> dict[str, MetricTolerance]:
        for name in value:
            if not 1 <= len(name) <= 120:
                raise ValueError("metric names must be 1-120 characters")
        return value


class ReproductionCreate(_Strict):
    experiment_id: str
    seeds: list[int] | None = Field(
        default=None, max_length=64, description="Fresh seeds (default: derived deterministically from the originals)"
    )
    tolerance: ReproductionTolerance | None = None

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("seeds must not be empty when given")
        if any(isinstance(s, bool) or not 0 <= s <= MAX_SEED for s in value):
            raise ValueError(f"seeds must be integers between 0 and {MAX_SEED}")
        if len(set(value)) != len(value):
            raise ValueError("seeds must be unique")
        return value


class ReproductionOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None = None
    experiment_id: str
    experiment_version_id: str
    original_run_ids: list[str]
    reproduction_run_ids: list[str]
    tolerance: dict[str, Any]
    status: str
    verdict: str | None = None
    metric_deltas: dict[str, Any]
    environment_diff: dict[str, Any]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    workflow_run_id: str | None = None
    requested_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------------------------
# Discoveries
# ---------------------------------------------------------------------------------------------
class DiscoveryCreate(_Strict):
    claim_id: str
    title: str | None = Field(default=None, min_length=3, max_length=300)
    summary: str | None = Field(default=None, max_length=10_000)


class DiscoveryOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None = None
    claim_id: str
    title: str
    summary: str | None = None
    evidence_ids: list[str]
    experiment_ids: list[str]
    reproduction_ids: list[str]
    verifier_ids: list[str]
    strategy_version_id: str | None = None
    confidence: float
    status: str
    version: int
    approval_id: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by_id: str | None = None
    published_at: datetime | None = None
    is_demo: bool
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DiscoveryVersionOut(ORMModel):
    id: str
    discovery_id: str
    version: int
    status: str
    snapshot: dict[str, Any]
    reason: str | None = None
    content_hash: str
    created_by_id: str | None = None
    created_at: datetime


class DiscoveryDetailOut(DiscoveryOut):
    versions: list[DiscoveryVersionOut]


class DiscoveryTransitionIn(_Strict):
    status: DiscoveryStatusName
    reason: str = Field(min_length=3, max_length=2000)


class DiscoveryReviewRequestIn(_Strict):
    note: str | None = Field(default=None, max_length=2000)


class DiscoveryReviewOut(BaseModel):
    discovery: DiscoveryOut
    approval_id: str
    approval_status: str


# ---------------------------------------------------------------------------------------------
# Scientific reviews
# ---------------------------------------------------------------------------------------------
class ReviewCreate(_Strict):
    subject_type: ReviewSubjectType
    subject_id: str


class ReviewOut(ORMModel):
    id: str
    project_id: str | None = None
    mission_id: str | None = None
    subject_type: str
    subject_id: str
    reviewer_type: str
    agent_run_id: str | None = None
    reviewer_user_id: str | None = None
    checks: dict[str, Any]
    findings: list[Any]
    verdict: str
    score: float | None = None
    created_at: datetime


# ---------------------------------------------------------------------------------------------
# Mission reports
# ---------------------------------------------------------------------------------------------
class MissionReportSummaryOut(ORMModel):
    id: str
    mission_id: str
    project_id: str
    version: int
    status: str
    markdown_artifact_id: str | None = Field(default=None, description="Artifact VERSION id of the Markdown file")
    evidence_ids: list[str]
    generated_by: str
    agent_run_id: str | None = None
    content_hash: str
    created_by_id: str | None = None
    created_at: datetime


class MissionReportOut(MissionReportSummaryOut):
    content: dict[str, Any]


class MissionReportDetailOut(MissionReportOut):
    markdown: str


class EvidenceChainOut(BaseModel):
    valid: bool
    records: int
    broken_indices: list[int]
    head: str | None = None
