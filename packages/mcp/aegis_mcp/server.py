"""MCP server exposing Aegis AI assurance tools.

Authentication and workspace scoping are enforced by the API: the server calls Aegis with the API key in
``AEGIS_API_KEY`` (create one under Settings → API Keys), so every tool respects that key's role, scopes
and organization. Configure ``AEGIS_BASE_URL`` for non-local deployments.

Security model
--------------
* The server holds no privileges of its own: every tool call is an API request made with ``AEGIS_API_KEY``, so
  the key's role and scopes bound what an MCP client can do. Use a ``service_account`` key with only the scopes
  you need (``read`` for inspection; add ``runtime`` for decisions, ``run`` to start audits).
* Tool inputs are length-checked before they reach the API; errors are returned as data, never raised into the
  host application.
* The server is a client of Aegis, not a bypass: tenant isolation, RBAC and rate limits are enforced by the API.

Run:  AEGIS_API_KEY=aeg_live_... aegis-mcp
"""

from __future__ import annotations

import json
import os
from typing import Any

from aegis_ai import Aegis
from aegis_ai.errors import AegisError

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp < 2.0 fallback
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("aegis-ai")


def _client() -> Aegis:
    api_key = os.environ.get("AEGIS_API_KEY", "")
    if not api_key:
        raise RuntimeError("AEGIS_API_KEY is not set. Create an API key under Settings → API Keys.")
    return Aegis(api_key=api_key, base_url=os.environ.get("AEGIS_BASE_URL", "http://localhost:8000"))


MAX_ARG = 2000


def _bounded(value: str | None, name: str, limit: int = 200) -> str | None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return value


def _run(call) -> str:
    try:
        with _client() as client:
            return json.dumps(call(client), default=str, indent=2)
    except (AegisError, ValueError) as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def aegis_get_risk_posture() -> str:
    """Get the workspace AI trust posture: dimension scores, open findings and critical risks."""
    return _run(lambda c: c.overview())


@mcp.tool()
def aegis_list_systems() -> str:
    """List the AI systems registered in the workspace."""
    return _run(lambda c: {"systems": c.systems.list(page_size=50).items})


@mcp.tool()
def aegis_create_audit(system_id: str, categories: list[str], intensity: str = "standard") -> str:
    """Start an audit for an AI system. Categories: fairness, hallucination, privacy, safety, agent_action, policy."""
    return _run(lambda c: c.audits.create(system_id=system_id, categories=categories, intensity=intensity))


@mcp.tool()
def aegis_get_audit(audit_id: str) -> str:
    """Get an audit's status, progress and summary."""
    return _run(lambda c: c.audits.get(audit_id))


@mcp.tool()
def aegis_get_findings(system_id: str | None = None, severity: str | None = None, status: str | None = None) -> str:
    """List findings, optionally filtered by system, severity or status."""
    return _run(
        lambda c: {
            "findings": c.findings.list(system_id=system_id, severity=severity, status=status, page_size=50).items
        }
    )


@mcp.tool()
def aegis_get_evidence(finding_id: str) -> str:
    """Get the evidence artifacts backing a finding."""
    return _run(lambda c: {"evidence": c.findings.evidence(finding_id)})


@mcp.tool()
def aegis_run_test(system_id: str, categories: list[str]) -> str:
    """Run a quick audit and wait for the result (blocking, up to 5 minutes)."""

    def go(c: Aegis) -> Any:
        audit = c.audits.create(system_id=system_id, categories=categories, intensity="quick")
        return c.audits.wait(audit["id"])

    return _run(go)


@mcp.tool()
def aegis_check_policy(policy_id: str) -> str:
    """Compile a policy into executable controls and return the controls with provenance."""
    return _run(lambda c: c.policies.compile(policy_id))


@mcp.tool()
def aegis_runtime_check(
    system_id: str,
    event_type: str,
    tool: str | None = None,
    payload_json: str = "{}",
    agent: str | None = None,
) -> str:
    """Ask the Runtime Guard whether an agent action is allowed *before* performing it.

    event_type: tool.call, mcp.tool.call, network.request, file.read, file.write, database.query, ...
    payload_json: JSON object describing the action (e.g. {"url": "...", "data_classification": "confidential"}).
    Returns decision (allow | flag | require_approval | block), whether to proceed, and the reason."""

    def go(c: Aegis) -> Any:
        _bounded(system_id, "system_id", 64)
        _bounded(tool, "tool", 160)
        payload = json.loads(_bounded(payload_json, "payload_json", MAX_ARG) or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload_json must be a JSON object")
        decision = c.runtime.check(
            system_id=system_id, event_type=event_type, tool=tool, payload=payload, agent=agent, source="mcp"
        )
        return {
            "decision": decision.decision,
            "proceed": decision.allowed,
            "reason": decision.reason,
            "approval_id": decision.approval_id,
        }

    return _run(go)


@mcp.tool()
def aegis_runtime_overview(hours: int = 24) -> str:
    """Runtime activity for the last N hours: events, decisions, top agents, pending approvals."""
    return _run(lambda c: c.runtime.overview(hours=max(1, min(hours, 24 * 30))))


@mcp.tool()
def aegis_verify_evidence(audit_id: str) -> str:
    """Recompute an audit's evidence hash chain (VERIFIED / TAMPERED / EMPTY)."""
    return _run(lambda c: c.evidence.verify(_bounded(audit_id, "audit_id", 64) or ""))


@mcp.tool()
def aegis_report_change(system_id: str, event_type: str, ref: str) -> str:
    """Report a change (deployment, pull_request, model_change, prompt_change, tool_change) to start a
    risk-selected continuous-assurance audit. Idempotent per (system, event_type, ref)."""
    return _run(
        lambda c: c.assurance.trigger(
            system_id=_bounded(system_id, "system_id", 64) or "",
            event_type=_bounded(event_type, "event_type", 32) or "",
            ref=_bounded(ref, "ref", 200) or "",
        )
    )


@mcp.tool()
def aegis_simulate_policy(policy_id: str, version: int, days: int = 7) -> str:
    """Replay recorded runtime events through a runtime policy version and report its real impact."""
    return _run(lambda c: c.policy.simulate(policy_id=policy_id, version=version, days=max(1, min(days, 90))))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
