"""Event log API and SSE streams (replay, Last-Event-ID resume, live wake-ups, late commits, isolation)."""

from __future__ import annotations

import threading
import uuid

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def short_streams(monkeypatch):
    """Streams end quickly (TestClient returns only once the response body is complete)."""
    from aegis_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "sse_max_stream_seconds", 1.2)
    monkeypatch.setattr(settings, "sse_heartbeat_seconds", 0.3)
    return settings


def _mission(lab) -> uuid.UUID:
    from aegis_api.lab.models import Mission

    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="Improve benchmark efficiency",
            objective="Find a cheaper configuration with equal accuracy",
        )
        db.add(mission)
        db.flush()
        return mission.id


def _emit(lab, mission_id: uuid.UUID | None, types: list[str], project_id: uuid.UUID | None = None) -> list[int]:
    from aegis_api.lab.core.events import emit

    with lab.db() as db:
        return [
            emit(
                db,
                organization_id=lab.org_id,
                type=event_type,
                payload={"seq": i},
                mission_id=mission_id,
                project_id=project_id or lab.project_id,
                workspace_id=lab.workspace_id,
                subject_type="mission" if mission_id else None,
                subject_id=mission_id,
            ).id
            for i, event_type in enumerate(types)
        ]


def _parse(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """(control lines such as retry/comments, event frames)."""
    control: list[str] = []
    frames: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":") or line.startswith("retry:"):
                control.append(line)
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "id" in fields:
            frames.append(fields)
    return control, frames


