"""Storage factory and signed download URLs."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from app.core.config import settings
from app.core.security import make_signed_payload, read_signed_payload
from app.storage.base import PREFIX_MEDIA as MEDIA_PREFIX
from app.storage.base import PRIVATE_PREFIX, StorageBackend

DOWNLOAD_PURPOSE = "download"


@lru_cache
def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "s3":
        from app.storage.s3 import S3Storage

        assert settings.S3_BUCKET
        return S3Storage(settings.S3_BUCKET, endpoint_url=settings.S3_ENDPOINT_URL, region=settings.S3_REGION,
                         access_key=settings.S3_ACCESS_KEY_ID, secret_key=settings.S3_SECRET_ACCESS_KEY)
    from app.storage.local import LocalStorage

    return LocalStorage(settings.STORAGE_LOCAL_ROOT)


def signed_download_url(key: str, filename: str, content_type: str, *, inline: bool = False) -> str:
    """Return a short-lived URL for an object the caller has already been authorized to read."""
    if key.startswith(PRIVATE_PREFIX + "/"):
        raise PermissionError("private objects are never downloadable")
    storage = get_storage()
    url = storage.presigned_url(key, filename=filename, content_type=content_type, ttl_seconds=settings.SIGNED_URL_TTL_SECONDS)
    if url:
        return url
    token = make_signed_payload({"k": key, "f": filename, "t": content_type, "i": int(inline)}, DOWNLOAD_PURPOSE,
                                settings.SIGNED_URL_TTL_SECONDS)
    return f"/api/v1/files/download?token={quote(token)}"


def media_url(key: str | None) -> str | None:
    """Public media (logos, screenshots, avatars). Keys are random and images are re-encoded on upload."""
    if not key:
        return None
    if not key.startswith(MEDIA_PREFIX + "/"):
        return None
    return f"/api/v1/files/{key}"


def read_download_token(token: str) -> dict | None:
    return read_signed_payload(token, DOWNLOAD_PURPOSE)
