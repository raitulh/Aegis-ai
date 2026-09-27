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
"""

from aegis_ai.client import Aegis, AsyncAegis
from aegis_ai.errors import (
    AegisAPIError,
    AegisConnectionError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from aegis_ai.models import Page

__version__ = "1.0.0"
__all__ = [
    "Aegis",
    "AegisAPIError",
    "AegisConnectionError",
    "AsyncAegis",
    "AuthenticationError",
    "ConflictError",
    "NotFoundError",
    "Page",
    "RateLimitError",
    "ValidationError",
]
