"""S3-compatible object storage via boto3 (AWS S3, MinIO, Cloudflare R2, Google Cloud Storage, Azure).

Configuration (``OBJECT_STORAGE_*`` settings):

* **AWS S3** — ``OBJECT_STORAGE_BACKEND=s3``, ``OBJECT_STORAGE_BUCKET``, ``OBJECT_STORAGE_REGION``; leave the
  endpoint unset. Access keys may be omitted to use the default credential chain (IAM role, IRSA).
* **MinIO / Ceph RGW** — ``OBJECT_STORAGE_ENDPOINT=http://minio:9000`` with
  ``OBJECT_STORAGE_FORCE_PATH_STYLE=true`` (the default) and the MinIO access/secret keys.
* **Cloudflare R2** — ``OBJECT_STORAGE_ENDPOINT=https://<account>.r2.cloudflarestorage.com``,
  ``OBJECT_STORAGE_REGION=auto``.
* **Google Cloud Storage** — use the S3 *interoperability* XML API:
  ``OBJECT_STORAGE_ENDPOINT=https://storage.googleapis.com``, ``OBJECT_STORAGE_REGION=auto`` and an HMAC key
  pair (Cloud Console → Cloud Storage → Settings → Interoperability) as access/secret key. Request and
  response checksums are only sent when an operation requires them, which keeps GCS/R2 interop working.
* **Azure Blob Storage** — has no native S3 API; run an S3-compatible gateway in front of it (e.g.
  s3proxy with the ``azureblob`` provider) and point ``OBJECT_STORAGE_ENDPOINT`` at the gateway.

Security: credentials never leave the server. Clients only ever receive short-lived presigned GET URLs
(``presign_get``) that force ``Content-Disposition: attachment`` and a vetted content type. When the
storage endpoint the API uses is not reachable by browsers (e.g. an in-cluster MinIO), set
``OBJECT_STORAGE_PUBLIC_ENDPOINT`` so presigned URLs are signed for the public host.

Botocore errors are mapped onto the platform's typed errors: missing objects → 404 ``object_not_found``;
connectivity/permission/bucket problems → 503 ``storage_unavailable`` (details are logged, never returned).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from typing import Any, BinaryIO

import boto3
import structlog
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from aegis_api.config import Settings, get_settings
from aegis_api.errors import PayloadTooLarge
from aegis_api.lab.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    DEFAULT_MAX_READ_BYTES,
    HashingLimitedReader,
    ObjectNotFound,
    ObjectStat,
    StorageUnavailable,
    StoredObject,
    read_bounded,
)
from aegis_api.lab.storage.keys import validate_key
from aegis_api.lab.storage.signing import content_disposition, safe_content_type

log = structlog.get_logger("aegis.lab.storage")

MAX_PRESIGN_TTL_SECONDS = 7 * 24 * 3600  # SigV4 limit
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NotFound", "404"})
_MISSING_BUCKET_CODES = frozenset({"NoSuchBucket"})


def _error_code(exc: ClientError) -> tuple[str, int | None]:
    error = exc.response.get("Error", {}) or {}
    status = (exc.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode")
    return str(error.get("Code", "")), status


class S3Storage:
    backend = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region: str | None = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        force_path_style: bool = True,
        auto_create_bucket: bool = False,
        public_endpoint_url: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 60.0,
        max_attempts: int = 3,
        multipart_threshold: int = 8 * 1024 * 1024,
        multipart_chunksize: int = 8 * 1024 * 1024,
        max_concurrency: int = 4,
    ) -> None:
        if not bucket:
            raise ValueError("An object storage bucket is required")
        self.bucket = bucket
        self.region = region or "us-east-1"
        self._auto_create_bucket = auto_create_bucket
        self._bucket_ready = not auto_create_bucket
        self._bucket_lock = threading.Lock()
        self._client = self._make_client(
            endpoint_url, access_key, secret_key, force_path_style, connect_timeout, read_timeout, max_attempts
        )
        self._presign_client = (
            self._make_client(
                public_endpoint_url,
                access_key,
                secret_key,
                force_path_style,
                connect_timeout,
                read_timeout,
                max_attempts,
            )
            if public_endpoint_url and public_endpoint_url != endpoint_url
            else self._client
        )
        self._transfer = TransferConfig(
            multipart_threshold=multipart_threshold,
            multipart_chunksize=multipart_chunksize,
            max_concurrency=max_concurrency,
            use_threads=max_concurrency > 1,
        )
        # Non-seekable streams are buffered part by part: bound memory to ≈ concurrency × chunk size.
        self._transfer.max_in_memory_upload_chunks = max(2, max_concurrency)

    def _make_client(
        self,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        force_path_style: bool,
        connect_timeout: float,
        read_timeout: float,
        max_attempts: int,
    ) -> Any:
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "auto"},
            retries={"max_attempts": max_attempts, "mode": "standard"},
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            user_agent_extra="aegis-lab",
        )
        session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=self.region,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=config,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> S3Storage:
        s = settings or get_settings()
        return cls(
            bucket=s.object_storage_bucket,
            endpoint_url=s.object_storage_endpoint,
            region=s.object_storage_region,
            access_key=s.object_storage_access_key,
            secret_key=s.object_storage_secret_key,
            force_path_style=s.object_storage_force_path_style,
            auto_create_bucket=not s.is_production,
            public_endpoint_url=getattr(s, "object_storage_public_endpoint", None),
        )

    # -- error mapping -----------------------------------------------------------------------------
    def _map(self, exc: Exception, operation: str) -> Exception:
        if isinstance(exc, ClientError):
            code, status = _error_code(exc)
            if code in _MISSING_BUCKET_CODES:
                log.error("s3_bucket_missing", operation=operation, bucket=self.bucket)
                return StorageUnavailable("Object storage bucket is not available")
            if code in _NOT_FOUND_CODES or status == 404:
                return ObjectNotFound()
            if code == "EntityTooLarge":
                return PayloadTooLarge("Object exceeds the storage provider's size limit")
            log.error("s3_request_failed", operation=operation, code=code, status=status)
            return StorageUnavailable("Object storage request failed")
        if isinstance(exc, BotoCoreError):
            log.error("s3_unreachable", operation=operation, error=type(exc).__name__)
            return StorageUnavailable("Object storage is unreachable")
        return exc

    # -- bucket ------------------------------------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Create the bucket when it does not exist (used lazily outside production)."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            code, status = _error_code(exc)
            if status != 404 and code not in _MISSING_BUCKET_CODES and code not in _NOT_FOUND_CODES:
                raise self._map(exc, "head_bucket") from exc
        except BotoCoreError as exc:
            raise self._map(exc, "head_bucket") from exc
        params: dict[str, Any] = {"Bucket": self.bucket}
        if self.region not in ("us-east-1", "auto"):
            params["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        try:
            self._client.create_bucket(**params)
            log.info("s3_bucket_created", bucket=self.bucket)
        except ClientError as exc:
            code, _status = _error_code(exc)
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise self._map(exc, "create_bucket") from exc
        except BotoCoreError as exc:
            raise self._map(exc, "create_bucket") from exc

    def _ready(self) -> None:
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if not self._bucket_ready:
                self.ensure_bucket()
                self._bucket_ready = True

    # -- writes ------------------------------------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, content_type: str = DEFAULT_CONTENT_TYPE) -> StoredObject:
        validate_key(key)
        self._ready()
        ctype = safe_content_type(content_type)
        digest = hashlib.sha256(data).hexdigest()
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=ctype, Metadata={"sha256": digest}
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "put_object") from exc
        return StoredObject(key=key, size=len(data), sha256=digest, content_type=ctype)

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        content_type: str = DEFAULT_CONTENT_TYPE,
        max_bytes: int | None = None,
    ) -> StoredObject:
        """Stream ``fileobj`` to S3 (multipart above the threshold) while hashing and enforcing ``max_bytes``.

        When the limit is exceeded the transfer is aborted before completion (s3transfer aborts the
        multipart upload), so no partial object becomes visible.
        """
        validate_key(key)
        self._ready()
        ctype = safe_content_type(content_type)
        reader = HashingLimitedReader(fileobj, max_bytes)
        try:
            self._client.upload_fileobj(
                reader, self.bucket, key, ExtraArgs={"ContentType": ctype}, Config=self._transfer
            )
        except PayloadTooLarge:
            raise
        except Exception as exc:
            if reader.exceeded:
                raise PayloadTooLarge(f"Object exceeds the maximum allowed size of {max_bytes} bytes") from exc
            raise self._map(exc, "upload") from exc
        return StoredObject(key=key, size=reader.size, sha256=reader.sha256, content_type=ctype)

    # -- reads -------------------------------------------------------------------------------------
    def _get_object(self, key: str) -> dict[str, Any]:
        validate_key(key)
        try:
            response: dict[str, Any] = self._client.get_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "get_object") from exc
        return response

    def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        response = self._get_object(key)
        return self._iter_body(response["Body"], max(4096, chunk_size))

    def _iter_body(self, body: Any, chunk_size: int) -> Iterator[bytes]:
        try:
            for chunk in body.iter_chunks(chunk_size):
                if chunk:
                    yield chunk
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "read") from exc
        finally:
            body.close()

    def get_bytes(self, key: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> bytes:
        response = self._get_object(key)
        body = response["Body"]
        length = int(response.get("ContentLength") or 0)
        if length > max_bytes:
            body.close()
            raise PayloadTooLarge(f"Object is {length} bytes, above the {max_bytes} byte read limit")
        return read_bounded(self._iter_body(body, DEFAULT_CHUNK_SIZE), max_bytes)

    def stat(self, key: str) -> ObjectStat:
        validate_key(key)
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "head_object") from exc
        metadata = head.get("Metadata") or {}
        return ObjectStat(
            key=key,
            size=int(head.get("ContentLength") or 0),
            content_type=head.get("ContentType"),
            sha256=metadata.get("sha256"),
            etag=str(head.get("ETag") or "").strip('"') or None,
            last_modified=head.get("LastModified"),
        )

    def exists(self, key: str) -> bool:
        try:
            self.stat(key)
        except ObjectNotFound:
            return False
        return True

    # -- lifecycle ---------------------------------------------------------------------------------
    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            mapped = self._map(exc, "delete_object")
            if isinstance(mapped, ObjectNotFound):
                return
            raise mapped from exc

    def copy(self, source_key: str, dest_key: str) -> ObjectStat:
        """Server-side copy (multipart copy for large objects); metadata is preserved."""
        validate_key(source_key)
        validate_key(dest_key)
        self._ready()
        try:
            self._client.copy({"Bucket": self.bucket, "Key": source_key}, self.bucket, dest_key, Config=self._transfer)
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "copy") from exc
        return self.stat(dest_key)

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        validate_key(key)
        ttl = get_settings().signed_url_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        ttl = max(1, min(ttl, MAX_PRESIGN_TTL_SECONDS))
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentDisposition": content_disposition(filename, fallback=key.rsplit("/", 1)[-1]),
            "ResponseContentType": safe_content_type(content_type),
            "ResponseCacheControl": "private, no-store",
        }
        try:
            url: str = self._presign_client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl, HttpMethod="GET"
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._map(exc, "presign") from exc
        return url

    def health(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except (ClientError, BotoCoreError):
            return False
