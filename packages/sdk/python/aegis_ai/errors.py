"""SDK exception hierarchy."""

from __future__ import annotations

from typing import Any


class AegisError(Exception):
    """Base class for all SDK errors."""


class AegisConnectionError(AegisError):
    """The API could not be reached."""


class AegisAPIError(AegisError):
    """The API returned an error response following the standard error envelope."""

    def __init__(
        self, status_code: int, code: str, message: str, request_id: str | None = None, details: Any = None
    ) -> None:
        super().__init__(f"[{status_code} {code}] {message}" + (f" (request_id={request_id})" if request_id else ""))
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details


class AuthenticationError(AegisAPIError):
    """401 — missing, invalid, expired or revoked credentials."""


class PermissionDeniedError(AuthenticationError):
    """403 — authenticated, but the key's role/scopes do not grant this capability."""


class PlanLimitError(PermissionDeniedError):
    """403 plan_limit_exceeded / feature_not_in_plan — ``details`` names the metric, usage and limit."""


class NotFoundError(AegisAPIError):
    """404 — resource not found."""


class ConflictError(AegisAPIError):
    """409 — conflict with current state."""


class ValidationError(AegisAPIError):
    """422 — request validation failed."""


class RateLimitError(AegisAPIError):
    """429 — rate limit exceeded."""

    def __init__(self, *args: Any, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


def raise_for_response(status_code: int, body: dict[str, Any], retry_after: int | None = None) -> None:
    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code", "error")
    message = error.get("message", "Request failed")
    request_id = error.get("request_id")
    details = error.get("details")
    if status_code == 403 and code in ("plan_limit_exceeded", "feature_not_in_plan", "feature_unavailable"):
        raise PlanLimitError(status_code, code, message, request_id, details)
    cls = {
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        409: ConflictError,
        422: ValidationError,
        429: RateLimitError,
    }.get(status_code, AegisAPIError)
    if cls is RateLimitError:
        raise RateLimitError(status_code, code, message, request_id, details, retry_after=retry_after or 60)
    raise cls(status_code, code, message, request_id, details)
