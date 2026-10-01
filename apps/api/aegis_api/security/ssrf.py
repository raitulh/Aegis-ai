"""Outbound URL validation and a connect-time guarded HTTP transport (SSRF protection).

Every user-supplied URL (AI system endpoints, provider base URLs, webhooks) is validated with
``validate_outbound_url`` when it is stored *and* again when it is used. Validation alone is not enough:
an attacker-controlled DNS name can resolve to a public address during validation and to an internal
one a moment later (DNS rebinding). ``guarded_client`` therefore returns an ``httpx.Client`` whose
transport resolves the host itself, rejects any disallowed address, and connects to the vetted IP. TLS
still verifies the certificate against the original hostname (httpcore passes the origin host as SNI).

Address policy (``is_disallowed_ip``):
  * always blocked: cloud metadata addresses, link-local, multicast, unspecified, reserved;
  * blocked unless private targets are explicitly allowed: every non-globally-routable address
    (RFC 1918, loopback, CGNAT 100.64/10, ULA fc00::/7, benchmarking ranges, …);
  * IPv4 embedded in IPv6 (mapped ``::ffff:``, NAT64 ``64:ff9b::/96``, 6to4 ``2002::/16``) is
    unwrapped and checked as IPv4.

Redirects are never followed and environment proxies are ignored for guarded clients.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx

from aegis_api.config import get_settings
from aegis_api.errors import ValidationFailed

BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "kubernetes.default",
    "kubernetes.default.svc",
}
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_6TO4 = ipaddress.ip_network("2002::/16")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class OutboundBlocked(httpx.ConnectError):
    """Raised at connect time when a host resolves to a disallowed address."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _6TO4:
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def is_disallowed_ip(ip: IPAddress, allow_private: bool) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return is_disallowed_ip(embedded, allow_private)
    if ip in METADATA_IPS:
        return True
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved or ip.is_link_local:
        return True
    if allow_private:
        return False
    return not ip.is_global


def _allow_private_default(allow_private: bool | None) -> bool:
    return get_settings().effective_allow_private if allow_private is None else allow_private


def resolve_vetted(host: str, port: int, allow_private: bool) -> list[str]:
    """Resolve ``host`` and return its addresses, raising if *any* resolved address is disallowed."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if is_disallowed_ip(literal, allow_private):
            raise ValidationFailed("URL resolves to a disallowed network address")
        return [str(literal)]
    lowered = host.lower().rstrip(".")
    if lowered in BLOCKED_HOSTNAMES or lowered.endswith(".internal"):
        raise ValidationFailed("URL host is not allowed")
    if not allow_private and lowered in {"localhost", "localhost.localdomain"}:
        raise ValidationFailed("URL resolves to a disallowed network address")
    try:
        infos = socket.getaddrinfo(lowered, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailed(f"Host could not be resolved: {host}") from exc
    addresses: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if is_disallowed_ip(ip, allow_private):
            raise ValidationFailed("URL resolves to a disallowed network address")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise ValidationFailed(f"Host could not be resolved: {host}")
    return addresses


def validate_outbound_url(
    url: str,
    *,
    allow_private: bool | None = None,
    allowed_schemes: frozenset[str] = frozenset({"https", "http"}),
    resolve: bool = True,
) -> str:
    """Validate and normalise a URL. Raises ``ValidationFailed`` if it may target internal resources."""
    allow_private = _allow_private_default(allow_private)
    if not url or len(url) > 1000:
        raise ValidationFailed("URL is empty or too long")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise ValidationFailed(f"URL scheme must be one of: {', '.join(sorted(allowed_schemes))}")
    if parts.username or parts.password:
        raise ValidationFailed("Credentials must not be embedded in URLs; use a stored secret instead")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationFailed("URL must include a host")
    if host in BLOCKED_HOSTNAMES or host.endswith(".internal"):
        raise ValidationFailed("URL host is not allowed")
    if scheme == "http" and not allow_private and get_settings().is_production:
        raise ValidationFailed("Plain HTTP is only allowed for private development targets")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationFailed("URL port is invalid") from exc

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if is_disallowed_ip(literal, allow_private):
            raise ValidationFailed("URL resolves to a disallowed network address")
    elif resolve:
        resolve_vetted(host, port, allow_private)

    return urlunsplit((scheme, parts.netloc, parts.path or "", parts.query, ""))


class _GuardedBackend(httpcore.NetworkBackend):
    """Network backend that vets every resolved address at connect time and connects to the vetted IP."""

    def __init__(self, allow_private: bool) -> None:
        self._inner = httpcore.SyncBackend()
        self._allow_private = allow_private

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = resolve_vetted(host, port, self._allow_private)
        except ValidationFailed as exc:
            raise httpcore.ConnectError(f"outbound connection blocked: {exc.message}") from exc
        last: Exception | None = None
        for address in addresses:
            try:
                return self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise httpcore.ConnectError(f"could not connect to {host}:{port}") from last

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.NetworkStream:  # pragma: no cover - never used for outbound calls
        raise httpcore.ConnectError("unix sockets are not permitted for outbound calls")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class GuardedTransport(httpx.HTTPTransport):
    def __init__(self, *, allow_private: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # httpcore.ConnectionPool hands this backend to every connection it opens.
        self._pool._network_backend = _GuardedBackend(allow_private)


def guarded_client(
    *, timeout: float = 30.0, allow_private: bool | None = None, headers: dict[str, str] | None = None
) -> httpx.Client:
    """An ``httpx.Client`` safe for user-controlled destinations (no redirects, no env proxies, IP pinning)."""
    return httpx.Client(
        transport=GuardedTransport(allow_private=_allow_private_default(allow_private)),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    )
