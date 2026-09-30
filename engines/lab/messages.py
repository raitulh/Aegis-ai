"""Signed inter-agent message envelopes: typed payloads, HMAC-SHA256 signatures and receive-side validation.

The agent runtime builds an :class:`AgentMessageEnvelope` from the *sender run row* (never from agent
output), signs it with :func:`sign_envelope` and stores it. On receipt, :func:`validate_envelope` rejects
unknown types/versions, schema violations, oversize or over-deep messages, sender-role impersonation and
permission escalation; :func:`verify_envelope` checks the signature in constant time and rejects stale or
future-dated messages.

Canonical form: JSON of the envelope without ``signature`` — sorted keys, no whitespace, UTF-8, UTC
timestamps, NaN/inf forbidden.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from engines.lab.states import AgentRole, AutonomyLevel, MissionPhase, autonomy_rank

MESSAGE_ENGINE_VERSION = "agent-messages-1.0.0"
MAX_ENVELOPE_BYTES = 256 * 1024
MAX_NESTING_DEPTH = 12
DEFAULT_MAX_AGE_SECONDS = 300
DEFAULT_CLOCK_SKEW_SECONDS = 30
MIN_KEY_BYTES = 16

REJECTION_REASONS: tuple[str, ...] = (
    "malformed",
    "too_deep",
    "oversize",
    "unknown_type",
    "unsupported_version",
    "missing_receiver",
    "payload_violation",
    "schema_violation",
    "impersonation",
    "escalation",
)


class MessageRejected(Exception):
    """A received message failed validation; ``reason`` is one of :data:`REJECTION_REASONS`."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# =============================================================================================
# Field types
# =============================================================================================
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Permission = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_*.-]{1,48}(?::[a-z0-9_*.-]{1,48}){0,2}$")]


ShortText = Annotated[str, StringConstraints(max_length=500)]
Text1k = Annotated[str, StringConstraints(max_length=1000)]
Text2k = Annotated[str, StringConstraints(max_length=2000)]
Text4k = Annotated[str, StringConstraints(max_length=4000)]
Text8k = Annotated[str, StringConstraints(max_length=8000)]
Text20k = Annotated[str, StringConstraints(max_length=20000)]
RequiredText4k = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
RequiredText8k = Annotated[str, StringConstraints(min_length=1, max_length=8000)]

SubjectType = Literal["hypothesis", "experiment", "claim", "report", "plan", "code", "analysis", "dataset"]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskRequestPayload(_Payload):
    task: RequiredText8k
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: Text2k | None = None
    deadline_seconds: int | None = Field(default=None, ge=1, le=86_400)
    priority: Literal["low", "normal", "high"] = "normal"


class TaskResultPayload(_Payload):
    request_message_id: Identifier
    status: Literal["succeeded", "failed", "partial"]
    summary: Text8k
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    error: Text2k | None = None


class CritiqueRequestPayload(_Payload):
    subject_type: SubjectType
    subject_id: Identifier
    focus: list[ShortText] = Field(default_factory=list, max_length=20)
    content: Text20k | None = None


class CritiqueIssue(_Payload):
    severity: Literal["low", "medium", "high", "critical"]
    description: Text2k
    location: ShortText | None = None


class CritiqueResultPayload(_Payload):
    request_message_id: Identifier
    subject_type: SubjectType
    subject_id: Identifier
    verdict: Literal["accept", "revise", "reject"]
    issues: list[CritiqueIssue] = Field(default_factory=list, max_length=50)
    summary: Text4k


class HandoffPayload(_Payload):
    to_role: AgentRole
    phase: MissionPhase | None = None
    context_summary: Text8k
    open_items: list[ShortText] = Field(default_factory=list, max_length=50)
    memory_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    artifact_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class EvidenceRef(_Payload):
    kind: Literal[
        "research_source",
        "experiment_run",
        "experiment_comparison",
        "artifact_version",
        "dataset_version",
        "evaluation_run",
        "memory",
        "claim",
    ]
    ref_id: Identifier
    note: Text1k | None = None


class EvidenceSharePayload(_Payload):
    claim: Text4k | None = None
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)
    summary: Text4k


class HumanQuestionPayload(_Payload):
    question: RequiredText4k
    options: list[ShortText] = Field(default_factory=list, max_length=10)
    blocking: bool = True
    context: Text8k | None = None
    urgency: Literal["low", "normal", "high"] = "normal"


class MessageTypeSpec(NamedTuple):
    schema_version: str
    payload_model: type[BaseModel]


#: Registry of supported message types: ``message_type → (schema_version, payload model)``.
MESSAGE_TYPES: dict[str, MessageTypeSpec] = {
    "task.request": MessageTypeSpec("1.0", TaskRequestPayload),
    "task.result": MessageTypeSpec("1.0", TaskResultPayload),
    "critique.request": MessageTypeSpec("1.0", CritiqueRequestPayload),
    "critique.result": MessageTypeSpec("1.0", CritiqueResultPayload),
    "handoff": MessageTypeSpec("1.0", HandoffPayload),
    "evidence.share": MessageTypeSpec("1.0", EvidenceSharePayload),
    "question.human": MessageTypeSpec("1.0", HumanQuestionPayload),
}


# =============================================================================================
# Envelope
# =============================================================================================
class AuthorizationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[Permission] = Field(default_factory=list, max_length=300)
    autonomy_level: AutonomyLevel
    granted_by_run_id: Identifier | None = None


class AgentMessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: Identifier
    sender_agent_run_id: Identifier
    sender_role: AgentRole
    receiver_agent_run_id: Identifier | None = None
    receiver_role: AgentRole | None = None
    mission_id: Identifier | None = None
    trace_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,64}$")] | None = None
    message_type: Annotated[str, StringConstraints(max_length=64)]
    schema_version: Annotated[str, StringConstraints(max_length=16)]
    timestamp: datetime
    authorization_context: AuthorizationContext
    payload: dict[str, Any]
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @field_validator("timestamp")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check(self) -> AgentMessageEnvelope:
        if self.receiver_agent_run_id is None and self.receiver_role is None:
            raise ValueError("a receiver_agent_run_id or receiver_role is required")
        spec = MESSAGE_TYPES.get(self.message_type)
        if spec is None:
            raise ValueError(f"unknown message_type '{self.message_type}'")
        if spec.schema_version != self.schema_version:
            raise ValueError(f"unsupported schema_version '{self.schema_version}' for '{self.message_type}'")
        self.payload = spec.payload_model.model_validate(self.payload).model_dump(mode="json")
        return self


# =============================================================================================
# Signing
# =============================================================================================
def canonical_bytes(envelope: AgentMessageEnvelope) -> bytes:
    """Canonical JSON bytes of the envelope without its signature (the signed content)."""
    data = envelope.model_dump(mode="json", exclude={"signature"})
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _check_key(key: bytes) -> None:
    if not isinstance(key, bytes | bytearray) or len(key) < MIN_KEY_BYTES:
        raise ValueError(f"message signing key must be at least {MIN_KEY_BYTES} bytes")


def sign_envelope(envelope: AgentMessageEnvelope, key: bytes) -> str:
    """Hex HMAC-SHA256 of :func:`canonical_bytes` under ``key``."""
    _check_key(key)
    return hmac.new(bytes(key), canonical_bytes(envelope), hashlib.sha256).hexdigest()


def verify_envelope(
    envelope: AgentMessageEnvelope,
    signature: str | None,
    key: bytes,
    *,
    now: datetime,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS,
) -> bool:
    """True iff ``signature`` matches (constant-time) and the timestamp is neither stale nor in the future."""
    _check_key(key)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    candidate = signature.lower() if isinstance(signature, str) else ""
    well_formed = bool(re.fullmatch(r"[0-9a-f]{64}", candidate))
    expected = sign_envelope(envelope, key)
    matches = hmac.compare_digest(expected, candidate if well_formed else "0" * 64)
    age = (now.astimezone(UTC) - envelope.timestamp).total_seconds()
    fresh = -clock_skew_seconds <= age <= max_age_seconds
    return well_formed and matches and fresh


# =============================================================================================
# Receive-side validation
# =============================================================================================
def _structure_check(raw: Mapping[str, Any]) -> None:
    """Reject nesting deeper than :data:`MAX_NESTING_DEPTH` and non-finite numbers (iteratively)."""
    stack: list[tuple[Any, int]] = [(raw, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            raise MessageRejected("too_deep", f"nesting deeper than {MAX_NESTING_DEPTH} levels")
        if isinstance(value, Mapping):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list | tuple):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise MessageRejected("malformed", "non-finite number")


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid"
    location = ".".join(str(part) for part in errors[0]["loc"])
    message = str(errors[0]["msg"])
    return f"{location}: {message}" if location else message


def validate_envelope(
    raw: Any,
    *,
    sender_granted_permissions: Iterable[str],
    sender_actual_role: str,
    sender_actual_run_id: str | None = None,
    sender_autonomy_level: str | None = None,
) -> AgentMessageEnvelope:
    """Parse and authorize a received envelope or raise :class:`MessageRejected`.

    ``sender_*`` arguments come from the platform's record of the sending run (never from the message).
    """
    if not isinstance(raw, Mapping):
        raise MessageRejected("malformed", "envelope must be a JSON object")
    _structure_check(raw)
    try:
        size = len(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode())
    except (TypeError, ValueError) as exc:
        raise MessageRejected("malformed", type(exc).__name__) from exc
    if size > MAX_ENVELOPE_BYTES:
        raise MessageRejected("oversize", f"{size} bytes > {MAX_ENVELOPE_BYTES}")
    message_type = raw.get("message_type")
    spec = MESSAGE_TYPES.get(message_type) if isinstance(message_type, str) else None
    if spec is None:
        raise MessageRejected("unknown_type", f"unknown message_type {message_type!r}")
    if raw.get("schema_version") != spec.schema_version:
        raise MessageRejected(
            "unsupported_version", f"{message_type} supports schema_version {spec.schema_version} only"
        )
    if not raw.get("receiver_agent_run_id") and not raw.get("receiver_role"):
        raise MessageRejected("missing_receiver", "receiver_agent_run_id or receiver_role is required")
    try:
        spec.payload_model.model_validate(raw.get("payload"))
    except ValidationError as exc:
        raise MessageRejected("payload_violation", _first_error(exc)) from exc
    try:
        envelope = AgentMessageEnvelope.model_validate(dict(raw))
    except ValidationError as exc:
        raise MessageRejected("schema_violation", _first_error(exc)) from exc
    if envelope.sender_role.value != sender_actual_role:
        raise MessageRejected(
            "impersonation", f"sender_role {envelope.sender_role.value} does not match the sending run's role"
        )
    if sender_actual_run_id is not None and envelope.sender_agent_run_id != sender_actual_run_id:
        raise MessageRejected("impersonation", "sender_agent_run_id does not match the sending run")
    granted = set(sender_granted_permissions)
    extra = sorted(set(envelope.authorization_context.permissions) - granted)
    if extra:
        raise MessageRejected("escalation", f"permissions not granted to the sender: {', '.join(extra[:10])}")
    if sender_autonomy_level is not None and autonomy_rank(envelope.authorization_context.autonomy_level) > (
        autonomy_rank(sender_autonomy_level)
    ):
        raise MessageRejected("escalation", "autonomy_level exceeds the sender's autonomy level")
    return envelope
