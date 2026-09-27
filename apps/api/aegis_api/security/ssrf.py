"""Outbound URL validation to prevent SSRF against internal infrastructure.

All user-supplied URLs (AI system endpoints, provider base URLs, webhooks) pass through
``validate_outbound_url`` before any request is made. Redirects are never followed for these calls.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from aegis_api.config import get_settings
from aegis_api.errors import ValidationFailed

BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "kubernetes.default",
    "kubernetes.default.svc",
}
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private: bool) -> bool:
    if ip in METADATA_IPS:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_disallowed_ip(ip.ipv4_mapped, allow_private)
    return bool(not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local))


def validate_outbound_url(
    url: str,
    *,
    allow_private: bool | None = None,
    allowed_schemes: frozenset[str] = frozenset({"https", "http"}),
    resolve: bool = True,
) -> str:
    """Validate and normalise a URL. Raises ``ValidationFailed`` if it may target internal resources."""
    if allow_private is None:
        allow_private = get_settings().allow_private_network_targets
    if not url or len(url) > 1000:
        raise ValidationFailed("URL is empty or too long")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in allowed_schemes:
        raise ValidationFailed(f"URL scheme must be one of: {', '.join(sorted(allowed_schemes))}")
    if parts.username or parts.password:
        raise ValidationFailed("Credentials must not be embedded in URLs; use a stored secret instead")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationFailed("URL must include a host")
    if host in BLOCKED_HOSTNAMES or host.endswith(".internal"):
        raise ValidationFailed("URL host is not allowed")
    if parts.scheme.lower() == "http" and not allow_private and get_settings().is_production:
        raise ValidationFailed("Plain HTTP is only allowed for private development targets")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_disallowed_ip(literal, allow_private):
            raise ValidationFailed("URL resolves to a disallowed network address")
    elif resolve:
        if not allow_private and host in {"localhost", "localhost.localdomain"}:
            raise ValidationFailed("URL resolves to a disallowed network address")
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise ValidationFailed(f"Host could not be resolved: {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if _is_disallowed_ip(ip, allow_private):
                raise ValidationFailed("URL resolves to a disallowed network address")

    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "", parts.query, ""))
