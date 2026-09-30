"""Signed inter-agent message envelopes.

Agents never trust each other's messages blindly. Every message carries sender/receiver/mission/trace ids, a
schema version, a timestamp and an *authorization context* (the sender's role and privilege rank as issued by
the runtime), and is HMAC-signed by the runtime with a server-side key. Receivers verify the signature, the
schema, freshness and that the sender is not claiming a higher privilege than its role allows. Agents cannot
sign messages themselves — only the runtime holds the key — so an agent cannot impersonate another agent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engines.lab.enums import AgentRole

SCHEMA_VERSION = "1.0"
MAX_MESSAGE_AGE = timedelta(hours=24)
MAX_PAYLOAD_BYTES = 256 * 1024

# Privilege ranks per role: higher ranks may request actions that lower ranks may not.
ROLE_PRIVILEGE: dict[str, int] = {
    AgentRole.QUEST: 3,
    AgentRole.PLANNER: 3,
    AgentRole.LITERATURE: 1,
    AgentRole.KNOWLEDGE: 1,
    AgentRole.HYPOTHESIS: 2,
    AgentRole.HYPOTHESIS_CRITIC: 2,
    AgentRole.EXPERIMENT_DESIGNER: 2,
    AgentRole.CODING: 1,
    AgentRole.SIMULATION: 1,
    AgentRole.DATA_ANALYST: 1,
    AgentRole.STATISTICAL_ANALYST: 1,
    AgentRole.FAILURE_ANALYZER: 1,
    AgentRole.EVOLUTION: 2,
    AgentRole.REPRODUCTION: 2,
    AgentRole.VERIFIER: 3,
    AgentRole.SCIENTIFIC_REVIEWER: 3,
    AgentRole.REPORT: 1,
}

MessageType = Literal["request", "result", "critique", "handoff", "notification", "command"]
# Minimum sender privilege for each message type (commands direct other agents' work).
MIN_PRIVILEGE: dict[str, int] = {
    "command": 3,
    "handoff": 2,
    "request": 1,
    "result": 1,
    "critique": 1,
    "notification": 1,
}


class MessageRejected(ValueError):
    """An inter-agent message failed validation."""


class AuthorizationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_role: str
    privilege: int
    organization_id: str
    issued_by: str = "runtime"


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    sender_agent_id: str
    receiver_agent_id: str
    mission_id: str
    trace_id: str
    message_type: MessageType
    schema_version: str = SCHEMA_VERSION
    timestamp: datetime
    authorization: AuthorizationContext
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str = ""

    def signing_bytes(self) -> bytes:
        data = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def sign(message: AgentMessage, key: bytes) -> AgentMessage:
    signature = hmac.new(key, message.signing_bytes(), hashlib.sha256).hexdigest()
    return message.model_copy(update={"signature": signature})


def build_message(
    *,
    key: bytes,
    message_id: str,
    sender_agent_id: str,
    sender_role: str,
    receiver_agent_id: str,
    mission_id: str,
    trace_id: str,
    organization_id: str,
    message_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> AgentMessage:
    """Runtime-side construction: the privilege is derived from the role, never supplied by the agent."""
    message = AgentMessage(
        message_id=message_id,
        sender_agent_id=sender_agent_id,
        receiver_agent_id=receiver_agent_id,
        mission_id=mission_id,
        trace_id=trace_id,
        message_type=message_type,  # type: ignore[arg-type]
        timestamp=now or datetime.now(UTC),
        authorization=AuthorizationContext(
            sender_role=sender_role, privilege=ROLE_PRIVILEGE.get(sender_role, 0), organization_id=organization_id
        ),
        payload=payload,
    )
    return sign(message, key)


def verify(
    raw: dict[str, Any] | AgentMessage,
    key: bytes,
    *,
    expected_receiver: str | None = None,
    expected_mission: str | None = None,
    expected_organization: str | None = None,
    known_sender_roles: dict[str, str] | None = None,
    now: datetime | None = None,
) -> AgentMessage:
    """Validate schema, signature, freshness, routing and privilege claims. Raises ``MessageRejected``."""
    try:
        message = raw if isinstance(raw, AgentMessage) else AgentMessage.model_validate(raw)
    except ValidationError as exc:
        raise MessageRejected(f"malformed message: {exc.errors()[0].get('msg')}") from exc
    if message.schema_version != SCHEMA_VERSION:
        raise MessageRejected(f"unsupported schema version {message.schema_version}")
    if len(json.dumps(message.payload, default=str)) > MAX_PAYLOAD_BYTES:
        raise MessageRejected("payload too large")
    expected_sig = hmac.new(key, message.signing_bytes(), hashlib.sha256).hexdigest()
    if not message.signature or not hmac.compare_digest(expected_sig, message.signature):
        raise MessageRejected("invalid signature (message forged or modified)")
    current = now or datetime.now(UTC)
    ts = message.timestamp if message.timestamp.tzinfo else message.timestamp.replace(tzinfo=UTC)
    if ts > current + timedelta(minutes=5) or current - ts > MAX_MESSAGE_AGE:
        raise MessageRejected("message timestamp outside the accepted window")
    if expected_receiver and message.receiver_agent_id != expected_receiver:
        raise MessageRejected("message addressed to a different agent")
    if expected_mission and message.mission_id != expected_mission:
        raise MessageRejected("message belongs to a different mission")
    if expected_organization and message.authorization.organization_id != expected_organization:
        raise MessageRejected("message belongs to a different organization")
    role = message.authorization.sender_role
    if message.authorization.privilege != ROLE_PRIVILEGE.get(role, 0):
        raise MessageRejected("sender claims a privilege that does not match its role")
    if known_sender_roles is not None and known_sender_roles.get(message.sender_agent_id) != role:
        raise MessageRejected("sender role does not match the registered agent (impersonation attempt)")
    if message.authorization.privilege < MIN_PRIVILEGE.get(message.message_type, 99):
        raise MessageRejected(f"role '{role}' may not send '{message.message_type}' messages")
    return message
