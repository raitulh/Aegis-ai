"""MCP server exposing Aegis AI assurance tools.

Authentication and workspace scoping are enforced by the API: the server calls Aegis with the API key in
``AEGIS_API_KEY`` (create one under Settings → API Keys), so every tool respects that key's role, scopes
and organization. Configure ``AEGIS_BASE_URL`` for non-local deployments.

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
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[attr-defined,no-redef]

mcp = _Server("aegis-ai")


def _client() -> Aegis:
    api_key = os.environ.get("AEGIS_API_KEY", "")
    if not api_key:
        raise RuntimeError("AEGIS_API_KEY is not set. Create an API key under Settings → API Keys.")
    return Aegis(api_key=api_key, base_url=os.environ.get("AEGIS_BASE_URL", "http://localhost:8000"))


def _run(call) -> str:
    try:
        with _client() as client:
            return json.dumps(call(client), default=str, indent=2)
    except AegisError as exc:
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
