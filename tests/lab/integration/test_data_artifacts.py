"""Artifacts: upload/list/get, immutable versions, downloads (redirect, signed URL, stream), tenancy,
permissions, retention, malware scanning, internal creation paths and the artifact processing activity."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select, text

from aegis_api.lab.data.scanning import ClamAVScanner, configure_scanner
from aegis_api.lab.storage import configure_storage
from aegis_api.lab.storage.local import LocalFilesystemStorage
from tests.conftest import requires_db
from tests.lab.unit.test_uploads import EICAR, FakeClamd

pytestmark = [requires_db, pytest.mark.db]

REPORT = b"%PDF-1.7\n% lab report\n"
CSV = b"metric,value\naccuracy,0.91\nloss,0.2\n"


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalFilesystemStorage]:
    store = LocalFilesystemStorage(tmp_path / "objects", public_base_url="http://testserver")
    configure_storage(store)
    yield store
    configure_storage(None)


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd()
    configure_scanner(ClamAVScanner("127.0.0.1", server.port, connect_timeout=2, timeout=10))
    yield server
    configure_scanner(None)
    server.close()


def _upload(lab, content: bytes = CSV, filename: str = "metrics.csv", ctype: str = "text/csv", **fields):
    data = {"project_id": str(lab.project_id)}
    data.update({k: (json.dumps(v) if isinstance(v, dict) else str(v)) for k, v in fields.items()})
    return lab.post("/api/v1/artifacts", files={"file": (filename, content, ctype)}, data=data)


def _signed_path(response) -> str:  # type: ignore[no-untyped-def]
    return urlparse(response.headers["location"]).path


def test_upload_get_list_and_download_modes(lab, storage):
    r = _upload(lab, name="Run metrics", metadata={"run": 7})
    assert r.status_code == 201, r.text
    artifact = r.json()
    assert artifact["kind"] == "csv" and artifact["name"] == "Run metrics"
    assert artifact["retention_class"] == "standard"
    version = artifact["current_version"]
    assert version["version"] == 1 and version["size_bytes"] == len(CSV)
    assert version["checksum"] == hashlib.sha256(CSV).hexdigest()
    assert version["mime_type"] == "text/csv" and version["original_filename"] == "metrics.csv"
    assert version["scan_status"] == "not_scanned" and version["metadata"] == {"run": 7}
    assert "storage_key" not in json.dumps(artifact)

    assert lab.get(f"/api/v1/artifacts/{artifact['id']}").json()["current_version"]["id"] == version["id"]
    assert lab.get(f"/api/v1/artifact-versions/{version['id']}").json()["checksum"] == version["checksum"]
    listed = lab.get(f"/api/v1/artifacts?project_id={lab.project_id}&kind=csv").json()
    assert [a["id"] for a in listed["items"]] == [artifact["id"]]
    assert listed["items"][0]["current_version"]["id"] == version["id"]
    assert lab.get("/api/v1/artifacts?kind=model").json()["items"] == []
    assert lab.get("/api/v1/artifacts?kind=bogus").status_code == 422

    redirect = lab.get(f"/api/v1/artifact-versions/{version['id']}/download", follow_redirects=False)
    assert redirect.status_code == 307 and "no-store" in redirect.headers["cache-control"]
    fetched = lab.ws.client.get(_signed_path(redirect))  # no session cookie needed
    assert fetched.status_code == 200 and fetched.content == CSV
    assert fetched.headers["content-disposition"] == 'attachment; filename="metrics.csv"'
    assert fetched.headers["x-content-type-options"] == "nosniff"

    streamed = lab.get(f"/api/v1/artifact-versions/{version['id']}/download?mode=stream")
    assert streamed.status_code == 200 and streamed.content == CSV
    assert streamed.headers["x-checksum-sha256"] == version["checksum"]
    assert streamed.headers["content-length"] == str(len(CSV))

    as_url = lab.get(f"/api/v1/artifact-versions/{version['id']}/download?mode=url").json()
    assert lab.ws.client.get(urlparse(as_url["url"]).path).content == CSV


def test_cursor_pagination(lab, storage):
    ids = [_upload(lab, name=f"a{i}").json()["id"] for i in range(3)]
    page1 = lab.get("/api/v1/artifacts?limit=2").json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = lab.get(f"/api/v1/artifacts?limit=2&cursor={page1['next_cursor']}").json()
    assert page2["next_cursor"] is None
    assert {a["id"] for a in page1["items"] + page2["items"]} == set(ids)


def test_new_versions_are_numbered_and_immutable(lab, storage):
    from sqlalchemy.exc import DBAPIError

    artifact = _upload(lab).json()
    r = lab.post(
        f"/api/v1/artifacts/{artifact['id']}/versions",
        files={"file": ("metrics.csv", CSV + b"f1,0.8\n", "text/csv")},
        data={"metadata": json.dumps({"note": "rerun"})},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["metadata"] == {"note": "rerun"}
    versions = lab.get(f"/api/v1/artifacts/{artifact['id']}/versions").json()
    assert [v["version"] for v in versions["items"]] == [2, 1]
    assert lab.get(f"/api/v1/artifacts/{artifact['id']}").json()["current_version_id"] == v2["id"]
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("update artifact_versions set checksum = 'x' where id = :id"), {"id": v2["id"]})
    with lab.db() as db:  # scan_status is the one mutable column
        db.execute(text("update artifact_versions set scan_status = 'clean' where id = :id"), {"id": v2["id"]})


def test_upload_validation_and_limits(lab, storage):
    exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 60
    r = _upload(lab, exe, "report.pdf", "application/pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_file_content"
    assert _upload(lab, b"x", "tool.exe", "application/octet-stream").status_code == 422
    assert _upload(lab, CSV, "metrics.csv", "image/png").status_code == 422
    assert _upload(lab, retention_class="forever").status_code == 422
    assert _upload(lab, kind="nonsense").status_code == 422
    assert _upload(lab, metadata="[1, 2]").status_code == 422
    assert _upload(lab, mission_id=str(uuid.uuid4())).status_code == 404
    assert lab.get("/api/v1/artifacts").json()["items"] == []


def test_cross_tenant_access_is_404_and_tokens_cannot_be_retargeted(lab, other_lab, storage):
    from aegis_api.lab.storage.keys import object_key

    artifact = _upload(lab).json()
    version_id = artifact["current_version"]["id"]
    other_artifact = _upload(other_lab, b"secret,1\n", "other.csv").json()
    for path in (
        f"/api/v1/artifacts/{artifact['id']}",
        f"/api/v1/artifacts/{artifact['id']}/versions",
        f"/api/v1/artifact-versions/{version_id}",
        f"/api/v1/artifact-versions/{version_id}/download",
        f"/api/v1/artifact-versions/{version_id}/download?mode=stream",
    ):
        assert other_lab.get(path, follow_redirects=False).status_code == 404, path
    assert other_lab.delete(f"/api/v1/artifacts/{artifact['id']}").status_code == 404
    r = other_lab.post(f"/api/v1/artifacts/{artifact['id']}/versions", files={"file": ("x.csv", CSV, "text/csv")})
    assert r.status_code == 404
    assert _upload(other_lab, name="own-project").status_code == 201  # own project works
    r = other_lab.post(
        "/api/v1/artifacts",
        files={"file": ("x.csv", CSV, "text/csv")},
        data={"project_id": str(lab.project_id)},
    )
    assert r.status_code == 404
    assert artifact["id"] not in {a["id"] for a in other_lab.get("/api/v1/artifacts").json()["items"]}

    # A signed URL for org A's object cannot be modified to fetch org B's object.
    redirect = lab.get(f"/api/v1/artifact-versions/{version_id}/download", follow_redirects=False)
    token = _signed_path(redirect).rsplit("/", 1)[1]
    payload_part, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)))
    with other_lab.db() as db:
        from aegis_api.lab.models import ArtifactVersion

        foreign = db.get(ArtifactVersion, uuid.UUID(other_artifact["current_version"]["id"]))
        assert foreign is not None
        payload["k"] = foreign.storage_key
    forged = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    r = lab.ws.client.get(f"/api/v1/storage/objects/{forged}.{signature}")
    assert r.status_code == 403
    guessed = object_key(other_lab.org_id, other_lab.project_id, "artifacts", other_artifact["id"])
    payload["k"] = guessed
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert lab.ws.client.get(f"/api/v1/storage/objects/{forged}.{signature}").status_code == 403


def test_viewer_can_see_metadata_but_not_download(lab, storage):
    artifact = _upload(lab).json()
    version_id = artifact["current_version"]["id"]
    created = lab.post("/api/v1/api-keys", json={"name": "viewer", "role": "viewer", "scopes": ["read"]}).json()
    headers = {"Authorization": f"Bearer {created['plaintext']}"}
    client = lab.ws.client
    assert client.get(f"/api/v1/artifacts/{artifact['id']}", headers=headers).status_code == 200
    for mode in ("redirect", "stream", "url"):
        r = client.get(
            f"/api/v1/artifact-versions/{version_id}/download?mode={mode}", headers=headers, follow_redirects=False
        )
        assert r.status_code == 403, mode
    r = client.post(
        "/api/v1/artifacts",
        headers=headers,
        files={"file": ("x.csv", CSV, "text/csv")},
        data={"project_id": str(lab.project_id)},
    )
    assert r.status_code == 403
    assert client.delete(f"/api/v1/artifacts/{artifact['id']}", headers=headers).status_code == 403


def test_retention_and_soft_delete(lab, storage):
    evidence = _upload(lab, REPORT, "report.pdf", "application/pdf", retention_class="evidence").json()
    assert evidence["kind"] == "report"
    r = lab.delete(f"/api/v1/artifacts/{evidence['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "retained"
    held = _upload(lab, name="held").json()
    with lab.db() as db:
        db.execute(text("update artifacts set legal_hold = true where id = :id"), {"id": held["id"]})
    r = lab.delete(f"/api/v1/artifacts/{held['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "legal_hold"

    standard = _upload(lab, name="scratch").json()
    assert lab.delete(f"/api/v1/artifacts/{standard['id']}").status_code == 204
    assert lab.get(f"/api/v1/artifacts/{standard['id']}").status_code == 404
    assert lab.get(f"/api/v1/artifact-versions/{standard['current_version']['id']}").status_code == 404
    assert lab.delete(f"/api/v1/artifacts/{standard['id']}").status_code == 404
    listed = {a["id"] for a in lab.get("/api/v1/artifacts").json()["items"]}
    assert standard["id"] not in listed and evidence["id"] in listed


def test_storage_usage_audit_and_evidence(lab, storage):
    from aegis_api.lab.models import StorageUsage
    from aegis_api.models import AuditLog, Evidence

    artifact = _upload(lab, REPORT, "report.pdf", "application/pdf", retention_class="evidence").json()
    version_id = artifact["current_version"]["id"]
    lab.get(f"/api/v1/artifact-versions/{version_id}/download?mode=stream")
    with lab.db() as db:
        usage = db.scalars(select(StorageUsage).where(StorageUsage.artifact_version_id == uuid.UUID(version_id))).all()
        assert [(u.bytes_delta, u.reason) for u in usage] == [(len(REPORT), "artifact_upload")]
        actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.resource_id == version_id)).all()}
        assert {"FILE_UPLOADED", "ARTIFACT_DOWNLOADED"} <= actions
        evidence = db.scalars(select(Evidence).where(Evidence.kind == "lab.artifact_version")).all()
        assert any(e.content["artifact_version_id"] == version_id for e in evidence)


def test_malware_is_rejected_and_audited(lab, storage, clamd):
    from aegis_api.models import AuditLog

    clean = _upload(lab, name="clean")
    assert clean.status_code == 201
    assert clean.json()["current_version"]["scan_status"] == "clean"
    assert clean.json()["current_version"]["metadata"]["scan"]["scanner"] == "clamav"

    infected = _upload(lab, b"id,payload\n1," + EICAR + b"\n", "evil.csv", name="evil")
    assert infected.status_code == 422
    body = infected.json()["error"]
    assert body["code"] == "malware_detected" and body["details"]["signature"] == "Eicar-Test-Signature"
    assert [a["name"] for a in lab.get("/api/v1/artifacts").json()["items"]] == ["clean"]
    assert not [p for p in storage.root.rglob("*evil*") if p.is_file()]
    with lab.db() as db:
        audits = db.scalars(select(AuditLog).where(AuditLog.action == "MALWARE_DETECTED")).all()
        assert len(audits) == 1 and audits[0].after["signature"] == "Eicar-Test-Signature"

    dataset = lab.post("/api/v1/datasets", json={"project_id": str(lab.project_id), "name": "d"}).json()
    r = lab.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        files={"file": ("d.csv", b"a\n" + EICAR + b"\n", "text/csv")},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "malware_detected"


def test_uploads_fail_closed_when_the_scanner_is_down(lab, storage):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    configure_scanner(ClamAVScanner("127.0.0.1", port, connect_timeout=0.5))
    try:
        r = _upload(lab)
        assert r.status_code == 503 and r.json()["error"]["code"] == "malware_scanner_unavailable"
    finally:
        configure_scanner(None)


def test_quarantined_versions_cannot_be_downloaded(lab, storage):
    from aegis_api.lab.data.artifacts import ArtifactQuarantined, open_artifact_stream

    artifact = _upload(lab).json()
    version_id = artifact["current_version"]["id"]
    with lab.db() as db:
        db.execute(text("update artifact_versions set scan_status = 'infected' where id = :id"), {"id": version_id})
    r = lab.get(f"/api/v1/artifact-versions/{version_id}/download?mode=stream")
    assert r.status_code == 403 and r.json()["error"]["code"] == "artifact_quarantined"
    with lab.db() as db, pytest.raises(ArtifactQuarantined):
        open_artifact_stream(db, lab.actor(), version_id)


def test_internal_creation_paths_and_bounded_reads(lab, storage):
    from aegis_api.errors import Forbidden, PayloadTooLarge, ValidationFailed
    from aegis_api.lab.data.artifacts import (
        create_artifact_version,
        get_artifact_version,
        open_artifact_stream,
        read_artifact_bytes,
    )
    from aegis_api.lab.storage.keys import object_key

    actor = lab.actor(role="researcher")
    with lab.db() as db:
        from_bytes = create_artifact_version(
            db, actor, project_id=lab.project_id, kind="log", name="stdout", data=b"line 1\nline 2\n"
        )
        assert from_bytes.version == 1 and from_bytes.mime_type == "application/octet-stream"
        assert read_artifact_bytes(db, actor, from_bytes.id, 1024) == b"line 1\nline 2\n"
        with pytest.raises(PayloadTooLarge):
            read_artifact_bytes(db, actor, from_bytes.id, 4)
        assert b"".join(open_artifact_stream(db, actor, from_bytes.id, 4096)) == b"line 1\nline 2\n"

        key = object_key(lab.org_id, lab.project_id, "jobs", "j1", "outputs", "model.pt")
        stored = storage.put_bytes(key, b"\x80weights", "application/octet-stream")
        from_stored = create_artifact_version(
            db, actor, project_id=lab.project_id, name="model", stored=stored, filename="model.pt"
        )
        assert from_stored.storage_key == key and from_stored.checksum == stored.sha256
        assert get_artifact_version(db, actor, from_stored.id).artifact_id == from_stored.artifact_id
        next_version = create_artifact_version(
            db, actor, project_id=lab.project_id, artifact_id=from_bytes.artifact_id, data=b"line 3\n"
        )
        assert next_version.version == 2

        foreign_key = object_key(uuid.uuid4(), lab.project_id, "x.bin")
        forged = type(stored)(key=foreign_key, size=1, sha256="0" * 64)
        with pytest.raises(ValidationFailed):
            create_artifact_version(db, actor, project_id=lab.project_id, name="x", stored=forged)
        with pytest.raises(ValidationFailed):
            create_artifact_version(db, actor, project_id=lab.project_id, name="x", data=b"1", stored=stored)
        viewer = lab.actor(role="viewer")
        with pytest.raises(Forbidden):
            create_artifact_version(db, viewer, project_id=lab.project_id, name="x", data=b"1")
        with pytest.raises(Forbidden):
            read_artifact_bytes(db, viewer, from_bytes.id, 1024)


def test_integrity_check_on_read(lab, storage):
    from aegis_api.lab.data.artifacts import ArtifactIntegrityError, read_artifact_bytes
    from aegis_api.lab.models import ArtifactVersion

    version_id = _upload(lab).json()["current_version"]["id"]
    with lab.db() as db:
        row = db.get(ArtifactVersion, uuid.UUID(version_id))
        assert row is not None
        storage.path_for(row.storage_key).write_bytes(CSV.replace(b"0.91", b"0.99"))
        with pytest.raises(ArtifactIntegrityError):
            read_artifact_bytes(db, lab.actor(), version_id, 1024)


def test_rollback_removes_the_stored_object(lab, storage):
    from aegis_api.lab.data.artifacts import create_artifact_version

    with pytest.raises(RuntimeError), lab.db() as db:
        create_artifact_version(db, lab.actor(), project_id=lab.project_id, name="tmp", data=b"x" * 10)
        assert [p for p in storage.root.rglob("*") if p.is_file()]
        raise RuntimeError("fail after write")
    assert not [p for p in storage.root.rglob("*") if p.is_file()]


def test_process_artifact_version_activity(lab, storage, clamd):
    from aegis_api.lab.data.activities import process_artifact_version
    from aegis_api.lab.data.artifacts import create_artifact_version
    from aegis_api.lab.models import ArtifactVersion
    from aegis_api.lab.storage.keys import object_key
    from aegis_api.lab.workflows.registry import ActivityContext

    actor = lab.actor()
    with lab.db() as db:
        key = object_key(lab.org_id, lab.project_id, "jobs", "j2", "plot.png")
        stored = storage.put_bytes(key, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")
        version = create_artifact_version(
            db, actor, project_id=lab.project_id, name="plot", stored=stored, filename="plot.png", mime_type="image/png"
        )
        assert version.scan_status == "not_scanned"  # platform-written objects are scanned asynchronously
        version_id = str(version.id)
    ctx = ActivityContext(actor=actor)
    result = process_artifact_version(ctx, {"artifact_version_id": version_id})
    assert result["scan_status"] == "clean" and result["sha256_verified"] and result["size_verified"]
    assert result["sniffed_content_type"] == "image/png" and result["content_type_mismatch"] is False
    assert process_artifact_version(ctx, {"artifact_version_id": version_id})["skipped"] is True

    with lab.db() as db:
        infected_key = object_key(lab.org_id, lab.project_id, "jobs", "j2", "out.txt")
        infected = create_artifact_version(
            db,
            actor,
            project_id=lab.project_id,
            name="out",
            stored=storage.put_bytes(infected_key, EICAR),
            filename="out.txt",
        )
        tampered = create_artifact_version(
            db,
            actor,
            project_id=lab.project_id,
            name="t",
            stored=storage.put_bytes(object_key(lab.org_id, lab.project_id, "jobs", "j2", "t.txt"), b"original"),
        )
        infected_id, tampered_id = str(infected.id), str(tampered.id)
        storage.path_for(tampered.storage_key).write_bytes(b"modified")
    assert process_artifact_version(ctx, {"artifact_version_id": infected_id})["scan_status"] == "infected"
    bad = process_artifact_version(ctx, {"artifact_version_id": tampered_id})
    assert bad["scan_status"] == "error" and bad["sha256_verified"] is False
    with lab.db() as db:
        assert db.get(ArtifactVersion, uuid.UUID(infected_id)).scan_status == "infected"  # type: ignore[union-attr]
    assert lab.get(f"/api/v1/artifact-versions/{infected_id}/download?mode=stream").status_code == 403


def test_upload_idempotency(lab, storage):
    headers = {"Idempotency-Key": f"artifact-{uuid.uuid4().hex}"}
    data = {"project_id": str(lab.project_id), "name": "idem"}
    first = lab.post("/api/v1/artifacts", headers=headers, files={"file": ("m.csv", CSV, "text/csv")}, data=data)
    second = lab.post("/api/v1/artifacts", headers=headers, files={"file": ("m.csv", CSV, "text/csv")}, data=data)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("idempotent-replayed") == "true"
    assert len(lab.get("/api/v1/artifacts").json()["items"]) == 1
