"""The Python SDK's lab namespace against the in-process app (TestClient transport)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db


def _sdk(workspace, role: str = "researcher"):
    from aegis_ai import Aegis

    key = workspace.post(
        "/api/v1/api-keys", json={"name": "sdk", "role": role, "scopes": ["read", "write", "run", "download"]}
    ).json()["plaintext"]
    client = Aegis(api_key=key)
    client._t._client = workspace.client  # route through the TestClient transport
    client._t._client.cookies.clear()
    client._t._client.headers.update({"Authorization": f"Bearer {key}"})
    return client


def test_sdk_lab_workflow(workspace, lab, tmp_path):
    from aegis_ai import ConflictError, ValidationError

    project = workspace.post("/api/v1/projects", json={"name": "SDK project"}).json()
    client = _sdk(workspace)
    lab_api = client.lab

    mission = lab_api.missions.create(
        project_id=project["id"], title="SDK mission", objective="Exercise the SDK against the lab API."
    )
    assert mission["status"] == "draft" and mission["autonomy_level"] == "L1_RESEARCH_AUTOMATION"
    updated = lab_api.missions.update(mission["id"], lock_version=mission["lock_version"], title="SDK mission v2")
    assert updated["title"] == "SDK mission v2"
    with pytest.raises(ConflictError):
        lab_api.missions.update(mission["id"], lock_version=mission["lock_version"], title="stale")
    assert [m["id"] for m in lab_api.missions.list(project_id=project["id"]).items] == [mission["id"]]

    key = f"sdk-{uuid.uuid4().hex}"
    first = lab_api.missions.launch(mission["id"], idempotency_key=key)
    assert lab_api.missions.launch(mission["id"], idempotency_key=key) == first
    lab_api.missions.cancel(mission["id"], reason="sdk test")
    events = list(lab_api.missions.stream_events(mission["id"], last_event_id=0))
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    assert {"MISSION_CREATED", "MISSION_CANCELLED"} <= {e["event_type"] for e in events}
    assert lab_api.missions.get(mission["id"])["status"] == "cancelled"

    assert lab_api.experiments.contract()["io_contract"]["network"] == "none"
    report = lab_api.experiments.validate({"objective": "x"})
    assert report["valid"] is False

    source = tmp_path / "notes.txt"
    source.write_bytes(b"sdk artifact")
    artifact = lab_api.artifacts.upload(project_id=project["id"], path=source)
    assert lab_api.artifacts.download(artifact["id"]) == b"sdk artifact"

    memory = lab_api.memory.propose(scope="project", project_id=project["id"], content="SDK-proposed lesson text.")
    assert memory["status"] == "proposed"  # automation cannot write durable memory without review

    with pytest.raises(ValidationError):
        lab_api.missions.create(project_id=project["id"], title="x", objective="too short")
    usage = lab_api.usage(mission_id=mission["id"])
    assert "spend" in usage
