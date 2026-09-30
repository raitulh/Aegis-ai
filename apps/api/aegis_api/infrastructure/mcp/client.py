"""Minimal, strict MCP client for remote **Streamable HTTP** servers.

Never trusts a server implicitly: the endpoint is SSRF-validated before every connection, redirects are
refused, every request has a timeout, responses are size-capped, JSON-RPC ids are checked, and results are
returned as plain data for the ToolBroker to validate, sanitize and audit.
"""

from __future__ import annotations

import contextlib
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from aegis_api.security.ssrf import validate_outbound_url

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "aegis-scientist-lab", "version": "1.0.0"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOOLS = 500


class MCPError(Exception):
    def __init__(self, message: str, *, code: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


@dataclass
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPCallResult:
    text: str
    structured: dict[str, Any] | None
    is_error: bool
    content_types: list[str]
    raw_bytes: int


class MCPClient:
    def __init__(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        url_validator: Callable[[str], str] = validate_outbound_url,
    ) -> None:
        self.endpoint = url_validator(endpoint)
        self._validator = url_validator
        self._headers = dict(headers or {})
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False, transport=transport
        )
        self._ids = itertools.count(1)
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None

    def __enter__(self) -> MCPClient:
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self.session_id:
            with contextlib.suppress(httpx.HTTPError):
                self._client.delete(self.endpoint, headers=self._base_headers(), timeout=5)
        self._client.close()

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self._headers,
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload: dict[str, Any], *, expect_response: bool) -> dict[str, Any] | None:
        self._validator(self.endpoint)  # re-validate at call time (DNS rebinding defence)
        try:
            with self._client.stream("POST", self.endpoint, json=payload, headers=self._base_headers()) as response:
                if response.status_code in (401, 403):
                    raise MCPError("MCP server rejected the credentials", code=response.status_code)
                if response.status_code == 404 and self.session_id:
                    raise MCPError("MCP session expired", code=404, transient=True)
                if response.status_code >= 400:
                    raise MCPError(
                        f"MCP server returned HTTP {response.status_code}",
                        code=response.status_code,
                        transient=response.status_code >= 500,
                    )
                sid = response.headers.get("mcp-session-id")
                if sid and not self.session_id:
                    if len(sid) > 256 or not sid.isprintable():
                        raise MCPError("MCP server sent an invalid session id")
                    self.session_id = sid
                if not expect_response:
                    return None
                ctype = response.headers.get("content-type", "")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise MCPError("MCP response exceeds the size limit")
        except httpx.TimeoutException as exc:
            raise MCPError("MCP request timed out", transient=True) from exc
        except httpx.TransportError as exc:
            raise MCPError(f"MCP server unreachable: {type(exc).__name__}", transient=True) from exc
        messages = _parse_sse(bytes(body)) if "text/event-stream" in ctype else [_json(bytes(body))]
        for message in messages:
            if isinstance(message, dict) and message.get("id") == payload.get("id"):
                if "error" in message:
                    err = message["error"] or {}
                    raise MCPError(f"MCP error: {str(err.get('message', 'unknown'))[:300]}", code=err.get("code"))
                result = message.get("result")
                return result if isinstance(result, dict) else {}
        raise MCPError("MCP server did not return a matching JSON-RPC response")

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            payload["params"] = params
        result = self._post(payload, expect_response=True)
        return result or {}

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}
        )
        self.protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self.server_info = dict(result.get("serverInfo") or {})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        return result

    def list_tools(self) -> list[MCPToolInfo]:
        tools: list[MCPToolInfo] = []
        cursor: str | None = None
        for _ in range(50):
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            for t in result.get("tools") or []:
                if not isinstance(t, dict) or not isinstance(t.get("name"), str):
                    continue
                raw_schema = t.get("inputSchema")
                schema: dict[str, Any] = dict(raw_schema) if isinstance(raw_schema, dict) else {"type": "object"}
                raw_ann = t.get("annotations")
                tools.append(
                    MCPToolInfo(
                        name=t["name"][:160],
                        description=str(t.get("description") or "")[:4000],
                        input_schema=schema,
                        annotations=dict(raw_ann) if isinstance(raw_ann, dict) else {},
                    )
                )
                if len(tools) >= MAX_TOOLS:
                    return tools
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPCallResult:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        texts: list[str] = []
        types: list[str] = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type", "unknown"))
            types.append(ctype)
            if ctype == "text":
                texts.append(str(item.get("text", "")))
            elif ctype == "resource" and isinstance(item.get("resource"), dict) and "text" in item["resource"]:
                texts.append(str(item["resource"]["text"]))
            else:
                texts.append(f"[{ctype} content omitted]")
        structured = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else None
        raw = json.dumps(result, default=str)
        return MCPCallResult(
            text="\n".join(texts),
            structured=structured,
            is_error=bool(result.get("isError")),
            content_types=types,
            raw_bytes=len(raw),
        )


def _json(data: bytes) -> Any:
    try:
        return json.loads(data or b"{}")
    except json.JSONDecodeError as exc:
        raise MCPError("MCP server returned invalid JSON") from exc


def _parse_sse(data: bytes) -> list[Any]:
    messages: list[Any] = []
    buffer: list[str] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            buffer.append(line[5:].strip())
        elif not line.strip() and buffer:
            with contextlib.suppress(json.JSONDecodeError):
                messages.append(json.loads("\n".join(buffer)))
            buffer = []
    if buffer:
        with contextlib.suppress(json.JSONDecodeError):
            messages.append(json.loads("\n".join(buffer)))
    return messages
