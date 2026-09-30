"""Pure tests of the workflow context helpers, the flow registry and the Temporal workflow wrappers."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest

from aegis_api.lab.workflows.ctx import (
    MAX_PAYLOAD_BYTES,
    ActivityFailed,
    ChildWorkflowFailed,
    PayloadError,
    StepKeys,
    WorkflowDefinitionError,
    attempts_allowed,
    backoff_seconds,
    build_activity_payload,
    error_record,
    is_non_retryable,
    normalize_mapping,
    normalize_payload,
    normalize_result,
    validate_signal_name,
    validate_step_key,
    validate_workflow_key,
)
from aegis_api.lab.workflows.registry import RetrySpec


class Color(StrEnum):
    RED = "red"


# ------------------------------------------------------------------------------------------------
# Step keys
# ------------------------------------------------------------------------------------------------
def _key_sequence() -> list[str]:
    keys = StepKeys()
    return [keys.next("a"), keys.next("b"), keys.next("a"), keys.next("now"), keys.next("a"), keys.next("b")]


def test_step_keys_are_deterministic_per_activity_name() -> None:
    first, second = _key_sequence(), _key_sequence()
    assert first == second == ["a#1", "b#1", "a#2", "now#1", "a#3", "b#2"]


def test_step_key_counters_are_independent_per_name() -> None:
    keys = StepKeys()
    assert keys.next("experiments.schedule_runs") == "experiments.schedule_runs#1"
    assert keys.peek("experiments.schedule_runs") == 1
    assert keys.peek("other") == 0


@pytest.mark.parametrize("key", ["", "has space", "x" * 301, "semi;colon", "quote'"])
def test_invalid_step_keys_rejected(key: str) -> None:
    with pytest.raises(WorkflowDefinitionError):
        validate_step_key(key)


@pytest.mark.parametrize("key", ["now#1", "uuid#3", "signal:go#1", "sleep#1", "child:X:y", "detached:X:y"])
def test_reserved_step_key_prefixes_rejected_for_explicit_keys(key: str) -> None:
    with pytest.raises(WorkflowDefinitionError):
        validate_step_key(key)
    assert validate_step_key(key, allow_reserved=True) == key


def test_signal_and_workflow_key_validation() -> None:
    assert validate_signal_name("approval") == "approval"
    assert validate_workflow_key("attempt-2") == "attempt-2"
    for bad in ("", "1abc", "with space", "x" * 65):
        with pytest.raises(PayloadError):
            validate_signal_name(bad)
    for bad in ("", "a/b", "x" * 65):
        with pytest.raises(PayloadError):
            validate_workflow_key(bad)


# ------------------------------------------------------------------------------------------------
# Payload normalization
# ------------------------------------------------------------------------------------------------
def test_normalize_payload_converts_common_types() -> None:
    uid = uuid.uuid4()
    when = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    out = normalize_payload(
        {
            "id": uid,
            uid: "uuid key",
            "when": when,
            "money": Decimal("12.345600"),
            "color": Color.RED,
            "items": (1, 2.5, None, True),
            "nested": {"list": [uid]},
        }
    )
    assert out == {
        "id": str(uid),
        str(uid): "uuid key",
        "when": "2026-09-30T12:00:00+00:00",
        "money": "12.345600",
        "color": "red",
        "items": [1, 2.5, None, True],
        "nested": {"list": [str(uid)]},
    }
    assert type(out["color"]) is str


@pytest.mark.parametrize(
    "value",
    [
        {"blob": b"bytes"},
        {"set": {1, 2}},
        {"obj": object()},
        {"nan": float("nan")},
        {"inf": float("inf")},
        {1: "int key"},
        {"dec": Decimal("NaN")},
    ],
)
def test_normalize_payload_rejects_non_json(value: dict[Any, Any]) -> None:
    with pytest.raises(PayloadError):
        normalize_payload(value)


def test_normalize_payload_rejects_deep_nesting() -> None:
    value: dict[str, Any] = {}
    cursor = value
    for _ in range(80):
        cursor["x"] = {}
        cursor = cursor["x"]
    with pytest.raises(PayloadError):
        normalize_payload(value)


def test_normalize_mapping_enforces_object_and_size() -> None:
    assert normalize_mapping(None, what="input") == {}
    with pytest.raises(PayloadError):
        normalize_mapping(["not", "an", "object"], what="input")  # type: ignore[arg-type]
    with pytest.raises(PayloadError):
        normalize_mapping({"big": "x" * (MAX_PAYLOAD_BYTES + 10)}, what="input")


def test_normalize_result_requires_dict() -> None:
    assert normalize_result(None, what="flow") == {}
    assert normalize_result({"a": uuid.UUID(int=1)}, what="flow") == {"a": str(uuid.UUID(int=1))}
    with pytest.raises(WorkflowDefinitionError):
        normalize_result([1, 2], what="flow")


def test_activity_payload_injects_actor_and_run_id_overriding_caller_values() -> None:
    run_id = str(uuid.uuid4())
    actor = {"kind": "workflow", "organization_id": str(uuid.uuid4()), "permissions": ["mission:read"]}
    body = build_activity_payload(
        {"job_id": uuid.UUID(int=7), "actor": {"kind": "system"}, "workflow_run_id": "spoofed"},
        actor_payload=actor,
        workflow_run_id=run_id,
    )
    assert body["job_id"] == str(uuid.UUID(int=7))
    assert body["actor"] == actor
    assert body["workflow_run_id"] == run_id


# ------------------------------------------------------------------------------------------------
# Retry classification & error records
# ------------------------------------------------------------------------------------------------
def test_non_retryable_classification_uses_the_exception_mro() -> None:
    from aegis_api.errors import NotFound, ValidationFailed
    from aegis_api.lab.core.errors import (
        ApprovalRequired,
        BudgetExceeded,
        ModelUnavailable,
        PermanentError,
        PolicyDenied,
        TransientError,
    )

    class CorruptArtifact(PermanentError):
        pass

    spec = RetrySpec()
    for exc in (
        PermanentError("x"),
        CorruptArtifact("x"),
        ValidationFailed("x"),
        PolicyDenied("x"),
        ApprovalRequired("x"),
        BudgetExceeded("x"),
        NotFound("x"),
        PayloadError("x"),
    ):
        assert is_non_retryable(exc, spec), type(exc).__name__
    for exc in (TransientError("x"), ModelUnavailable("x"), TimeoutError("x"), RuntimeError("x")):
        assert not is_non_retryable(exc, spec), type(exc).__name__


def test_error_record_carries_typed_error_details() -> None:
    from aegis_api.lab.core.errors import ApprovalRequired

    record = error_record(
        ApprovalRequired("needs review", approval_id="apr-1"), RetrySpec(), activity="x.y", attempts=1
    )
    assert record["type"] == "ApprovalRequired"
    assert record["code"] == "approval_required"
    assert record["non_retryable"] is True
    assert record["details"]["approval_id"] == "apr-1"
    failure = ActivityFailed.from_record("x.y", record)
    assert failure.error_type == "ApprovalRequired"
    assert failure.details["approval_id"] == "apr-1"
    assert ActivityFailed.from_record("x.y", failure.to_record()).to_record() == failure.to_record()


def test_child_failure_record_round_trip() -> None:
    failure = ChildWorkflowFailed("ExperimentWorkflow", "run-1", "FAILED", "boom")
    again = ChildWorkflowFailed.from_record(failure.to_record())
    assert (again.kind, again.workflow_run_id, again.status, again.message) == (
        "ExperimentWorkflow",
        "run-1",
        "FAILED",
        "boom",
    )


def test_backoff_is_exponential_and_bounded() -> None:
    spec = RetrySpec(initial_interval_seconds=1.0, backoff_coefficient=2.0, max_interval_seconds=5.0)
    assert [backoff_seconds(spec, n) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert attempts_allowed(spec, None) == 3
    assert attempts_allowed(spec, 0) == 0
    assert attempts_allowed(spec, 7) == 7


# ------------------------------------------------------------------------------------------------
# Flow registry & generic flows
# ------------------------------------------------------------------------------------------------
class RecordingContext:
    """Minimal in-memory WorkflowContext used to drive flows without an engine."""

    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.workflow_run_id = str(uuid.uuid4())
        self.kind = "Test"
        self.input: dict[str, Any] = {}
        self.actor_payload: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self._results = results

    async def activity(
        self, name: str, payload: Any = None, *, step_key: str | None = None, **_: Any
    ) -> dict[str, Any]:
        self.calls.append((name, dict(payload or {}), step_key))
        return self._results[name]


def test_generic_flows_are_registered() -> None:
    from aegis_api.lab.workflows.definitions import FLOW_KINDS, KNOWN_FLOW_KINDS, get_flow, has_flow

    assert "ExecutionJobWorkflow" in FLOW_KINDS
    assert "AgentRunWorkflow" in FLOW_KINDS
    assert has_flow("ExecutionJobWorkflow")
    assert get_flow("AgentRunWorkflow") is FLOW_KINDS["AgentRunWorkflow"]
    assert set(KNOWN_FLOW_KINDS) >= {"ExecutionJobWorkflow", "AgentRunWorkflow", "MissionWorkflow"}


def test_execution_job_flow_runs_the_job_on_the_execution_activity() -> None:
    from aegis_api.lab.workflows.definitions import execution_job_flow

    ctx = RecordingContext({"execution.run_job": {"status": "SUCCEEDED", "exit_code": 0}})
    result = asyncio.run(execution_job_flow(ctx, {"job_id": "job-1"}))  # type: ignore[arg-type]
    assert result == {"status": "SUCCEEDED", "exit_code": 0}
    assert ctx.calls == [("execution.run_job", {"job_id": "job-1"}, "execution.run_job")]
    with pytest.raises(ValueError):
        asyncio.run(execution_job_flow(ctx, {}))  # type: ignore[arg-type]


def test_agent_run_flow_passes_run_identity_and_context() -> None:
    from aegis_api.lab.workflows.definitions import agent_run_flow

    ctx = RecordingContext({"agents.run": {"agent_run_id": "ar-1", "status": "COMPLETED"}})
    flow_input = {"agent_run_id": "ar-1", "role": "LiteratureAgent", "project_id": "p-1", "mission_id": None}
    result = asyncio.run(agent_run_flow(ctx, flow_input))  # type: ignore[arg-type]
    assert result["status"] == "COMPLETED"
    name, payload, key = ctx.calls[0]
    assert (name, key) == ("agents.run", "agents.run")
    assert payload == {"agent_run_id": "ar-1", "role": "LiteratureAgent", "project_id": "p-1"}


def test_register_flow_validation() -> None:
    from aegis_api.lab.workflows.definitions import register_flow, validate_kind

    async def flow_a(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def flow_b(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    kind = f"UnitFlow{uuid.uuid4().hex[:8]}"
    assert register_flow(kind, flow_a) is flow_a
    assert register_flow(kind, flow_a) is flow_a  # idempotent for the same function
    with pytest.raises(ValueError):
        register_flow(kind, flow_b)
    register_flow(kind, flow_b, replace=True)
    for bad in ("lowercase", "Has-Dash", "X" * 49, "", "1Digit"):
        with pytest.raises(ValueError):
            validate_kind(bad)
    with pytest.raises(ValueError):
        register_flow(f"UnitFlow{uuid.uuid4().hex[:8]}", flow_a, execution_timeout_seconds=0)


# ------------------------------------------------------------------------------------------------
# Temporal wrappers
# ------------------------------------------------------------------------------------------------
def test_temporal_workflow_classes_are_generated_per_kind() -> None:
    from temporalio.workflow import _Definition

    from aegis_api.lab.workflows import temporal_workflows

    cls = temporal_workflows.workflow_class("ExecutionJobWorkflow")
    assert cls.__name__ == "ExecutionJobWorkflow"
    assert temporal_workflows.workflow_class("ExecutionJobWorkflow") is cls
    # Importable by name (this is how the Temporal sandbox re-imports it).
    from aegis_api.lab.workflows.temporal_workflows import ExecutionJobWorkflow  # type: ignore[attr-defined]

    assert ExecutionJobWorkflow is cls
    defn = _Definition.must_from_class(cls)
    assert defn.name == "ExecutionJobWorkflow"
    assert "signal" in defn.signals
    assert "status" in defn.queries
    names = {c.__name__ for c in temporal_workflows.workflow_classes()}
    assert {"ExecutionJobWorkflow", "AgentRunWorkflow"} <= names


def test_temporal_retry_policy_mirrors_retry_spec() -> None:
    from aegis_api.lab.workflows.temporal_workflows import retry_policy

    spec = RetrySpec(max_attempts=4, initial_interval_seconds=0.5, backoff_coefficient=3.0, max_interval_seconds=9.0)
    policy = retry_policy(spec)
    assert policy.maximum_attempts == 4
    assert policy.initial_interval.total_seconds() == 0.5
    assert policy.backoff_coefficient == 3.0
    assert policy.maximum_interval is not None and policy.maximum_interval.total_seconds() == 9.0
    assert "PermanentError" in (policy.non_retryable_error_types or [])
    assert retry_policy(spec, 1).maximum_attempts == 1


def test_activity_error_maps_to_activity_failed() -> None:
    from temporalio.exceptions import ActivityError, ApplicationError, RetryState

    from aegis_api.lab.workflows.temporal_workflows import activity_failed_from_error

    record = {"activity": "x.y", "type": "PermanentError", "message": "bad", "non_retryable": True, "attempts": 1}
    cause = ApplicationError("bad", record, type="PermanentError", non_retryable=True)
    error = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="w",
        activity_type="x.y",
        activity_id="1",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    error.__cause__ = cause
    failure = activity_failed_from_error("x.y", error)
    assert failure.error_type == "PermanentError"
    assert failure.message == "bad"
    assert failure.non_retryable is True


def test_non_retryable_names_come_from_the_activity_retry_spec() -> None:
    """Names added to ``registry.DEFAULT_NON_RETRYABLE`` (or an activity's own list) apply to both engines."""

    class LLMValidationError(Exception):
        pass

    class LLMTransientError(Exception):
        pass

    spec = RetrySpec(non_retryable=("LLMValidationError",))
    assert is_non_retryable(LLMValidationError("bad output"), spec)
    assert not is_non_retryable(LLMTransientError("try again"), spec)
    assert not is_non_retryable(LLMValidationError("bad output"), RetrySpec(non_retryable=()))


def test_worker_cli_arguments() -> None:
    from aegis_api.lab.workflows.worker import parse_args

    assert parse_args([]).queue_list == ["default", "execution"]
    assert parse_args(["--queues", "execution, default,execution"]).queue_list == ["execution", "default"]
    assert parse_args(["--queues", "default", "--max-activities", "4"]).max_activities == 4
    for bad in (["--queues", "gpu"], ["--queues", ""]):
        with pytest.raises(SystemExit):
            parse_args(bad)
