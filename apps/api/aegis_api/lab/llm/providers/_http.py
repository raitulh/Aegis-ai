"""Shared HTTP plumbing for provider adapters: client construction, error mapping, SSE parsing.

* Redirects are never followed (a provider API has no reason to redirect; following could leak credentials).
* Every error message is scrubbed of the API key and truncated; request bodies (prompts) are never echoed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from aegis_api.lab.llm.errors import (
    LLMError,
    LLMPermanentError,
    LLMTransientError,
    LLMUnavailableError,
    LLMValidationError,
)

MAX_ERROR_CHARS = 240
TRANSIENT_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def build_client(timeout: float, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        follow_redirects=False,
        transport=transport,
        headers={"user-agent": "aegis-lab/1.0"},
    )


def scrub(text: str, *secrets: str | None) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_ERROR_CHARS]


def _error_payload(response: httpx.Response) -> tuple[str, str, Any]:
    """(status text, message, details) from a JSON error body (Google/OpenAI/Anthropic shapes)."""
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return "", "", None
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict):
        return "", "", None
    error = body.get("error", body)
    if isinstance(error, str):
        return "", error, None
    if not isinstance(error, dict):
        return "", "", None
    status = str(error.get("status") or error.get("type") or error.get("code") or "")
    message = str(error.get("message") or "")
    return status, message, error.get("details")


def parse_retry_after(response: httpx.Response, details: Any = None) -> float | None:
    """Seconds to wait from ``Retry-After`` (delta-seconds or HTTP-date) or a Google ``RetryInfo`` detail."""
    header = response.headers.get("retry-after")
    if header:
        header = header.strip()
        try:
            return max(float(header), 0.0)
        except ValueError:
            try:
                when = parsedate_to_datetime(header)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                return max((when - datetime.now(UTC)).total_seconds(), 0.0)
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
                delay = str(item.get("retryDelay", "")).rstrip("s")
                try:
                    return max(float(delay), 0.0)
                except ValueError:
                    return None
    return None


def map_http_error(provider: str, response: httpx.Response, *, secret: str | None = None) -> LLMError:
    """Map a non-2xx provider response onto the typed error taxonomy."""
    status_code = response.status_code
    status_text, message, details = _error_payload(response)
    summary = scrub(f"{provider} HTTP {status_code} {status_text}: {message}".rstrip(": "), secret)
    retry_after = parse_retry_after(response, details)
    if status_code == 429:
        return LLMTransientError(
            summary, code="llm_rate_limited", provider=provider, status=429, retry_after=retry_after
        )
    if status_code in TRANSIENT_STATUSES or status_code >= 500:
        return LLMTransientError(
            summary, code="llm_provider_unavailable", provider=provider, status=status_code, retry_after=retry_after
        )
    if status_code in (401, 403):
        return LLMPermanentError(
            f"{provider} rejected the credentials (HTTP {status_code})",
            code="llm_auth_failed",
            provider=provider,
            status=status_code,
        )
    if status_code == 404:
        return LLMPermanentError(summary, code="llm_model_not_found", provider=provider, status=404)
    if status_code == 413:
        return LLMValidationError(summary, code="llm_request_too_large", provider=provider, status=413)
    return LLMValidationError(summary, code="llm_invalid_request", provider=provider, status=status_code)


def map_transport_error(provider: str, exc: httpx.HTTPError) -> LLMError:
    name = type(exc).__name__
    if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError):
        return LLMUnavailableError(f"{provider} is unreachable ({name})", code="llm_unreachable", provider=provider)
    if isinstance(exc, httpx.TimeoutException):
        return LLMTransientError(f"{provider} timed out ({name})", code="llm_timeout", provider=provider)
    return LLMTransientError(f"{provider} transport error ({name})", code="llm_transport_error", provider=provider)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Exactly one HTTP request; returns the JSON object body or raises a typed LLM error."""
    try:
        response = client.request(method, url, json=body, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise map_transport_error(provider, exc) from None
    if 300 <= response.status_code < 400:
        raise LLMPermanentError(
            f"{provider} answered with an unexpected redirect (HTTP {response.status_code})",
            code="llm_unexpected_redirect",
            provider=provider,
            status=response.status_code,
        )
    if response.status_code >= 400:
        raise map_http_error(provider, response, secret=secret)
    if not response.content:
        return {}
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        raise LLMTransientError(
            f"{provider} returned a non-JSON response", code="llm_bad_response", provider=provider
        ) from None
    if not isinstance(data, dict):
        raise LLMTransientError(
            f"{provider} returned an unexpected JSON shape", code="llm_bad_response", provider=provider
        )
    return data


@dataclass(frozen=True)
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None
    raw: bool = False  # a non-SSE line block (e.g. a raw JSON error body)


def iter_sse(lines: Iterable[str]) -> Iterator[SSEEvent]:
    """Parse Server-Sent Events. Data lines are joined with ``\\n``; ``:`` comments are ignored; blocks of
    lines that are not SSE fields are yielded as ``raw`` events (providers send JSON errors that way)."""
    data: list[str] = []
    raw: list[str] = []
    event: str | None = None
    event_id: str | None = None

    def flush() -> Iterator[SSEEvent]:
        nonlocal data, raw, event, event_id
        if data:
            yield SSEEvent(data="\n".join(data), event=event, id=event_id)
        if raw:
            yield SSEEvent(data="\n".join(raw), raw=True)
        data, raw, event, event_id = [], [], None, None

    for line in lines:
        line = line.rstrip("\r")
        if not line:
            yield from flush()
            continue
        if line.startswith(":"):
            continue
        field, sep, value = line.partition(":")
        if sep and field in ("data", "event", "id", "retry"):
            value = value[1:] if value.startswith(" ") else value
            if field == "data":
                data.append(value)
            elif field == "event":
                event = value
            elif field == "id":
                event_id = value
            continue
        raw.append(line)
    yield from flush()


def raise_for_stream_status(provider: str, response: httpx.Response, *, secret: str | None = None) -> None:
    """For ``client.stream`` responses: read the (small) error body and raise the mapped error."""
    if response.status_code < 300:
        return
    response.read()
    if response.status_code < 400:
        raise LLMPermanentError(
            f"{provider} answered with an unexpected redirect (HTTP {response.status_code})",
            code="llm_unexpected_redirect",
            provider=provider,
            status=response.status_code,
        )
    raise map_http_error(provider, response, secret=secret)


def raw_json_error(provider: str, text: str, *, secret: str | None = None) -> LLMError | None:
    """A raw (non-``data:``) JSON block inside a stream that carries an error object, mapped by its code."""
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return None
    error = body["error"]
    code = error.get("code")
    status = code if isinstance(code, int) else 500
    return map_http_error(provider, httpx.Response(status, json=body), secret=secret)
