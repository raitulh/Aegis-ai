"""The engine-agnostic workflow context used by flow code, plus the pure helpers both engines share.

A *flow* is a plain coroutine ``async def flow(ctx: WorkflowContext, input: dict) -> dict`` registered with
:func:`aegis_api.lab.workflows.definitions.register_flow`. The same flow runs unchanged on Temporal and on
the local durable engine; both re-execute (replay) flow code after a restart and feed it the recorded
results of completed steps, so flow code must be deterministic:

* no IO, no database, no HTTP, no LLM calls — every side effect is an activity (``await ctx.activity(...)``);
* no ``datetime.now()``, ``uuid.uuid4()``, ``random`` or environment reads — use ``ctx.now()`` / ``ctx.uuid()``;
* no iteration over unordered collections whose order influences which activities run;
* import only pure modules (``engines.*``, this module, the registries).

Steps are identified by *step keys*: an explicit ``step_key`` or ``"<activity>#<n>"`` where ``n`` counts the
calls of that activity in this execution — deterministic across replays as long as the flow is.

This module performs no IO and is safe to import inside the Temporal workflow sandbox.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from aegis_api.lab.workflows.registry import RetrySpec

MAX_STEP_KEY_LENGTH = 300  # workflow_steps.step_key
MAX_PAYLOAD_BYTES = 1_000_000  # well below Temporal's payload limits
MAX_SIGNAL_PAYLOAD_BYTES = 64_000
MAX_PAYLOAD_DEPTH = 64
MAX_ERROR_MESSAGE_CHARS = 2000

# Keys the engines inject into every activity payload (caller-provided values are overwritten).
RESERVED_PAYLOAD_KEYS: tuple[str, ...] = ("actor", "workflow_run_id")

# Step-key namespaces used by the engines themselves (explicit activity step keys may not use them).
RESERVED_STEP_PREFIXES: tuple[str, ...] = ("now#", "uuid#", "signal:", "sleep#", "child:", "detached:")

_STEP_KEY_RE = re.compile(r"^[A-Za-z0-9_.:#/@=+\-]{1,300}$")
_SIGNAL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:\-]{0,63}$")
_WORKFLOW_KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


# ------------------------------------------------------------------------------------------------
# Errors surfaced to flow code
# ------------------------------------------------------------------------------------------------
class PayloadError(ValueError):
    """A workflow/activity payload is not plain JSON (or is too large)."""


class WorkflowDefinitionError(RuntimeError):
    """Flow code is invalid: unknown activity/flow, bad step key, non-dict result, …"""


class WorkflowDeterminismError(RuntimeError):
    """On replay, the flow issued a different command than the one recorded for the same step key."""


class ActivityTimeout(Exception):
    """An activity attempt exceeded its start-to-close or heartbeat timeout (retryable)."""


class ActivityFailed(Exception):
    """An activity failed permanently (non-retryable error, or retries exhausted).

    Raised into flow code by both engines with the same attributes, so flows can react to failures
    (e.g. wait for a human decision when ``error_type == "ApprovalRequired"``) independently of the engine.
    """

    def __init__(
        self,
        activity: str,
        error_type: str,
        message: str,
        *,
        non_retryable: bool = True,
        code: str | None = None,
        details: Any = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(f"{activity} failed: {error_type}: {message}")
        self.activity = activity
        self.error_type = error_type
        self.message = message
        self.non_retryable = non_retryable
        self.code = code
        self.details = details
        self.attempts = attempts

    def to_record(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "type": self.error_type,
            "message": self.message,
            "non_retryable": self.non_retryable,
            "code": self.code,
            "details": self.details,
            "attempts": self.attempts,
        }

    @classmethod
    def from_record(cls, activity: str, record: Mapping[str, Any] | None) -> ActivityFailed:
        data = dict(record or {})
        return cls(
            str(data.get("activity") or activity),
            str(data.get("type") or "ActivityError"),
            str(data.get("message") or ""),
            non_retryable=bool(data.get("non_retryable", True)),
            code=data.get("code"),
            details=data.get("details"),
            attempts=data.get("attempts"),
        )


class ChildWorkflowFailed(Exception):
    """A child workflow ended FAILED / CANCELLED / TIMED_OUT."""

    def __init__(self, kind: str, workflow_run_id: str | None, status: str, message: str) -> None:
        super().__init__(f"child {kind} ({workflow_run_id}) ended {status}: {message}")
        self.kind = kind
        self.workflow_run_id = workflow_run_id
        self.status = status
        self.message = message

    def to_record(self) -> dict[str, Any]:
        return {
            "type": "ChildWorkflowFailed",
            "kind": self.kind,
            "workflow_run_id": self.workflow_run_id,
            "status": self.status,
            "message": self.message,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | None) -> ChildWorkflowFailed:
        data = dict(record or {})
        return cls(
            str(data.get("kind") or ""),
            data.get("workflow_run_id"),
            str(data.get("status") or "FAILED"),
            str(data.get("message") or ""),
        )


# ------------------------------------------------------------------------------------------------
# The protocol flows program against
# ------------------------------------------------------------------------------------------------
@runtime_checkable
class WorkflowContext(Protocol):
    """What a flow may do. Implemented by ``LocalWorkflowContext`` and ``TemporalWorkflowContext``."""

    workflow_run_id: str
    kind: str
    input: dict[str, Any]
    actor_payload: dict[str, Any]

    def now(self) -> datetime:
        """Deterministic current time (UTC; recorded on first execution, replayed afterwards)."""
        ...

    def uuid(self) -> str:
        """Deterministic random UUID (string)."""
        ...

    async def activity(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        step_key: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        heartbeat_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Run activity ``name`` (at most once per step key) and return its JSON result."""
        ...

    async def child(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a child workflow and wait for its result (raises :class:`ChildWorkflowFailed`)."""
        ...

    async def start_detached(
        self,
        kind: str,
        input: Mapping[str, Any],
        *,
        workflow_key: str = "default",
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> str:
        """Launch a workflow that outlives this one; returns its workflow_run_id."""
        ...

    async def gather(self, *aws: Awaitable[Any]) -> list[Any]:
        """Run awaitables concurrently (deterministic scheduling order)."""
        ...

    async def wait_signal(self, name: str, *, timeout_seconds: float | None) -> dict[str, Any] | None:
        """Wait for (and consume) the next signal ``name``; ``None`` on timeout."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Durable timer."""
        ...

    def log(self, msg: str, **kw: Any) -> None:
        """Replay-safe log line (suppressed while replaying recorded history)."""
        ...

    def cancelled(self) -> bool:
        """Whether cancellation of this workflow has been requested."""
        ...


FlowFn = Callable[[WorkflowContext, dict[str, Any]], Awaitable[dict[str, Any]]]


# ------------------------------------------------------------------------------------------------
# Deterministic step keys
# ------------------------------------------------------------------------------------------------
class StepKeys:
    """Per-execution counters producing ``"<name>#<n>"`` keys (n starts at 1 for every name)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def next(self, name: str) -> str:
        n = self._counts.get(name, 0) + 1
        self._counts[name] = n
        return validate_step_key(f"{name}#{n}", allow_reserved=True)

    def peek(self, name: str) -> int:
        return self._counts.get(name, 0)


def validate_step_key(key: str, *, allow_reserved: bool = False) -> str:
    if not isinstance(key, str) or not _STEP_KEY_RE.match(key):
        raise WorkflowDefinitionError(
            f"Invalid step key {key!r}: 1..{MAX_STEP_KEY_LENGTH} chars of letters, digits and _.:#/@=+-"
        )
    if not allow_reserved and key.startswith(RESERVED_STEP_PREFIXES):
        raise WorkflowDefinitionError(f"Step key {key!r} uses a reserved engine prefix")
    return key


def validate_signal_name(name: str) -> str:
    if not isinstance(name, str) or not _SIGNAL_NAME_RE.match(name):
        raise PayloadError("Signal names are 1..64 chars: a letter, then letters, digits and _.:-")
    return name


def validate_workflow_key(key: str) -> str:
    if not isinstance(key, str) or not _WORKFLOW_KEY_RE.match(key):
        raise PayloadError("workflow_key must be 1..64 chars of letters, digits and _.:-")
    return key


# ------------------------------------------------------------------------------------------------
# JSON normalization
# ------------------------------------------------------------------------------------------------
def normalize_payload(value: Any, *, path: str = "$", _depth: int = 0) -> Any:
    """Return a plain-JSON copy of ``value``.

    UUIDs, dates/datetimes (ISO 8601), Decimals (as strings, lossless), enums and tuples are converted.
    Anything else that is not JSON — bytes, sets, arbitrary objects, NaN/Infinity, non-string keys — is
    rejected with :class:`PayloadError` instead of being silently stringified.
    """
    if _depth > MAX_PAYLOAD_DEPTH:
        raise PayloadError(f"{path}: payload nested deeper than {MAX_PAYLOAD_DEPTH} levels")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PayloadError(f"{path}: NaN/Infinity are not valid JSON")
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PayloadError(f"{path}: non-finite Decimal")
        return str(value)
    if isinstance(value, Enum):
        return normalize_payload(value.value, path=path, _depth=_depth + 1)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, uuid.UUID):
                key = str(key)
            if not isinstance(key, str):
                raise PayloadError(f"{path}: object keys must be strings, got {type(key).__name__}")
            out[str(key)] = normalize_payload(item, path=f"{path}.{key}", _depth=_depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [normalize_payload(item, path=f"{path}[{i}]", _depth=_depth + 1) for i, item in enumerate(value)]
    raise PayloadError(f"{path}: {type(value).__name__} is not JSON-serializable")


def payload_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def normalize_mapping(
    value: Mapping[str, Any] | None, *, what: str, max_bytes: int = MAX_PAYLOAD_BYTES
) -> dict[str, Any]:
    """Normalize a JSON object payload and enforce the size bound."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PayloadError(f"{what} must be a JSON object, got {type(value).__name__}")
    out = normalize_payload(value)
    size = payload_size(out)
    if size > max_bytes:
        raise PayloadError(f"{what} is {size} bytes; the limit is {max_bytes}")
    return out


def normalize_result(value: Any, *, what: str) -> dict[str, Any]:
    """Flow and activity results are JSON objects (``None`` means ``{}``)."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise WorkflowDefinitionError(f"{what} must return a dict, got {type(value).__name__}")
    return normalize_mapping(value, what=what)


def build_activity_payload(
    payload: Mapping[str, Any] | None, *, actor_payload: Mapping[str, Any], workflow_run_id: str
) -> dict[str, Any]:
    """Normalize ``payload`` and inject the workflow actor and run id (overriding caller values)."""
    body = normalize_mapping(payload, what="activity payload")
    body["actor"] = normalize_payload(dict(actor_payload))
    body["workflow_run_id"] = str(workflow_run_id)
    return body


# ------------------------------------------------------------------------------------------------
# Retry classification
# ------------------------------------------------------------------------------------------------
def is_non_retryable(exc: BaseException, retry: RetrySpec) -> bool:
    """Non-retryable when the exception (or any base class) is listed in ``retry.non_retryable``.

    Matching the MRO means subclasses of listed errors (e.g. a context's own ``PermanentError`` subclass)
    are non-retryable too, on both engines.
    """
    if isinstance(exc, PayloadError | WorkflowDefinitionError):
        return True
    names = set(retry.non_retryable)
    return any(cls.__name__ in names for cls in type(exc).__mro__)


def error_record(exc: BaseException, retry: RetrySpec, *, activity: str, attempts: int) -> dict[str, Any]:
    """JSON description of an activity failure (type, message, retryability, typed-error extras)."""
    details: Any = getattr(exc, "details", None)
    approval_id = getattr(exc, "approval_id", None)
    if approval_id:
        details = {**(details if isinstance(details, dict) else {}), "approval_id": str(approval_id)}
    try:
        details = normalize_payload(details) if details is not None else None
    except PayloadError:
        details = None
    code = getattr(exc, "code", None)
    return {
        "activity": activity,
        "type": type(exc).__name__,
        "message": (str(exc) or type(exc).__name__)[:MAX_ERROR_MESSAGE_CHARS],
        "non_retryable": is_non_retryable(exc, retry),
        "code": code if isinstance(code, str) else None,
        "details": details,
        "attempts": attempts,
    }


def backoff_seconds(retry: RetrySpec, attempt: int) -> float:
    """Delay before attempt ``attempt + 1`` (bounded exponential backoff)."""
    delay = retry.initial_interval_seconds * (retry.backoff_coefficient ** max(0, attempt - 1))
    return float(max(0.0, min(delay, retry.max_interval_seconds)))


def attempts_allowed(retry: RetrySpec, override: int | None) -> int:
    """Maximum attempts (0 = unlimited, as in Temporal)."""
    value = retry.max_attempts if override is None else override
    return max(0, int(value))


def describe_exception(exc: BaseException) -> str:
    """Compact ``Type: message`` string for run errors (bounded, no tracebacks)."""
    message = str(exc) or type(exc).__name__
    text = message if isinstance(exc, ActivityFailed | ChildWorkflowFailed) else f"{type(exc).__name__}: {message}"
    return text[:MAX_ERROR_MESSAGE_CHARS]
