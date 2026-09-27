"""Aegis policy DSL (YAML) — the machine-readable form of a compiled policy.

Example::

    id: HR
    name: Hiring AI Policy
    version: "2.1"
    controls:
      - id: FAIR-003
        requirement: "Protected attributes must not materially alter decisions"
        test_type: counterfactual
        severity: high
        threshold: {max_delta: 0.05}
      - id: HUM-004
        requirement: "Final decisions require human review"
        test_type: human_oversight
        threshold: {tools: [send_rejection_email, update_candidate_status]}
      - id: PRIV-001
        requirement: "AI must not expose sensitive personal information"
        test_type: pii_leakage
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from engines.common.types import Severity, TestType

VALID_TEST_TYPES = {t.value for t in TestType}
DOMAIN_BY_TEST_TYPE: dict[str, str] = {
    TestType.COUNTERFACTUAL: "fairness",
    TestType.GROUNDEDNESS: "truthfulness",
    TestType.SOURCE_REQUIRED: "truthfulness",
    TestType.PII_LEAKAGE: "privacy",
    TestType.SAFETY_REFUSAL: "safety",
    TestType.PROMPT_INJECTION: "security",
    TestType.HUMAN_OVERSIGHT: "governance",
    TestType.AUTHORIZATION: "governance",
    TestType.TOOL_PERMISSION: "governance",
    TestType.OUTPUT_CONSTRAINT: "governance",
    TestType.CUSTOM_RULE: "governance",
}
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{2,4}$")


class ControlDSL(BaseModel):
    id: str
    requirement: str
    test_type: str
    name: str | None = None
    severity: str = Severity.MEDIUM
    automation: str = "automated"
    threshold: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] | None = None
    required_evidence: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    source: dict[str, Any] = Field(default_factory=dict)

    @field_validator("test_type")
    @classmethod
    def _valid_test_type(cls, v: str) -> str:
        if v not in VALID_TEST_TYPES:
            raise ValueError(f"unknown test_type '{v}' (allowed: {sorted(VALID_TEST_TYPES)})")
        return v

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, v: str) -> str:
        if v not in {s.value for s in Severity}:
            raise ValueError(f"unknown severity '{v}'")
        return v

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"control id '{v}' must look like 'FAIR-003'")
        return v

    @property
    def domain(self) -> str:
        return DOMAIN_BY_TEST_TYPE.get(self.test_type, "governance")


class PolicyDSL(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    description: str | None = None
    controls: list[ControlDSL] = Field(default_factory=list)

    @field_validator("controls")
    @classmethod
    def _unique_ids(cls, controls: list[ControlDSL]) -> list[ControlDSL]:
        seen: set[str] = set()
        for c in controls:
            if c.id in seen:
                raise ValueError(f"duplicate control id '{c.id}'")
            seen.add(c.id)
        return controls


def parse_dsl(text: str) -> PolicyDSL:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"policy DSL is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("policy DSL must be a mapping")
    try:
        return PolicyDSL.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"policy DSL validation failed: {exc.errors(include_url=False)}") from exc


def to_yaml(policy: PolicyDSL) -> str:
    return yaml.safe_dump(
        policy.model_dump(exclude_none=True, exclude_defaults=False), sort_keys=False, allow_unicode=True
    )
