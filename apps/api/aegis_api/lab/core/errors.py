"""Lab-specific typed errors (rendered through the standard ``{"error": {...}}`` envelope).

The retry policy (``engines.lab`` / workflows) distinguishes these classes of failure:

* transient   — ``TransientError`` / ``ServiceUnavailable``: retried with bounded backoff
* permanent   — ``PermanentError``: never retried
* policy      — ``PolicyDenied`` / ``ApprovalRequired``: never retried; may wait for a human decision
* validation  — ``ValidationFailed`` / ``InvalidTransition``: never retried
* quota       — ``QuotaExceeded`` / ``BudgetExceeded``: never retried; mission pauses
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from aegis_api.errors import AppError, Conflict, Forbidden, PlanLimitExceeded, ServiceUnavailable, error_body
from engines.lab.states import InvalidTransitionError


class PolicyDenied(Forbidden):
    """The action is denied by governance policy."""

    code = "policy_denied"


class ApprovalRequired(AppError):
    """The action requires human approval before it can proceed."""

    status_code = 409
    code = "approval_required"

    def __init__(self, message: str | None = None, *, approval_id: str | None = None, details: Any = None) -> None:
        payload = dict(details or {})
        if approval_id:
            payload["approval_id"] = approval_id
        super().__init__(message, details=payload or None)
        self.approval_id = approval_id


class BudgetExceeded(AppError):
    """The mission or project budget would be exceeded."""

    status_code = 409
    code = "budget_exceeded"


class QuotaExceeded(PlanLimitExceeded):
    """An organization quota has been reached."""

    code = "quota_exceeded"


class InvalidTransition(Conflict):
    """The requested lifecycle transition is not allowed from the current state."""

    code = "invalid_state_transition"


class ConcurrentModification(Conflict):
    """The resource was modified concurrently. Reload and retry."""

    code = "concurrent_modification"


class FeatureDisabled(Forbidden):
    """This feature is disabled for your organization."""

    code = "feature_disabled"


class IdempotencyConflict(Conflict):
    """The Idempotency-Key was already used with a different request."""

    code = "idempotency_key_reused"


class ModelUnavailable(ServiceUnavailable):
    """No configured model provider is available for this task."""

    code = "model_unavailable"


class ExecutionUnavailable(ServiceUnavailable):
    """The experiment execution backend is unavailable."""

    code = "execution_unavailable"


class TransientError(Exception):
    """A retryable infrastructure failure (network timeout, 5xx, lock contention)."""


class PermanentError(Exception):
    """A non-retryable failure (bad input, unsupported operation, corrupted artifact)."""


def invalid_transition(exc: InvalidTransitionError) -> InvalidTransition:
    return InvalidTransition(
        str(exc),
        details={"machine": exc.machine, "from": exc.current, "to": exc.target, "allowed": sorted(exc.allowed)},
    )


def install_lab_error_handlers(app: FastAPI) -> None:
    """Map domain exceptions raised below the service layer onto the error envelope."""

    @app.exception_handler(InvalidTransitionError)
    async def _transition(request: Request, exc: InvalidTransitionError) -> JSONResponse:
        err = invalid_transition(exc)
        return JSONResponse(
            error_body(err.code, err.message, getattr(request.state, "request_id", None), err.details),
            status_code=err.status_code,
        )

    @app.exception_handler(StaleDataError)
    async def _stale(request: Request, exc: StaleDataError) -> JSONResponse:
        err = ConcurrentModification()
        return JSONResponse(
            error_body(err.code, err.message, getattr(request.state, "request_id", None)),
            status_code=err.status_code,
        )
