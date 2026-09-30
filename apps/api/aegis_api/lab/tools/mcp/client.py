"""Dual-era MCP Streamable HTTP client (synchronous, httpx).

Two protocol eras are deployed side by side:

* **modern** (``2026-07-28`` and later, stateless): no ``initialize`` handshake and no session. Every request
  carries ``params._meta`` with ``io.modelcontextprotocol/protocolVersion``/``clientInfo``/``clientCapabilities``
  and the headers ``MCP-Protocol-Version``, ``Mcp-Method`` and (``tools/call``) ``Mcp-Name``.
* **legacy** (``2025-11-25``/``2025-06-18``/``2025-03-26``): ``initialize`` (capturing ``Mcp-Session-Id`` and the
  negotiated version) → ``notifications/initialized`` (202) → requests with ``MCP-Protocol-Version`` and
  ``Mcp-Session-Id``; a 404 on a session means it expired → re-initialize once; ``DELETE`` ends the session.

The client tries the modern form first. A 400/404/405 carrying a recognized modern error (``-32022`` with
``data.supported``) switches to a supported version (modern if we speak one, else legacy); an empty or
unrecognized error body means a legacy server → fall back to the handshake. The era is cached per client (and
per server by the registry, via ``protocol_version``).

Responses are ``application/json`` or ``text/event-stream``. SSE events are parsed incrementally (``data:``
lines joined with ``\\n``; ``:`` comments and empty data skipped); the JSON-RPC response whose ``id`` matches the
request is returned, notifications are ignored and server→client requests (sampling, elicitation, roots…) are
answered with JSON-RPC error ``-32601`` via a POST. Response size is capped while streaming, redirects are never
followed, the endpoint is SSRF-validated, and credentials (``Authorization``) are never logged.
"""

from __future__ import annotations

import base64
import codecs
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import structlog

from aegis_api.config import get_settings
from aegis_api.errors import ValidationFailed
from aegis_api.security.ssrf import validate_outbound_url

log = structlog.get_logger("aegis.lab.mcp")

Era = Literal["modern", "legacy"]
JSONRPC_VERSION = "2.0"
CLIENT_NAME = "aegis-lab"
CLIENT_VERSION = "1.0.0"
STATELESS_SINCE = "2026-07-28"
LEGACY_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")
META_PREFIX = "io.modelcontextprotocol/"
MAX_TOOLS = 500
MAX_PAGES = 50
MAX_ERROR_BODY_BYTES = 64 * 1024
FALLBACK_STATUSES = frozenset({400, 404, 405})

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
MISSING_CAPABILITY = -32021
UNSUPPORTED_VERSION = -32022
MODERN_ERRORS = frozenset({HEADER_MISMATCH, MISSING_CAPABILITY, UNSUPPORTED_VERSION})
_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------
class MCPError(Exception):
    """Base class. ``message`` never contains credentials."""

    kind = "mcp_error"

    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        data: Any = None,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data
        self.status = status
        self.retryable = retryable


class MCPTransportError(MCPError):
    """Network failure, timeout, redirect or non-JSON-RPC HTTP error."""

    kind = "mcp_transport_error"


class MCPProtocolError(MCPError):
    """The server's reply does not follow the protocol."""

    kind = "mcp_protocol_error"


class MCPRemoteError(MCPError):
    """The server answered with a JSON-RPC error."""

    kind = "mcp_remote_error"


class MCPResponseTooLarge(MCPError):
    """The response exceeded the configured size limit."""

    kind = "mcp_response_too_large"


class MCPInputRequired(MCPError):
    """The server asked for user input (``resultType: input_required``), which an automated broker cannot give."""

    kind = "mcp_input_required"


class _FallbackToLegacy(Exception):
    def __init__(self, version: str | None = None) -> None:
        super().__init__(version or "")
        self.version = version


@dataclass
class _Reply:
    status: int
    headers: httpx.Headers
    message: dict[str, Any] | None = None
    raw_error: str | None = None

    @property
    def error(self) -> dict[str, Any] | None:
        if self.message is None:
            return None
        err = self.message.get("error")
        return err if isinstance(err, dict) else None


@dataclass
class ServerDescription:
    era: Era | None
    protocol_version: str | None
    server_info: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None


def is_modern_version(version: str | None) -> bool:
    return bool(version and _VERSION_RE.match(version) and version >= STATELESS_SINCE)


