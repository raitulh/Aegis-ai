"""Memory write policy: decide the initial status and trust level of a new memory item.

Memory is potentially attacker-controlled (agent output, tool output, web content), so writes are gated:

1. prompt-injection risk ≥ 0.7 → ``QUARANTINED`` (untrusted, review required);
2. confidence < 0.3 → ``PROPOSED`` (review required);
3. human writes → ``ACTIVE`` (``trusted`` only with provenance and when the author may review memory);
4. non-human writes without the provenance keys required for their source → ``PROPOSED``;
5. restricted-sensitivity non-human writes → ``PROPOSED``;
6. system / experiment (platform-measured) writes → ``ACTIVE`` + ``reviewed``;
7. agent / tool / external writes at project, workspace, organization or user scope → ``PROPOSED``;
8. agent / tool / external writes at mission (or short-term) scope with confidence ≥ 0.5 → ``ACTIVE`` but
   ``untrusted`` (usable inside the mission only), otherwise ``PROPOSED``.

External and tool content is never better than ``untrusted`` until a human reviews it. A caller cannot
upgrade its own trust by claiming a privileged ``source_type``: human / experiment / system sources are
only honoured for the actor kinds that can legitimately produce them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.states import MemoryCategory, MemoryStatus

MEMORY_POLICY_VERSION = "memory-policy-1.0.0"

SourceType = Literal["human", "agent", "tool", "experiment", "external", "system"]
MemoryScope = Literal["organization", "workspace", "project", "mission", "user"]
Sensitivity = Literal["normal", "confidential", "restricted"]
TrustLevel = Literal["trusted", "reviewed", "untrusted"]
ActorKind = Literal["user", "api_key", "service_account", "agent", "workflow", "system"]

INJECTION_QUARANTINE_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.3
MISSION_ACTIVE_CONFIDENCE = 0.5

#: Provenance keys that must be present (non-empty) for a write from each source type.
PROVENANCE_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "human": ("user_id",),
    "agent": ("agent_run_id", "mission_id"),
    "tool": ("tool_name", "invocation_id"),
    "experiment": ("experiment_run_id",),
    "external": ("source_uri", "retrieved_at"),
    "system": ("component",),
}
#: Actor kinds allowed to claim a privileged source type (other source types may come from any actor).
SOURCE_ACTOR_KINDS: dict[str, frozenset[str]] = {
    "human": frozenset({"user", "api_key"}),
    "experiment": frozenset({"workflow", "system"}),
    "system": frozenset({"workflow", "system"}),
}
BROAD_SCOPES = frozenset({"organization", "workspace", "project", "user"})
UNTRUSTED_SOURCES = frozenset({"tool", "external"})


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MemoryCategory
    scope: MemoryScope
    source_type: SourceType
    actor_kind: ActorKind
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: dict[str, Any] = Field(default_factory=dict)
    injection_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitivity: Sensitivity = "normal"
    actor_can_review: bool = False


class MemoryWriteDecision(BaseModel):
    status: MemoryStatus
    trust_level: TrustLevel
    review_required: bool
    reasons: list[str]
    effective_source_type: SourceType
    missing_provenance: list[str] = Field(default_factory=list)
    policy_version: str = MEMORY_POLICY_VERSION


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict | tuple | set):
        return len(value) > 0
    return True


def missing_provenance(source_type: str, provenance: dict[str, Any]) -> list[str]:
    """Required provenance keys for ``source_type`` that are absent or empty in ``provenance``."""
    return [key for key in PROVENANCE_REQUIRED_KEYS.get(source_type, ()) if not _present(provenance.get(key))]


def decide_memory_write(request: MemoryWriteRequest) -> MemoryWriteDecision:
    """Decide status / trust / review for a memory write (pure, deterministic)."""
    reasons: list[str] = []
    source: SourceType = request.source_type
    allowed = SOURCE_ACTOR_KINDS.get(source)
    if allowed is not None and request.actor_kind not in allowed:
        reasons.append(
            f"source_type '{source}' cannot be claimed by a '{request.actor_kind}' actor; treated as 'agent'"
        )
        source = "agent"
    missing = missing_provenance(source, request.provenance)
    narrow = request.scope == "mission" or request.category == MemoryCategory.SHORT_TERM

    def decision(status: MemoryStatus, trust: TrustLevel, review: bool, reason: str) -> MemoryWriteDecision:
        return MemoryWriteDecision(
            status=status,
            trust_level=trust,
            review_required=review,
            reasons=[*reasons, reason],
            effective_source_type=source,
            missing_provenance=missing,
        )

    if request.injection_risk >= INJECTION_QUARANTINE_THRESHOLD:
        return decision(
            MemoryStatus.QUARANTINED,
            "untrusted",
            True,
            f"prompt-injection risk {request.injection_risk:.2f} ≥ {INJECTION_QUARANTINE_THRESHOLD}: quarantined",
        )
    if request.confidence < LOW_CONFIDENCE_THRESHOLD:
        return decision(
            MemoryStatus.PROPOSED,
            "untrusted",
            True,
            f"confidence {request.confidence:.2f} < {LOW_CONFIDENCE_THRESHOLD}: requires review",
        )
    if source == "human":
        if missing:
            return decision(
                MemoryStatus.ACTIVE,
                "untrusted",
                False,
                f"human write without provenance ({', '.join(missing)}): active but untrusted",
            )
        if request.actor_can_review:
            return decision(MemoryStatus.ACTIVE, "trusted", False, "human reviewer write with provenance: trusted")
        return decision(
            MemoryStatus.ACTIVE,
            "untrusted",
            False,
            "human write with provenance by an author without memory review rights: active, not trusted",
        )
    if missing:
        return decision(
            MemoryStatus.PROPOSED,
            "untrusted",
            True,
            f"{source} write missing provenance ({', '.join(missing)}): requires review",
        )
    if request.sensitivity == "restricted":
        return decision(MemoryStatus.PROPOSED, "untrusted", True, "restricted-sensitivity content requires review")
    if source in {"system", "experiment"}:
        label = "platform-measured experiment result" if source == "experiment" else "platform-generated record"
        return decision(MemoryStatus.ACTIVE, "reviewed", False, f"{label}: active, reviewed")
    if not narrow:
        return decision(
            MemoryStatus.PROPOSED,
            "untrusted",
            True,
            f"{source} write at {request.scope} scope requires human review before it can influence other work",
        )
    if request.confidence >= MISSION_ACTIVE_CONFIDENCE:
        suffix = " (external/tool content stays untrusted)" if source in UNTRUSTED_SOURCES else ""
        return decision(
            MemoryStatus.ACTIVE,
            "untrusted",
            False,
            f"{source} write at mission scope with provenance and confidence ≥ {MISSION_ACTIVE_CONFIDENCE}: "
            f"active within the mission only{suffix}",
        )
    return decision(
        MemoryStatus.PROPOSED,
        "untrusted",
        True,
        f"{source} write at mission scope with confidence < {MISSION_ACTIVE_CONFIDENCE}: requires review",
    )
