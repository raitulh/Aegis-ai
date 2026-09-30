"""Reproducibility manifest: everything needed to re-run an experiment run bit-for-bit (or explain why not)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CodeSnapshot(BaseModel):
    artifact_id: str | None = None
    sha256: str | None = None
    git_repository: str | None = None
    git_commit: str | None = None
    entrypoint: list[str] = Field(default_factory=list)


class ModelProvenance(BaseModel):
    provider: str | None = None
    model: str | None = None
    model_revision: str | None = None
    prompt_ref: str | None = None
    prompt_sha256: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    agent_run_id: str | None = None


class EnvironmentProvenance(BaseModel):
    environment_id: str | None = None
    image: str | None = None
    image_digest: str | None = None
    lockfile_sha256: str | None = None
    packages: dict[str, str] = Field(default_factory=dict)
    env_var_names: list[str] = Field(default_factory=list, description="Names only; values are never recorded")


class HardwareProvenance(BaseModel):
    backend: str | None = None
    runtime: str | None = None
    cpu: float | None = None
    memory_mb: int | None = None
    gpu_type: str | None = None
    gpu_count: int = 0
    node: str | None = None


class OutputRecord(BaseModel):
    name: str
    artifact_id: str | None = None
    sha256: str
    size_bytes: int | None = None


class ReproducibilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    experiment_id: str
    experiment_version: int
    run_id: str
    run_kind: str
    code: CodeSnapshot = Field(default_factory=CodeSnapshot)
    generated_by: ModelProvenance | None = None
    dataset_version_id: str | None = None
    dataset_checksum: str | None = None
    environment: EnvironmentProvenance = Field(default_factory=EnvironmentProvenance)
    seeds: list[int] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    hardware: HardwareProvenance = Field(default_factory=HardwareProvenance)
    resources: dict[str, Any] = Field(default_factory=dict)
    command: list[str] = Field(default_factory=list)
    network_policy: str = "none"
    logs_artifact_id: str | None = None
    outputs: list[OutputRecord] = Field(default_factory=list)
    harness: dict[str, Any] = Field(default_factory=dict)
    evaluators: list[dict[str, Any]] = Field(default_factory=list)
    spec_sha256: str | None = None

    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("code.sha256", "code snapshot checksum"),
    ("environment.image", "container image"),
    ("environment.image_digest", "container image digest"),
    ("seeds", "random seeds"),
    ("command", "command"),
    ("hardware.backend", "execution backend"),
    ("outputs", "output checksums"),
    ("harness.key", "evaluation harness"),
    ("evaluators", "evaluator versions"),
    ("spec_sha256", "experiment specification hash"),
)


def _get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def completeness(
    manifest: ReproducibilityManifest, *, requires_dataset: bool = False, llm_generated_code: bool = False
) -> tuple[float, list[str]]:
    """Return (score 0..1, missing descriptions)."""
    data = manifest.model_dump(mode="json")
    required = list(REQUIRED_FIELDS)
    if requires_dataset:
        required += [("dataset_version_id", "dataset version"), ("dataset_checksum", "dataset checksum")]
    if llm_generated_code:
        required += [("generated_by.model", "code-generation model"), ("generated_by.prompt_sha256", "prompt hash")]
    missing = [label for path, label in required if _get(data, path) in (None, "", [], {})]
    return round(1 - len(missing) / len(required), 4), missing
