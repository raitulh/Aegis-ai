"""Webhook management API: CRUD, SSRF protection, one-time secrets, rotation, test/redeliver, isolation."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

PUBLIC_URL = "https://hooks.aegis-ci.test/hooks/aegis"
FAKE_PUBLIC_IP = "93.184.215.14"


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """Resolve the reserved ``.test`` TLD to a public address for SSRF validation.

    Test webhooks point at ``*.test`` hosts, which never resolve outside tests, so a consumer run against
    the test database can never send real traffic; outbound HTTP itself is always faked below.
    """
    import socket

    real_getaddrinfo = socket.getaddrinfo

    def resolver(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(host, str) and host.endswith(".test"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (FAKE_PUBLIC_IP, port or 443))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)


class _Captured:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[dict] = []

    def __call__(self, url, *, content, headers, timeout, follow_redirects):  # type: ignore[no-untyped-def]
        import httpx

        self.requests.append({"url": url, "body": content, "headers": dict(headers), "redirects": follow_redirects})
        return httpx.Response(self.status, text="ok", request=httpx.Request("POST", url))


@pytest.fixture
def fake_http(monkeypatch) -> _Captured:
    from aegis_api.services import webhook_service

    captured = _Captured()
    monkeypatch.setattr(webhook_service.httpx, "post", captured)
    return captured


def _create(lab, **overrides):  # type: ignore[no-untyped-def]
    body = {"url": PUBLIC_URL, "description": "CI notifications", "events": ["mission.completed"], **overrides}
    return lab.post("/api/v1/webhooks", json=body)


def test_webhook_crud_and_secret_shown_once(lab):
    r = _create(lab, events=["mission.completed", "approval.required", "mission.completed"])
    assert r.status_code == 201, r.text
    created = r.json()
    secret = created["secret"]
    assert secret.startswith("whsec_") and len(secret) > 40
    assert created["events"] == ["mission.completed", "approval.required"]
    assert created["secret_last4"] == secret[-4:]
    hook_id = created["id"]

    got = lab.get(f"/api/v1/webhooks/{hook_id}")
    assert got.status_code == 200
    assert "secret" not in got.json() and got.json()["secret_last4"] == secret[-4:]
    listed = lab.get("/api/v1/webhooks").json()
    assert [h["id"] for h in listed["items"]] == [hook_id]
    assert "secret" not in listed["items"][0] and listed["items"][0]["secret_last4"] == secret[-4:]
    assert secret not in json.dumps(listed)

    patched = lab.patch(f"/api/v1/webhooks/{hook_id}", json={"events": ["budget.exceeded"], "active": False})
    assert patched.status_code == 200
    assert patched.json()["events"] == ["budget.exceeded"] and patched.json()["active"] is False

    rotated = lab.post(f"/api/v1/webhooks/{hook_id}/rotate-secret")
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != secret and rotated.json()["secret_last4"] == new_secret[-4:]

    types = lab.get("/api/v1/webhooks/event-types").json()["events"]
    assert {"mission.completed", "approval.required", "finding.created"} <= set(types)

    assert lab.delete(f"/api/v1/webhooks/{hook_id}").status_code == 204
    assert lab.get(f"/api/v1/webhooks/{hook_id}").status_code == 404
    with lab.db() as db:
        from sqlalchemy import select

        from aegis_api.models import AuditLog, Secret

        assert db.scalar(select(Secret).where(Secret.name == f"webhook-signing:{hook_id}")) is None
        actions = db.scalars(
            select(AuditLog.after).where(AuditLog.action == "WEBHOOK_CHANGED", AuditLog.resource_id == hook_id)
        ).all()
        ops = sorted(a["op"] for a in actions)
        assert ops == ["create", "delete", "rotate_secret", "update"]
        assert all(secret not in json.dumps(a) and new_secret not in json.dumps(a) for a in actions)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8080/hook",
        "http://127.0.0.1/hook",
        "http://10.0.0.5/hook",
        "https://metadata.google.internal/x",
        "ftp://hooks.aegis-ci.test/x",
        "https://user:pass@hooks.aegis-ci.test/x",
    ],
)
def test_webhook_ssrf_targets_rejected(lab, url):
    r = _create(lab, url=url)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"


def test_webhook_validation(lab):
    r = _create(lab, events=["mission.completed", "not.a.real.event"])
    assert r.status_code == 422
    assert r.json()["error"]["details"]["unknown"] == ["not.a.real.event"]
    hook_id = _create(lab).json()["id"]
    assert lab.patch(f"/api/v1/webhooks/{hook_id}", json={"url": "http://169.254.169.254/"}).status_code == 422
    assert lab.patch(f"/api/v1/webhooks/{hook_id}", json={"events": ["nope"]}).status_code == 422
    assert lab.get("/api/v1/webhooks/not-a-uuid").status_code == 422


def test_webhooks_are_tenant_isolated(lab, other_lab):
    hook_id = _create(lab).json()["id"]
    assert other_lab.get(f"/api/v1/webhooks/{hook_id}").status_code == 404
    assert other_lab.patch(f"/api/v1/webhooks/{hook_id}", json={"active": False}).status_code == 404
    assert other_lab.delete(f"/api/v1/webhooks/{hook_id}").status_code == 404
    assert other_lab.post(f"/api/v1/webhooks/{hook_id}/rotate-secret").status_code == 404
    assert other_lab.post(f"/api/v1/webhooks/{hook_id}/test").status_code == 404
    assert other_lab.get(f"/api/v1/webhooks/{hook_id}/deliveries").status_code == 404
    assert other_lab.get("/api/v1/webhooks").json()["items"] == []
    delivery_id = lab.post(f"/api/v1/webhooks/{hook_id}/test").json()["id"]
    assert other_lab.post(f"/api/v1/webhook-deliveries/{delivery_id}/redeliver").status_code == 404


def test_webhook_permissions(lab):
    from aegis_api.errors import Forbidden
    from aegis_api.lab.core.actor import Actor
    from aegis_api.lab.events import webhooks
    from aegis_api.lab.events.schemas import WebhookCreate
    from aegis_api.security.rbac import permissions_for_role

    key = lab.post("/api/v1/api-keys", json={"name": "ci", "role": "admin", "scopes": ["read", "write", "run"]})
    assert key.status_code == 201, key.text
    headers = {"Authorization": f"Bearer {key.json()['plaintext']}"}
    r = lab.ws.client.post("/api/v1/webhooks", json={"url": PUBLIC_URL, "events": []}, headers=headers)
    assert r.status_code == 403
    assert lab.ws.client.get("/api/v1/webhooks", headers=headers).status_code == 403

    # Defense in depth: even a non-human actor holding webhook:manage cannot create/delete/rotate.
    machine = Actor(
        kind="api_key",
        organization_id=lab.org_id,
        permissions=permissions_for_role("owner"),
        label="api_key:test",
        api_key_id=uuid.uuid4(),
        auth_method="api_key",
    )
    with lab.db() as db, pytest.raises(Forbidden):
        webhooks.create_webhook(db, machine, WebhookCreate(url=PUBLIC_URL))
    hook_id = uuid.UUID(_create(lab).json()["id"])
    with lab.db() as db, pytest.raises(Forbidden):
        webhooks.delete_webhook(db, machine, hook_id)
    with lab.db() as db, pytest.raises(Forbidden):
        webhooks.rotate_secret(db, machine, hook_id)
    viewer = lab.actor(role="viewer")
    with pytest.raises(Forbidden):
        viewer.require("webhook:manage")


def test_test_delivery_is_signed_and_redeliverable(lab, fake_http):
    from aegis_api.lab.events.webhooks import deliver_due
    from aegis_api.security.webhook_signing import verify_signature

    created = _create(lab).json()
    hook_id, secret = created["id"], created["secret"]
    queued = lab.post(f"/api/v1/webhooks/{hook_id}/test")
    assert queued.status_code == 202
    delivery = queued.json()
    assert delivery["status"] == "pending" and delivery["event_type"] == "webhook.test"
    assert fake_http.requests == []  # never sent inside the request

    stats = deliver_due(lab.org_id)
    assert stats.succeeded == 1
    sent = fake_http.requests[0]
    assert sent["url"] == PUBLIC_URL and sent["redirects"] is False
    assert sent["headers"]["aegis-event"] == "webhook.test"
    assert sent["headers"]["aegis-delivery"] == delivery["id"]
    assert verify_signature(secret, sent["body"], sent["headers"]["aegis-signature"])
    assert not verify_signature("whsec_wrong", sent["body"], sent["headers"]["aegis-signature"])
    assert json.loads(sent["body"])["type"] == "webhook.test"

    page = lab.get(f"/api/v1/webhooks/{hook_id}/deliveries?limit=10").json()
    assert page["items"][0]["status"] == "succeeded" and page["items"][0]["attempts"] == 1
    assert page["items"][0]["response_status"] == 200

    again = lab.post(f"/api/v1/webhook-deliveries/{delivery['id']}/redeliver")
    assert again.status_code == 202
    assert again.json()["id"] != delivery["id"] and again.json()["event_id"] == delivery["event_id"]
    deliver_due(lab.org_id)
    assert len(fake_http.requests) == 2
    assert fake_http.requests[1]["body"] == sent["body"]  # same payload

    lab.patch(f"/api/v1/webhooks/{hook_id}", json={"active": False})
    assert lab.post(f"/api/v1/webhooks/{hook_id}/test").status_code == 409


def test_failed_delivery_is_retried_with_backoff(lab, monkeypatch):
    from aegis_api.lab.events.webhooks import deliver_due
    from aegis_api.services import webhook_service

    failing = _Captured(status=500)
    monkeypatch.setattr(webhook_service.httpx, "post", failing)
    hook_id = _create(lab).json()["id"]
    lab.post(f"/api/v1/webhooks/{hook_id}/test")
    stats = deliver_due(lab.org_id)
    assert stats.retrying == 1 and stats.succeeded == 0
    item = lab.get(f"/api/v1/webhooks/{hook_id}/deliveries").json()["items"][0]
    assert item["status"] == "retrying" and item["attempts"] == 1
    assert item["last_error"] == "HTTP 500" and item["next_attempt_at"]
    assert deliver_due(lab.org_id).claimed == 0  # not due yet (backoff)
