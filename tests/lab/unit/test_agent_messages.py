"""Signed inter-agent message envelopes: signing, verification, tampering, staleness, impersonation, escalation."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from engines.lab.messages import (
    MAX_ENVELOPE_BYTES,
    MESSAGE_TYPES,
    AgentMessageEnvelope,
    MessageRejected,
    canonical_bytes,
    sign_envelope,
    validate_envelope,
    verify_envelope,
)

KEY = b"k" * 32
OTHER_KEY = b"z" * 32
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
GRANTED = {"experiment:read", "experiment:create", "memory:read"}

VALID_PAYLOADS: dict[str, dict] = {
    "task.request": {"task": "Implement the baseline", "inputs": {"dataset": "d1"}, "priority": "high"},
    "task.result": {"request_message_id": "m0", "status": "succeeded", "summary": "done", "artifact_ids": ["a1"]},
    "critique.request": {"subject_type": "hypothesis", "subject_id": "h1", "focus": ["novelty"]},
    "critique.result": {
        "request_message_id": "m0",
        "subject_type": "hypothesis",
        "subject_id": "h1",
        "verdict": "revise",
        "issues": [{"severity": "high", "description": "no baseline"}],
        "summary": "needs a baseline",
    },
    "handoff": {"to_role": "CodingAgent", "phase": "EXECUTION", "context_summary": "design approved"},
    "evidence.share": {"evidence": [{"kind": "experiment_run", "ref_id": "run-1"}], "summary": "supports"},
    "question.human": {"question": "Approve GPU usage?", "options": ["yes", "no"]},
}


def raw_envelope(message_type: str = "task.request", **overrides) -> dict:
    data = {
        "message_id": "msg-1",
        "sender_agent_run_id": "run-planner",
        "sender_role": "PlannerAgent",
        "receiver_role": "CodingAgent",
        "mission_id": "mission-1",
        "trace_id": "abc123",
        "message_type": message_type,
        "schema_version": "1.0",
        "timestamp": NOW.isoformat(),
        "authorization_context": {
            "permissions": ["experiment:read", "experiment:create"],
            "autonomy_level": "L2_AUTOMATED_EXPERIMENT_DESIGN",
        },
        "payload": copy.deepcopy(VALID_PAYLOADS[message_type]),
    }
    data.update(overrides)
    return data


def validate(raw, **kwargs) -> AgentMessageEnvelope:
    kwargs.setdefault("sender_granted_permissions", GRANTED)
    kwargs.setdefault("sender_actual_role", "PlannerAgent")
    return validate_envelope(raw, **kwargs)


def rejected(raw, **kwargs) -> MessageRejected:
    with pytest.raises(MessageRejected) as exc_info:
        validate(raw, **kwargs)
    return exc_info.value


# ---------------------------------------------------------------------------------------------
# Signing and verification
# ---------------------------------------------------------------------------------------------
def test_sign_and_verify_round_trip():
    env = validate(raw_envelope())
    signature = sign_envelope(env, KEY)
    assert len(signature) == 64
    assert verify_envelope(env, signature, KEY, now=NOW + timedelta(seconds=5))


def test_signature_survives_serialization_round_trip():
    env = validate(raw_envelope())
    signature = sign_envelope(env, KEY)
    stored = json.loads(env.model_dump_json())
    reloaded = AgentMessageEnvelope.model_validate(stored)
    assert verify_envelope(reloaded, signature, KEY, now=NOW)


def test_canonical_form_ignores_key_order_and_signature_field():
    a = validate(raw_envelope())
    reordered = dict(reversed(list(raw_envelope().items())))
    b = validate(reordered)
    assert canonical_bytes(a) == canonical_bytes(b)
    signed = a.model_copy(update={"signature": sign_envelope(a, KEY)})
    assert canonical_bytes(signed) == canonical_bytes(a)
    body = canonical_bytes(a).decode()
    assert " " not in body.split('"payload"')[0]
    assert '"timestamp":"2026-09-30T12:00:00Z"' in body


def test_equivalent_timestamps_in_other_offsets_sign_identically():
    shifted = NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
    a = validate(raw_envelope())
    b = validate(raw_envelope(timestamp=shifted))
    assert b.timestamp.tzinfo == UTC
    assert sign_envelope(a, KEY) == sign_envelope(b, KEY)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.model_copy(update={"payload": {**e.payload, "task": "Delete everything"}}),
        lambda e: e.model_copy(update={"sender_role": "VerifierAgent"}),
        lambda e: e.model_copy(update={"receiver_role": "ReportAgent"}),
        lambda e: e.model_copy(update={"timestamp": e.timestamp + timedelta(seconds=1)}),
        lambda e: e.model_copy(
            update={
                "authorization_context": e.authorization_context.model_copy(
                    update={"permissions": ["experiment:read", "experiment:execute"]}
                )
            }
        ),
    ],
)
def test_tampering_breaks_the_signature(mutate):
    env = validate(raw_envelope())
    signature = sign_envelope(env, KEY)
    assert not verify_envelope(mutate(env), signature, KEY, now=NOW)


def test_wrong_key_fails():
    env = validate(raw_envelope())
    assert not verify_envelope(env, sign_envelope(env, KEY), OTHER_KEY, now=NOW)


@pytest.mark.parametrize("signature", [None, "", "zz" * 32, "abc", "A" * 64 + "0"])
def test_malformed_signatures_fail(signature):
    env = validate(raw_envelope())
    assert not verify_envelope(env, signature, KEY, now=NOW)


def test_uppercase_hex_signature_is_accepted():
    env = validate(raw_envelope())
    assert verify_envelope(env, sign_envelope(env, KEY).upper(), KEY, now=NOW)


def test_stale_and_future_messages_fail():
    env = validate(raw_envelope())
    signature = sign_envelope(env, KEY)
    assert verify_envelope(env, signature, KEY, now=NOW + timedelta(seconds=300))
    assert not verify_envelope(env, signature, KEY, now=NOW + timedelta(seconds=301))
    assert verify_envelope(env, signature, KEY, now=NOW + timedelta(hours=2), max_age_seconds=3 * 3600)
    assert verify_envelope(env, signature, KEY, now=NOW - timedelta(seconds=30))
    assert not verify_envelope(env, signature, KEY, now=NOW - timedelta(seconds=31))


def test_short_key_and_naive_now_are_rejected():
    env = validate(raw_envelope())
    with pytest.raises(ValueError):
        sign_envelope(env, b"short")
    with pytest.raises(ValueError):
        verify_envelope(env, "0" * 64, KEY, now=datetime(2026, 9, 30, 12, 0))


# ---------------------------------------------------------------------------------------------
# Receive-side validation
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("message_type", sorted(MESSAGE_TYPES))
def test_every_registered_type_validates(message_type):
    env = validate(raw_envelope(message_type))
    assert env.message_type == message_type
    assert env.schema_version == MESSAGE_TYPES[message_type].schema_version


def test_required_message_types_are_registered():
    required = {
        "task.request",
        "task.result",
        "critique.request",
        "critique.result",
        "handoff",
        "evidence.share",
        "question.human",
    }
    assert required <= set(MESSAGE_TYPES)


def test_unknown_type_rejected():
    assert rejected(raw_envelope(message_type="task.request") | {"message_type": "shell.exec"}).reason == "unknown_type"
    assert rejected(raw_envelope() | {"message_type": 7}).reason == "unknown_type"


def test_unsupported_version_rejected():
    assert rejected(raw_envelope(schema_version="2.0")).reason == "unsupported_version"


@pytest.mark.parametrize(
    "payload",
    [
        {"task": "x", "run_shell": "rm -rf /"},  # extra field
        {"task": ""},  # empty task
        {"task": "x" * 8001},  # oversize string
        {"task": "x", "priority": "urgent"},  # bad enum
        {"inputs": {}},  # missing task
        "not an object",
    ],
)
def test_payload_violations_rejected(payload):
    assert rejected(raw_envelope(payload=payload)).reason == "payload_violation"


def test_payload_list_limits():
    payload = {"request_message_id": "m0", "status": "succeeded", "summary": "s", "artifact_ids": ["a"] * 101}
    assert rejected(raw_envelope("task.result", payload=payload)).reason == "payload_violation"
    critique = copy.deepcopy(VALID_PAYLOADS["critique.result"])
    critique["issues"][0]["exploit"] = True
    assert rejected(raw_envelope("critique.result", payload=critique)).reason == "payload_violation"


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected_field": 1},
        {"timestamp": "2026-09-30T12:00:00"},  # naive
        {"sender_role": "RootAgent"},
        {"message_id": "has spaces"},
        {"trace_id": "x" * 65},
        {"authorization_context": {"permissions": ["experiment:read"], "autonomy_level": "L9"}},
        {"authorization_context": {"permissions": ["Experiment Read!"], "autonomy_level": "L0_ASSISTED"}},
        {"authorization_context": {"permissions": [], "autonomy_level": "L0_ASSISTED", "is_admin": True}},
        {"signature": "not-hex"},
    ],
)
def test_schema_violations_rejected(overrides):
    assert rejected(raw_envelope(**overrides)).reason == "schema_violation"


def test_missing_receiver_rejected():
    raw = raw_envelope()
    raw.pop("receiver_role")
    assert rejected(raw).reason == "missing_receiver"
    assert validate(raw | {"receiver_agent_run_id": "run-coder"}).receiver_role is None


def test_impersonation_rejected():
    error = rejected(raw_envelope(sender_role="VerifierAgent"))
    assert error.reason == "impersonation"
    assert rejected(raw_envelope(), sender_actual_run_id="run-other").reason == "impersonation"
    assert validate(raw_envelope(), sender_actual_run_id="run-planner").sender_agent_run_id == "run-planner"


def test_permission_escalation_rejected():
    raw = raw_envelope(
        authorization_context={"permissions": ["experiment:read", "admin:policy"], "autonomy_level": "L0_ASSISTED"}
    )
    error = rejected(raw)
    assert error.reason == "escalation"
    assert "admin:policy" in error.detail


def test_autonomy_escalation_rejected():
    raw = raw_envelope()
    assert rejected(raw, sender_autonomy_level="L1_RESEARCH_AUTOMATION").reason == "escalation"
    assert validate(raw, sender_autonomy_level="L3_AUTOMATED_EXECUTION").message_id == "msg-1"


def test_oversize_rejected():
    raw = raw_envelope(payload={"task": "x", "inputs": {"blob": "y" * (MAX_ENVELOPE_BYTES + 1)}})
    assert rejected(raw).reason == "oversize"


def test_too_deep_rejected():
    nested: dict = {}
    cursor = nested
    for _ in range(30):
        cursor["n"] = {}
        cursor = cursor["n"]
    assert rejected(raw_envelope(payload={"task": "x", "inputs": nested})).reason == "too_deep"


def test_non_finite_numbers_rejected():
    assert rejected(raw_envelope(payload={"task": "x", "inputs": {"v": float("nan")}})).reason == "malformed"


@pytest.mark.parametrize("raw", [None, [], "string", 42])
def test_non_object_rejected(raw):
    assert rejected(raw).reason == "malformed"


def test_rejection_message_includes_reason():
    error = rejected(raw_envelope(schema_version="9"))
    assert str(error).startswith("unsupported_version")


def test_direct_construction_validates_payload_and_type():
    data = raw_envelope()
    data["timestamp"] = NOW
    env = AgentMessageEnvelope(**data)
    assert env.payload["priority"] == "high"
    assert env.payload["inputs"] == {"dataset": "d1"}
    with pytest.raises(ValueError):
        AgentMessageEnvelope(**(data | {"message_type": "nope"}))
    with pytest.raises(ValueError):
        AgentMessageEnvelope(**(data | {"payload": {"task": "x", "evil": 1}}))
