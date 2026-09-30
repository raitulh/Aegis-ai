"""Outbox → webhook consumer: exactly-once enqueueing, offsets, settle window, signed delivery."""

from __future__ import annotations

import json
import threading
import uuid

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

PUBLIC_URL = "https://hooks.aegis-ci.test/hooks/consumer"
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


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    import httpx

    from aegis_api.services import webhook_service

    sent: list[dict] = []

    def fake_post(url, *, content, headers, timeout, follow_redirects):  # type: ignore[no-untyped-def]
        sent.append({"url": url, "body": content, "headers": dict(headers)})
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(webhook_service.httpx, "post", fake_post)
    return sent


def _webhook(lab, events: list[str]) -> tuple[str, str]:
    r = lab.post("/api/v1/webhooks", json={"url": PUBLIC_URL, "events": events})
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["secret"]


def _emit(lab, types: list[str]):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.events import emit

    with lab.db() as db:
        events = [
            emit(
                db,
                organization_id=lab.org_id,
                type=t,
                payload={"n": i},
                project_id=lab.project_id,
                workspace_id=lab.workspace_id,
                subject_type="mission",
                subject_id=uuid.uuid4(),
            )
            for i, t in enumerate(types)
        ]
        return [(e.id, str(e.event_uuid)) for e in events]


def _deliveries(lab):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from aegis_api.models import WebhookDelivery

    with lab.db() as db:
        return list(db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)).all())


def _offset(lab, name: str = "webhooks") -> int:
    from aegis_api.lab.models import EventConsumerOffset

    with lab.db() as db:
        row = db.get(EventConsumerOffset, (name, lab.org_id))
        return row.last_event_id if row else 0


def test_consumer_enqueues_exactly_once_under_concurrency_and_reruns(lab):
    from aegis_api.lab.events.consumer import EventConsumer

    _webhook(lab, ["mission.completed", "approval.required"])
    events = _emit(lab, ["MISSION_COMPLETED", "AGENT_STEP", "APPROVAL_REQUESTED", "MISSION_FAILED"])
    consumer = EventConsumer(settle_seconds=0, deliver=False)

    barrier = threading.Barrier(2)
    results = []

    def run() -> None:
        barrier.wait()
        results.append(consumer.process_organization(lab.org_id))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    consumer.process_organization(lab.org_id)  # re-run
    consumer.process_organization(lab.org_id)

    deliveries = _deliveries(lab)
    assert sorted(d.event_type for d in deliveries) == ["approval.required", "mission.completed"]
    by_type = {d.event_type: d for d in deliveries}
    assert by_type["mission.completed"].event_id == events[0][1]  # event_id = event_uuid
    assert by_type["approval.required"].event_id == events[2][1]
    payload = by_type["mission.completed"].payload
    assert payload["id"] == events[0][1] and payload["type"] == "mission.completed"
    assert payload["data"]["event_id"] == events[0][0] and payload["data"]["n"] == 0
    assert _offset(lab) >= events[-1][0]
    assert sum(r.enqueued for r in results) == 2


def test_consumer_skips_duplicates_when_offset_is_behind(lab):
    """Crash between enqueue and offset commit (simulated by resetting the offset) never duplicates."""
    from sqlalchemy import update

    from aegis_api.lab.events.consumer import EventConsumer
    from aegis_api.lab.models import EventConsumerOffset

    _webhook(lab, [])
    _emit(lab, ["MISSION_STARTED", "BUDGET_EXCEEDED"])
    consumer = EventConsumer(settle_seconds=0, deliver=False)
    first = consumer.process_organization(lab.org_id)
    assert first.enqueued == 2
    with lab.db() as db:
        db.execute(update(EventConsumerOffset).values(last_event_id=0))
    again = consumer.process_organization(lab.org_id)
    assert again.enqueued == 0 and again.duplicates == 2
    assert len(_deliveries(lab)) == 2


def test_settle_window_keeps_offset_behind_fresh_events(lab):
    from aegis_api.lab.events.consumer import EventConsumer

    _webhook(lab, ["strategy.promoted"])
    _emit(lab, ["STRATEGY_PROMOTED"])
    consumer = EventConsumer(settle_seconds=60, deliver=False)
    r1 = consumer.process_organization(lab.org_id)
    r2 = consumer.process_organization(lab.org_id)
    assert r1.enqueued == 1 and r2.duplicates == 1 and r2.enqueued == 0
    assert _offset(lab) == 0  # fresh events stay re-readable until they settle


def test_webhook_created_after_event_is_not_backfilled(lab):
    from aegis_api.lab.events.consumer import EventConsumer

    _emit(lab, ["MISSION_COMPLETED"])
    _webhook(lab, ["mission.completed"])
    result = EventConsumer(settle_seconds=0, deliver=False).process_organization(lab.org_id)
    assert result.enqueued == 0
    assert _deliveries(lab) == []


def test_consumer_advisory_lock_skips_busy_organization(lab):
    from aegis_api.lab.core.locks import advisory_xact_lock
    from aegis_api.lab.events.consumer import EventConsumer

    _webhook(lab, [])
    _emit(lab, ["MISSION_COMPLETED"])
    with lab.db() as db:
        advisory_xact_lock(db, f"event-consumer:webhooks:{lab.org_id}")
        busy = EventConsumer(settle_seconds=0, deliver=False).process_organization(lab.org_id)
    assert busy.locked is False and busy.enqueued == 0
    assert EventConsumer(settle_seconds=0, deliver=False).process_organization(lab.org_id).enqueued == 1


def test_run_once_delivers_signed_payloads(lab, other_lab, captured):
    from aegis_api.lab.events.consumer import EventConsumer
    from aegis_api.security.webhook_signing import verify_signature

    hook_id, secret = _webhook(lab, ["discovery.created"])
    events = _emit(lab, ["DISCOVERY_CREATED"])
    consumer = EventConsumer(settle_seconds=0)
    assert lab.org_id in consumer.organization_ids()
    assert other_lab.org_id not in consumer.organization_ids()  # no webhooks, nothing pending

    result = consumer.run_once()
    mine = next(r for r in result.organizations if r.organization_id == lab.org_id)
    assert mine.enqueued == 1 and mine.delivered == 1
    ours = [c for c in captured if c["url"] == PUBLIC_URL and json.loads(c["body"])["id"] == events[0][1]]
    assert len(ours) == 1
    request = ours[0]
    assert verify_signature(secret, request["body"], request["headers"]["aegis-signature"])
    assert request["headers"]["aegis-event"] == "discovery.created"
    body = json.loads(request["body"])
    assert body["type"] == "discovery.created" and body["data"]["event_type"] == "DISCOVERY_CREATED"

    consumer.run_once()  # nothing new: no second delivery of the same event
    assert len([c for c in captured if json.loads(c["body"])["id"] == events[0][1]]) == 1
    delivery = next(d for d in _deliveries(lab) if d.event_id == events[0][1])
    assert delivery.status == "succeeded" and str(delivery.webhook_id) == hook_id
