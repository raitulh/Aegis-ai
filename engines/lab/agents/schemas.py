"""Structured output schemas for each agent role.

Agents must answer with JSON matching these schemas (sent to providers as ``response_format`` JSON Schema).
Outputs are validated before anything is persisted; invalid outputs fail the step rather than being "fixed up".
Scores and confidences are the model's *self-assessment* and are always stored as such — never as measurements.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MissionBrief(_Out):
    restated_objective: str
    measurable_success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PlanPhase(_Out):
    name: str
    goal: str
    steps: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class ResearchPlan(_Out):
    summary: str
    phases: list[PlanPhase] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list, max_length=20)
    hypothesis_directions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class Finding(_Out):
    statement: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class LiteratureReview(_Out):
    queries: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class Relation(_Out):
    subject: str
    relation: Literal[
        "supports",
        "contradicts",
        "extends",
        "uses",
        "tests",
        "generated_by",
        "failed_because_of",
        "evolved_from",
        "reproduces",
        "verified_by",
        "derived_from",
    ]
    object: str
    source_ids: list[str] = Field(default_factory=list)


class KnowledgeSynthesis(_Out):
    summary: str
    entities: list[dict[str, str]] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


class MeasurablePrediction(_Out):
    metric: str
    direction: Literal["increase", "decrease", "no_change"]
    magnitude: float | None = None
    unit: str | None = None


class HypothesisProposal(_Out):
    statement: str = Field(min_length=10)
    rationale: str
    expected_outcome: str
    measurable_prediction: MeasurablePrediction
    assumptions: list[str] = Field(default_factory=list)
    novelty_notes: str = ""
    feasibility: float = Field(ge=0, le=1)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)
    supporting_source_ids: list[str] = Field(default_factory=list)
    contradicting_source_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict, description="Candidate configuration to test")


class HypothesisSet(_Out):
    hypotheses: list[HypothesisProposal] = Field(min_length=1, max_length=20)


class HypothesisCritique(_Out):
    hypothesis_index: int = Field(ge=0)
    falsifiable: bool
    novelty: float = Field(ge=0, le=1)
    feasibility: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=1)
    recommendation: Literal["select", "revise", "reject"]


class HypothesisCritiques(_Out):
    critiques: list[HypothesisCritique]


class ExperimentDesign(_Out):
    rationale: str
    spec: dict[str, Any] = Field(description="ExperimentSpec fields (validated by the platform)")


class CodeFile(_Out):
    path: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}$")
    content: str = Field(max_length=200_000)


class CodeBundle(_Out):
    files: list[CodeFile] = Field(min_length=1, max_length=40)
    entrypoint: list[str] = Field(min_length=1)
    notes: str = ""


class DataAnalysis(_Out):
    observations: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    suggested_checks: list[str] = Field(default_factory=list)


class StatisticalInterpretation(_Out):
    interpretation: str
    caveats: list[str] = Field(default_factory=list)
    recommended_tests: list[str] = Field(default_factory=list)


class RecoverySuggestion(_Out):
    action: str
    parameter_changes: dict[str, Any] = Field(default_factory=dict)


class FailureDiagnosis(_Out):
    root_cause: str
    contributing_factors: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    recovery_suggestions: list[RecoverySuggestion] = Field(default_factory=list)
    lesson: str = ""


class MutationProposal(_Out):
    parameter_changes: dict[str, Any]
    rationale: str


class MutationProposals(_Out):
    proposals: list[MutationProposal] = Field(min_length=1, max_length=16)


class ReproductionPlan(_Out):
    differences_to_control: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class VerificationAssessment(_Out):
    assessment: Literal["supports", "contradicts", "insufficient"]
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ReviewCheck(_Out):
    check: str
    verdict: Literal["pass", "concern", "fail"]
    comment: str = ""


class ScientificReview(_Out):
    checks: list[ReviewCheck] = Field(default_factory=list)
    overall: Literal["acceptable", "needs_revision", "reject"]
    overclaiming_flags: list[str] = Field(default_factory=list)


class ReportNarrative(_Out):
    executive_summary: str
    sections: dict[str, str] = Field(default_factory=dict)
    cited_evidence_ids: list[str] = Field(default_factory=list)


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Provider-friendly JSON schema (inlined definitions, no titles)."""
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    return _strip_titles(schema)


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node
