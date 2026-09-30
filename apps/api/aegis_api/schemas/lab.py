"""API contract for the AI Scientist Evolution Lab (``/api/v1``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aegis_api.schemas.common import ORMModel
from engines.lab.enums import AutonomyLevel


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- organization / workspaces / projects / teams --------------------------------------------------------------


class OrganizationOut(ORMModel):
    id: str
    name: str
    slug: str
    plan: str | None = None
    created_at: datetime


class OrganizationUpdate(_In):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceIn(_In):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] | None = None


class WorkspaceUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] | None = None
    archived: bool | None = None


class WorkspaceOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None
    is_default: bool
    is_demo: bool
    settings: dict[str, Any]
    created_at: datetime


class MemberIn(_In):
    user_id: str
    role: str = Field(min_length=1, max_length=48)


class ProjectIn(_In):
    name: str = Field(min_length=1, max_length=200)
    workspace_id: str | None = None
    description: str | None = Field(default=None, max_length=8000)
    domain: str = Field(default="general", max_length=48)
    visibility: Literal["organization", "private"] = "organization"
    budget: dict[str, float] | None = None
    verification_criteria: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class ProjectUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    visibility: Literal["organization", "private"] | None = None
    budget: dict[str, float] | None = None
    verification_criteria: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    archived: bool | None = None


class ProjectOut(ORMModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    description: str | None
    domain: str
    visibility: str
    budget: dict[str, Any]
    verification_criteria: dict[str, Any]
    settings: dict[str, Any]
    is_demo: bool
    created_at: datetime


class ProjectMemberOut(ORMModel):
    user_id: str
    role: str
    created_at: datetime


class TeamIn(_In):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class TeamMembersIn(_In):
    user_ids: list[str] = Field(default_factory=list, max_length=500)


class TeamOut(ORMModel):
    id: str
    name: str
    description: str | None
    created_at: datetime


# --- missions ---------------------------------------------------------------------------------------------------


class MissionIn(_In):
    project_id: str
    title: str = Field(min_length=3, max_length=300)
    objective: str = Field(min_length=10, max_length=20000)
    domain: str | None = Field(default=None, max_length=48)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    budget: dict[str, float] | None = None
    compute_budget: dict[str, float] | None = None
    time_budget_seconds: int | None = Field(default=None, gt=0)
    deadline: datetime | None = None
    allowed_tools: list[str] = Field(default_factory=list, max_length=40)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    autonomy_level: AutonomyLevel | None = None
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    max_cycles: int = Field(default=1, ge=1, le=50)


class MissionUpdate(_In):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    objective: str | None = Field(default=None, min_length=10, max_length=20000)
    domain: str | None = None
    constraints: list[str] | None = None
    success_criteria: list[dict[str, Any]] | None = None
    budget: dict[str, float] | None = None
    compute_budget: dict[str, float] | None = None
    time_budget_seconds: int | None = None
    deadline: datetime | None = None
    allowed_tools: list[str] | None = None
    risk_level: Literal["low", "medium", "high", "critical"] | None = None
    approval_policy: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    max_cycles: int | None = Field(default=None, ge=1, le=50)


class AutonomyIn(_In):
    autonomy_level: AutonomyLevel
    reason: str = Field(min_length=3, max_length=2000)


class ReasonIn(_In):
    reason: str | None = Field(default=None, max_length=2000)


class MissionOut(ORMModel):
    id: str
    project_id: str
    workspace_id: str
    title: str
    objective: str
    domain: str
    constraints: list[str]
    success_criteria: list[dict[str, Any]]
    budget: dict[str, Any]
    compute_budget: dict[str, Any]
    time_budget_seconds: int | None
    deadline: datetime | None
    allowed_tools: list[str]
    risk_level: str
    autonomy_level: str
    approval_policy: dict[str, Any]
    config: dict[str, Any]
    status: str
    phase: str | None
    status_reason: str | None
    current_cycle: int
    max_cycles: int
    brief: dict[str, Any]
    plan: dict[str, Any]
    workflow_run_id: str | None
    evidence_head_hash: str | None
    evidence_seq: int
    is_demo: bool
    lock_version: int
    created_by_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MissionVersionOut(ORMModel):
    id: str
    version: int
    snapshot: dict[str, Any]
    change_summary: str | None
    created_by_id: str | None
    created_at: datetime


class EventOut(ORMModel):
    id: str
    seq: int
    event_type: str
    level: str
    message: str
    data: dict[str, Any]
    actor: str | None
    trace_id: str | None
    created_at: datetime


class WorkflowRunOut(ORMModel):
    id: str
    workflow: str
    business_key: str
    engine: str
    status: str
    waiting_on: str | None
    wake_at: datetime | None
    attempts: int
    error: str | None
    result: dict[str, Any] | None
    parent_run_id: str | None
    temporal_workflow_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# --- agents / prompts -------------------------------------------------------------------------------------------


class AgentIn(_In):
    role: str
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    project_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AgentVersionIn(_In):
    config: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=2000)


class AgentOut(ORMModel):
    id: str
    role: str
    name: str
    description: str | None
    status: str
    project_id: str | None
    current_version_id: str | None
    created_at: datetime


class AgentVersionOut(ORMModel):
    id: str
    version: int
    config: dict[str, Any]
    config_sha256: str
    change_note: str | None
    created_at: datetime


class AgentRunOut(ORMModel):
    id: str
    mission_id: str | None
    project_id: str | None
    agent_id: str | None
    agent_version_id: str | None
    parent_run_id: str | None
    workflow_run_id: str | None
    role: str
    purpose: str | None
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    output_valid: bool | None
    error: str | None
    prompt_ref: str | None
    prompt_template_sha256: str | None
    prompt_hash: str | None
    provider: str | None
    model: str | None
    model_params: dict[str, Any]
    tools_used: list[str]
    steps: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    injection_score: float
    trace_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class PromptIn(_In):
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    template: str = Field(min_length=10, max_length=40000)
    variables: list[str] = Field(default_factory=list)
    description: str | None = None


# --- research / knowledge / memory ------------------------------------------------------------------------------


class ResearchIn(_In):
    project_id: str
    mission_id: str | None = None
    title: str = Field(min_length=3, max_length=300)
    question: str = Field(min_length=10, max_length=10000)
    mode: Literal["deep_research", "literature_pipeline"] = "literature_pipeline"
    require_plan_approval: bool = True


class ResearchTaskOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    title: str
    question: str
    mode: str
    status: str
    require_plan_approval: bool
    plan: dict[str, Any]
    provider: str | None
    provider_agent: str | None
    provider_interaction_id: str | None
    provider_status: str | None
    citations_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    report_artifact_id: str | None
    error: str | None
    workflow_run_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ResearchEventOut(ORMModel):
    seq: int
    event_type: str
    provider_event_id: str | None
    data: dict[str, Any]
    created_at: datetime


class SourceOut(ORMModel):
    id: str
    project_id: str
    research_task_id: str | None
    source_type: str
    url: str | None
    title: str | None
    publisher: str | None
    authors: list[str]
    publication_date: str | None
    retrieved_at: datetime | None
    doi: str | None
    arxiv_id: str | None
    snippet: str | None
    checksum: str
    trust_metadata: dict[str, Any]
    created_at: datetime


class UrlIngestIn(_In):
    project_id: str
    url: str = Field(min_length=8, max_length=2000)
    title: str | None = None


class DocumentOut(ORMModel):
    id: str
    project_id: str
    title: str
    doc_type: str
    source_kind: str
    source_url: str | None
    filename: str | None
    current_version_id: str | None
    status: str
    created_at: datetime


class SearchIn(_In):
    query: str = Field(min_length=2, max_length=500)
    project_id: str | None = None
    mission_id: str | None = None
    scopes: list[str] | None = None
    categories: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class MemoryIn(_In):
    scope: Literal["short_term", "mission", "project", "organization"]
    category: Literal["literature", "experiment", "failure", "strategy", "evidence", "discovery", "general"] = "general"
    content: str = Field(min_length=3, max_length=20000)
    title: str | None = Field(default=None, max_length=300)
    project_id: str | None = None
    mission_id: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_ref: dict[str, Any] = Field(default_factory=dict)
    sensitivity: Literal["normal", "sensitive"] = "normal"


class MemoryReviewIn(_In):
    approve: bool
    reason: str | None = Field(default=None, max_length=2000)


class MemorySupersedeIn(_In):
    content: str = Field(min_length=3, max_length=20000)


class MemoryOut(ORMModel):
    id: str
    scope: str
    category: str
    title: str | None
    content: str
    confidence: float
    source: str
    source_ref: dict[str, Any]
    status: str
    requires_review: bool
    project_id: str | None
    mission_id: str | None
    version: int
    supersedes_id: str | None
    injection_score: float
    sensitivity: str
    created_at: datetime


# --- hypotheses / experiments / runs ------------------------------------------------------------------------------


class HypothesisIn(_In):
    project_id: str
    mission_id: str | None = None
    statement: str = Field(min_length=10, max_length=4000)
    rationale: str | None = None
    expected_outcome: str | None = None
    measurable_prediction: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)
    novelty_notes: str | None = None
    feasibility: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_hypothesis_id: str | None = None


class TransitionIn(_In):
    status: str
    reason: str | None = Field(default=None, max_length=2000)


class HypothesisEvidenceIn(_In):
    source_type: Literal["research_source", "evidence", "memory", "claim", "run"]
    source_id: str = Field(min_length=1, max_length=64)
    relation: Literal["supports", "contradicts"]
    note: str | None = None


class HypothesisOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    statement: str
    rationale: str | None
    expected_outcome: str | None
    measurable_prediction: dict[str, Any]
    assumptions: list[str]
    novelty_notes: str | None
    feasibility: float | None
    estimated_cost_usd: float | None
    confidence: float | None
    parameters: dict[str, Any]
    status: str
    critique: dict[str, Any]
    critique_score: float | None
    selection_reason: str | None
    outcome_summary: str | None
    generated_by_run_id: str | None
    created_at: datetime


class ExperimentIn(_In):
    project_id: str
    mission_id: str | None = None
    hypothesis_id: str | None = None
    title: str = Field(min_length=3, max_length=300)
    spec: dict[str, Any]
    change_note: str | None = None


class ExperimentVersionIn(_In):
    spec: dict[str, Any]
    change_note: str | None = None


class CodeBundleIn(_In):
    files: list[dict[str, str]] = Field(min_length=1, max_length=40)
    entrypoint: list[str] = Field(min_length=1, max_length=10)
    notes: str = ""


class ExperimentOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    hypothesis_id: str | None
    strategy_version_id: str | None
    title: str
    status: str
    current_version_id: str | None
    version_count: int
    outcome: dict[str, Any]
    error: str | None
    retry_count: int
    is_demo: bool
    created_at: datetime


class ExperimentVersionOut(ORMModel):
    id: str
    version: int
    spec: dict[str, Any]
    spec_sha256: str
    validation: dict[str, Any]
    code_artifact_id: str | None
    code_sha256: str | None
    generated_by_run_id: str | None
    change_note: str | None
    created_at: datetime


class ExperimentRunOut(ORMModel):
    id: str
    experiment_id: str
    experiment_version_id: str
    mission_id: str | None
    run_kind: str
    variant: str | None
    seed: int
    parameters: dict[str, Any]
    status: str
    attempt: int
    metrics: dict[str, Any]
    self_reported: bool
    resources: dict[str, Any]
    manifest_sha256: str | None
    reproduction_of_run_id: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class EvaluationRunOut(ORMModel):
    id: str
    experiment_id: str
    experiment_version_id: str | None
    evaluator_key: str
    evaluator_version: str
    evaluator_fingerprint: str
    verdict: str
    passed: bool | None
    confidence: float
    metrics: dict[str, Any]
    warnings: list[str]
    evidence_id: str | None
    independent: bool
    evaluated_by: str
    created_at: datetime


class RunComparisonOut(ORMModel):
    id: str
    metric: str
    direction: str
    baseline_mean: float | None
    candidate_mean: float | None
    delta: float | None
    relative_change: float | None
    ci_low: float | None
    ci_high: float | None
    test: str | None
    p_value: float | None
    p_adjusted: float | None
    effect_size: float | None
    config_diff: dict[str, Any]
    created_at: datetime


# --- datasets / artifacts / environments --------------------------------------------------------------------------


class DatasetIn(_In):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    license: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=1000)


class DatasetOut(ORMModel):
    id: str
    project_id: str
    name: str
    description: str | None
    license: str | None
    source: str | None
    current_version_id: str | None
    is_demo: bool
    created_at: datetime


class DatasetVersionOut(ORMModel):
    id: str
    dataset_id: str
    version: int
    parent_version_id: str | None
    checksum: str
    size_bytes: int
    schema_info: dict[str, Any] = Field(serialization_alias="schema")
    license: str | None
    source: str | None
    transformations: list[dict[str, Any]]
    files: list[dict[str, Any]]
    status: str
    created_at: datetime


class ArtifactOut(ORMModel):
    id: str
    project_id: str | None
    mission_id: str | None
    experiment_run_id: str | None
    kind: str
    name: str
    current_version_id: str | None
    retention_class: str
    created_by: str | None
    created_at: datetime


class ArtifactVersionOut(ORMModel):
    id: str
    version: int
    sha256: str
    size_bytes: int
    content_type: str
    scan_status: str
    purged_at: datetime | None
    created_at: datetime


class EnvironmentIn(_In):
    name: str = Field(min_length=1, max_length=120)
    image: str = Field(min_length=3, max_length=500)
    description: str | None = None
    packages: dict[str, str] | list[str] = Field(default_factory=dict)
    runtime: Literal["cpu", "gpu"] = "cpu"


# --- claims / verification / discoveries / failures ---------------------------------------------------------------


class ClaimIn(_In):
    project_id: str
    statement: str = Field(min_length=10, max_length=4000)
    mission_id: str | None = None
    experiment_id: str | None = None
    claim_type: str | None = None
    metric: str | None = None


class ClaimOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    experiment_id: str | None
    hypothesis_id: str | None
    statement: str
    claim_type: str
    metric: str | None
    source: str
    status: str
    confidence: float
    details: dict[str, Any]
    uncertainty: dict[str, Any]
    verified_at: datetime | None
    created_at: datetime


class VerifyIn(_In):
    criteria_overrides: dict[str, Any] | None = None


class DecisionIn(_In):
    approve: bool
    reason: str | None = Field(default=None, max_length=2000)


class DiscoveryOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    claim_id: str
    title: str
    summary: str
    status: str
    confidence: float
    limitations: list[str]
    evidence_ids: list[str]
    experiment_ids: list[str]
    reproduction_ids: list[str]
    verifier_ids: list[str]
    reviewed_by_id: str | None
    reviewed_at: datetime | None
    is_demo: bool
    created_at: datetime


class FailureOut(ORMModel):
    id: str
    project_id: str | None
    mission_id: str | None
    experiment_id: str | None
    experiment_run_id: str | None
    stage: str
    failure_type: str
    rule_id: str
    confidence: float
    root_cause: str
    evidence_lines: list[str]
    signature: str
    recurrence_count: int
    detected_at: datetime
    diagnosis: dict[str, Any]
    similar_failure_ids: list[str]
    recovery_actions: list[dict[str, Any]]
    recovery_status: str
    lesson_id: str | None
    evidence_id: str | None


class LessonOut(ORMModel):
    id: str
    project_id: str | None
    signature: str
    failure_type: str
    lesson: str
    recovery_kind: str
    resolved: bool | None
    confidence: float
    occurrences: int
    memory_id: str | None
    created_at: datetime


# --- strategies / evolution -------------------------------------------------------------------------------------


class StrategyIn(_In):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    project_id: str | None = None
    definition: dict[str, Any]


class StrategyVersionIn(_In):
    definition: dict[str, Any]
    parent_version_id: str | None = None


class PromoteIn(_In):
    version_id: str
    reason: str = Field(min_length=3, max_length=2000)
    override_gate: bool = False


class StrategyOut(ORMModel):
    id: str
    project_id: str | None
    name: str
    kind: str
    description: str | None
    status: str
    promoted_version_id: str | None
    version_count: int
    is_demo: bool
    created_at: datetime


class EvolutionIn(_In):
    strategy_id: str
    mode: Literal["experiment", "benchmark"] = "experiment"
    experiment_template_id: str | None = None
    benchmark: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    max_generations: int = Field(default=3, ge=1, le=100)
    mission_id: str | None = None


# --- governance -------------------------------------------------------------------------------------------------


class ApprovalDecisionIn(_In):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalOut(ORMModel):
    id: str
    project_id: str | None
    mission_id: str | None
    kind: str
    status: str
    resource_type: str
    resource_id: str
    title: str
    request: dict[str, Any]
    requester_type: str
    requester_label: str | None
    approver_id: str | None
    required_permission: str
    policy: dict[str, Any]
    decision_reason: str | None
    decided_at: datetime | None
    expires_at: datetime | None
    workflow_run_id: str | None
    created_at: datetime


class PolicyIn(_In):
    key: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    document: dict[str, Any]
    change_note: str | None = None


class PolicyVersionIn(_In):
    document: dict[str, Any]
    change_note: str | None = None


class PolicyStatusIn(_In):
    status: Literal["active", "disabled"]


class PolicySimulateIn(_In):
    action: str = Field(min_length=1, max_length=80)
    facts: dict[str, Any] = Field(default_factory=dict)


class LabPolicyOut(ORMModel):
    id: str
    key: str
    name: str
    description: str | None
    status: str
    current_version_id: str | None
    version_count: int
    created_at: datetime


class MCPServerIn(_In):
    name: str = Field(min_length=1, max_length=120)
    endpoint: str = Field(min_length=8, max_length=1000)
    auth_method: Literal["none", "bearer", "header"] = "none"
    auth_header: str | None = Field(default=None, max_length=80)
    credential: str | None = Field(default=None, max_length=4000)
    risk_level: Literal["low", "medium", "high", "critical"] = "high"
    allowed_tools: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)


class MCPToolApproveIn(_In):
    approve: bool
    risk_level: Literal["low", "medium", "high", "critical"] | None = None


class MCPServerOut(ORMModel):
    id: str
    name: str
    endpoint: str
    transport: str
    auth_method: str
    allowed_tools: list[str]
    project_ids: list[str]
    risk_level: str
    status: str
    server_info: dict[str, Any]
    protocol_version: str | None
    last_health_check_at: datetime | None
    last_health_status: str | None
    created_at: datetime


class MCPToolOut(ORMModel):
    id: str
    server_id: str
    name: str
    description: str | None
    input_schema: dict[str, Any]
    schema_sha256: str
    risk_level: str
    approved: bool
    last_seen_at: datetime | None


class ToolCallOut(ORMModel):
    id: str
    mission_id: str | None
    agent_run_id: str | None
    tool_id: str
    status: str
    risk_level: str
    policy_decision: str
    arguments: dict[str, Any]
    result_summary: str | None
    result_sha256: str | None
    injection_score: float
    latency_ms: int | None
    approval_id: str | None
    error: str | None
    created_at: datetime


class ModelConfigIn(_In):
    provider: Literal["gemini", "openai", "anthropic", "ollama"]
    model: str = Field(min_length=1, max_length=160)
    tier: Literal["fast", "default", "reasoning", "deep_research"] = "default"
    features: list[str] = Field(default_factory=list)
    input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float | None = Field(default=None, ge=0)
    typical_latency_ms: int | None = Field(default=None, ge=0)
    is_agent: bool = False
    enabled: bool = True


class RoutePreviewIn(_In):
    task_type: str
    complexity: float = Field(default=0.5, ge=0, le=1)
    latency_budget_ms: int | None = Field(default=None, gt=0)
    cost_budget_usd: float | None = Field(default=None, ge=0)


class WebhookIn(_In):
    url: str = Field(min_length=8, max_length=1000)
    events: list[str] = Field(default_factory=list, max_length=50)
    description: str | None = Field(default=None, max_length=300)


class WebhookOut(ORMModel):
    id: str
    url: str
    description: str | None
    events: list[str]
    active: bool
    failure_count: int
    last_delivery_at: datetime | None
    created_at: datetime


class BenchmarkIn(_In):
    suite_key: str
    subject_ref: str | None = None
    project_id: str | None = None


class BenchmarkRunOut(ORMModel):
    id: str
    suite_key: str
    suite_version: str
    subject_kind: str
    subject_ref: str
    status: str
    n_cases: int
    mean_score: float | None
    pass_rate: float | None
    comparison: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class ReportOut(ORMModel):
    id: str
    project_id: str
    mission_id: str | None
    title: str
    status: str
    markdown_artifact_id: str | None
    content_hash: str
    cited_evidence_ids: list[str]
    citation_check: dict[str, Any]
    generated_by: str | None
    created_at: datetime


class Accepted(BaseModel):
    """202 response for work dispatched to a durable workflow."""

    id: str
    status: str
    workflow_run_id: str | None = None
