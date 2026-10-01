"""Application error model.

Every error response has the same shape:

    {"error": {"code": "<stable_machine_code>", "message": "<human text>",
               "details": {...} | null, "request_id": "<id>"}}

Codes are stable and documented in docs/API.md; the frontend maps them to copy.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."

    def __init__(self, message: str | None = None, *, code: str | None = None,
                 status_code: int | None = None, details: dict[str, Any] | None = None) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"
    message = "Some fields are invalid."


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Please sign in to continue."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to do that."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "We could not find what you were looking for."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "This conflicts with the current state."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please slow down and try again shortly."


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The uploaded content is too large."


def field_errors(**fields: str) -> ValidationFailed:
    return ValidationFailed(details={"fields": fields})
