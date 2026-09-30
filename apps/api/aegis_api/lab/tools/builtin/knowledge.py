"""Knowledge tools: ``memory_search`` / ``file_search`` (knowledge hybrid search) and ``dataset_search``.

All searches run in a short RLS-scoped unit of work as the calling actor, restricted to the invocation's
project; the knowledge context's search applies memory status/sensitivity rules and project visibility.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from aegis_api.lab.core.access import load_project
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.models import Dataset
from aegis_api.lab.tools.registry import (
    ToolDefinition,
    ToolExecutionContext,
    ToolOutput,
    ToolUnavailable,
    register_tool,
)
from engines.lab.states import RiskLevel

MAX_SNIPPET_CHARS = 800


def _knowledge_search(ctx: ToolExecutionContext, *, query: str, kinds: list[str], limit: int, **extra: Any) -> list[Any]:
    try:
        module = importlib.import_module("aegis_api.lab.knowledge.search")
    except ModuleNotFoundError as exc:
        raise ToolUnavailable("Knowledge search is not installed in this deployment") from exc
    search = getattr(module, "search", None)
    query_model = getattr(module, "SearchQuery", None)
    if not callable(search) or query_model is None:
        raise ToolUnavailable("Knowledge search is not available in this deployment")
    params: dict[str, Any] = {"q": query, "project_id": ctx.project_id, "kinds": kinds, "limit": limit}
    if ctx.mission_id is not None:
        params["mission_id"] = ctx.mission_id
    params.update({k: v for k, v in extra.items() if v is not None})
    ctx.check()
    with tenant_uow(ctx.actor) as db:
        load_project(db, ctx.actor, ctx.project_id)
        hits = search(db, ctx.actor, query_model(**params))
        return [_hit_view(hit) for hit in list(hits or [])[:limit]]


def _hit_view(hit: Any) -> dict[str, Any]:
    if isinstance(hit, dict):
        data = dict(hit)
    elif callable(getattr(hit, "model_dump", None)):
        data = hit.model_dump(mode="json")
    else:
        data = {k: getattr(hit, k, None) for k in ("kind", "id", "title", "snippet", "score")}
    snippet = data.get("snippet")
    return {
        "kind": data.get("kind"),
        "id": str(data.get("id")) if data.get("id") is not None else None,
        "title": (str(data.get("title"))[:300] if data.get("title") else None),
        "snippet": (str(snippet)[:MAX_SNIPPET_CHARS] if snippet else None),
        "score": data.get("score"),
        "source_quality": data.get("source_quality"),
    }


# ---------------------------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------------------------
class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=500)
    categories: list[Literal["fact", "method", "negative_result", "pitfall", "dataset_note"]] | None = Field(
        default=None, max_length=5
    )
    limit: int = Field(default=10, ge=1, le=50)


def _memory_search(ctx: ToolExecutionContext, args: MemorySearchInput) -> ToolOutput:
    hits = _knowledge_search(ctx, query=args.query, kinds=["memory"], limit=args.limit, categories=args.categories)
    return ToolOutput(content={"query": args.query, "count": len(hits), "results": hits}, metadata={"count": len(hits)})


MEMORY_SEARCH = register_tool(
    ToolDefinition(
        name="memory_search",
        description="Search the lab's scientific memory (facts, methods, negative results, pitfalls) of this project.",
        input_model=MemorySearchInput,
        handler=_memory_search,
        risk_level=RiskLevel.LOW,
        permissions=frozenset({"memory:read"}),
        rate_limit_per_min=120,
        category="knowledge",
    )
)


# ---------------------------------------------------------------------------------------------
# file_search
# ---------------------------------------------------------------------------------------------
class FileSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=500)
    include_sources: bool = Field(default=False, description="Also match research source metadata")
    limit: int = Field(default=10, ge=1, le=50)


def _file_search(ctx: ToolExecutionContext, args: FileSearchInput) -> ToolOutput:
    kinds = ["chunk", "source"] if args.include_sources else ["chunk"]
    hits = _knowledge_search(ctx, query=args.query, kinds=kinds, limit=args.limit)
    return ToolOutput(content={"query": args.query, "count": len(hits), "results": hits}, metadata={"count": len(hits)})


FILE_SEARCH = register_tool(
    ToolDefinition(
        name="file_search",
        description="Search the text of documents ingested into this project (papers, reports, uploaded files).",
        input_model=FileSearchInput,
        handler=_file_search,
        risk_level=RiskLevel.LOW,
        permissions=frozenset({"memory:read"}),
        rate_limit_per_min=120,
        category="knowledge",
    )
)


# ---------------------------------------------------------------------------------------------
# dataset_search
# ---------------------------------------------------------------------------------------------
class DatasetSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=200, description="Words matched against dataset names/descriptions")
    tag: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _dataset_search(ctx: ToolExecutionContext, args: DatasetSearchInput) -> ToolOutput:
    ctx.check()
    with tenant_uow(ctx.actor) as db:
        project = load_project(db, ctx.actor, ctx.project_id, "dataset:read")
        stmt = select(Dataset).where(
            Dataset.organization_id == ctx.organization_id, Dataset.project_id == project.id
        )
        for word in [w for w in args.query.split() if w][:8]:
            pattern = _like(word)
            stmt = stmt.where(
                or_(Dataset.name.ilike(pattern, escape="\\"), Dataset.description.ilike(pattern, escape="\\"))
            )
        rows = db.scalars(stmt.order_by(Dataset.created_at.desc(), Dataset.id.desc()).limit(200)).all()
        if args.tag:
            rows = [row for row in rows if args.tag in (row.tags or [])]
        results = [
            {
                "id": str(row.id),
                "name": row.name,
                "description": (row.description or "")[:500] or None,
                "license": row.license,
                "source": row.source,
                "tags": list(row.tags or [])[:20],
                "current_version_id": str(row.current_version_id) if row.current_version_id else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows[: args.limit]
        ]
    return ToolOutput(
        content={"query": args.query, "count": len(results), "datasets": results},
        metadata={"count": len(results), "project_id": str(uuid.UUID(str(ctx.project_id)))},
    )


DATASET_SEARCH = register_tool(
    ToolDefinition(
        name="dataset_search",
        description="Find datasets registered in this project by name, description or tag.",
        input_model=DatasetSearchInput,
        handler=_dataset_search,
        risk_level=RiskLevel.LOW,
        permissions=frozenset({"dataset:read"}),
        rate_limit_per_min=120,
        category="knowledge",
    )
)
