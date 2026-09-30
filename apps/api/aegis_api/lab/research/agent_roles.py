"""LiteratureAgent: synthesizes retrieved sources into cited findings, gaps and follow-up queries.

The agent only *proposes*: findings become LITERATURE memory items whose status the memory write policy
decides (mission scope with provenance → active but untrusted; broader scope → proposed for review), linked
to the sources they cite. Citations are checked against the project's sources — unknown ids are dropped and
findings left without a valid citation are not stored. Nothing is ever marked verified.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.lab.agents.roles import RoleSpec, register_role
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.knowledge.schemas import MemoryScope
from aegis_api.lab.models import AgentRun, MemoryLink, Mission, Project, ResearchSource
from aegis_api.lab.research.sources import top_sources
from engines.lab.states import AgentRole, AutonomyLevel, MemoryCategory

MAX_CONTEXT_SOURCES = 20
DEFAULT_CONTEXT_SOURCES = 12
MAX_ABSTRACT_CHARS = 1500

Statement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
SourceRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class KeyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: Statement
    source_ids: list[SourceRef] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)


class LiteratureSynthesis(BaseModel):
    """Structured output of the LiteratureAgent (validated by the gateway)."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=4000)
    key_findings: list[KeyFinding] = Field(default_factory=list, max_length=20)
    gaps: list[ShortText] = Field(default_factory=list, max_length=20)
    suggested_queries: list[Query] = Field(default_factory=list, max_length=10)


def run_provenance(run: AgentRun) -> dict[str, Any]:
    """Model/prompt/strategy provenance of an agent run (stored with everything the run writes)."""
    return {
        "agent_run_id": str(run.id),
        "mission_id": str(run.mission_id) if run.mission_id else None,
        "provider": run.provider,
        "model": run.model,
        "model_version": run.model_version,
        "prompt_key": run.prompt_key,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "strategy_version_id": str(run.strategy_version_id) if run.strategy_version_id else None,
    }


def _source_ids(values: Any) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values or []:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out[:MAX_CONTEXT_SOURCES]


def _question(db: Session, run: AgentRun) -> str:
    question = str((run.input or {}).get("question") or "").strip()
    if not question and run.mission_id is not None:
        mission = db.get(Mission, run.mission_id)
        if mission is not None:
            question = mission.objective.strip()
    if not question:
        raise ValidationFailed("LiteratureAgent needs a research question (input.question or a mission objective)")
    return question[:4000]


def _describe(source: ResearchSource) -> dict[str, Any]:
    trust = source.trust_metadata or {}
    return {
        "ref": str(source.id),
        "title": source.title,
        "authors": list(source.authors or [])[:6],
        "year": source.publication_date.year if source.publication_date else None,
        "venue": source.publisher,
        "doi": source.doi,
        "source_type": source.source_type,
        "trust": {"score": trust.get("score"), "tier": trust.get("tier"), "flags": trust.get("flags", [])},
        "abstract": (source.abstract or "")[:MAX_ABSTRACT_CHARS],
    }


def build_context(db: Session, actor: Actor, run: AgentRun) -> dict[str, Any]:
    """Template variables for ``literature.synthesize``: ``question`` and the (untrusted) ``sources``."""
    if run.project_id is None:
        raise ValidationFailed("LiteratureAgent runs inside a project")
    project = db.get(Project, run.project_id)
    if project is None:
        raise ValidationFailed("Project not found")
    requested = _source_ids((run.input or {}).get("source_ids"))
    if requested:
        rows = list(
            db.scalars(
                select(ResearchSource).where(ResearchSource.id.in_(requested), ResearchSource.project_id == project.id)
            )
        )
    else:
        limit = int((run.input or {}).get("max_sources") or DEFAULT_CONTEXT_SOURCES)
        limit = max(1, min(limit, MAX_CONTEXT_SOURCES))
        rows = top_sources(db, project, mission_id=run.mission_id, limit=limit)
        if not rows and run.mission_id is not None:
            rows = top_sources(db, project, limit=limit)
    sources = [_describe(s) for s in rows]
    return {
        "question": _question(db, run),
        "sources": json.dumps(sources, ensure_ascii=False, indent=1) if sources else "(no sources retrieved yet)",
    }


