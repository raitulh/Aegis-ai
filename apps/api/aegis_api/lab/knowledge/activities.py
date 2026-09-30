"""Knowledge activities (idempotent; object-storage reads, parsing and embedding outside transactions).

* ``knowledge.ingest_source`` {source_id} → {document_id?, chunks, status}
* ``knowledge.ingest_artifact_version`` {artifact_version_id, project_id, source_id?} → {document_id, chunks, status}
* ``knowledge.record_mission_memory`` {mission_id, category, title, content, source_ref, confidence,
  source_type?} → {memory_id, status}
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.knowledge.ingestion import ingest_artifact_version_job, ingest_source_job
from aegis_api.lab.knowledge.memory import write_memory
from aegis_api.lab.knowledge.schemas import MemoryWrite
from aegis_api.lab.models import Mission
from aegis_api.lab.workflows.registry import ActivityContext, activity
from engines.lab.states import MemoryCategory

MISSION_MEMORY_SOURCES = frozenset({"system", "experiment", "agent", "tool", "external"})


def _required(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise ValidationFailed(f"'{key}' is required")
    return value


@activity("knowledge.ingest_source", timeout_seconds=600, heartbeat_seconds=120)
def ingest_source(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.heartbeat({"stage": "ingesting", "source_id": payload.get("source_id")})
    document = ingest_source_job(ctx.actor, _required(payload, "source_id"))
    if document is None:
        return {"document_id": None, "chunks": 0, "status": "skipped"}
    return {"document_id": str(document.id), "chunks": document.chunk_count, "status": document.status}


@activity("knowledge.ingest_artifact_version", timeout_seconds=900, heartbeat_seconds=120)
def ingest_artifact_version(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.heartbeat({"stage": "ingesting", "artifact_version_id": payload.get("artifact_version_id")})
    document = ingest_artifact_version_job(
        ctx.actor,
        _required(payload, "artifact_version_id"),
        project_id=_required(payload, "project_id"),
        source_id=payload.get("source_id"),
        title=payload.get("title"),
    )
    assert document is not None
    return {"document_id": str(document.id), "chunks": document.chunk_count, "status": document.status}


def _source_type(payload: dict[str, Any], category: str) -> tuple[str, dict[str, Any]]:
    """Source type + provenance for a workflow-recorded memory (the write policy re-checks both)."""
    source_ref = payload.get("source_ref") or {}
    requested = payload.get("source_type")
    if requested is not None and requested not in MISSION_MEMORY_SOURCES:
        raise ValidationFailed(f"source_type must be one of {', '.join(sorted(MISSION_MEMORY_SOURCES))}")
    if requested is None:
        experiment_run = source_ref.get("experiment_run_id") if isinstance(source_ref, dict) else None
        requested = "experiment" if category == MemoryCategory.EXPERIMENT and experiment_run else "system"
    provenance: dict[str, Any] = {"component": "workflow"}
    if isinstance(source_ref, dict):
        for key in ("experiment_run_id", "tool_name", "invocation_id", "source_uri", "retrieved_at"):
            if source_ref.get(key):
                provenance[key] = source_ref[key]
    return str(requested), provenance


@activity("knowledge.record_mission_memory", timeout_seconds=120)
def record_mission_memory(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    mission_id = _required(payload, "mission_id")
    try:
        category = MemoryCategory(str(_required(payload, "category")))
    except ValueError as exc:
        raise ValidationFailed(f"Unknown memory category '{payload.get('category')}'") from exc
    source_type, provenance = _source_type(payload, category)
    with tenant_uow(ctx.actor) as db:
        mission = get_owned(db, Mission, mission_id, ctx.actor, label="Mission")
        try:
            data = MemoryWrite.model_validate(
                {
                    "category": category,
                    "scope": "mission",
                    "mission_id": str(mission.id),
                    "project_id": str(mission.project_id),
                    "title": str(_required(payload, "title"))[:500],
                    "content": str(_required(payload, "content"))[:20_000],
                    "source_type": source_type,
                    "source_ref": payload.get("source_ref") or {},
                    "confidence": payload.get("confidence", 0.5),
                    "provenance": provenance,
                    "tags": [str(t) for t in payload.get("tags") or []][:20],
                }
            )
        except PydanticValidationError as exc:
            raise ValidationFailed(f"Invalid mission memory: {exc.errors()[0].get('msg', 'invalid')}") from exc
        memory = write_memory(db, ctx.actor, data)
        return {"memory_id": str(memory.id), "status": memory.status, "trust_level": memory.trust_level}
