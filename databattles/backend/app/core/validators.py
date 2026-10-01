"""Input validators shared across modules: URLs, file names, uploads, images."""

from __future__ import annotations

import io
import ipaddress
import posixpath
import re
import unicodedata
import zipfile
from urllib.parse import urlsplit

from app.core.errors import ValidationFailed

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")
_ALLOWED_DATASET_EXT = {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".txt", ".md", ".zip", ".ipynb", ".png", ".jpg", ".jpeg"}
_EXECUTABLE_EXT = {".exe", ".bat", ".cmd", ".sh", ".ps1", ".dll", ".so", ".dylib", ".js", ".html", ".htm", ".svg", ".php", ".jar"}


def validate_external_url(url: str | None, field: str = "url") -> str | None:
    """Accept only absolute http(s) URLs without credentials. Prevents javascript:/data: links."""
    if url is None or url.strip() == "":
        return None
    url = url.strip()
    if len(url) > 500:
        raise ValidationFailed(details={"fields": {field: "URL is too long."}})
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValidationFailed(details={"fields": {field: "Use a full http(s):// URL."}})
    if parts.username or parts.password:
        raise ValidationFailed(details={"fields": {field: "URLs must not contain credentials."}})
    if any(ch.isspace() for ch in url):
        raise ValidationFailed(details={"fields": {field: "URLs must not contain spaces."}})
    return url


def is_public_hostname(host: str) -> bool:
    """SSRF guard for any feature that fetches user-provided URLs server-side."""
    host = host.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


def safe_filename(name: str, *, max_length: int = 150) -> str:
    """Normalize a client-supplied filename; rejects traversal and control characters."""
    if not name:
        raise ValidationFailed("A file name is required.", code="invalid_file")
    name = unicodedata.normalize("NFKC", name)
    if "/" in name or "\\" in name or "\x00" in name or name in {".", ".."} or name.startswith("."):
        raise ValidationFailed("File names must not contain paths or start with a dot.", code="invalid_file")
    base = posixpath.basename(name)
    cleaned = _SAFE_NAME_RE.sub("_", base).strip(" .")
    if not cleaned:
        raise ValidationFailed("File name is not valid.", code="invalid_file")
    if len(cleaned) > max_length:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (stem[: max_length - len(ext) - 1] + "." + ext) if dot else cleaned[:max_length]
    return cleaned


def extension(name: str) -> str:
    return posixpath.splitext(name.lower())[1]


def check_dataset_extension(name: str) -> None:
    ext = extension(name)
    if ext in _EXECUTABLE_EXT or ext not in _ALLOWED_DATASET_EXT:
        raise ValidationFailed(
            f"Files of type '{ext or 'unknown'}' are not allowed. Allowed: {', '.join(sorted(_ALLOWED_DATASET_EXT))}.",
            code="invalid_file_type",
        )


MAX_ZIP_UNCOMPRESSED = 4 * 1024 * 1024 * 1024  # 4 GiB declared total
MAX_ZIP_RATIO = 200  # compression ratio above which an entry is treated as a zip bomb


def validate_zip_archive(data_head: bytes | None = None, path: str | None = None, *, max_entries: int = 5000) -> None:
    """Reject zip-slip paths, too many entries, and zip bombs (huge declared size or extreme compression ratio).

    Archives are never extracted server-side; these checks protect anyone who downloads and extracts them
    and any future processing step."""
    source = path if path is not None else io.BytesIO(data_head or b"")
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ValidationFailed("Archive contains too many files.", code="invalid_archive")
            total = sum(i.file_size for i in infos)
            if total > MAX_ZIP_UNCOMPRESSED:
                raise ValidationFailed("Archive expands to more than the allowed size.", code="invalid_archive")
            for info in infos:
                if info.file_size > 1024 * 1024 and info.file_size > MAX_ZIP_RATIO * max(info.compress_size, 1):
                    raise ValidationFailed("Archive looks like a compression bomb.", code="invalid_archive")
                entry = info.filename.replace("\\", "/")
                normalized = posixpath.normpath(entry)
                if entry.startswith("/") or normalized.startswith("..") or "/../" in f"/{entry}/" or re.match(r"^[A-Za-z]:", entry):
                    raise ValidationFailed("Archive contains unsafe paths.", code="invalid_archive")
    except zipfile.BadZipFile:
        raise ValidationFailed("The archive is corrupted or not a zip file.", code="invalid_archive")


def looks_like_text(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        # Might be cut mid-character at the sample boundary.
        try:
            sample[:-3].decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False


def clean_tags(tags: list[str] | None, *, limit: int = 12) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        t = re.sub(r"[^a-z0-9+#.\- ]", "", tag.strip().lower())[:48].strip()
        if t and t not in out:
            out.append(t)
    return out[:limit]
