"""SSRF protection: address policy, URL validation and the connect-time guarded transport."""

from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest

from aegis_api.errors import ValidationFailed
from aegis_api.security import ssrf


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "100.64.0.1",  # CGNAT — not covered by ipaddress.is_private
        "::1",
        "fd12:3456::1",  # IPv6 ULA
        "::ffff:10.0.0.1",  # IPv4-mapped
        "64:ff9b::a00:1",  # NAT64 → 10.0.0.1
        "2002:a00:1::",  # 6to4 → 10.0.0.1
        "198.18.0.1",  # benchmarking
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind
    ],
)
def test_private_and_special_addresses_blocked_when_private_disallowed(address: str) -> None:
    assert ssrf.is_disallowed_ip(ipaddress.ip_address(address), allow_private=False)


@pytest.mark.parametrize("address", ["169.254.169.254", "169.254.170.2", "fe80::1", "fd00:ec2::254", "224.0.0.1"])
def test_metadata_and_link_local_always_blocked(address: str) -> None:
    assert ssrf.is_disallowed_ip(ipaddress.ip_address(address), allow_private=True)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_allowed(address: str) -> None:
    assert not ssrf.is_disallowed_ip(ipaddress.ip_address(address), allow_private=False)


def test_private_allowed_only_when_opted_in() -> None:
    assert not ssrf.is_disallowed_ip(ipaddress.ip_address("10.0.0.5"), allow_private=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "http://foo.internal/",
        "ftp://example.com/",
        "https://user:pass@example.com/",
        "http://[::ffff:127.0.0.1]/",
        "http://100.64.1.1/",
    ],
)
def test_validate_rejects_dangerous_urls(url: str) -> None:
    with pytest.raises(ValidationFailed):
        ssrf.validate_outbound_url(url, allow_private=False)


def test_validate_rejects_hostname_resolving_privately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
    )
    with pytest.raises(ValidationFailed):
        ssrf.validate_outbound_url("https://looks-public.example/", allow_private=False)


def test_validate_rejects_if_any_resolved_address_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValidationFailed):
        ssrf.validate_outbound_url("https://multi.example/", allow_private=False)


def test_guarded_client_blocks_dns_rebinding_at_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validation sees a public address; by connect time the name resolves to loopback. The guarded
    transport re-resolves and refuses — it never connects to the rebinding target."""
    answers = iter(["93.184.216.34", "127.0.0.1"])

    def fake_getaddrinfo(host: str, port: int, *a: object, **k: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    url = ssrf.validate_outbound_url("http://rebind.example/hook", allow_private=False)
    client = ssrf.guarded_client(timeout=2, allow_private=False)
    with pytest.raises(httpx.ConnectError) as excinfo:
        client.post(url, json={})
    assert "blocked" in str(excinfo.value)


def test_guarded_client_does_not_follow_redirects_or_use_env_proxies() -> None:
    client = ssrf.guarded_client(timeout=2, allow_private=True)
    assert client.follow_redirects is False
    assert client._trust_env is False  # environment proxies would bypass connect-time pinning
