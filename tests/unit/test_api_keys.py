"""Unit tests for Python SDK ApiKeys resource."""

from __future__ import annotations

from unittest.mock import MagicMock

from aegis_ai.client import Aegis, ApiKeys


def test_api_keys_resource_calls() -> None:
    mock_transport = MagicMock()
    mock_transport.request.return_value = {"status": "ok"}

    keys = ApiKeys(mock_transport)

    # 1. list
    keys.list()
    mock_transport.request.assert_called_with("GET", "/api/v1/api-keys")

    # 2. create
    keys.create(name="ci-runner", role="admin", scopes=["read", "write"], expires_in_days=30, test=True)
    mock_transport.request.assert_called_with(
        "POST",
        "/api/v1/api-keys",
        json={
            "name": "ci-runner",
            "role": "admin",
            "scopes": ["read", "write"],
            "expires_in_days": 30,
            "test": True,
        },
    )

    # 3. get
    keys.get("key_123")
    mock_transport.request.assert_called_with("GET", "/api/v1/api-keys/key_123")

    # 4. update
    keys.update("key_123", name="new-name", scopes=["read"])
    mock_transport.request.assert_called_with(
        "PATCH",
        "/api/v1/api-keys/key_123",
        json={"name": "new-name", "scopes": ["read"]},
    )

    # 5. rotate
    keys.rotate("key_123")
    mock_transport.request.assert_called_with("POST", "/api/v1/api-keys/key_123/rotate")

    # 6. revoke
    keys.revoke("key_123")
    mock_transport.request.assert_called_with("DELETE", "/api/v1/api-keys/key_123")


def test_aegis_client_exposes_api_keys() -> None:
    client = Aegis("aegis_live_mockkey123")
    assert hasattr(client, "api_keys")
    assert isinstance(client.api_keys, ApiKeys)
