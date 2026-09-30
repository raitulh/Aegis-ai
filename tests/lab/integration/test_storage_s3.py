"""S3-compatible storage adapter against a real S3 API.

The endpoint comes from ``TEST_S3_ENDPOINT`` (+ ``TEST_S3_ACCESS_KEY``/``TEST_S3_SECRET_KEY``) when set;
otherwise a throwaway MinIO container is started with Docker for the test session and removed afterwards.
Without either, the tests are skipped.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from aegis_api.errors import PayloadTooLarge
from aegis_api.lab.storage.base import ObjectNotFound, StorageUnavailable
from aegis_api.lab.storage.keys import object_key
from aegis_api.lab.storage.s3 import S3Storage

# Upstream minio/minio is no longer published on Docker Hub; default to the pinned build docker-compose.yml uses.
MINIO_IMAGE = os.environ.get(
    "TEST_MINIO_IMAGE",
    "pgsty/minio:RELEASE.2026-08-04T00-00-00Z@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372",
)
MIB = 1024 * 1024


@dataclass(frozen=True)
class S3Endpoint:
    url: str
    access_key: str
    secret_key: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/minio/health/live", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _docker(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603, S607


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[S3Endpoint]:
    configured = os.environ.get("TEST_S3_ENDPOINT")
    if configured:
        yield S3Endpoint(
            configured.rstrip("/"),
            os.environ.get("TEST_S3_ACCESS_KEY", "testing"),
            os.environ.get("TEST_S3_SECRET_KEY", "testing"),
        )
        return
    if shutil.which("docker") is None:
        pytest.skip("S3 tests need TEST_S3_ENDPOINT or Docker to start MinIO")
    try:
        if _docker("image", "inspect", MINIO_IMAGE).returncode != 0:
            pulled = _docker("pull", MINIO_IMAGE, timeout=300)
            if pulled.returncode != 0:
                pytest.skip(f"MinIO image unavailable: {pulled.stderr.strip()[:200]}")
        port = _free_port()
        user, password = "aegis-test", f"aegis-test-{uuid.uuid4().hex[:12]}"
        started = _docker(
            "run",
            "-d",
            "--rm",
            "-p",
            f"127.0.0.1:{port}:9000",
            "-e",
            f"MINIO_ROOT_USER={user}",
            "-e",
            f"MINIO_ROOT_PASSWORD={password}",
            MINIO_IMAGE,
            "server",
            "/data",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Docker unavailable: {exc}")
    if started.returncode != 0:
        pytest.skip(f"Could not start MinIO: {started.stderr.strip()[:200]}")
    container = started.stdout.strip()
    try:
        url = f"http://127.0.0.1:{port}"
        if not _wait_ready(url):
            pytest.skip("MinIO did not become ready")
        yield S3Endpoint(url, user, password)
    finally:
        _docker("stop", container, timeout=60)


@pytest.fixture
def s3(s3_endpoint: S3Endpoint) -> S3Storage:
    return S3Storage(
        bucket=f"aegis-test-{uuid.uuid4().hex[:10]}",
        endpoint_url=s3_endpoint.url,
        region="us-east-1",
        access_key=s3_endpoint.access_key,
        secret_key=s3_endpoint.secret_key,
        force_path_style=True,
        auto_create_bucket=True,
        multipart_threshold=5 * MIB,
        multipart_chunksize=5 * MIB,
    )


def key(*parts: str) -> str:
    return object_key(uuid.UUID(int=1), uuid.UUID(int=2), *parts)


class Chunked(io.RawIOBase):
    def __init__(self, total: int) -> None:
        self.remaining = total
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, b):  # type: ignore[no-untyped-def]
        n = min(len(b), self.remaining)
        chunk = bytes((i * 31) % 251 for i in range(256)) * (n // 256 + 1)
        chunk = chunk[:n]
        b[:n] = chunk
        self.remaining -= n
        self.digest.update(chunk)
        return n


@pytest.mark.s3
def test_round_trip(s3: S3Storage):
    k = key("artifacts", "a", "v1", "hello.txt")
    stored = s3.put_bytes(k, b"hello s3", "text/plain")
    assert stored.sha256 == hashlib.sha256(b"hello s3").hexdigest()
    assert s3.exists(k)
    info = s3.stat(k)
    assert info.size == 8 and info.sha256 == stored.sha256 and info.content_type == "text/plain"
    assert s3.get_bytes(k, 100) == b"hello s3"
    assert b"".join(s3.open_stream(k, chunk_size=4096)) == b"hello s3"
    s3.delete(k)
    assert not s3.exists(k)
    s3.delete(k)  # idempotent
    with pytest.raises(ObjectNotFound):
        s3.stat(k)
    with pytest.raises(ObjectNotFound):
        s3.open_stream(k)
    assert s3.health()


@pytest.mark.s3
def test_multipart_streaming_upload_and_chunked_read(s3: S3Storage):
    total = 12 * MIB + 7
    source = Chunked(total)
    k = key("big.bin")
    stored = s3.put_stream(k, source, "application/octet-stream", max_bytes=total)  # type: ignore[arg-type]
    assert stored.size == total and stored.sha256 == source.digest.hexdigest()
    digest = hashlib.sha256()
    size = 0
    for chunk in s3.open_stream(k, chunk_size=MIB):
        assert len(chunk) <= MIB
        digest.update(chunk)
        size += len(chunk)
    assert size == total and digest.hexdigest() == stored.sha256
    assert s3.stat(k).size == total
    with pytest.raises(PayloadTooLarge):
        s3.get_bytes(k, max_bytes=MIB)


@pytest.mark.s3
@pytest.mark.parametrize("total", [2 * MIB, 12 * MIB])
def test_size_limit_aborts_without_creating_an_object(s3: S3Storage, total: int):
    k = key(f"too-big-{total}.bin")
    with pytest.raises(PayloadTooLarge):
        s3.put_stream(k, Chunked(total), max_bytes=total - 1)  # type: ignore[arg-type]
    assert not s3.exists(k)
    # A multipart upload that hit the limit is aborted, not left behind as billable parts.
    assert s3._client.list_multipart_uploads(Bucket=s3.bucket).get("Uploads", []) == []


@pytest.mark.s3
def test_presigned_url_downloads_as_attachment(s3: S3Storage):
    k = key("reports", "summary.pdf")
    s3.put_bytes(k, b"%PDF-1.4 test", "application/pdf")
    url = s3.presign_get(k, 60, 'summ"ary.pdf', "application/pdf")
    assert s3.bucket in url and "X-Amz-Signature" in url
    response = httpx.get(url, timeout=10.0)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert response.headers["content-disposition"] == 'attachment; filename="summ_ary.pdf"'
    assert response.headers["content-type"] == "application/pdf"
    tampered = url.replace("summary.pdf", "other.pdf") if "summary.pdf" in url else url + "x"
    assert httpx.get(tampered, timeout=10.0).status_code in (400, 403, 404)


@pytest.mark.s3
def test_copy_preserves_bytes_and_metadata(s3: S3Storage):
    src, dst = key("a.csv"), key("datasets", "d", "v1", "a.csv")
    stored = s3.put_bytes(src, b"x,y\n1,2\n", "text/csv")
    info = s3.copy(src, dst)
    assert info.size == stored.size
    assert s3.get_bytes(dst, 100) == b"x,y\n1,2\n"
    with pytest.raises(ObjectNotFound):
        s3.copy(key("missing"), key("x"))


@pytest.mark.s3
def test_missing_bucket_is_created_lazily_outside_production(s3_endpoint: S3Endpoint):
    storage = S3Storage(
        bucket=f"aegis-lazy-{uuid.uuid4().hex[:10]}",
        endpoint_url=s3_endpoint.url,
        access_key=s3_endpoint.access_key,
        secret_key=s3_endpoint.secret_key,
        auto_create_bucket=True,
    )
    assert not storage.health()
    storage.put_bytes(key("first.txt"), b"1")
    assert storage.health()
    strict = S3Storage(
        bucket=f"aegis-missing-{uuid.uuid4().hex[:10]}",
        endpoint_url=s3_endpoint.url,
        access_key=s3_endpoint.access_key,
        secret_key=s3_endpoint.secret_key,
        auto_create_bucket=False,
    )
    with pytest.raises(StorageUnavailable):
        strict.put_bytes(key("x.txt"), b"1")


def test_unreachable_endpoint_maps_to_service_unavailable():
    storage = S3Storage(
        bucket="aegis-unreachable",
        endpoint_url=f"http://127.0.0.1:{_free_port()}",
        access_key="x",
        secret_key="y",
        connect_timeout=0.5,
        read_timeout=0.5,
        max_attempts=1,
    )
    assert storage.health() is False
    with pytest.raises(StorageUnavailable) as info:
        storage.put_bytes(key("x.txt"), b"1")
    assert info.value.status_code == 503
    assert "y" not in info.value.message.split()  # credentials never leak into errors
    with pytest.raises(StorageUnavailable):
        storage.open_stream(key("x.txt"))
    # Presigning is local (no network) and never embeds the secret key.
    url = storage.presign_get(key("x.txt"), 60, "x.txt", "text/plain")
    assert "X-Amz-Credential=x%2F" in url and "response-content-disposition" in url
