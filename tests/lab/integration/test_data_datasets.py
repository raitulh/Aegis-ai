"""Datasets: creation, immutable versions (schema/row count/checksum), splits and held-out data, lineage,
downloads, tenancy, permissions, idempotency and the dataset processing activity."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select, text

from aegis_api.lab.storage import configure_storage
from aegis_api.lab.storage.local import LocalFilesystemStorage
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

CSV = b"id,score,passed,label\n1,0.5,true,a\n2,1.5,false,b\n3,,true,c\n"
SPLIT_CSV = b"x,y,split\n1,2,train\n3,4,train\n5,6,test\n7,8,train\n"


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalFilesystemStorage]:
    store = LocalFilesystemStorage(tmp_path / "objects", public_base_url="http://testserver")
    configure_storage(store)
    yield store
    configure_storage(None)


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, object]]:
    """Record workflow starts instead of running them in the background (keeps direct activity calls deterministic)."""
    from aegis_api.lab.workflows import launcher

    started: list[tuple[object, object]] = []
    monkeypatch.setattr(launcher, "start_run", lambda org, run_id: started.append((org, run_id)))
    return started


def _dataset(lab, name: str = "benchmarks") -> dict:
    r = lab.post("/api/v1/datasets", json={"project_id": str(lab.project_id), "name": name, "license": "CC-BY-4.0"})
    assert r.status_code == 201, r.text
    return r.json()


def _upload(lab, dataset_id: str, content: bytes = CSV, filename: str = "data.csv", **fields):
    data = {k: (json.dumps(v) if isinstance(v, dict | list) else str(v)) for k, v in fields.items()}
    return lab.post(
        f"/api/v1/datasets/{dataset_id}/versions", files={"file": (filename, content, "text/csv")}, data=data
    )


def _viewer_headers(lab) -> dict[str, str]:
    created = lab.post("/api/v1/api-keys", json={"name": "viewer", "role": "viewer", "scopes": ["read"]})
    assert created.status_code in (200, 201), created.text
    return {"Authorization": f"Bearer {created.json()['plaintext']}"}


def test_create_list_get_and_duplicate_name(lab, storage):
    dataset = _dataset(lab)
    assert dataset["project_id"] == str(lab.project_id)
    assert dataset["workspace_id"] == str(lab.workspace_id)
    assert dataset["current_version_id"] is None
    dup = lab.post("/api/v1/datasets", json={"project_id": str(lab.project_id), "name": "benchmarks"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "dataset_exists"
    listed = lab.get(f"/api/v1/datasets?project_id={lab.project_id}").json()
    assert [d["id"] for d in listed["items"]] == [dataset["id"]]
    assert lab.get("/api/v1/datasets?q=bench").json()["meta"]["total"] == 1
    assert lab.get("/api/v1/datasets?q=nomatch").json()["meta"]["total"] == 0
    assert lab.get(f"/api/v1/datasets/{dataset['id']}").json()["name"] == "benchmarks"
    bad = lab.post("/api/v1/datasets", json={"project_id": str(lab.project_id), "name": "  "})
    assert bad.status_code == 422


def test_csv_version_records_schema_rows_checksum_evidence_audit_usage(lab, storage):
    from aegis_api.lab.models import Dataset, DatasetVersion, StorageUsage
    from aegis_api.models import AuditLog, Evidence

    dataset = _dataset(lab)
    r = _upload(lab, dataset["id"], source="https://example.org/data", transformations=[{"op": "dedupe"}])
    assert r.status_code == 201, r.text
    version = r.json()
    assert version["version"] == 1
    assert version["format"] == "csv"
    assert version["row_count"] == 3
    assert version["checksum"] == hashlib.sha256(CSV).hexdigest()
    assert version["size_bytes"] == len(CSV)
    assert version["license"] == "CC-BY-4.0"  # inherited from the dataset
    assert version["transformations"] == [{"op": "dedupe"}]
    columns = {c["name"]: c for c in version["schema"]["columns"]}
    assert columns["id"]["type"] == "int"
    assert columns["score"]["type"] == "float" and columns["score"]["null_count"] == 1
    assert columns["passed"]["type"] == "bool"
    assert "storage_key" not in json.dumps(version)
    with lab.db() as db:
        row = db.get(DatasetVersion, uuid.UUID(version["id"]))
        assert row is not None and storage.get_bytes(row.storage_key, 1000) == CSV
        assert row.storage_key.startswith(f"org/{lab.org_id}/proj/{lab.project_id}/datasets/{dataset['id']}/v1-")
        assert db.get(Dataset, uuid.UUID(dataset["id"])).current_version_id == row.id  # type: ignore[union-attr]
        usage = db.scalars(select(StorageUsage).where(StorageUsage.dataset_version_id == row.id)).all()
        assert [u.bytes_delta for u in usage] == [len(CSV)] and usage[0].reason == "dataset_upload"
        evidence = db.scalars(select(Evidence).where(Evidence.kind == "lab.dataset_version")).all()
        assert any(e.content["sha256"] == version["checksum"] for e in evidence)
        audits = db.scalars(select(AuditLog).where(AuditLog.action == "DATASET_VERSION_CREATED")).all()
        assert [a.resource_id for a in audits] == [version["id"]]


def test_versions_parent_lineage_and_validation(lab, storage):
    dataset = _dataset(lab)
    v1 = _upload(lab, dataset["id"]).json()
    v2 = _upload(lab, dataset["id"], CSV + b"4,2.0,false,d\n", parent_version_id=v1["id"]).json()
    assert v2["version"] == 2 and v2["parent_version_id"] == v1["id"] and v2["row_count"] == 4
    lineage = lab.get(f"/api/v1/dataset-versions/{v2['id']}/lineage").json()
    assert [v["id"] for v in lineage["items"]] == [v2["id"], v1["id"]] and lineage["depth"] == 1
    versions = lab.get(f"/api/v1/datasets/{dataset['id']}/versions").json()
    assert [v["version"] for v in versions["items"]] == [2, 1]
    assert lab.get(f"/api/v1/datasets/{dataset['id']}").json()["current_version_id"] == v2["id"]
    other = _dataset(lab, "other")
    foreign_parent = _upload(lab, other["id"], parent_version_id=v1["id"])
    assert foreign_parent.status_code == 422
    assert _upload(lab, dataset["id"], format="jsonl").status_code == 422  # declared format mismatch
    assert _upload(lab, dataset["id"], schema="{not json").status_code == 422


def test_declared_schema_is_kept_and_inferred_schema_recorded(lab, storage):
    dataset = _dataset(lab)
    declared = {"columns": [{"name": "id", "type": "int"}]}
    version = _upload(lab, dataset["id"], schema=declared).json()
    assert version["schema"] == declared
    assert version["metadata"]["inferred_schema"]["columns"][0]["name"] == "id"


def test_dataset_versions_are_immutable(lab, storage):
    from sqlalchemy.exc import DBAPIError

    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("update dataset_versions set checksum = 'tampered' where id = :id"), {"id": version["id"]})
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("delete from dataset_versions where id = :id"), {"id": version["id"]})


def test_invalid_files_are_rejected(lab, storage):
    dataset = _dataset(lab)
    exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 60
    r = _upload(lab, dataset["id"], exe, "data.csv")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_file_content"
    r = lab.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        files={"file": ("paper.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_file_type"
    assert lab.post(f"/api/v1/datasets/{dataset['id']}/versions", data={"format": "csv"}).status_code == 422
    assert lab.get(f"/api/v1/datasets/{dataset['id']}/versions").json()["meta"]["total"] == 0


def test_split_column_keeps_held_out_data_away_from_experiments(lab, storage):
    from aegis_api.lab.data.datasets import EvaluatorOnlyData, open_dataset_split, read_dataset_bytes

    dataset = _dataset(lab)
    r = _upload(
        lab,
        dataset["id"],
        SPLIT_CSV,
        split_column="split",
        splits={"train": {"visibility": "experiment"}, "test": {"visibility": "evaluator_only"}},
    )
    assert r.status_code == 201, r.text
    version = r.json()
    assert version["row_count"] == 4
    assert version["splits"]["train"]["rows"] == 3 and version["splits"]["test"]["rows"] == 1
    assert version["splits"]["test"]["visibility"] == "evaluator_only"
    assert "storage_key" not in version["splits"]["test"]

    researcher = lab.actor(role="researcher")
    with lab.db() as db:
        train = b"".join(open_dataset_split(db, researcher, version["id"], "train"))
        assert train == b"x,y,split\n1,2,train\n3,4,train\n7,8,train\n"
        with pytest.raises(EvaluatorOnlyData):
            open_dataset_split(db, researcher, version["id"], "test")
        with pytest.raises(EvaluatorOnlyData):
            open_dataset_split(db, researcher, version["id"])  # the full file contains the held-out rows
        held_out = read_dataset_bytes(db, researcher, version["id"], 1000, "test", for_evaluator=True)
        assert held_out == b"x,y,split\n5,6,test\n"
        assert b"".join(open_dataset_split(db, researcher, version["id"], for_evaluator=True)) == SPLIT_CSV
        agent = researcher.for_agent(
            agent_run_id=uuid.uuid4(),
            agent_role="experimenter",
            agent_permissions=researcher.permissions,
            autonomy_level=None,
        )
        with pytest.raises(EvaluatorOnlyData):
            open_dataset_split(db, agent, version["id"], "test", for_evaluator=True)
        viewer = lab.actor(role="viewer")  # dataset:read but no evaluation:run
        from aegis_api.errors import Forbidden

        with pytest.raises(Forbidden):
            open_dataset_split(db, viewer, version["id"], "test", for_evaluator=True)

    # HTTP: experiment split streams; evaluator-only split refused unless explicitly requested by a human.
    stream = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?split=train&mode=stream")
    assert stream.status_code == 200 and stream.content.startswith(b"x,y,split\n1,2,train")
    assert stream.headers["content-disposition"] == 'attachment; filename="train.csv"'
    refused = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?split=test&mode=stream")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "evaluator_only_split"
    assert lab.get(f"/api/v1/dataset-versions/{version['id']}/download?mode=stream").status_code == 403
    allowed = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?split=test&mode=stream&for_evaluator=true")
    assert allowed.status_code == 200 and allowed.content == b"x,y,split\n5,6,test\n"
    assert lab.get(f"/api/v1/dataset-versions/{version['id']}/download?split=nope").status_code == 404


def test_split_column_rejects_undeclared_values(lab, storage):
    dataset = _dataset(lab)
    r = _upload(lab, dataset["id"], SPLIT_CSV, split_column="split", splits={"train": "experiment"})
    assert r.status_code == 422 and "test" in r.json()["error"]["message"]
    r = _upload(lab, dataset["id"], SPLIT_CSV, split_column="split")
    assert r.status_code == 422


def test_one_file_per_split_uses_a_manifest(lab, storage):
    dataset = _dataset(lab)
    train, test = b"a,b\n1,2\n3,4\n", b"a,b\n5,6\n"
    r = lab.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        files=[("files", ("train.csv", train, "text/csv")), ("files", ("heldout.csv", test, "text/csv"))],
        data={"splits": json.dumps({"test": {"visibility": "evaluator_only", "file": "heldout.csv"}})},
    )
    assert r.status_code == 201, r.text
    version = r.json()
    assert set(version["splits"]) == {"train", "test"}
    assert version["splits"]["train"]["visibility"] == "experiment"
    assert version["splits"]["test"]["checksum"] == hashlib.sha256(test).hexdigest()
    assert version["row_count"] == 3 and version["size_bytes"] == len(train) + len(test)
    got = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?split=train&mode=stream")
    assert got.content == train
    manifest = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?mode=stream&for_evaluator=true")
    assert manifest.status_code == 200
    assert hashlib.sha256(manifest.content).hexdigest() == version["checksum"]
    assert json.loads(manifest.content)["splits"]["test"]["sha256"] == version["splits"]["test"]["checksum"]
    mixed = lab.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        files=[
            ("files", ("train.csv", train, "text/csv")),
            ("files", ("test.jsonl", b'{"a":1}\n', "application/json")),
        ],
    )
    assert mixed.status_code == 422


def test_download_redirects_to_signed_url(lab, storage):
    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    r = lab.get(f"/api/v1/dataset-versions/{version['id']}/download", follow_redirects=False)
    assert r.status_code == 307
    assert "no-store" in r.headers["cache-control"]
    signed = lab.ws.client.get(urlparse(r.headers["location"]).path)  # no cookies: the token is the grant
    assert signed.status_code == 200 and signed.content == CSV
    as_url = lab.get(f"/api/v1/dataset-versions/{version['id']}/download?mode=url").json()
    assert as_url["checksum"] == version["checksum"] and as_url["size_bytes"] == len(CSV)


def test_cross_tenant_access_is_404(lab, other_lab, storage):
    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    assert other_lab.get(f"/api/v1/datasets/{dataset['id']}").status_code == 404
    assert other_lab.get(f"/api/v1/datasets/{dataset['id']}/versions").status_code == 404
    assert other_lab.get(f"/api/v1/dataset-versions/{version['id']}").status_code == 404
    assert other_lab.get(f"/api/v1/dataset-versions/{version['id']}/lineage").status_code == 404
    assert other_lab.get(f"/api/v1/dataset-versions/{version['id']}/download?mode=stream").status_code == 404
    assert _upload(other_lab, dataset["id"]).status_code == 404
    r = other_lab.post("/api/v1/datasets", json={"project_id": str(lab.project_id), "name": "x"})
    assert r.status_code == 404
    assert other_lab.get("/api/v1/datasets").json()["meta"]["total"] == 0
    assert _upload(lab, dataset["id"], parent_version_id=str(uuid.uuid4())).status_code == 404


def test_viewer_can_read_but_not_write(lab, storage):
    dataset = _dataset(lab)
    headers = _viewer_headers(lab)
    client = lab.ws.client
    assert client.get(f"/api/v1/datasets/{dataset['id']}", headers=headers).status_code == 200
    r = client.post("/api/v1/datasets", headers=headers, json={"project_id": str(lab.project_id), "name": "v"})
    assert r.status_code == 403
    r = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions", headers=headers, files={"file": ("d.csv", CSV, "text/csv")}
    )
    assert r.status_code == 403


def test_upload_idempotency(lab, storage):
    dataset = _dataset(lab)
    headers = {"Idempotency-Key": f"upload-{uuid.uuid4().hex}"}
    url = f"/api/v1/datasets/{dataset['id']}/versions"
    first = lab.post(url, headers=headers, files={"file": ("d.csv", CSV, "text/csv")})
    second = lab.post(url, headers=headers, files={"file": ("d.csv", CSV, "text/csv")})
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.headers.get("idempotent-replayed") == "true"
    reused = lab.post(url, headers=headers, files={"file": ("d.csv", CSV + b"9,9,true,z\n", "text/csv")})
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "idempotency_key_reused"
    assert lab.get(f"/api/v1/datasets/{dataset['id']}/versions").json()["meta"]["total"] == 1


def test_internal_callers_and_artifact_sourced_versions(lab, storage):
    from aegis_api.errors import ValidationFailed
    from aegis_api.lab.data.artifacts import create_artifact_version
    from aegis_api.lab.data.datasets import create_dataset, create_dataset_version, get_dataset_version
    from aegis_api.lab.data.schemas import DatasetCreate

    actor = lab.actor(role="researcher")
    with lab.db() as db:
        dataset = create_dataset(db, actor, DatasetCreate(project_id=lab.project_id, name="derived"))
        v1 = create_dataset_version(db, actor, dataset.id, data=b'{"a": 1}\n{"a": 2}\n', filename="rows.jsonl")
        assert v1.format == "jsonl" and v1.row_count == 2
        artifact_version = create_artifact_version(
            db, actor, project_id=lab.project_id, kind="csv", name="results", data=CSV, filename="results.csv"
        )
        v2 = create_dataset_version(
            db, actor, dataset.id, artifact_version_id=artifact_version.id, parent_version_id=v1.id
        )
        assert v2.version == 2 and v2.format == "csv" and v2.row_count == 3
        assert v2.checksum == artifact_version.checksum
        assert v2.storage_key != artifact_version.storage_key  # copied into the dataset namespace
        assert v2.dataset_metadata["derived_from"]["artifact_version_id"] == str(artifact_version.id)
        assert storage.get_bytes(v2.storage_key, 1000) == CSV
        v2_id = v2.id
        from_stream = create_dataset_version(db, actor, dataset.id, fileobj=io.BytesIO(CSV), format="csv")
        assert from_stream.version == 3 and from_stream.row_count == 3
        with pytest.raises(ValidationFailed):
            create_dataset_version(db, actor, dataset.id, fileobj=io.BytesIO(CSV))  # no filename, no format
        as_dict = create_dataset(db, actor, {"project_id": str(lab.project_id), "name": "from-dict"})
        assert as_dict.name == "from-dict"
        with pytest.raises(ValidationFailed):
            create_dataset(db, actor, {"project_id": str(lab.project_id)})
    with lab.db() as db:
        assert get_dataset_version(db, actor, v2_id).parent_version_id is not None


def test_failed_transaction_removes_written_objects(lab, storage):
    from aegis_api.lab.data.datasets import create_dataset, create_dataset_version
    from aegis_api.lab.data.schemas import DatasetCreate

    actor = lab.actor()
    with lab.db() as db:
        dataset_id = create_dataset(db, actor, DatasetCreate(project_id=lab.project_id, name="rollback")).id
    with pytest.raises(RuntimeError), lab.db() as db:
        create_dataset_version(db, actor, dataset_id, data=CSV, filename="d.csv")
        assert any(p.is_file() for p in storage.root.rglob("*"))
        raise RuntimeError("abort after storing")
    assert not [p for p in storage.root.rglob("*") if p.is_file()]


def test_process_dataset_version_activity_profiles_and_is_idempotent(lab, storage, launched):
    from aegis_api.lab.data.activities import process_dataset_version
    from aegis_api.lab.models import Artifact, ArtifactVersion, WorkflowRun
    from aegis_api.lab.workflows.registry import ActivityContext

    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    # Every new version gets a DatasetProcessingWorkflow run (started after commit).
    with lab.db() as db:
        run = db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.subject_type == "dataset_version", WorkflowRun.subject_id == version["id"]
            )
        )
        assert run is not None and run.kind == "DatasetProcessingWorkflow"
        assert (lab.org_id, run.id) in launched
    beats: list[object] = []
    ctx = ActivityContext(actor=lab.actor(), heartbeat=beats.append)
    first = process_dataset_version(ctx, {"dataset_version_id": version["id"]})
    assert first["reused"] is False and first["row_count"] == 3
    assert first["integrity"]["main"]["sha256_verified"] is True
    again = process_dataset_version(ctx, {"dataset_version_id": version["id"]})
    assert again["reused"] is True and again["profile_artifact_version_id"] == first["profile_artifact_version_id"]
    with lab.db() as db:
        profile_version = db.get(ArtifactVersion, uuid.UUID(first["profile_artifact_version_id"]))
        assert profile_version is not None
        artifact = db.get(Artifact, profile_version.artifact_id)
        assert artifact is not None and artifact.retention_class == "evidence"
        document = json.loads(storage.get_bytes(profile_version.storage_key, 100_000))
        columns = {c["name"]: c for c in document["profile"]["columns"]}
        assert columns["score"]["max"] == 1.5 and columns["score"]["null_count"] == 1


def test_process_dataset_version_detects_tampering(lab, storage, launched):
    from aegis_api.lab.core.errors import PermanentError
    from aegis_api.lab.data.activities import process_dataset_version
    from aegis_api.lab.models import DatasetVersion
    from aegis_api.lab.workflows.registry import ActivityContext

    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    with lab.db() as db:
        row = db.get(DatasetVersion, uuid.UUID(version["id"]))
        assert row is not None
        storage.path_for(row.storage_key).write_bytes(CSV.replace(b"0.5", b"9.5"))
    with pytest.raises(PermanentError):
        process_dataset_version(ActivityContext(actor=lab.actor()), {"dataset_version_id": version["id"]})


def test_dataset_processing_workflow_runs_on_the_local_engine(lab, storage, launched):
    from aegis_api.lab.models import WorkflowRun
    from aegis_api.lab.workflows.local_engine import LocalWorkflowEngine

    dataset = _dataset(lab)
    version = _upload(lab, dataset["id"]).json()
    with lab.db() as db:
        run_id = db.scalar(select(WorkflowRun.id).where(WorkflowRun.subject_id == version["id"]))
    assert run_id is not None
    outcome = LocalWorkflowEngine().run(run_id, organization_id=lab.org_id)
    assert outcome.status == "COMPLETED", outcome.error
    assert outcome.result is not None and outcome.result["row_count"] == 3
