"""Agent role catalogue: task class, prompt template, output schema, default tools and limits per role.

These are *defaults*. Organizations create agent definitions (versioned, in the database) that reference a
role and may narrow — never widen — tools, budgets and timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from engines.lab.agents import schemas as s
from engines.lab.enums import AgentRole, MemoryScope, TaskClass


@dataclass(frozen=True)
class AgentRoleSpec:
    role: AgentRole
    description: str
    task_class: TaskClass
    prompt: str  # prompt template name (versioned in the registry)
    output_model: type[BaseModel]
    default_tools: tuple[str, ...] = ()
    max_steps: int = 4
    timeout_seconds: int = 300
    readable_memory: tuple[MemoryScope, ...] = (MemoryScope.MISSION, MemoryScope.PROJECT)
    can_propose_memory: bool = False


ROLE_SPECS: dict[AgentRole, AgentRoleSpec] = {
    spec.role: spec
    for spec in (
        AgentRoleSpec(
            AgentRole.QUEST,
            "Clarifies the mission into measurable objectives",
            TaskClass.PLANNING,
            "quest.brief",
            s.MissionBrief,
        ),
        AgentRoleSpec(
            AgentRole.PLANNER,
            "Plans research phases, queries and hypothesis directions",
            TaskClass.PLANNING,
            "planner.plan",
            s.ResearchPlan,
            ("memory_search",),
        ),
        AgentRoleSpec(
            AgentRole.LITERATURE,
            "Searches and synthesizes literature with citations",
            TaskClass.RESEARCH,
            "literature.review",
            s.LiteratureReview,
            ("paper_search", "web_search", "url_fetch", "memory_search"),
            max_steps=6,
            timeout_seconds=900,
            can_propose_memory=True,
        ),
        AgentRoleSpec(
            AgentRole.KNOWLEDGE,
            "Extracts entities and relations into the knowledge graph",
            TaskClass.EXTRACTION,
            "knowledge.synthesize",
            s.KnowledgeSynthesis,
            ("file_search", "memory_search"),
            can_propose_memory=True,
        ),
        AgentRoleSpec(
            AgentRole.HYPOTHESIS,
            "Generates falsifiable, measurable hypotheses",
            TaskClass.HYPOTHESIS_GENERATION,
            "hypothesis.generate",
            s.HypothesisSet,
            ("memory_search",),
        ),
        AgentRoleSpec(
            AgentRole.HYPOTHESIS_CRITIC,
            "Critiques hypotheses for falsifiability, novelty and feasibility",
            TaskClass.HYPOTHESIS_CRITIQUE,
            "hypothesis.critique",
            s.HypothesisCritiques,
            ("memory_search",),
        ),
        AgentRoleSpec(
            AgentRole.EXPERIMENT_DESIGNER,
            "Designs controlled, reproducible experiments",
            TaskClass.EXPERIMENT_DESIGN,
            "experiment.design",
            s.ExperimentDesign,
            ("dataset_search", "memory_search"),
        ),
        AgentRoleSpec(
            AgentRole.CODING,
            "Writes experiment code for the sandbox",
            TaskClass.CODING,
            "coding.experiment",
            s.CodeBundle,
            (),
            timeout_seconds=600,
        ),
        AgentRoleSpec(
            AgentRole.SIMULATION,
            "Writes simulation code and configurations",
            TaskClass.CODING,
            "simulation.build",
            s.CodeBundle,
            (),
            timeout_seconds=600,
        ),
        AgentRoleSpec(
            AgentRole.DATA_ANALYST,
            "Inspects results and data for anomalies",
            TaskClass.RESULT_ANALYSIS,
            "analysis.data",
            s.DataAnalysis,
            ("object_storage",),
        ),
        AgentRoleSpec(
            AgentRole.STATISTICAL_ANALYST,
            "Interprets statistical evidence and caveats",
            TaskClass.RESULT_ANALYSIS,
            "analysis.statistics",
            s.StatisticalInterpretation,
        ),
        AgentRoleSpec(
            AgentRole.FAILURE_ANALYZER,
            "Assists diagnosis of classified failures",
            TaskClass.FAILURE_ANALYSIS,
            "failure.diagnose",
            s.FailureDiagnosis,
            ("memory_search",),
            can_propose_memory=True,
        ),
        AgentRoleSpec(
            AgentRole.EVOLUTION,
            "Proposes bounded parameter mutations",
            TaskClass.STRATEGY_EVOLUTION,
            "evolution.propose",
            s.MutationProposals,
            ("memory_search",),
        ),
        AgentRoleSpec(
            AgentRole.REPRODUCTION,
            "Plans independent reproduction checks",
            TaskClass.VERIFICATION,
            "reproduction.plan",
            s.ReproductionPlan,
        ),
        AgentRoleSpec(
            AgentRole.VERIFIER,
            "Independently assesses whether evidence supports a claim",
            TaskClass.VERIFICATION,
            "verification.assess",
            s.VerificationAssessment,
        ),
        AgentRoleSpec(
            AgentRole.SCIENTIFIC_REVIEWER,
            "Reviews design, statistics, leakage and overclaiming",
            TaskClass.SCIENTIFIC_REVIEW,
            "review.scientific",
            s.ScientificReview,
        ),
        AgentRoleSpec(
            AgentRole.REPORT,
            "Writes evidence-cited report narrative",
            TaskClass.REPORT_GENERATION,
            "report.narrative",
            s.ReportNarrative,
        ),
    )
}


def spec_for(role: str) -> AgentRoleSpec:
    return ROLE_SPECS[AgentRole(role)]
