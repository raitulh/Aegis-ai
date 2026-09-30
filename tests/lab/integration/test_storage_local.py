"""Local filesystem object storage: streaming, limits, atomicity, containment and signed downloads."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

from aegis_api.errors import PayloadTooLarge
from aegis_api.lab.storage import configure_storage
from aegis_api.lab.storage.base import InvalidObjectKey, ObjectNotFound, stream_reader
from aegis_api.lab.storage.keys import object_key
from aegis_api.lab.storage.local import LocalFilesystemStorage
from aegis_api.lab.storage.signing import sign_download
from tests.conftest import requires_db

ORG, PROJECT = uuid.uuid4(), uuid.uuid4()


def key(*parts: str) -> str:
    return object_key(ORG, PROJECT, *parts)


@pytest.fixture
def storage(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(tmp_path / "objects", public_base_url="http://testserver")


class Chunked(io.RawIOBase):
    """A non-seekable source that produces ``total`` bytes without holding them in memory."""

    def __init__(self, total: int, block: bytes = b"0123456789abcdef") -> None:
        self.remaining = total
        self.block = block
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, b):  # type: ignore[no-untyped-def]
        n = min(len(b), self.remaining)
        chunk = (self.block * (n // len(self.block) + 1))[:n]
        b[:n] = chunk
        self.remaining -= n
        self.digest.update(chunk)
        return n


def _no_temp_files(root: Path) -> bool:
    return not [p for p in root.rglob("*") if p.name.startswith(".upload-")]


def test_put_get_stat_exists_delete(storage):
    k = key("artifacts", "a", "v1", "hello.txt")
    stored = storage.put_bytes(k, b"hello world", "text/plain")
    assert stored.size == 11 and stored.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert storage.exists(k)
    assert storage.get_bytes(k, 100) == b"hello world"
    info = storage.stat(k)
    assert info.size == 11
    if info.sha256 is not None:  # extended attributes are optional
        assert info.sha256 == stored.sha256 and info.content_type == "text/plain"
    storage.delete(k)
    assert not storage.exists(k)
    storage.delete(k)  # idempotent
    with pytest.raises(ObjectNotFound):
        storage.stat(k)
    with pytest.raises(ObjectNotFound):
        storage.open_stream(k)
    # Empty parent directories are pruned, the root survives.
    assert storage.root.exists() and not any(storage.root.iterdir())


def test_streaming_write_and_read_are_chunked(storage):
    total = 5 * 1024 * 1024 + 123
    source = Chunked(total)
    k = key("big.bin")
    stored = storage.put_stream(k, source, "application/octet-stream", max_bytes=total)  # type: ignore[arg-type]
    assert stored.size == total and stored.sha256 == source.digest.hexdigest()
    chunks = list(storage.open_stream(k, chunk_size=64 * 1024))
    assert all(len(c) <= 64 * 1024 for c in chunks)
    assert sum(len(c) for c in chunks) == total
    assert hashlib.sha256(b"".join(chunks)).hexdigest() == stored.sha256
    with stream_reader(storage.open_stream(k)) as reader:
        assert reader.read(16) == b"0123456789abcdef"
    assert _no_temp_files(storage.root)


def test_size_limit_aborts_and_leaves_nothing(storage):
    k = key("too-big.bin")
    with pytest.raises(PayloadTooLarge):
        storage.put_stream(k, Chunked(3 * 1024 * 1024), max_bytes=1024 * 1024)  # type: ignore[arg-type]
    assert not storage.exists(k)
    assert _no_temp_files(storage.root)


def test_failed_overwrite_keeps_previous_object(storage):
    k = key("stable.txt")
    storage.put_bytes(k, b"v1")
    with pytest.raises(PayloadTooLarge):
        storage.put_stream(k, io.BytesIO(b"x" * 100), max_bytes=10)
    assert storage.get_bytes(k, 10) == b"v1"


def test_get_bytes_refuses_large_objects(storage):
    k = key("large.bin")
    storage.put_bytes(k, b"x" * 1000)
    with pytest.raises(PayloadTooLarge):
        storage.get_bytes(k, max_bytes=999)


def test_invalid_keys_never_touch_the_filesystem(storage, tmp_path):
    outside = tmp_path / "outside.txt"
    for bad in ["../outside.txt", f"{key('x')}/../../../../outside.txt", "/etc/passwd", "a\\..\\b", "%2e%2e/x"]:
        with pytest.raises(InvalidObjectKey):
            storage.put_bytes(bad, b"pwned")
        with pytest.raises(InvalidObjectKey):
            storage.open_stream(bad)
    assert not outside.exists()


def test_symlink_escape_is_refused(storage, tmp_path):
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "data.txt").write_text("top secret")
    link_parent = storage.root / "org" / str(ORG) / "proj" / str(PROJECT)
    link_parent.mkdir(parents=True)
    (link_parent / "escape").symlink_to(secret_dir)
    with pytest.raises(InvalidObjectKey):
        storage.open_stream(key("escape", "data.txt"))
    with pytest.raises(InvalidObjectKey):
        storage.put_bytes(key("escape", "new.txt"), b"x")
    assert not (secret_dir / "new.txt").exists()


def test_copy_and_health(storage):
    src, dst = key("a.txt"), key("copy", "a.txt")
    storage.put_bytes(src, b"copy me", "text/plain")
    info = storage.copy(src, dst)
    assert info.size == 7 and info.sha256 == hashlib.sha256(b"copy me").hexdigest()
    assert storage.get_bytes(dst, 100) == b"copy me"
    with pytest.raises(ObjectNotFound):
        storage.copy(key("missing"), key("x"))
    assert storage.health()


def test_presign_get_returns_signed_api_url(storage):
    k = key("report.pdf")
    storage.put_bytes(k, b"%PDF-1.4")
    url = storage.presign_get(k, 60, "report.pdf", "application/pdf")
    parsed = urlparse(url)
    assert parsed.netloc == "testserver"
    assert parsed.path.startswith("/api/v1/storage/objects/")
    assert "aeg" not in parsed.query  # nothing secret in the URL besides the token


@pytest.fixture
def served_storage(tmp_path: Path) -> Iterator[LocalFilesystemStorage]:
    store = LocalFilesystemStorage(tmp_path / "served", public_base_url="http://testserver")
    configure_storage(store)
    yield store
    configure_storage(None)


@requires_db
@pytest.mark.db
def test_signed_object_endpoint(client, served_storage):
    k = key("artifacts", "x", "v1", "data.csv")
    served_storage.put_bytes(k, b"a,b\n1,2\n", "text/csv")
    url = served_storage.presign_get(k, 60, "data.csv", "text/csv")
    response = client.get(urlparse(url).path)
    assert response.status_code == 200
    assert response.content == b"a,b\n1,2\n"
    assert response.headers["content-disposition"] == 'attachment; filename="data.csv"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-type"].startswith("text/csv")

    # Tampering with the token (e.g. pointing it at another object) is refused.
    token = urlparse(url).path.rsplit("/", 1)[1]
    payload, signature = token.split(".")
    other = sign_download(key("other"), ttl_seconds=60).split(".")[0]
    assert client.get(f"/api/v1/storage/objects/{other}.{signature}").status_code == 403
    assert client.get(f"/api/v1/storage/objects/{payload}.AAAA").status_code == 403
    assert client.get("/api/v1/storage/objects/garbage").status_code == 403
    expired = sign_download(k, ttl_seconds=1, now=1_000_000_000)
    r = client.get(f"/api/v1/storage/objects/{expired}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_signed_url"

    # Valid token for a missing object → 404.
    missing = sign_download(key("missing.bin"), ttl_seconds=60)
    assert client.get(f"/api/v1/storage/objects/{missing}").status_code == 404
