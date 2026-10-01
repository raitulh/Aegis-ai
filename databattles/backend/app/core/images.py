"""Image upload validation: decode with Pillow, enforce limits, strip metadata, re-encode.

Re-encoding guarantees the stored bytes are a plain raster image (no polyglots, no EXIF/GPS, no SVG).
"""

from __future__ import annotations

import io
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import PayloadTooLarge, ValidationFailed
from app.storage import get_storage, media_url
from app.storage.base import PREFIX_MEDIA, new_key

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
_ALLOWED = {"PNG", "JPEG", "WEBP"}


def store_image(stream: BinaryIO, *, max_side: int = 2048, square: bool = False) -> tuple[str, str, int, int]:
    raw = stream.read(settings.MAX_IMAGE_MB * 1024 * 1024 + 1)
    if len(raw) > settings.MAX_IMAGE_MB * 1024 * 1024:
        raise PayloadTooLarge(f"Images must be under {settings.MAX_IMAGE_MB} MB.", code="file_too_large")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in _ALLOWED:
            raise ValidationFailed("Upload a PNG, JPEG or WebP image.", code="invalid_image")
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationFailed("That file is not a valid image.", code="invalid_image") from None
    if square:
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    img.thumbnail((max_side, max_side))
    img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=88, method=4)
    out.seek(0)
    key = new_key(PREFIX_MEDIA, ".webp")
    get_storage().save_stream(key, out, max_bytes=settings.MAX_IMAGE_MB * 1024 * 1024, content_type="image/webp")
    return key, media_url(key) or "", img.width, img.height
