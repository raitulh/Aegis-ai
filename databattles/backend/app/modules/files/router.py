"""Signed file delivery. Authorization happens when the URL is issued; the token proves it."""

from __future__ import annotations

import mimetypes
import re

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.core.errors import NotFound
from app.storage import get_storage, read_download_token
from app.storage.base import PREFIX_MEDIA, PRIVATE_PREFIX

router = APIRouter(prefix="/files", tags=["files"])

_MEDIA_KEY_RE = re.compile(r"^media/\d{4}/\d{2}/[0-9a-f]{32}\.(png|jpg|jpeg|webp)$")
_SAFE_DISPOSITION_RE = re.compile(r"[^A-Za-z0-9._ -]")


def _disposition(filename: str, inline: bool) -> str:
    safe = _SAFE_DISPOSITION_RE.sub("_", filename)[:150] or "download"
    return f'{"inline" if inline else "attachment"}; filename="{safe}"'


@router.get("/download")
def download(token: str = Query(..., max_length=2000)):  # noqa: ANN201
    payload = read_download_token(token)
    if payload is None:
        raise NotFound("This download link is invalid or has expired.", code="link_expired")
    key: str = payload["k"]
    if key.startswith(PRIVATE_PREFIX + "/"):
        raise NotFound()
    storage = get_storage()
    headers = {"Content-Disposition": _disposition(payload.get("f", "download"), bool(payload.get("i"))),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=0, no-store"}
    # Never let user-controlled content render as HTML in our origin.
    media_type = payload.get("t") or "application/octet-stream"
    if media_type.startswith(("text/html", "image/svg")) or "javascript" in media_type:
        media_type = "application/octet-stream"
    local = storage.local_path(key)
    if local:
        try:
            return FileResponse(local, media_type=media_type, headers=headers)
        except FileNotFoundError:
            raise NotFound() from None
    fh = storage.open(key)
    return StreamingResponse(iter(lambda: fh.read(1024 * 1024), b""), media_type=media_type, headers=headers)


@router.get("/media/{path:path}")
def media(path: str):  # noqa: ANN201
    key = f"{PREFIX_MEDIA}/{path}"
    if not _MEDIA_KEY_RE.match(key):
        raise NotFound()
    storage = get_storage()
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'"}
    local = storage.local_path(key)
    if local:
        import os

        if not os.path.exists(local):
            raise NotFound()
        return FileResponse(local, media_type=media_type, headers=headers)
    fh = storage.open(key)
    return StreamingResponse(iter(lambda: fh.read(1024 * 1024), b""), media_type=media_type, headers=headers)
