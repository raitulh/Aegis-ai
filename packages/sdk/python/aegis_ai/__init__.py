"""Official Python SDK for the Aegis AI assurance & governance API.

Example
-------
    from aegis_ai import Aegis

    client = Aegis(api_key="aeg_live_...", base_url="https://api.aegis.example")
    system = client.systems.create(name="My Agent", system_type="agent", provider_id=pid)
    audit = client.audits.create(system_id=system["id"], categories=["fairness", "privacy"])
    audit = client.audits.wait(audit["id"])
    for finding in client.findings.list(system_id=system["id"]).items:
        print(finding["number"], finding["title"], finding["risk_level"])

    # Runtime guard: ask before acting.
    decision = client.runtime.check(system_id=system["id"], event_type="tool.call", tool="send_email",
                                    payload={"to": "customer@example.com"})
    if decision.allowed:
        ...
"""

from aegis_ai.client import Aegis, AsyncAegis, RuntimeDecision, RuntimeTrace
from aegis_ai.errors import (
    AegisAPIError,
    AegisConnectionError,
    AegisError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PlanLimitError,
    RateLimitError,
    ValidationError,
)
from aegis_ai.models import Page

__version__ = "1.1.0"
__all__ = [
    "Aegis",
    "AegisAPIError",
    "AegisConnectionError",
    "AegisError",
    "AsyncAegis",
    "AuthenticationError",
    "ConflictError",
    "NotFoundError",
    "Page",
    "PermissionDeniedError",
    "PlanLimitError",
    "RateLimitError",
    "RuntimeDecision",
    "RuntimeTrace",
    "ValidationError",
]
