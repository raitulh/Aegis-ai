"""Validation for uploaded policy documents. Uploaded files are parsed as data and never executed."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Protocol

from aegis_api.config import get_settings
from aegis_api.errors import PayloadTooLarge, ValidationFailed

ALLOWED_TYPES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/x-markdown", "text/plain"},
}
GENERIC_TYPES = {"application/octet-stream", ""}


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str
    content_type: str
    data: bytes


class MalwareScanner(Protocol):
    """Extension point: plug in ClamAV / a cloud scanning service. Must return a status string."""

    def scan(self, filename: str, data: bytes) -> str: ...


class NoopScanner:
    def scan(self, filename: str, data: bytes) -> str:
        return "not_scanned"


def sanitize_filename(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "document"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = stem[:100]
    return f"{stem}.{ext.lower()}" if ext else stem


def _sniff(extension: str, data: bytes) -> None:
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValidationFailed("File content does not match a PDF document")
    elif extension == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise ValidationFailed("File content does not match a DOCX document")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                    raise ValidationFailed("DOCX archive is missing required parts")
                if any(n.lower().endswith((".exe", ".dll", ".js", ".vbs", ".bin")) for n in names):
                    raise ValidationFailed("DOCX archive contains disallowed embedded content")
                total = sum(i.file_size for i in zf.infolist())
                if total > 50 * 1024 * 1024:
                    raise ValidationFailed("DOCX archive expands beyond the allowed size")
        except zipfile.BadZipFile as exc:
            raise ValidationFailed("DOCX archive is corrupt") from exc
    else:
        if b"\x00" in data:
            raise ValidationFailed("Text documents must not contain binary data")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed("Text documents must be UTF-8 encoded") from exc


def validate_upload(filename: str, content_type: str | None, data: bytes) -> ValidatedUpload:
    settings = get_settings()
    if len(data) == 0:
        raise ValidationFailed("Uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLarge(f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit")
    safe_name = sanitize_filename(filename or "document")
    extension = PurePath(safe_name).suffix.lower()
    if extension not in ALLOWED_TYPES:
        raise ValidationFailed("Unsupported file type. Allowed: PDF, DOCX, TXT, Markdown")
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_TYPES[extension] and declared not in GENERIC_TYPES:
        raise ValidationFailed(f"Declared content type '{declared}' does not match extension {extension}")
    _sniff(extension, data)
    canonical_type = sorted(ALLOWED_TYPES[extension])[0]
    return ValidatedUpload(filename=safe_name, extension=extension, content_type=canonical_type, data=data)
