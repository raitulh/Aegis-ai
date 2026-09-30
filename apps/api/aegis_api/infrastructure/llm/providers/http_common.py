"""Shared HTTP plumbing for REST-based LLM providers."""

from __future__ import annotations

from typing import Any

import httpx

from aegis_api.infrastructure.llm.base import LLMError, LLMErrorKind, classify_status


def post_json(
    client: httpx.Client, url: str, body: dict[str, Any], *, provider: str, timeout: float | None = None
) -> dict[str, Any]:
    try:
        response = client.post(url, json=body, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LLMError(f"{provider} request timed out", kind=LLMErrorKind.TIMEOUT, provider=provider) from exc
    except httpx.TransportError as exc:
        raise LLMError(
            f"{provider} unreachable: {type(exc).__name__}", kind=LLMErrorKind.UNAVAILABLE, provider=provider
        ) from exc
    if response.status_code >= 400:
        detail = ""
        try:
            err = response.json().get("error")
            detail = str(err.get("message") if isinstance(err, dict) else err or "")[:300]
        except ValueError:
            pass
        retry_after = response.headers.get("retry-after")
        raise LLMError(
            f"{provider} error (HTTP {response.status_code}{': ' + detail if detail else ''})",
            kind=classify_status(response.status_code),
            provider=provider,
            status=response.status_code,
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{provider} returned non-JSON", kind=LLMErrorKind.TRANSIENT, provider=provider) from exc
    return data if isinstance(data, dict) else {}