def encode_header_value(value: str) -> str:
    """Header-safe value: plain visible ASCII as-is, otherwise ``=?base64?<b64 of UTF-8>?=``."""
    if value and value == value.strip() and all(0x20 < ord(c) < 0x7F or c == " " for c in value):
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


# ---------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------
class MCPClient:
    def __init__(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_response_bytes: int | None = None,
        protocol_version: str | None = None,
        legacy_version: str | None = None,
        era: Era | None = None,
        transport: httpx.BaseTransport | None = None,
        validate_endpoint: bool = True,
    ) -> None:
        settings = get_settings()
        if validate_endpoint:
            schemes = frozenset({"https"}) if settings.is_production else frozenset({"https", "http"})
            try:
                endpoint = validate_outbound_url(endpoint, allowed_schemes=schemes)
            except ValidationFailed as exc:
                raise MCPTransportError(f"MCP endpoint rejected: {exc.message}", code="endpoint_rejected") from None
        self.endpoint = endpoint
        self._auth_headers = dict(headers or {})
        self.timeout = float(timeout or settings.mcp_timeout_seconds)
        self.max_response_bytes = int(max_response_bytes or settings.mcp_max_response_bytes)
        self.protocol_version = protocol_version or settings.mcp_protocol_version
        self.legacy_version = legacy_version or settings.mcp_legacy_protocol_version
        self.era: Era | None = era
        self.negotiated_version: str | None = None
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.instructions: str | None = None
        self._legacy_hint: str | None = None
        self._initialized = False
        self._next_id = 0
        self._http = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
            follow_redirects=False,
            transport=transport,
        )

    # -- lifecycle -----------------------------------------------------------------------------
    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """End a legacy session (``DELETE``; 405 is fine) and release connections. Never raises."""
        try:
            if self.era == "legacy" and self.session_id:
                headers = {**self._auth_headers, **self._legacy_headers()}
                try:
                    self._http.delete(self.endpoint, headers=headers)
                except httpx.HTTPError:
                    log.debug("mcp_session_delete_failed")
        finally:
            self.session_id = None
            self._initialized = False
            self._http.close()

    # -- public API ----------------------------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        """All tools (following ``nextCursor``; at most :data:`MAX_TOOLS`)."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _page in range(MAX_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            items = result.get("tools")
            if not isinstance(items, list):
                raise MCPProtocolError("tools/list result has no 'tools' array")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    tools.append(item)
                    if len(tools) >= MAX_TOOLS:
                        log.warning("mcp_tool_list_capped", limit=MAX_TOOLS)
                        return tools
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                break
            seen.add(next_cursor)
            cursor = next_cursor
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """``tools/call`` → ``{content, structuredContent, isError}`` (tool failures are ``isError: true``)."""
        result = self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        content = result.get("content")
        if content is None:
            content = []
        if not isinstance(content, list):
            raise MCPProtocolError("tools/call result 'content' must be an array")
        structured = result.get("structuredContent")
        return {
            "content": content,
            "structuredContent": structured if isinstance(structured, dict) else None,
            "isError": bool(result.get("isError", False)),
        }

    def describe(self) -> ServerDescription:
        """Era, negotiated version and server info (legacy: from ``initialize``; modern: ``server/discover``)."""
        if self.era is None:
            self.list_tools()
        if self.era == "modern" and not self.server_info:
            try:
                result = self.request("server/discover", {})
            except MCPError:
                result = {}
            meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
            info = result.get("serverInfo") or meta.get(f"{META_PREFIX}serverInfo")
            caps = result.get("capabilities") or meta.get(f"{META_PREFIX}serverCapabilities")
            self.server_info = info if isinstance(info, dict) else {}
            self.capabilities = caps if isinstance(caps, dict) else {}
        return ServerDescription(
            era=self.era,
            protocol_version=self.negotiated_version,
            server_info=dict(self.server_info),
            capabilities=dict(self.capabilities),
            instructions=self.instructions,
        )

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-RPC request in the right era and return its ``result``."""
        params = dict(params or {})
        if self.era != "legacy":
            try:
                result = self._modern(method, params)
                self.era = "modern"
                return result
            except _FallbackToLegacy as fallback:
                log.info("mcp_legacy_fallback", method=method)
                self.era = "legacy"
                self._legacy_hint = fallback.version
        return self._legacy(method, params)

    # -- modern --------------------------------------------------------------------------------
    def _modern(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        version = self.negotiated_version if self.era == "modern" and self.negotiated_version else self.protocol_version
        tried: set[str] = set()
        while True:
            tried.add(version)
            meta = dict(params.get("_meta") or {}) if isinstance(params.get("_meta"), dict) else {}
            meta.update(
                {
                    f"{META_PREFIX}protocolVersion": version,
                    f"{META_PREFIX}clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                    f"{META_PREFIX}clientCapabilities": {},
                }
            )
            message = self._message(method, {**params, "_meta": meta})
            headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
            if method == "tools/call" and isinstance(params.get("name"), str):
                headers["Mcp-Name"] = encode_header_value(params["name"])
            reply = self._send(message, headers)
            error = reply.error
            code = error.get("code") if error else None
            if code == UNSUPPORTED_VERSION and error is not None:
                data = error.get("data") if isinstance(error.get("data"), dict) else {}
                supported = [v for v in data.get("supported") or [] if isinstance(v, str)]
                modern = sorted(
                    (v for v in supported if is_modern_version(v) and v <= self.protocol_version and v not in tried),
                    reverse=True,
                )
                if modern:
                    version = modern[0]
                    continue
                legacy = sorted((v for v in supported if v in LEGACY_VERSIONS), reverse=True)
                if legacy:
                    raise _FallbackToLegacy(legacy[0])
                raise MCPRemoteError(
                    "The MCP server supports no protocol version this client speaks",
                    code=UNSUPPORTED_VERSION,
                    data={"supported": supported[:20]},
                )
            if reply.status in FALLBACK_STATUSES and code not in MODERN_ERRORS:
                if self.era == "modern" and error is not None:
                    # A known-modern server answered with an ordinary JSON-RPC error.
                    return self._result(reply)
                raise _FallbackToLegacy(None)
            self.negotiated_version = version
            return self._result(reply)

    # -- legacy --------------------------------------------------------------------------------
    def _legacy_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.negotiated_version:
            headers["MCP-Protocol-Version"] = self.negotiated_version
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _initialize(self) -> None:
        version = self._legacy_hint or self.legacy_version
        message = self._message(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        reply = self._send(message, {})
        if reply.status >= 400 and reply.message is None:
            raise MCPTransportError(f"MCP initialize failed with HTTP {reply.status}", status=reply.status)
        result = self._result(reply)
        negotiated = result.get("protocolVersion")
        if not isinstance(negotiated, str) or negotiated not in LEGACY_VERSIONS:
            raise MCPProtocolError(
                "The MCP server negotiated an unsupported protocol version",
                data={"protocolVersion": str(negotiated)[:40]},
            )
        self.negotiated_version = negotiated
        session = reply.headers.get("mcp-session-id")
        self.session_id = session if session and all(0x20 < ord(c) < 0x7F for c in session) else None
        info = result.get("serverInfo")
        caps = result.get("capabilities")
        self.server_info = info if isinstance(info, dict) else {}
        self.capabilities = caps if isinstance(caps, dict) else {}
        instructions = result.get("instructions")
        self.instructions = instructions[:4000] if isinstance(instructions, str) else None
        ack = self._send({"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"}, self._legacy_headers())
        if ack.status >= 400:
            raise MCPProtocolError(
                f"The MCP server rejected notifications/initialized (HTTP {ack.status})", status=ack.status
            )
        self._initialized = True

    def _legacy(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            self._initialize()
        reply = self._send(self._message(method, params), self._legacy_headers())
        if reply.status == 404 and self.session_id:
            log.info("mcp_session_expired", method=method)
            self.session_id = None
            self._initialized = False
            self._initialize()
            reply = self._send(self._message(method, params), self._legacy_headers())
        if reply.status >= 400 and reply.message is None:
            raise MCPTransportError(f"MCP request failed with HTTP {reply.status}", status=reply.status)
        return self._result(reply)

    # -- transport -----------------------------------------------------------------------------
    def _message(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        return {"jsonrpc": JSONRPC_VERSION, "id": self._next_id, "method": method, "params": params}

    def _result(self, reply: _Reply) -> dict[str, Any]:
        message = reply.message
        if message is None:
            raise MCPProtocolError(f"The MCP server sent no JSON-RPC response (HTTP {reply.status})", status=reply.status)
        error = reply.error
        if error is not None or "error" in message:
            err = error or {}
            code = err.get("code")
            text = str(err.get("message") or "MCP error")[:500]
            raise MCPRemoteError(text, code=code if isinstance(code, int) else None, data=err.get("data"))
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError("The MCP response 'result' must be an object")
        if result.get("resultType") == "input_required":
            raise MCPInputRequired("The MCP server requires interactive input, which the tool broker cannot provide")
        return result

    def _send(self, message: dict[str, Any], headers: dict[str, str]) -> _Reply:
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._auth_headers,
            **headers,
        }
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request_id = message.get("id")
        try:
            with self._http.stream("POST", self.endpoint, content=body, headers=request_headers) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise MCPTransportError("The MCP server redirected the request; redirects are not followed", status=status)
                if status in (401, 403):
                    raise MCPTransportError("The MCP server rejected the credentials", status=status, code="unauthorized")
                if status == 429 or status >= 500:
                    raise MCPTransportError(
                        f"The MCP server is unavailable (HTTP {status})", status=status, retryable=True
                    )
                if status == 202 or request_id is None:
                    return _Reply(status, response.headers)
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type == "text/event-stream" and status < 400:
                    return _Reply(status, response.headers, self._read_sse(response, request_id))
                raw = self._read_body(response, limit=self.max_response_bytes if status < 400 else MAX_ERROR_BODY_BYTES)
                return _Reply(status, response.headers, self._pick(raw, request_id), None if status < 400 else "")
        except httpx.TimeoutException as exc:
            raise MCPTransportError("The MCP server did not respond in time", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"Network error talking to the MCP server ({type(exc).__name__})", retryable=True) from exc

    def _read_body(self, response: httpx.Response, *, limit: int) -> bytes:
        buffer = bytearray()
        for chunk in response.iter_bytes():
            buffer.extend(chunk)
            if len(buffer) > limit:
                if limit == MAX_ERROR_BODY_BYTES:
                    return bytes(buffer[:limit])
                raise MCPResponseTooLarge(f"The MCP response exceeded {limit} bytes")
        return bytes(buffer)

    def _pick(self, raw: bytes, request_id: Any) -> dict[str, Any] | None:
        if not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        candidates = data if isinstance(data, list) else [data]
        fallback: dict[str, Any] | None = None
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("id") == request_id and ("result" in item or "error" in item):
                return item
            if "error" in item and item.get("id") is None and fallback is None:
                fallback = item  # e.g. {"id": null, "error": {...}} for requests the server could not parse
        return fallback

    def _read_sse(self, response: httpx.Response, request_id: Any) -> dict[str, Any]:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        total = 0
        pending = ""
        data_lines: list[str] = []

        def dispatch() -> dict[str, Any] | None:
            data = "\n".join(data_lines)
            data_lines.clear()
            if not data.strip():
                return None
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                log.debug("mcp_sse_invalid_json")
                return None
            for item in payload if isinstance(payload, list) else [payload]:
                found = self._on_stream_message(item, request_id)
                if found is not None:
                    return found
            return None

        def feed(line: str) -> dict[str, Any] | None:
            if line == "":
                return dispatch()
            if line.startswith(":"):
                return None
            name, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if name == "data":
                data_lines.append(value)
            return None

        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise MCPResponseTooLarge(f"The MCP response exceeded {self.max_response_bytes} bytes")
            pending += decoder.decode(chunk)
            pending = pending.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                found = feed(line)
                if found is not None:
                    return found
        pending += decoder.decode(b"", final=True)
        for line in pending.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            found = feed(line)
            if found is not None:
                return found
        found = dispatch()
        if found is not None:
            return found
        raise MCPProtocolError("The MCP event stream ended without a response to the request")

    def _on_stream_message(self, item: Any, request_id: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        method = item.get("method")
        if isinstance(method, str):
            if "id" in item and item.get("id") is not None:
                self._reject_server_request(item["id"], method)
            return None  # notifications (progress, logging, list_changed…) are ignored
        if item.get("id") == request_id and ("result" in item or "error" in item):
            return item
        return None

    def _reject_server_request(self, request_id: Any, method: str) -> None:
        """Server→client requests (sampling, elicitation, roots) are not supported by the tool broker."""
        message = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": METHOD_NOT_FOUND, "message": f"Method '{method[:80]}' is not supported by this client"},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._auth_headers,
        }
        if self.era == "legacy":
            headers.update(self._legacy_headers())
        else:
            headers["MCP-Protocol-Version"] = self.negotiated_version or self.protocol_version
        try:
            self._http.post(self.endpoint, content=json.dumps(message).encode("utf-8"), headers=headers)
        except httpx.HTTPError:
            log.debug("mcp_server_request_reject_failed", method=method[:80])