def _valid_sources(db: Session, run: AgentRun, output: LiteratureSynthesis) -> set[str]:
    cited = {sid for finding in output.key_findings for sid in finding.source_ids}
    ids = _source_ids(sorted(cited))
    if not ids or run.project_id is None:
        return set()
    rows = db.scalars(
        select(ResearchSource.id).where(ResearchSource.id.in_(ids), ResearchSource.project_id == run.project_id)
    )
    return {str(r) for r in rows}


def _link(db: Session, memory_id: uuid.UUID, organization_id: uuid.UUID, source_ids: list[str]) -> None:
    for source_id in source_ids:
        db.execute(
            insert(MemoryLink)
            .values(
                id=uuid.uuid4(),
                organization_id=organization_id,
                memory_id=memory_id,
                target_type="research_source",
                target_id=uuid.UUID(source_id),
                relation="cites",
                confidence=1.0,
            )
            .on_conflict_do_nothing(constraint="uq_memory_links_edge")
        )


def apply(db: Session, actor: Actor, run: AgentRun, output: BaseModel) -> dict[str, Any]:
    """Persist findings as LITERATURE memory (policy-gated) linked to the cited sources."""
    from aegis_api.lab.knowledge.memory import write_memory
    from aegis_api.lab.knowledge.schemas import MemoryWrite

    synthesis = output if isinstance(output, LiteratureSynthesis) else LiteratureSynthesis.model_validate(output)
    if run.project_id is None:
        raise ValidationFailed("LiteratureAgent runs inside a project")
    valid = _valid_sources(db, run, synthesis)
    provenance = run_provenance(run)
    scope: MemoryScope = "mission" if run.mission_id else "project"
    memory_ids: list[str] = []
    statuses: dict[str, int] = {}
    dropped: set[str] = set()
    uncited = 0
    for finding in synthesis.key_findings:
        cited = [sid for sid in dict.fromkeys(finding.source_ids) if sid in valid]
        dropped |= {sid for sid in finding.source_ids if sid not in valid}
        if not cited:
            uncited += 1
            continue
        memory = write_memory(
            db,
            actor,
            MemoryWrite(
                category=MemoryCategory.LITERATURE,
                scope=scope,
                project_id=str(run.project_id),
                mission_id=str(run.mission_id) if run.mission_id else None,
                title=finding.statement[:200],
                content=finding.statement,
                source_type="agent",
                source_ref={"agent_run_id": str(run.id), "source_ids": cited},
                confidence=finding.confidence,
                provenance=provenance,
                tags=["literature", "finding"],
            ),
        )
        _link(db, memory.id, run.organization_id, cited)
        memory_ids.append(str(memory.id))
        statuses[memory.status] = statuses.get(memory.status, 0) + 1
    gaps_memory_id: str | None = None
    if synthesis.gaps:
        gaps = write_memory(
            db,
            actor,
            MemoryWrite(
                category=MemoryCategory.LITERATURE,
                scope=scope,
                project_id=str(run.project_id),
                mission_id=str(run.mission_id) if run.mission_id else None,
                title="Open questions from the literature",
                content="\n".join(f"- {gap}" for gap in synthesis.gaps),
                source_type="agent",
                source_ref={"agent_run_id": str(run.id)},
                confidence=0.5,
                provenance=provenance,
                tags=["literature", "gaps"],
            ),
        )
        gaps_memory_id = str(gaps.id)
        statuses[gaps.status] = statuses.get(gaps.status, 0) + 1
    return {
        "memory_ids": memory_ids,
        "gaps_memory_id": gaps_memory_id,
        "memory_statuses": statuses,
        "findings": len(synthesis.key_findings),
        "uncited_findings": uncited,
        "dropped_source_ids": sorted(dropped),
        "suggested_queries": list(synthesis.suggested_queries),
        "summary": synthesis.summary[:1000],
    }


LITERATURE_ROLE = register_role(
    RoleSpec(
        role=AgentRole.LITERATURE,
        task_type="research",
        prompt_key="literature.synthesize",
        output_model=LiteratureSynthesis,
        build_context=build_context,
        apply=apply,
        description="Synthesizes retrieved papers into cited findings, open gaps and follow-up queries.",
        default_tools=("paper_search", "memory_search"),
        default_permissions=frozenset({"research:read", "memory:read", "memory:write"}),
        min_autonomy_level=AutonomyLevel.L1_RESEARCH_AUTOMATION,
        untrusted_context_keys=("sources",),
        tier="default",
        temperature=0.2,
        max_steps=6,
    )
)
