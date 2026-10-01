"""S3-compatible storage adapter (AWS S3, Cloudflare R2, MinIO, Backblaze B2).

Requires the optional `boto3` dependency: `pip install databattles-api[s3]`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import datetime
from typing import Any, BinaryIO

from app.storage.base import StoredObject, copy_with_limits


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, *, endpoint_url: str | None, region: str | None,
                 access_key: str | None, secret_key: str | None) -> None:
        import boto3  # optional dependency

        self.bucket = bucket
        self.client: Any = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        )

    def save_stream(self, key: str, stream: BinaryIO, *, max_bytes: int, content_type: str) -> StoredObject:
        # Spool locally first so size limits and hashing happen before anything is uploaded.
        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as spool:
            size, sha, head = copy_with_limits(stream, spool, max_bytes)
            spool.seek(0)
            self.client.upload_fileobj(spool, self.bucket, key, ExtraArgs={"ContentType": content_type,
                                                                           "Metadata": {"sha256": sha}})
        return StoredObject(key=key, size_bytes=size, sha256=sha, head=head)

    def open(self, key: str) -> BinaryIO:
        tmp = tempfile.TemporaryFile()  # noqa: SIM115 — caller owns the handle
        self.client.download_fileobj(self.bucket, key, tmp)
        tmp.seek(0)
        return tmp  # type: ignore[return-value]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def presigned_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                    "ResponseContentType": content_type},
            ExpiresIn=ttl_seconds,
        )

    def list_keys(self, prefix: str) -> Iterator[tuple[str, datetime]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["LastModified"]

    def local_path(self, key: str) -> str | None:
        return None
