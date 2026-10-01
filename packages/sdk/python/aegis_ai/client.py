"""HTTP client and resource namespaces."""

from __future__ import annotations

import time
import uuid
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
USER_AGENT = "aegis-ai-python/1.1.0"
RETRY_STATUSES = {500, 502, 503, 504}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class _Transport:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required (create one under API & SDK → API keys)")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff = 0.75
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        files: Any = None,
    ) -> Any:
        """Send a request with retries. POSTs carry an ``Idempotency-Key`` (generated once and reused for every
        retry), so retrying after a timeout can never create a duplicate audit, trigger or event batch."""
        headers: dict[str, str] = {}
        if method.upper() == "POST":
            headers["Idempotency-Key"] = idempotency_key or uuid.uuid4().hex
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method, path, params=_clean(params), json=json, headers=headers, files=files
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise AegisConnectionError(f"Could not reach Aegis API at {self.base_url}: {exc}") from exc
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(self.backoff * (attempt + 1))
                continue
            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(min(int(response.headers.get("Retry-After", 1)), 30))
                continue
            if response.status_code >= 400:
                body = _safe_json(response)
                raise_for_response(response.status_code, body, retry_after=int(response.headers.get("Retry-After", 60)))
            if response.headers.get("content-type", "").startswith(("application/zip", "application/pdf")):
                return response.content
            return _safe_json(response) if response.content else None
        raise AegisConnectionError("Request failed after retries") from last_exc

    def close(self) -> None:
        self._client.close()


