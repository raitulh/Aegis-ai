"""Data-processing flows: ``DatasetProcessingWorkflow`` and ``ArtifactProcessingWorkflow``.

Flow code is deterministic and IO-free (see ``aegis_api.lab.workflows.ctx``); the work happens in the
``data.process_*`` activities. Launched after commit by :func:`launch_dataset_processing` /
:func:`launch_artifact_processing` (one run per version; re-launching returns the existing run).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aegis_api.lab.workflows.ctx import WorkflowContext
from aegis_api.lab.workflows.definitions import register_flow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from aegis_api.lab.core.actor import Actor
    from aegis_api.lab.models import ArtifactVersion, DatasetVersion, WorkflowRun


def _require_id(data: dict[str, Any], key: str, kind: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} input requires '{key}'")
    return value


async def dataset_processing_flow(ctx: WorkflowContext, input: dict[str, Any]) -> dict[str, Any]:
    """Re-verify a dataset version and store its full profile (row counts, column statistics)."""
    version_id = _require_id(input, "dataset_version_id", "DatasetProcessingWorkflow")
    return await ctx.activity(
        "data.process_dataset_version", {"dataset_version_id": version_id}, step_key="data.process_dataset_version"
    )


async def artifact_processing_flow(ctx: WorkflowContext, input: dict[str, Any]) -> dict[str, Any]:
    """Re-verify an artifact version and (re-)scan it; only ``scan_status`` changes."""
    version_id = _require_id(input, "artifact_version_id", "ArtifactProcessingWorkflow")
    return await ctx.activity(
        "data.process_artifact_version",
        {"artifact_version_id": version_id, "force": bool(input.get("force", False))},
        step_key="data.process_artifact_version",
    )


register_flow(
    "DatasetProcessingWorkflow",
    dataset_processing_flow,
    description="Verify and profile an immutable dataset version.",
)
register_flow(
    "ArtifactProcessingWorkflow",
    artifact_processing_flow,
    description="Verify, type-sniff and malware-scan an artifact version.",
)


# -- launch helpers (called by the data services inside the caller's transaction) --------------------
def launch_dataset_processing(db: Session, actor: Actor, version: DatasetVersion) -> WorkflowRun:
    from aegis_api.lab.workflows.launcher import launch_workflow

    return launch_workflow(
        db,
        actor,
        "DatasetProcessingWorkflow",
        subject_type="dataset_version",
        subject_id=version.id,
        input={"dataset_version_id": str(version.id)},
        project_id=version.project_id,
    )


def launch_artifact_processing(db: Session, actor: Actor, version: ArtifactVersion) -> WorkflowRun:
    from aegis_api.lab.workflows.launcher import launch_workflow

    return launch_workflow(
        db,
        actor,
        "ArtifactProcessingWorkflow",
        subject_type="artifact_version",
        subject_id=version.id,
        input={"artifact_version_id": str(version.id)},
        project_id=version.project_id,
    )
