"""HTTP client and resource namespaces."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from aegis_ai.errors import AegisConnectionError, raise_for_response
from aegis_ai.models import Page

# The resource classes below each define a ``list`` method, which shadows the
# ``list`` builtin inside their own annotations. This module-scope alias lets
# collection return types keep the modern builtin-generic style without collision.
JsonList = list[dict[str, Any]]

DEFAULT_BASE_URL = "http://localhost:8000"
USER_AGENT = "aegis-ai-python/1.0.0"
RETRY_STATUSES = {500, 502, 503, 504}


class _Transport:
    def __init__(self, api_key: str, base_url: str, timeout: float, max_retries: int) -> None:
        if not api_key:
            raise ValueError("An API key is required (create one in Settings → API Keys)")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(method, path, params=_clean(params), json=json)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise AegisConnectionError(f"Could not reach Aegis API at {self.base_url}: {exc}") from exc
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(0.75 * (attempt + 1))
                continue
            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(int(response.headers.get("Retry-After", 1)))
                continue
            if response.status_code >= 400:
                body = _safe_json(response)
                raise_for_response(response.status_code, body, retry_after=int(response.headers.get("Retry-After", 60)))
            return _safe_json(response) if response.content else None
        raise AegisConnectionError("Request failed after retries") from last_exc

    def close(self) -> None:
        self._client.close()


def _clean(params: dict | None) -> dict | None:
    return {k: v for k, v in params.items() if v is not None} if params else None


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"error": {"code": "invalid_response", "message": response.text[:200]}}


class _Resource:
    def __init__(self, transport: _Transport) -> None:
        self._t = transport


class Systems(_Resource):
    def list(self, *, page: int = 1, page_size: int = 25, environment: str | None = None, q: str | None = None) -> Page:
        return Page.from_response(
            self._t.request(
                "GET",
                "/api/v1/systems",
                params={"page": page, "page_size": page_size, "environment": environment, "q": q},
            )
        )

    def create(self, **fields: Any) -> dict[str, Any]:
        return self._t.request("POST", "/api/v1/systems", json=fields)

    def get(self, system_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/systems/{system_id}")

    def update(self, system_id: str, **fields: Any) -> dict[str, Any]:
        return self._t.request("PATCH", f"/api/v1/systems/{system_id}", json=fields)

    def delete(self, system_id: str) -> dict[str, Any]:
        return self._t.request("DELETE", f"/api/v1/systems/{system_id}")


class Audits(_Resource):
    def list(
        self, *, page: int = 1, page_size: int = 25, status: str | None = None, system_id: str | None = None
    ) -> Page:
        return Page.from_response(
            self._t.request(
                "GET",
                "/api/v1/audits",
                params={"page": page, "page_size": page_size, "status": status, "system_id": system_id},
            )
        )

    def create(
        self,
        *,
        system_id: str,
        categories: Sequence[str],
        policy_version_ids: Sequence[str] | None = None,
        intensity: str = "standard",
        config: dict | None = None,
        name: str | None = None,
        start: bool = True,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/audits",
            json={
                "system_id": system_id,
                "categories": categories,
                "policy_version_ids": policy_version_ids or [],
                "intensity": intensity,
                "config": config or {},
                "name": name,
                "start": start,
            },
        )

    def get(self, audit_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/audits/{audit_id}")

    def cancel(self, audit_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/audits/{audit_id}/cancel")

    def results(self, audit_id: str, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", f"/api/v1/audits/{audit_id}/results", params=params))

    def evidence(self, audit_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/audits/{audit_id}/evidence")

    def report(self, audit_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/audits/{audit_id}/report")

    def events(self, audit_id: str, after: int = 0) -> JsonList:
        return self._t.request("GET", f"/api/v1/audits/{audit_id}/events", params={"after": after})

    def wait(self, audit_id: str, *, poll_interval: float = 1.0, timeout: float = 300.0) -> dict[str, Any]:
        """Block until the audit reaches a terminal state."""
        deadline = time.time() + timeout
        terminal = {"completed", "partially_completed", "failed", "cancelled"}
        while time.time() < deadline:
            audit = self.get(audit_id)
            if audit["status"] in terminal:
                return audit
            time.sleep(poll_interval)
        raise TimeoutError(f"Audit {audit_id} did not finish within {timeout}s")


class Policies(_Resource):
    def list(self, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", "/api/v1/policies", params=params))

    def create(self, *, name: str, key: str, source_text: str | None = None, **fields: Any) -> dict[str, Any]:
        return self._t.request(
            "POST", "/api/v1/policies", json={"name": name, "key": key, "source_text": source_text, **fields}
        )

    def get(self, policy_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/policies/{policy_id}")

    def compile(self, policy_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/policies/{policy_id}/compile")

    def new_version(
        self,
        policy_id: str,
        *,
        source_text: str | None = None,
        dsl_yaml: str | None = None,
        change_note: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/api/v1/policies/{policy_id}/version",
            json={"source_text": source_text, "dsl_yaml": dsl_yaml, "change_note": change_note},
        )


class Findings(_Resource):
    def list(self, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", "/api/v1/findings", params=params))

    def get(self, finding_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/findings/{finding_id}")

    def update(self, finding_id: str, **fields: Any) -> dict[str, Any]:
        return self._t.request("PATCH", f"/api/v1/findings/{finding_id}", json=fields)

    def evidence(self, finding_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/findings/{finding_id}/evidence")

    def recommendation(self, finding_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/findings/{finding_id}/recommendation")

    def retest(self, finding_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/findings/{finding_id}/retest")


class Evidence(_Resource):
    def list(self, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", "/api/v1/evidence", params=params))

    def get(self, evidence_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/evidence/{evidence_id}")


class RedTeam(_Resource):
    def create(
        self,
        *,
        system_id: str,
        corpus: Sequence[dict[str, Any]] | None = None,
        corpus_name: str = "custom",
        max_probes: int = 100,
        max_depth: int = 0,
        name: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/redteam/runs",
            json={
                "system_id": system_id,
                "corpus": corpus or [],
                "corpus_name": corpus_name,
                "max_probes": max_probes,
                "max_depth": max_depth,
                "name": name,
            },
        )

    def get(self, run_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/redteam/runs/{run_id}")

    def probes(self, run_id: str) -> JsonList:
        return self._t.request("GET", f"/api/v1/redteam/runs/{run_id}/probes")


class Agents(_Resource):
    def traces(self, system_id: str, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", f"/api/v1/agents/{system_id}/traces", params=params))

    def trace(self, trace_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/traces/{trace_id}")

    def ingest_trace(
        self, *, system_id: str, events: Sequence[dict[str, Any]], name: str | None = None, trace_id: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/traces",
            json={"system_id": system_id, "events": events, "name": name, "trace_id": trace_id},
        )


class Monitoring(_Resource):
    def overview(self, *, days: int = 7, system_id: str | None = None) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/monitoring/overview", params={"days": days, "system_id": system_id})

    def ingest(
        self,
        *,
        system_id: str,
        input: str,
        output: str,
        model_version: str | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/monitoring/events",
            json={
                "system_id": system_id,
                "input": input,
                "output": output,
                "model_version": model_version,
                "latency_ms": latency_ms,
            },
        )


class Providers(_Resource):
    def list(self) -> JsonList:
        return self._t.request("GET", "/api/v1/providers")

    def create(self, **fields: Any) -> dict[str, Any]:
        return self._t.request("POST", "/api/v1/providers", json=fields)

    def test(self, provider_id: str) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/providers/{provider_id}/test")


class Aegis:
    """Synchronous Aegis API client."""

    def __init__(
        self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0, max_retries: int = 2
    ) -> None:
        self._t = _Transport(api_key, base_url, timeout, max_retries)
        self.systems = Systems(self._t)
        self.audits = Audits(self._t)
        self.policies = Policies(self._t)
        self.findings = Findings(self._t)
        self.evidence = Evidence(self._t)
        self.redteam = RedTeam(self._t)
        self.agents = Agents(self._t)
        self.monitoring = Monitoring(self._t)
        self.providers = Providers(self._t)

    def overview(self) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/overview")

    def search(self, query: str) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/search", params={"q": query})

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> Aegis:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncAegis:
    """Async client is planned; use ``Aegis`` (synchronous) for now."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("AsyncAegis is not yet available; use the synchronous Aegis client.")