def _stream(lab, path: str, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    with lab.ws.client.stream("GET", path, cookies=lab.ws.cookies, headers=headers or {}) as response:
        body = response.read().decode()
        return response, body


# --- SSE -----------------------------------------------------------------------------------------------
def test_mission_stream_replays_then_resumes_from_last_event_id(lab, short_streams):
    import json

    mission_id = _mission(lab)
    types = ["MISSION_CREATED", "MISSION_STARTED", "PHASE_CHANGED"]
    ids = _emit(lab, mission_id, types)
    path = f"/api/v1/missions/{mission_id}/events/stream"

    response, body = _stream(lab, path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert body.startswith("retry: 3000\n\n")
    control, frames = _parse(body)
    assert [int(f["id"]) for f in frames] == ids
    assert [f["event"] for f in frames] == types
    data = json.loads(frames[0]["data"])
    assert data["id"] == ids[0] and data["type"] == types[0]
    assert data["mission_id"] == str(mission_id) and data["payload"] == {"seq": 0}
    assert {"created_at", "subject_type", "subject_id", "project_id"} <= set(data)
    assert ": ping" in control  # heartbeat comments while idle

    _, resumed = _stream(lab, path, headers={"Last-Event-ID": str(ids[0])})
    assert [int(f["id"]) for f in _parse(resumed)[1]] == ids[1:]

    _, by_query = _stream(lab, f"{path}?last_event_id={ids[1]}")
    assert [int(f["id"]) for f in _parse(by_query)[1]] == ids[2:]

    _, filtered = _stream(lab, f"{path}?type=PHASE_CHANGED,MISSION_CREATED")
    assert [f["event"] for f in _parse(filtered)[1]] == ["MISSION_CREATED", "PHASE_CHANGED"]


def test_mission_stream_access_control(lab, other_lab, short_streams):
    mission_id = _mission(lab)
    _emit(lab, mission_id, ["MISSION_CREATED"])
    r = other_lab.get(f"/api/v1/missions/{mission_id}/events/stream")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert other_lab.get(f"/api/v1/missions/{uuid.uuid4()}/events/stream").status_code == 404
    bad = lab.get(f"/api/v1/missions/{mission_id}/events/stream", headers={"Last-Event-ID": "-5"})
    assert bad.status_code == 422
    lab.ws.client.cookies.clear()
    assert lab.ws.client.get(f"/api/v1/missions/{mission_id}/events/stream").status_code == 401


def test_live_events_are_pushed_while_streaming(lab, short_streams):
    from aegis_api.lab.observability.metrics import SSE_CONNECTIONS

    mission_id = _mission(lab)
    first = _emit(lab, mission_id, ["MISSION_STARTED"])
    emitted: list[int] = []
    timer = threading.Timer(0.4, lambda: emitted.extend(_emit(lab, mission_id, ["AGENT_STARTED", "AGENT_STEP"])))
    before = SSE_CONNECTIONS._value.get()
    timer.start()
    try:
        _, body = _stream(lab, f"/api/v1/missions/{mission_id}/events/stream")
    finally:
        timer.join()
    ids = [int(f["id"]) for f in _parse(body)[1]]
    assert ids == first + emitted
    assert SSE_CONNECTIONS._value.get() == before  # gauge decremented when the stream closed


def test_event_committed_late_with_lower_id_is_still_streamed(lab, short_streams):
    """Ids are allocated at INSERT but become visible at COMMIT; a slow transaction must not be skipped."""
    from aegis_api.db.session import session_factory
    from aegis_api.lab.core.events import emit

    mission_id = _mission(lab)
    slow = session_factory()()
    slow.info["org_id"] = lab.org_id
    slow.info["user_id"] = lab.user_id
    late_id = emit(
        slow, organization_id=lab.org_id, type="AGENT_COMPLETED", mission_id=mission_id, project_id=lab.project_id
    ).id  # flushed, not committed
    fast_id = _emit(lab, mission_id, ["AGENT_STEP"])[0]
    assert late_id < fast_id

    def _commit_slow() -> None:
        slow.commit()
        slow.close()

    timer = threading.Timer(0.4, _commit_slow)
    timer.start()
    try:
        _, body = _stream(lab, f"/api/v1/missions/{mission_id}/events/stream")
    finally:
        timer.join()
        slow.close()
    ids = [int(f["id"]) for f in _parse(body)[1]]
    assert ids == [fast_id, late_id]  # late event delivered (out of id order), nothing duplicated


def test_org_stream_starts_at_tail_and_filters(lab, other_lab, short_streams):
    mission_id = _mission(lab)
    old = _emit(lab, mission_id, ["MISSION_CREATED"])
    _emit(other_lab, None, ["MISSION_CREATED"], project_id=other_lab.project_id)
    live: list[int] = []
    timer = threading.Timer(0.4, lambda: live.extend(_emit(lab, None, ["BUDGET_THRESHOLD", "TOOL_CALLED"])))
    timer.start()
    try:
        _, body = _stream(lab, "/api/v1/events/stream?types=BUDGET_THRESHOLD")
    finally:
        timer.join()
    frames = _parse(body)[1]
    assert [int(f["id"]) for f in frames] == live[:1]  # tail start + type filter

    _, history = _stream(lab, "/api/v1/events/stream?last_event_id=0")
    history_ids = [int(f["id"]) for f in _parse(history)[1]]
    assert old[0] in history_ids and set(live) <= set(history_ids)
    other_ids = set(_emit(other_lab, None, ["MISSION_UPDATED"], project_id=other_lab.project_id))
    assert not other_ids & set(history_ids)  # never another tenant's events


# --- event log API -------------------------------------------------------------------------------------
def test_events_cursor_pagination_and_filters(lab):
    mission_id = _mission(lab)
    ids = _emit(lab, mission_id, ["MISSION_CREATED", "MISSION_PLANNED", "MISSION_APPROVED", "MISSION_STARTED", "X"])
    collected: list[int] = []
    cursor = None
    pages = 0
    while True:
        query = f"/api/v1/events?mission_id={mission_id}&limit=2" + (f"&cursor={cursor}" if cursor else "")
        r = lab.get(query)
        assert r.status_code == 200, r.text
        page = r.json()
        collected += [item["id"] for item in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert collected == ids and pages == 3
    typed = lab.get(f"/api/v1/events?mission_id={mission_id}&type=MISSION_PLANNED&type=MISSION_STARTED").json()
    assert [i["type"] for i in typed["items"]] == ["MISSION_PLANNED", "MISSION_STARTED"]
    one = lab.get(f"/api/v1/events/{ids[0]}")
    assert one.status_code == 200
    body = one.json()
    assert body["type"] == "MISSION_CREATED" and body["mission_id"] == str(mission_id)
    assert uuid.UUID(body["event_uuid"])
    mission_events = lab.get(f"/api/v1/missions/{mission_id}/events?limit=10").json()
    assert [i["id"] for i in mission_events["items"]] == ids
    assert lab.get("/api/v1/events?cursor=not-a-cursor").status_code == 422


def test_events_are_tenant_isolated(lab, other_lab):
    mission_id = _mission(lab)
    ids = _emit(lab, mission_id, ["MISSION_CREATED"])
    assert other_lab.get(f"/api/v1/events/{ids[0]}").status_code == 404
    assert other_lab.get(f"/api/v1/missions/{mission_id}/events").status_code == 404
    listed = other_lab.get(f"/api/v1/events?mission_id={mission_id}").json()
    assert listed["items"] == []
    assert other_lab.get(f"/api/v1/events?project_id={lab.project_id}").status_code == 404


def test_restricted_project_events_hidden_from_non_members(lab):
    from aegis_api.db.session import session_factory
    from aegis_api.lab.core.pagination import CursorParams
    from aegis_api.lab.events import service
    from aegis_api.lab.models import Project

    admin = session_factory(admin=True)()
    try:
        secret_project = Project(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            name="Restricted",
            slug=f"restricted-{uuid.uuid4().hex[:6]}",
            visibility="restricted",
            created_by_id=lab.user_id,
        )
        admin.add(secret_project)
        admin.commit()
        restricted_id = secret_project.id
    finally:
        admin.close()
    hidden = _emit(lab, None, ["HYPOTHESIS_CREATED"], project_id=restricted_id)[0]
    visible = _emit(lab, None, ["HYPOTHESIS_CREATED"])[0]

    def ids_for(role: str) -> set[int]:
        with lab.db() as db:
            page = service.list_events(
                db, lab.actor(role=role), service.EventFilter(), CursorParams(limit=500), lambda e: e.id
            )
            return set(page.items)

    assert {hidden, visible} <= ids_for("owner")
    viewer_ids = ids_for("viewer")
    assert visible in viewer_ids and hidden not in viewer_ids
    with lab.db() as db:
        from aegis_api.errors import NotFound

        with pytest.raises(NotFound):
            service.get_event(db, lab.actor(role="viewer"), hidden)


def test_api_key_can_read_events(lab):
    created = lab.post("/api/v1/api-keys", json={"name": "reader", "role": "viewer", "scopes": ["read"]})
    assert created.status_code == 201, created.text
    key = created.json()["plaintext"]
    ids = _emit(lab, None, ["REPORT_GENERATED"])
    r = lab.ws.client.get(f"/api/v1/events/{ids[0]}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
