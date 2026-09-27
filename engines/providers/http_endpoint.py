"""Generic HTTP endpoint adapter for customer AI systems.

Request:  POST <endpoint> {"input": str, "context": {...}, "metadata": {...}}
Response: {"output": str, "tool_calls": [{"name": str, "arguments": {...}}]?,
           "retrieved": [{"id": str, "title": str, "text": str, "url": str?}]?, "model": str?}

The URL must be validated by the caller (SSRF protection) before this adapter is constructed.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from engines.evaluation.base import RetrievedDoc, SystemInvocation, TestInput, ToolCallRecord
from engines.providers.base import elapsed_ms


class HTTPEndpointTarget:
    def __init__(
        self,
        url: str,
        *,
        auth_header: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url
        self.headers = {"content-type": "application/json", "user-agent": "AegisAI-Auditor/1.0"}
        if auth_header:
            self.headers["authorization"] = auth_header
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def invoke(self, test_input: TestInput, repetition: int) -> SystemInvocation:
        started = time.perf_counter()
        base = SystemInvocation(
            variant=test_input.variant, repetition=repetition, prompt=test_input.prompt, provider="http_endpoint"
        )
        try:
            response = self.client.post(
                self.url,
                json={
                    "input": test_input.prompt,
                    "context": test_input.context,
                    "metadata": {"repetition": repetition},
                },
                headers=self.headers,
            )
        except httpx.HTTPError as exc:
            base.error = f"endpoint unreachable: {exc.__class__.__name__}"
            return base
        base.latency_ms = elapsed_ms(started)
        if response.status_code >= 400:
            base.error = f"endpoint returned HTTP {response.status_code}"
            return base
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            base.output = response.text[:20000]
            return base
        base.output = str(data.get("output", ""))[:20000]
        base.model = data.get("model")
        base.tool_calls = [
            ToolCallRecord(name=str(t.get("name")), arguments=t.get("arguments") or {}, result=t.get("result"))
            for t in data.get("tool_calls") or []
            if isinstance(t, dict) and t.get("name")
        ]
        base.retrieved = [
            RetrievedDoc(
                doc_id=str(r.get("id", i)), title=str(r.get("title", "")), text=str(r.get("text", "")), url=r.get("url")
            )
            for i, r in enumerate(data.get("retrieved") or [])
            if isinstance(r, dict)
        ]
        return base
