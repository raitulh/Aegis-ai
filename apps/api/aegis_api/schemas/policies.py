"""Policy, version, requirement, control and framework schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aegis_api.models.enums import Severity, TestType
from aegis_api.schemas.common import ORMModel


class PolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    description: str | None = None
    category: str | None = None
    owner_name: str | None = None
    source_text: str | None = Field(default=None, description="Policy text to compile into controls")


class PolicyOut(ORMModel):
    id: str
    name: str
    key: str
    description: str | None
    category: str | None
    status: str
    owner_name: str | None
    current_version_id: str | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class PolicyVersionOut(ORMModel):
    id: str
    version: str
    status: str
    source_type: str
    compiled_at: datetime | None
    compiler_version: str | None
    compile_report: dict[str, Any]
    change_note: str | None
    created_at: datetime


class RequirementOut(ORMModel):
    id: str
    requirement_key: str
    text: str
    modality: str
    page_number: int | None
    section: str | None
    source_excerpt: str
    confidence: float
    needs_human_review: bool


class ControlOut(ORMModel):
    id: str
    control_id: str
    name: str
    description: str | None
    domain: str
    test_type: str
    threshold: dict[str, Any]
    severity: str
    automation: str
    required_evidence: list[str]
    status: str
    confidence: float
    needs_human_review: bool
    source: str


class ManualControlCreate(BaseModel):
    control_id: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,7}-\d{2,4}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    test_type: TestType
    severity: Severity = Severity.MEDIUM
    threshold: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] | None = None
    required_evidence: list[str] = Field(default_factory=list)


class CompileResultOut(BaseModel):
    policy_version_id: str
    requirements: list[RequirementOut]
    controls: list[ControlOut]
    report: dict[str, Any]


class PolicyVersionCreate(BaseModel):
    source_text: str | None = None
    dsl_yaml: str | None = None
    change_note: str | None = None


class PolicyDiffOut(BaseModel):
    from_version: str
    to_version: str
    added: list[str]
    removed: list[str]
    changed: list[dict[str, Any]]
    unchanged: int
    requires_reaudit: bool
    affected_controls: list[str]


class FrameworkControlOut(ORMModel):
    id: str
    ref: str
    title: str
    description: str | None
    group: str | None
    domains: list[str]
    test_types: list[str]


class FrameworkOut(ORMModel):
    id: str
    key: str
    name: str
    version: str
    version_date: str | None
    publisher: str | None
    description: str | None
    source_url: str | None
    kind: str
    disclaimer: str | None
    controls: list[FrameworkControlOut] = Field(default_factory=list)


class ControlMappingOut(BaseModel):
    control_id: str
    control_ref: str
    framework_key: str
    framework_ref: str
    framework_title: str
    rationale: str | None
    confidence: float
    mapping_type: str
