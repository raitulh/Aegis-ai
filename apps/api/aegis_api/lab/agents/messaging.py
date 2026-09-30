"""Signed inter-agent messages.

The runtime — never the model — builds each envelope from the *sender run row*: sender id and role, mission,
trace and an authorization context equal to the sender's granted permissions and autonomy level. The envelope
is validated and signed (HMAC-SHA256, ``settings.agent_message_key``) with :mod:`engines.lab.messages` and stored.

On receipt every message is re-validated against the platform's record of the sending run: schema and
payload, sender-role impersonation, sender-run mismatch, permission/autonomy escalation, the signature
(constant time) and the message age. Valid messages are ``consumed``; invalid ones are ``rejected`` with a
reason and an audit entry. Message payloads are agent-produced content and remain UNTRUSTED data for the
receiving agent's prompt.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.models import AgentMessage, AgentRun, AgentStep
from engines.lab.messages import (
    DEFAULT_CLOCK_SKEW_SECONDS,
    MESSAGE_TYPES,
    AgentMessageEnvelope,
    AuthorizationContext,
    MessageRejected,
    sign_envelope,
    validate_envelope,
    verify_envelope,
)
from engines.lab.states import AgentRole, AgentRunStatus, AutonomyLevel

log = structlog.get_logger("aegis.lab.agents")

STATUS_DELIVERED = "delivered"
STATUS_CONSUMED = "consumed"
STATUS_REJECTED = "rejected"
MESSAGE_MAX_AGE_SECONDS = 3600
AUDIT_MESSAGE_REJECTED = "AGENT_MESSAGE_REJECTED"
_TRACE_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_CLOSED_SENDERS = frozenset({AgentRunStatus.FAILED, AgentRunStatus.CANCELLED})


def _uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound("Agent run not found") from exc


def _next_seq(db: Session, run: AgentRun) -> int:
    run.step_count = (run.step_count or 0) + 1
    return run.step_count


def _step(db: Session, run: AgentRun, summary: str, data: dict[str, Any]) -> None:
    db.add(
        AgentStep(
            organization_id=run.organization_id,
            agent_run_id=run.id,
            seq=_next_seq(db, run),
            kind="message",
            summary=summary[:2000],
            data=data,
        )
    )
    db.flush()


def authorization_context(run: AgentRun) -> AuthorizationContext:
    return AuthorizationContext(
        permissions=sorted(set(run.granted_permissions or [])),
        autonomy_level=AutonomyLevel(run.autonomy_level or AutonomyLevel.L0_ASSISTED),
        granted_by_run_id=str(run.parent_run_id) if run.parent_run_id else None,
    )


def build_envelope(
    sender: AgentRun,
    *,
    message_id: uuid.UUID,
    timestamp: datetime,
    message_type: str,
    payload: dict[str, Any],
    receiver_run_id: uuid.UUID | None,
    receiver_role: str | None,
) -> AgentMessageEnvelope:
    """The envelope for ``sender`` — identity, role and authorization come from the run row only."""
    spec = MESSAGE_TYPES.get(message_type)
    if spec is None:
        raise ValidationFailed(f"Unknown message type '{message_type}'", details={"allowed": sorted(MESSAGE_TYPES)})
    trace = sender.trace_id if sender.trace_id and _TRACE_RE.match(sender.trace_id) else None
    try:
        return AgentMessageEnvelope(
            message_id=str(message_id),
            sender_agent_run_id=str(sender.id),
            sender_role=AgentRole(sender.role),
            receiver_agent_run_id=str(receiver_run_id) if receiver_run_id else None,
            receiver_role=AgentRole(receiver_role) if receiver_role else None,
            mission_id=str(sender.mission_id) if sender.mission_id else None,
            trace_id=trace,
            message_type=message_type,
            schema_version=spec.schema_version,
            timestamp=timestamp,
            authorization_context=authorization_context(sender),
            payload=payload,
        )
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        raise ValidationFailed("Invalid agent message", details={"errors": errors}) from None


def send_message(
    db: Session,
    sender_run: AgentRun,
    *,
    receiver_run_id: uuid.UUID | str | None = None,
    receiver_role: str | None = None,
    message_type: str,
    payload: dict[str, Any],
) -> AgentMessage:
    """Build, validate, sign and store a message from ``sender_run`` (status ``delivered``)."""
    if sender_run.status in _CLOSED_SENDERS:
        raise Conflict(f"A {sender_run.status} agent run cannot send messages", code="agent_run_closed")
    receiver_id = _uuid(receiver_run_id)
    if receiver_id is None and not receiver_role:
        raise ValidationFailed("A receiver_run_id or receiver_role is required")
    if receiver_role is not None:
        try:
            receiver_role = AgentRole(receiver_role).value
        except ValueError as exc:
            raise ValidationFailed(f"Unknown receiver role '{receiver_role}'") from exc
    if receiver_id is not None:
        receiver = db.get(AgentRun, receiver_id)
        if receiver is None or receiver.organization_id != sender_run.organization_id:
            raise NotFound("Receiving agent run not found")
        if sender_run.mission_id is not None and receiver.mission_id != sender_run.mission_id:
            raise ValidationFailed("Agents can only message runs of the same mission")
        if receiver.project_id != sender_run.project_id:
            raise ValidationFailed("Agents can only message runs of the same project")
        if receiver_role is not None and receiver.role != receiver_role:
            raise ValidationFailed("receiver_role does not match the receiving run's role")
    message_id = uuid.uuid4()
    timestamp = utcnow()
    envelope = build_envelope(
        sender_run,
        message_id=message_id,
        timestamp=timestamp,
        message_type=message_type,
        payload=payload,
        receiver_run_id=receiver_id,
        receiver_role=receiver_role,
    )
    signature = sign_envelope(envelope, get_settings().agent_message_key)
    row = AgentMessage(
        id=message_id,
        organization_id=sender_run.organization_id,
        mission_id=sender_run.mission_id,
        sender_run_id=sender_run.id,
        sender_role=sender_run.role,
        receiver_run_id=receiver_id,
        receiver_role=receiver_role,
        message_type=message_type,
        schema_version=envelope.schema_version,
        trace_id=envelope.trace_id,
        payload=envelope.payload,
        auth_context=envelope.authorization_context.model_dump(mode="json"),
        signature=signature,
        status=STATUS_DELIVERED,
        created_at=envelope.timestamp,
    )
    db.add(row)
    db.flush()
    target = str(receiver_id) if receiver_id else receiver_role
    _step(
        db,
        sender_run,
        f"sent {message_type} to {target}",
        {"message_id": str(message_id), "message_type": message_type, "receiver_run_id": str(receiver_id) if receiver_id else None, "receiver_role": receiver_role},
    )
    return row


def raw_envelope(row: AgentMessage) -> dict[str, Any]:
    """The stored message as a raw envelope (exactly the signed content when untampered)."""
    created = row.created_at if row.created_at.tzinfo is not None else row.created_at.replace(tzinfo=UTC)
    return {
        "message_id": str(row.id),
        "sender_agent_run_id": str(row.sender_run_id),
        "sender_role": row.sender_role,
        "receiver_agent_run_id": str(row.receiver_run_id) if row.receiver_run_id else None,
        "receiver_role": row.receiver_role,
        "mission_id": str(row.mission_id) if row.mission_id else None,
        "trace_id": row.trace_id,
        "message_type": row.message_type,
        "schema_version": row.schema_version,
        "timestamp": created.astimezone(UTC).isoformat(),
        "authorization_context": dict(row.auth_context or {}),
        "payload": row.payload,
    }


def check_message(
    row: AgentMessage, sender: AgentRun | None, *, now: datetime, max_age_seconds: float = MESSAGE_MAX_AGE_SECONDS
) -> str | None:
    """``None`` when the message is authentic and authorized, else a rejection reason."""
    if sender is None or sender.organization_id != row.organization_id:
        return "impersonation: the sending run does not exist"
    try:
        envelope = validate_envelope(
            raw_envelope(row),
            sender_granted_permissions=list(sender.granted_permissions or []),
            sender_actual_role=sender.role,
            sender_actual_run_id=str(sender.id),
            sender_autonomy_level=sender.autonomy_level,
        )
    except MessageRejected as exc:
        return f"{exc.reason}: {exc.detail}"[:500] if exc.detail else exc.reason
    key = get_settings().agent_message_key
    if not verify_envelope(envelope, row.signature, key, now=envelope.timestamp):
        return "bad_signature: the message was altered or not signed by the platform"
    age = (now.astimezone(UTC) - envelope.timestamp).total_seconds()
    if age < -DEFAULT_CLOCK_SKEW_SECONDS:
        return "future_timestamp: the message is dated in the future"
    if age > max_age_seconds:
        return f"stale: the message is older than {int(max_age_seconds)} s"
    return None


def receive_messages(
    db: Session,
    run: AgentRun,
    *,
    now: datetime | None = None,
    max_age_seconds: float = MESSAGE_MAX_AGE_SECONDS,
    limit: int = 50,
) -> list[AgentMessage]:
    """Verify and deliver pending messages addressed to ``run`` (by id, or by role within its mission/project).

    Returns the consumed messages; rejected ones are marked with a reason and audited.
    """
    moment = now or utcnow()
    sender = aliased(AgentRun)
    if run.mission_id is not None:
        role_scope = AgentMessage.mission_id == run.mission_id
    else:
        role_scope = AgentMessage.mission_id.is_(None)
    rows = db.scalars(
        select(AgentMessage)
        .join(sender, sender.id == AgentMessage.sender_run_id)
        .where(
            AgentMessage.organization_id == run.organization_id,
            AgentMessage.status == STATUS_DELIVERED,
            AgentMessage.sender_run_id != run.id,
            sender.project_id == run.project_id,
            or_(
                AgentMessage.receiver_run_id == run.id,
                and_(AgentMessage.receiver_run_id.is_(None), AgentMessage.receiver_role == run.role, role_scope),
            ),
        )
        .order_by(AgentMessage.created_at, AgentMessage.id)
        .limit(limit)
        .with_for_update(of=AgentMessage, skip_locked=True)
    ).all()
    delivered: list[AgentMessage] = []
    system = Actor.system(run.organization_id, "system:agent-messaging")
    for row in rows:
        reason = check_message(row, db.get(AgentRun, row.sender_run_id), now=moment, max_age_seconds=max_age_seconds)
        if reason is None:
            row.status = STATUS_CONSUMED
            delivered.append(row)
            _step(
                db,
                run,
                f"received {row.message_type} from {row.sender_role}",
                {"message_id": str(row.id), "message_type": row.message_type, "sender_run_id": str(row.sender_run_id)},
            )
            continue
        row.status = STATUS_REJECTED
        row.rejection_reason = reason[:2000]
        audit(
            db,
            system,
            AUDIT_MESSAGE_REJECTED,
            "agent_message",
            row.id,
            after={
                "reason": reason[:500],
                "message_type": row.message_type,
                "sender_run_id": str(row.sender_run_id),
                "claimed_sender_role": row.sender_role,
                "receiver_run_id": str(run.id),
            },
        )
        log.warning("agent_message_rejected", message_id=str(row.id), reason=reason.split(":", 1)[0])
    db.flush()
    return delivered


def consumed_messages(db: Session, run: AgentRun, *, limit: int = 20) -> list[AgentMessage]:
    """Messages this run already consumed (addressed to it by id, or recorded in its message steps)."""
    step_ids = [
        uuid.UUID(str(data["message_id"]))
        for data in db.scalars(
            select(AgentStep.data).where(AgentStep.agent_run_id == run.id, AgentStep.kind == "message")
        ).all()
        if isinstance(data, dict) and data.get("message_id") and data.get("sender_run_id")
    ]
    stmt = select(AgentMessage).where(
        AgentMessage.organization_id == run.organization_id,
        AgentMessage.status == STATUS_CONSUMED,
        or_(AgentMessage.receiver_run_id == run.id, AgentMessage.id.in_(step_ids or [uuid.uuid4()])),
    )
    rows = db.scalars(stmt.order_by(AgentMessage.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))
