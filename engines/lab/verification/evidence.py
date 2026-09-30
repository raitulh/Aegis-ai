"""EvidenceValidator: provenance completeness and integrity checks over a claim's lineage.

The application layer assembles the lineage (claim → experiment → run → code snapshot → dataset version →
environment → raw artifacts → evaluations → verification) into plain dicts; this module checks it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from engines.evidence.hashing import verify_chain

REQUIRED_LINEAGE = (
    "experiment",
    "experiment_run",
    "code_snapshot",
    "environment",
    "evaluation",
    "raw_artifact",
)


@dataclass
class EvidenceValidation:
    complete: bool
    integrity_ok: bool
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    chain: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.complete and self.integrity_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "integrity_ok": self.integrity_ok,
            "missing": self.missing,
            "problems": self.problems,
            "chain": self.chain,
        }


class EvidenceValidator:
    def __init__(self, required: Sequence[str] = REQUIRED_LINEAGE, *, require_dataset: bool = False) -> None:
        self.required = list(required) + (["dataset_version"] if require_dataset else [])

    def validate(
        self,
        lineage_nodes: Sequence[Mapping[str, Any]],
        *,
        artifact_checksums: Mapping[str, tuple[str | None, str | None]] | None = None,
        evidence_chain: Sequence[Mapping[str, Any]] | None = None,
    ) -> EvidenceValidation:
        """``lineage_nodes``: [{"type": ..., "id": ..., ...}]. ``artifact_checksums``: id → (recorded, actual)."""
        types = {str(n.get("type")) for n in lineage_nodes}
        missing = [t for t in self.required if t not in types]
        problems: list[str] = []
        for node in lineage_nodes:
            if node.get("type") == "code_snapshot" and not node.get("sha256"):
                problems.append(f"code snapshot {node.get('id')} has no checksum")
            if node.get("type") == "environment" and not (node.get("image_digest") or node.get("image")):
                problems.append(f"environment {node.get('id')} has no image reference")
            if node.get("type") == "evaluation" and not node.get("evaluator_fingerprint"):
                problems.append(f"evaluation {node.get('id')} has no evaluator fingerprint")
        for artifact_id, (recorded, actual) in (artifact_checksums or {}).items():
            if recorded is None:
                problems.append(f"artifact {artifact_id} has no recorded checksum")
            elif actual is not None and recorded != actual:
                problems.append(f"artifact {artifact_id} checksum mismatch (tampered or corrupted)")
        chain_result = None
        if evidence_chain:
            chain_result = verify_chain([dict(r) for r in evidence_chain])
            if not chain_result["valid"]:
                problems.append(f"evidence hash chain broken at {chain_result['broken_indices']}")
        return EvidenceValidation(
            complete=not missing,
            integrity_ok=not problems,
            missing=missing,
            problems=problems,
            chain=chain_result,
        )
