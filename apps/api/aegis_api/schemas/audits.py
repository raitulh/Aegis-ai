"""Audit, test, evidence and report schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field

from aegis_api.models.enums import Intensity, TestCategory
from aegis_api.schemas.common import ORMModel


class AuditConfig(BaseModel):
    concurrency: Annotated[int, Field(ge=1, le=16)] = 4
    cases_per_category: Annotated[int, Field(ge=1, le=100)] | None = None
    repetitions: Annotated[int, Field(ge=1, le=20)] | None = None
    evaluator_preference: str | None = Field(default=None, pattern=r"^(local|external)$")
    attributes: list[str] = Field(default_factory=list)
    use_model_judge: bool = True
    seed: Annotated[int, Field(ge=0)] = 1337
    corpus: list[dict[str, Any]] = Field(default_factory=list)
    corpus_name: str = "custom"


class AuditCreate(BaseModel):
    system_id: str
    name: str | None = None
    categories: list[TestCategory] = Field(min_length=1)
    policy_version_ids: list[str] = Field(default_factory=list)
    intensity: Intensity = Intensity.STANDARD
    config: AuditConfig = Field(default_factory=AuditConfig)
    start: bool = True


class AuditOut(ORMModel):
    id: str
    system_id: str
    name: str
    kind: str
    status: str
    intensity: str
    categories: list[str]
    policy_version_ids: list[str]
    progress: int
    stage: str | None
    test_count: int
    tests_completed: int
    findings_count: int
    evidence_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    missing_categories: list[dict[str, Any]]
    summary: dict[str, Any]
    cost: dict[str, Any]
    is_demo: bool
    created_at: datetime


class AuditSummary(ORMModel):
    id: str
    system_id: str
    name: str
    status: str
    intensity: str
    progress: int
    findings_count: int
    created_at: datetime
    completed_at: datetime | None


class AuditEventOut(ORMModel):
    id: str
    seq: int
    type: str
    stage: str | None
    level: str
    message: str
    progress: int
    data: dict[str, Any]
    created_at: datetime


class TestResultOut(ORMModel):
    id: str
    category: str
    test_type: str
    control_ref: str | None
    status: str
    score: float | None
    severity: str | None
    confidence: float | None
    summary: str | None
    observed: dict[str, Any]
    evaluator_key: str
    evaluator_version: str
    finding_id: str | None


class TestMatrixRow(BaseModel):
    category: str
    test_type: str
    samples: int
    failures: int
    errors: int
    status: str
    severity: str
    confidence: float | None


class ClaimOut(ORMModel):
    id: str
    text: str
    status: str
    support_confidence: float
    contradiction_confidence: float
    reason: str | None
    span_start: int | None
    span_end: int | None
    source_title: str | None
    source_url: str | None
    source_excerpt: str | None
    verification_method: str | None


class EvidenceOut(ORMModel):
    id: str
    seq: int
    kind: str
    title: str
    content: dict[str, Any]
    sensitive: bool
    content_hash: str
    chain_hash: str
    confidence_level: str
    confidence_reasons: list[str]
    created_at: datetime


class EvidenceRevealOut(BaseModel):
    id: str
    content: dict[str, Any]


class ReportOut(ORMModel):
    id: str
    audit_id: str
    title: str
    status: str
    content: dict[str, Any]
    content_hash: str
    created_at: datetime


class AuditCompareOut(BaseModel):
    audit_a: str
    audit_b: str
    new_findings: list[dict[str, Any]]
    resolved_findings: list[dict[str, Any]]
    regressions: list[dict[str, Any]]
    unchanged: int
    risk_delta: dict[str, Any]