def _drop_none(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


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

    def verify(self, audit_id: str) -> dict[str, Any]:
        """Recompute an audit's evidence hash chain server-side (status VERIFIED / TAMPERED / EMPTY)."""
        return self._t.request("GET", f"/api/v1/audits/{audit_id}/evidence/verify")

    def export(self, audit_id: str) -> bytes:
        """Download the signed, self-verifying evidence package (zip). It contains ``verify.py``."""
        return self._t.request("POST", f"/api/v1/audits/{audit_id}/evidence/export")

    def verify_package(self, package: bytes, public_key: str | None = None) -> dict[str, Any]:
        """Verify a package with the server's copy of the verifier (the package's own verify.py works offline)."""
        return self._t.request(
            "POST",
            "/api/v1/evidence/verify-package",
            params={"public_key": public_key},
            files={"file": ("package.zip", package, "application/zip")},
        )

    def signing_key(self) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/evidence/signing-key")


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


class Runtime(_Resource):
    """Runtime Agent Guard: send agent telemetry and ask for decisions before acting."""

    def events(self, events: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Ingest up to 500 events (schema ``aegis.runtime.v1``). Idempotent per ``event_id``."""
        return self._t.request("POST", "/api/v1/runtime/events", json={"events": list(events)})

    def check(
        self,
        *,
        system_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        tool: str | None = None,
        agent: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        event_id: str | None = None,
        **envelope: Any,
    ) -> RuntimeDecision:
        """Synchronous decision for an action the agent is about to take (honoured in enforce mode)."""
        body = {
            "system_id": system_id,
            "event_type": event_type,
            "payload": payload or {},
            "tool": tool,
            "agent": agent,
            "trace_id": trace_id,
            "session_id": session_id,
            "event_id": event_id or uuid.uuid4().hex,
            **envelope,
        }
        return RuntimeDecision.from_api(self._t.request("POST", "/api/v1/runtime/check", json=_drop_none(body)))

    def approval(self, approval_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/runtime/approvals/{approval_id}")

    def wait_for_approval(self, approval_id: str, *, timeout: float = 600.0, poll_interval: float = 2.0) -> bool:
        """Block until a person approves (True) or denies / lets it expire (False)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.approval(approval_id)["status"]
            if status != "pending":
                return status == "approved"
            time.sleep(poll_interval)
        return False

    def overview(self, *, hours: int = 24, system_id: str | None = None) -> dict[str, Any]:
        return self._t.request("GET", "/api/v1/runtime/overview", params={"hours": hours, "system_id": system_id})

    def set_mode(self, system_id: str, mode: str) -> dict[str, Any]:
        return self._t.request("PUT", f"/api/v1/systems/{system_id}/runtime-mode", json={"mode": mode})

    def trace(
        self, system_id: str, *, agent: str | None = None, session_id: str | None = None, enforce: bool = True
    ) -> RuntimeTrace:
        """Context manager that groups events under one trace. See :class:`RuntimeTrace`."""
        return RuntimeTrace(self, system_id, agent=agent, session_id=session_id, enforce=enforce)


class RuntimeDecision:
    """A guard decision. ``allowed`` is what the agent should honour (``effective_decision`` in enforce mode)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.raw = data
        self.decision: str = data.get("decision", "allow")
        self.effective_decision: str = data.get("effective_decision", "allow")
        self.allowed: bool = bool(data.get("allowed", True))
        self.reason: str | None = data.get("reason")
        self.mode: str = data.get("mode", "observe")
        self.approval_id: str | None = data.get("approval_id")
        self.matches: list[dict[str, Any]] = data.get("matches", [])

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RuntimeDecision:
        return cls(data)

    @property
    def requires_approval(self) -> bool:
        return self.effective_decision == "require_approval"

    def __repr__(self) -> str:
        return f"RuntimeDecision(decision={self.decision!r}, allowed={self.allowed}, mode={self.mode!r})"


class RuntimeTrace:
    """Groups an agent run into one trace.

    With ``enforce=True`` every :meth:`check` is a synchronous decision; plain :meth:`event` calls are batched
    and flushed on exit (or every 100 events)::

        with client.runtime.trace(system_id, agent="support-bot") as trace:
            decision = trace.check("tool.call", tool="send_email", payload={"to": "a@b.example"})
            if decision.allowed:
                send_email(...)
            trace.event("model.response", payload={"output": text})
    """

    def __init__(
        self, runtime: Runtime, system_id: str, *, agent: str | None, session_id: str | None, enforce: bool
    ) -> None:
        self._runtime = runtime
        self.system_id = system_id
        self.agent = agent
        self.session_id = session_id or uuid.uuid4().hex
        self.trace_id = uuid.uuid4().hex
        self.enforce = enforce
        self._buffer: list[dict[str, Any]] = []

    def _envelope(self, event_type: str, payload: dict[str, Any] | None, tool: str | None) -> dict[str, Any]:
        return _drop_none(
            {
                "event_id": uuid.uuid4().hex,
                "event_type": event_type,
                "system_id": self.system_id,
                "agent": self.agent,
                "session_id": self.session_id,
                "trace_id": self.trace_id,
                "span_id": uuid.uuid4().hex[:16],
                "tool": tool,
                "payload": payload or {},
            }
        )

    def check(
        self, event_type: str, *, tool: str | None = None, payload: dict[str, Any] | None = None
    ) -> RuntimeDecision:
        envelope = self._envelope(event_type, payload, tool)
        if not self.enforce:
            self._buffer.append(envelope)
            return RuntimeDecision({"decision": "allow", "effective_decision": "allow", "allowed": True})
        return RuntimeDecision.from_api(self._runtime._t.request("POST", "/api/v1/runtime/check", json=envelope))

    def event(self, event_type: str, *, tool: str | None = None, payload: dict[str, Any] | None = None) -> None:
        self._buffer.append(self._envelope(event_type, payload, tool))
        if len(self._buffer) >= 100:
            self.flush()

    def flush(self) -> None:
        while self._buffer:
            batch, self._buffer = self._buffer[:500], self._buffer[500:]
            self._runtime.events(batch)

    def __enter__(self) -> RuntimeTrace:
        self.event("agent.start")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.event("agent.stop", payload={"status": "error" if exc_type else "ok"})
        self.flush()


class PolicyStudio(_Resource):
    """Runtime policies (YAML rules): validate, simulate against recorded telemetry, publish, roll back."""

    def check(self, **kwargs: Any) -> RuntimeDecision:
        """Shortcut for :meth:`Runtime.check` — evaluate an action against the published policies."""
        return Runtime(self._t).check(**kwargs)

    def list(self, **params: Any) -> Page:
        return Page.from_response(self._t.request("GET", "/api/v1/runtime-policies", params=params))

    def templates(self) -> JsonList:
        return self._t.request("GET", "/api/v1/runtime-policies/templates")

    def create(
        self, *, source_yaml: str | None = None, template_key: str | None = None, name: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/runtime-policies",
            json=_drop_none({"source_yaml": source_yaml, "template_key": template_key, "name": name}),
        )

    def validate(self, source_yaml: str) -> dict[str, Any]:
        return self._t.request("POST", "/api/v1/runtime-policies/validate", json={"source_yaml": source_yaml})

    def new_version(self, policy_id: str, source_yaml: str, change_note: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/api/v1/runtime-policies/{policy_id}/versions",
            json={"source_yaml": source_yaml, "change_note": change_note},
        )

    def simulate(
        self,
        *,
        source_yaml: str | None = None,
        policy_id: str | None = None,
        version: int | None = None,
        days: int = 7,
        system_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/api/v1/runtime-policies/simulate",
            json=_drop_none(
                {
                    "source_yaml": source_yaml,
                    "policy_id": policy_id,
                    "version": version,
                    "days": days,
                    "system_ids": list(system_ids),
                }
            ),
        )

    def publish(self, policy_id: str, version: int) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/runtime-policies/{policy_id}/publish", json={"version": version})

    def rollback(self, policy_id: str, version: int) -> dict[str, Any]:
        return self._t.request("POST", f"/api/v1/runtime-policies/{policy_id}/rollback", json={"version": version})

    def assign(
        self,
        policy_id: str,
        *,
        scope_type: str = "organization",
        system_id: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/api/v1/runtime-policies/{policy_id}/assignments",
            json=_drop_none({"scope_type": scope_type, "system_id": system_id, "environment": environment}),
        )


class Assurance(_Resource):
    """Continuous assurance: report changes from CI/CD and manage schedules."""

    def trigger(
        self, *, system_id: str, event_type: str, ref: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Report a change (deployment, pull_request, model_change, prompt_change, tool_change, …). Starts a
        risk-selected audit; idempotent per (system, event_type, ref)."""
        return self._t.request(
            "POST",
            "/api/v1/assurance/triggers",
            json={"system_id": system_id, "event_type": event_type, "ref": ref, "metadata": metadata or {}},
            idempotency_key=f"trigger:{system_id}:{event_type}:{ref}"[:128],
        )

    def regression(self, audit_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/api/v1/audits/{audit_id}/regression")

    def schedules(self, system_id: str | None = None) -> JsonList:
        return self._t.request("GET", "/api/v1/assurance/schedules", params={"system_id": system_id})


class Aegis:
    """Synchronous Aegis API client."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._t = _Transport(api_key, base_url, timeout, max_retries, transport)
        self.systems = Systems(self._t)
        self.audits = Audits(self._t)
        self.policies = Policies(self._t)
        self.findings = Findings(self._t)
        self.evidence = Evidence(self._t)
        self.redteam = RedTeam(self._t)
        self.agents = Agents(self._t)
        self.monitoring = Monitoring(self._t)
        self.providers = Providers(self._t)
        self.runtime = Runtime(self._t)
        self.policy = PolicyStudio(self._t)
        self.assurance = Assurance(self._t)

    def audit(
        self,
        system_id: str,
        categories: Sequence[str],
        *,
        intensity: str = "standard",
        wait: bool = True,
        timeout: float = 600.0,
        **options: Any,
    ) -> dict[str, Any]:
        """Run an audit (and by default wait for the result)."""
        audit = self.audits.create(system_id=system_id, categories=categories, intensity=intensity, **options)
        return self.audits.wait(audit["id"], timeout=timeout) if wait else audit

    def evaluate(self, system_id: str, categories: Sequence[str], **options: Any) -> dict[str, Any]:
        """Evaluate a system against the given categories: a quick audit, waited for. Alias of :meth:`audit`."""
        options.setdefault("intensity", "quick")
        return self.audit(system_id, categories, **options)

    def usage(self) -> dict[str, Any]:
        """Plan, quotas and usage for the current billing period."""
        return self._t.request("GET", "/api/v1/usage")

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
    """Asyncio client for the latency-sensitive paths: runtime decisions/ingestion, audits and evidence.

    Same semantics as :class:`Aegis` (idempotent POST retries, typed errors)::

        async with AsyncAegis(api_key=...) as client:
            decision = await client.runtime_check(system_id=sid, event_type="tool.call", tool="send_email")
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required (create one under API & SDK → API keys)")
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        import asyncio

        headers = {"Idempotency-Key": uuid.uuid4().hex} if method.upper() == "POST" else {}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, path, params=_clean(params), json=json, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise AegisConnectionError(f"Could not reach Aegis API: {exc}") from exc
            if response.status_code in RETRY_STATUSES | {429} and attempt < self.max_retries:
                await asyncio.sleep(
                    min(int(response.headers.get("Retry-After", 1)), 5)
                    if response.status_code == 429
                    else 0.75 * (attempt + 1)
                )
                continue
            if response.status_code >= 400:
                raise_for_response(
                    response.status_code, _safe_json(response), retry_after=int(response.headers.get("Retry-After", 60))
                )
            return _safe_json(response) if response.content else None
        raise AegisConnectionError("Request failed after retries") from last_exc

    async def runtime_check(self, *, system_id: str, event_type: str, **fields: Any) -> RuntimeDecision:
        body = _drop_none({"system_id": system_id, "event_type": event_type, "event_id": uuid.uuid4().hex, **fields})
        return RuntimeDecision.from_api(await self.request("POST", "/api/v1/runtime/check", json=body))

    async def runtime_events(self, events: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return await self.request("POST", "/api/v1/runtime/events", json={"events": list(events)})

    async def create_audit(
        self, *, system_id: str, categories: Sequence[str], intensity: str = "standard"
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/v1/audits",
            json={"system_id": system_id, "categories": list(categories), "intensity": intensity},
        )

    async def get_audit(self, audit_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/v1/audits/{audit_id}")

    async def verify_evidence(self, audit_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/v1/audits/{audit_id}/evidence/verify")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncAegis:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
