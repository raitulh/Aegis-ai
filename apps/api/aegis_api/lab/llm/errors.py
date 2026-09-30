"""Typed LLM errors.

Every provider failure is mapped onto one of these classes so that the gateway, the workflow retry policy
and the failure classifier can reason about it without parsing messages:

================  ==========  ==========  ============================================================
class             retryable   fallback    examples
================  ==========  ==========  ============================================================
transient         yes         yes         429 (honours Retry-After), 500/502/503/504, timeouts
unavailable       no          yes         provider not configured, connection refused
permanent         no          yes         401/403 (credentials), 404 (unknown model)
validation        no          no          400 invalid request, structured output invalid after repair
policy            no          no          prompt blocked, SAFETY/RECITATION/… finish reasons
================  ==========  ==========  ============================================================

They are also ``AppError`` subclasses, so if one escapes into an HTTP request it renders through the
standard error envelope. Messages never contain API keys or prompt text.
"""

from __future__ import annotations

from typing import Any, ClassVar

from aegis_api.errors import AppError
from aegis_api.lab.core.errors import PermanentError, TransientError


class LLMError(AppError):
    """The model provider request failed."""

    status_code = 502
    code = "llm_error"
    error_class: ClassVar[str] = "permanent"
    retryable: ClassVar[bool] = False
    fallback: ClassVar[bool] = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        provider: str | None = None,
        status: int | None = None,
        retry_after: float | None = None,
        details: Any = None,
    ) -> None:
        payload = dict(details or {})
        if provider:
            payload.setdefault("provider", provider)
        payload.setdefault("error_class", self.error_class)
        super().__init__(message, code=code, details=payload)
        self.provider = provider
        self.status = status
        self.retry_after = retry_after


class LLMTransientError(LLMError, TransientError):
    """The model provider is temporarily unavailable. Retry later."""

    status_code = 503
    code = "llm_transient_error"
    error_class = "transient"
    retryable = True
    fallback = True


class LLMUnavailableError(LLMError, TransientError):
    """The model provider is not reachable or not configured."""

    status_code = 503
    code = "llm_provider_unavailable"
    error_class = "unavailable"
    retryable = False
    fallback = True


class LLMPermanentError(LLMError, PermanentError):
    """The model provider rejected the request (credentials or unknown model)."""

    status_code = 502
    code = "llm_provider_error"
    error_class = "permanent"
    retryable = False
    fallback = True


class LLMValidationError(LLMError, PermanentError):
    """The model request or response was invalid."""

    status_code = 502
    code = "llm_invalid_request"
    error_class = "validation"
    retryable = False
    fallback = False


class LLMOutputInvalid(LLMValidationError):
    """The model output did not match the required schema (after one repair attempt)."""

    code = "llm_output_invalid"


class LLMPolicyError(LLMError, PermanentError):
    """The model provider blocked the prompt or the response for policy reasons."""

    status_code = 422
    code = "llm_content_blocked"
    error_class = "policy"
    retryable = False
    fallback = False


def error_code(exc: BaseException) -> str:
    """Short error code for the usage ledger (``model_usage.error_code`` is 64 chars)."""
    code = getattr(exc, "code", None) or type(exc).__name__
    return str(code)[:64]


def error_class_of(exc: BaseException) -> str:
    return getattr(exc, "error_class", None) or ("transient" if isinstance(exc, TransientError) else "permanent")
