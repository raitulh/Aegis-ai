"""Lab audit-log API: cursor pagination, filters, permissions and tenant isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def write_entries(lab, count: int, *, action: str = "ADMIN_ACTION", resource_type: str = "test") -> list[str]:  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.audit import audit

    ids = []
    with lab.db() as db:
        for i in range(count):
            entry = audit(db, lab.actor(), action, resource_type, f"res-{i}", after={"i": i})
            db.flush()
            ids.append(str(entry.id))
    return ids


def test_pagination_walks_every_entry_once(lab):  # type: ignore[no-untyped-def]
    written = set(write_entries(lab, 7, resource_type="paging"))
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 3, "resource_type": "paging"}
        if cursor:
            params["cursor"] = cursor
        r = lab.get("/api/v1/audit", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 7 and set(seen) == written
    created = [
        item["created_at"] for item in lab.get("/api/v1/audit", params={"resource_type": "paging"}).json()["items"]
    ]
    assert created == sorted(created, reverse=True)  # newest first


def test_filters(lab):  # type: ignore[no-untyped-def]
    write_entries(lab, 2, action="QUOTA_CHANGED", resource_type="organization_settings")
    write_entries(lab, 3, action="ADMIN_ACTION", resource_type="other")
    body = lab.get("/api/v1/audit", params={"action": "QUOTA_CHANGED"}).json()
    assert len(body["items"]) == 2 and {i["action"] for i in body["items"]} == {"QUOTA_CHANGED"}
    assert body["items"][0]["actor_type"] == "user" and body["items"][0]["after"]["i"] in (0, 1)
    body = lab.get("/api/v1/audit", params={"resource_type": "other", "resource_id": "res-1"}).json()
    assert len(body["items"]) == 1
    assert len(lab.get("/api/v1/audit", params={"actor_type": "user", "resource_type": "other"}).json()["items"]) == 3
    assert lab.get("/api/v1/audit", params={"actor_type": "agent", "resource_type": "other"}).json()["items"] == []
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert lab.get("/api/v1/audit", params={"since": future}).json()["items"] == []
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert len(lab.get("/api/v1/audit", params={"since": past, "resource_type": "other"}).json()["items"]) == 3
    assert lab.get("/api/v1/audit", params={"until": past, "resource_type": "other"}).json()["items"] == []
    assert lab.get("/api/v1/audit", params={"user_id": str(lab.user_id), "resource_type": "other"}).json()["items"]
    assert lab.get("/api/v1/audit", params={"actor_type": "martian"}).status_code == 422
    assert lab.get("/api/v1/audit", params={"cursor": "not-a-cursor"}).status_code == 422
    assert lab.get("/api/v1/audit", params={"since": future, "until": past}).status_code == 422


def test_governance_actions_appear_in_the_log(lab):  # type: ignore[no-untyped-def]
    r = lab.post("/api/v1/governance/policies", json={"key": f"k-{uuid.uuid4().hex[:6]}", "name": "P", "rules": []})
    assert r.status_code == 201
    body = lab.get("/api/v1/audit", params={"action": "POLICY_CHANGED"}).json()
    assert body["items"][0]["resource_id"] == r.json()["id"]
    assert body["items"][0]["resource_type"] == "governance_policy"


def test_audit_permission_and_tenancy(lab, other_lab, client):  # type: ignore[no-untyped-def]
    write_entries(lab, 2, resource_type="secret-ish")
    key = lab.post("/api/v1/api-keys", json={"name": "viewer", "role": "viewer", "scopes": ["read"]}).json()[
        "plaintext"
    ]
    r = client.get("/api/v1/audit", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403  # viewers lack audit:read
    key = lab.post("/api/v1/api-keys", json={"name": "auditor", "role": "auditor", "scopes": ["read"]}).json()[
        "plaintext"
    ]
    assert client.get("/api/v1/audit", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert other_lab.get("/api/v1/audit", params={"resource_type": "secret-ish"}).json()["items"] == []
