"""Validation of lab uploads (datasets, artifacts). Uploaded files are parsed as data, never executed.

``validate_lab_upload`` spools the upload to a temporary file while hashing it and enforcing the size limit
(never holding the whole body in memory), then sniffs the content against the declared extension:

* text (``.csv .tsv .txt .md``): strict UTF-8, no NUL bytes;
* ``.json`` / ``.jsonl``: parsed (bounded) — JSON fully up to a limit, JSONL line by line;
* ``.ipynb``: JSON with an integer ``nbformat`` and cells;
* ``.pdf`` (``%PDF-``), ``.parquet`` (``PAR1`` head and tail), ``.npy`` (``\\x93NUMPY`` + a safe header —
  object/pickled dtypes are refused), ``.png``/``.jpg`` (magic);
* ``.zip``/``.npz`` and ``.tar.gz``/``.tgz``: at most 5000 entries, no absolute paths, ``..`` segments,
  symlinks, hard links or device files, total uncompressed size ≤ 1 GiB and compression ratio ≤ 100
  (zip-bomb defence), no encrypted members, no executables inside.

Executables and scripts are refused by magic (PE/``MZ``, ELF, Mach-O, and ``#!`` for anything but plain
text), whatever the extension says. The declared content type must belong to the extension's family or
be generic (``application/octet-stream``).
"""

from __future__ import annotations

import ast
import codecs
import contextlib
import csv
import gzip
import hashlib
import io
import json
import re
import stat
import struct
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, BinaryIO, cast

from aegis_api.config import get_settings
from aegis_api.errors import PayloadTooLarge, ValidationFailed
from aegis_api.security.uploads import sanitize_filename

CHUNK = 1024 * 1024
SPOOL_MEMORY_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
MAX_ARCHIVE_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
RATIO_CHECK_MIN_BYTES = 1024 * 1024
JSON_PARSE_LIMIT = 32 * 1024 * 1024
NOTEBOOK_PARSE_LIMIT = 64 * 1024 * 1024
JSONL_PARSE_LINES = 10_000
JSONL_MAX_LINE_BYTES = 16 * 1024 * 1024
NPY_MAX_HEADER_BYTES = 64 * 1024
GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream", "application/unknown"})
BLOCKED_ARCHIVE_EXTENSIONS = frozenset(
    {".exe", ".dll", ".scr", ".msi", ".com", ".bat", ".cmd", ".vbs", ".vbe", ".wsf", ".ps1", ".jar", ".apk", ".lnk"}
)


class UnsupportedFileType(ValidationFailed):
    """The file type is not accepted."""

    code = "unsupported_file_type"


class InvalidFileContent(ValidationFailed):
    """The file content does not match its declared type or failed safety checks."""

    code = "invalid_file_content"


@dataclass(frozen=True)
class FileType:
    kind: str
    canonical_type: str
    content_types: frozenset[str]
    plain_text: bool = False  # ``#!`` is only tolerated for plain text / markdown


def _ft(kind: str, canonical: str, *extra: str, plain_text: bool = False) -> FileType:
    return FileType(kind, canonical, frozenset({canonical, *extra}), plain_text)


FILE_TYPES: dict[str, FileType] = {
    ".csv": _ft("csv", "text/csv", "application/csv", "text/x-csv", "application/vnd.ms-excel", "text/plain"),
    ".tsv": _ft("tsv", "text/tab-separated-values", "text/tsv", "text/plain"),
    ".txt": _ft("text", "text/plain", plain_text=True),
    ".md": _ft("markdown", "text/markdown", "text/x-markdown", "text/plain", plain_text=True),
    ".json": _ft("json", "application/json", "text/json", "text/plain"),
    ".jsonl": _ft(
        "jsonl",
        "application/x-ndjson",
        "application/jsonl",
        "application/x-jsonlines",
        "application/json-lines",
        "application/json",
        "text/plain",
    ),
    ".ipynb": _ft("notebook", "application/x-ipynb+json", "application/json"),
    ".pdf": _ft("pdf", "application/pdf", "application/x-pdf"),
    ".parquet": _ft("parquet", "application/vnd.apache.parquet", "application/x-parquet"),
    ".npy": _ft("npy", "application/x-npy", "application/npy"),
    ".npz": _ft("npz", "application/x-npz", "application/zip", "application/x-zip-compressed"),
    ".zip": _ft("zip", "application/zip", "application/x-zip-compressed", "application/x-zip", "multipart/x-zip"),
    ".tar.gz": _ft(
        "tar_gz", "application/gzip", "application/x-gzip", "application/x-tar", "application/x-compressed-tar"
    ),
    ".tgz": _ft(
        "tar_gz", "application/gzip", "application/x-gzip", "application/x-tar", "application/x-compressed-tar"
    ),
    ".png": _ft("png", "image/png"),
    ".jpg": _ft("jpeg", "image/jpeg", "image/pjpeg"),
    ".jpeg": _ft("jpeg", "image/jpeg", "image/pjpeg"),
}
ALL_KINDS = frozenset(ft.kind for ft in FILE_TYPES.values())
TEXT_KINDS = frozenset({"csv", "tsv", "text", "markdown"})
DATASET_KINDS = frozenset({"csv", "tsv", "json", "jsonl", "parquet", "npy", "npz", "zip", "tar_gz", "text"})


@dataclass
class ValidatedLabUpload:
    """A validated upload. ``file`` is positioned at 0; call :meth:`close` (or use ``with``) when done."""

    filename: str
    extension: str
    content_type: str
    size: int
    sha256: str
    kind: str
    file: BinaryIO = field(repr=False)
    owns_file: bool = field(default=True, repr=False)

    def open(self) -> BinaryIO:
        self.file.seek(0)
        return self.file

    def close(self) -> None:
        if self.owns_file:
            with contextlib.suppress(Exception):
                self.file.close()

    def __enter__(self) -> ValidatedLabUpload:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def detect_extension(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".tar.gz"):
        return ".tar.gz"
    return PurePosixPath(lower).suffix


def normalize_content_type(value: str | None) -> str:
    return (value or "").split(";")[0].strip().lower()


def file_type_for(filename: str) -> FileType | None:
    return FILE_TYPES.get(detect_extension(filename))


# -- spooling ---------------------------------------------------------------------------------------
def _is_seekable(fileobj: Any) -> bool:
    try:
        return bool(fileobj.seekable())
    except (AttributeError, ValueError, OSError):
        return False


def _spool(fileobj: BinaryIO, limit: int) -> tuple[BinaryIO, bool, int, str]:
    """Hash + size the input without loading it in memory. Seekable inputs are used in place."""
    digest = hashlib.sha256()
    size = 0
    if _is_seekable(fileobj):
        fileobj.seek(0)
        while chunk := fileobj.read(CHUNK):
            size += len(chunk)
            if size > limit:
                raise PayloadTooLarge(f"File exceeds the {limit} byte upload limit")
            digest.update(chunk)
        fileobj.seek(0)
        return fileobj, False, size, digest.hexdigest()
    spool = cast(BinaryIO, tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES, mode="w+b"))  # noqa: SIM115
    try:
        while chunk := fileobj.read(CHUNK):
            size += len(chunk)
            if size > limit:
                raise PayloadTooLarge(f"File exceeds the {limit} byte upload limit")
            digest.update(chunk)
            spool.write(chunk)
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return spool, True, size, digest.hexdigest()


# -- generic checks ---------------------------------------------------------------------------------
_MACHO_MAGICS = (
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)


def looks_executable(head: bytes) -> bool:
    """PE/DOS, ELF or Mach-O binaries (also Java class files, which share the fat Mach-O magic)."""
    if head.startswith(b"\x7fELF") or head.startswith(_MACHO_MAGICS):
        return True
    return head.startswith(b"MZ") and len(head) >= 64 and b"\x00" in head[:64]  # DOS header is 64 bytes


def _head(file: BinaryIO, n: int) -> bytes:
    file.seek(0)
    data = file.read(n)
    file.seek(0)
    return data


def _tail(file: BinaryIO, size: int, n: int) -> bytes:
    file.seek(max(0, size - n))
    data = file.read(n)
    file.seek(0)
    return data


def _check_utf8_text(file: BinaryIO) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    file.seek(0)
    try:
        while chunk := file.read(CHUNK):
            if b"\x00" in chunk:
                raise InvalidFileContent("Text files must not contain binary (NUL) data")
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise InvalidFileContent("Text files must be UTF-8 encoded") from exc
    finally:
        file.seek(0)


def _text_stream(file: BinaryIO) -> io.TextIOWrapper:
    file.seek(0)
    return io.TextIOWrapper(file, encoding="utf-8-sig", errors="strict", newline="")


def _detached(wrapper: io.TextIOWrapper, file: BinaryIO) -> None:
    with contextlib.suppress(ValueError):
        wrapper.detach()
    file.seek(0)


def _check_delimited(file: BinaryIO, delimiter: str) -> None:
    wrapper = _text_stream(file)
    try:
        reader = csv.reader(wrapper, delimiter=delimiter)
        header = next(reader, None)
        if not header or not any(cell.strip() for cell in header):
            raise InvalidFileContent("Delimited files must start with a non-empty header row")
    except csv.Error as exc:
        raise InvalidFileContent("File is not a valid delimited text file") from exc
    finally:
        _detached(wrapper, file)


def _check_json(file: BinaryIO, size: int) -> None:
    _check_utf8_text(file)
    if size <= JSON_PARSE_LIMIT:
        wrapper = _text_stream(file)
        try:
            json.load(wrapper)
        except (ValueError, RecursionError) as exc:
            raise InvalidFileContent("File is not valid JSON") from exc
        finally:
            _detached(wrapper, file)
        return
    # Too large to parse in the request path: verify it is structurally a JSON document.
    first = _head(file, 4096).lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    last = _tail(file, size, 4096).rstrip(b" \t\r\n")[-1:]
    if (first, last) not in ((b"{", b"}"), (b"[", b"]")):
        raise InvalidFileContent("File is not a JSON object or array")


def _check_jsonl(file: BinaryIO) -> None:
    _check_utf8_text(file)
    file.seek(0)
    try:
        for index in range(JSONL_PARSE_LINES):
            line = file.readline(JSONL_MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > JSONL_MAX_LINE_BYTES:
                raise InvalidFileContent("A JSON Lines record exceeds the maximum line length")
            text = line.decode("utf-8-sig" if index == 0 else "utf-8").strip()
            if not text:
                continue
            try:
                json.loads(text)
            except (ValueError, RecursionError) as exc:
                raise InvalidFileContent(f"Line {index + 1} is not valid JSON") from exc
    finally:
        file.seek(0)


def _check_notebook(file: BinaryIO, size: int) -> None:
    if size > NOTEBOOK_PARSE_LIMIT:
        raise InvalidFileContent("Notebook is too large to validate")
    _check_utf8_text(file)
    wrapper = _text_stream(file)
    try:
        notebook = json.load(wrapper)
    except (ValueError, RecursionError) as exc:
        raise InvalidFileContent("Notebook is not valid JSON") from exc
    finally:
        _detached(wrapper, file)
    if not isinstance(notebook, dict) or not isinstance(notebook.get("nbformat"), int) or notebook["nbformat"] < 3:
        raise InvalidFileContent("Notebook is missing a valid 'nbformat'")
    if not isinstance(notebook.get("cells", notebook.get("worksheets")), list):
        raise InvalidFileContent("Notebook has no cells")


def parse_npy_header(head: bytes) -> dict[str, Any]:
    """Parse and validate a ``.npy`` header without numpy. Object (pickled) dtypes are refused."""
    if not head.startswith(b"\x93NUMPY") or len(head) < 10:
        raise InvalidFileContent("File content does not match a NumPy .npy array")
    major = head[6]
    if major == 1:
        header_len = struct.unpack("<H", head[8:10])[0]
        start = 10
    elif major in (2, 3):
        if len(head) < 12:
            raise InvalidFileContent("Truncated .npy header")
        header_len = struct.unpack("<I", head[8:12])[0]
        start = 12
    else:
        raise InvalidFileContent("Unsupported .npy format version")
    if header_len > NPY_MAX_HEADER_BYTES or len(head) < start + header_len:
        raise InvalidFileContent("Invalid .npy header length")
    raw = head[start : start + header_len].decode("latin-1").strip()
    try:
        header = ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError) as exc:
        raise InvalidFileContent("Invalid .npy header") from exc
    if not isinstance(header, dict) or {"descr", "fortran_order", "shape"} - set(header):
        raise InvalidFileContent("Invalid .npy header")
    shape = header["shape"]
    if not isinstance(shape, tuple) or not all(isinstance(d, int) and d >= 0 for d in shape):
        raise InvalidFileContent("Invalid .npy shape")
    if "O" in repr(header["descr"]):
        raise InvalidFileContent("NumPy object arrays (pickled data) are not accepted")
    return header


def _check_member_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    if not normalized or len(normalized) > 1024 or "\x00" in normalized:
        raise InvalidFileContent("Archive contains an invalid entry name")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise InvalidFileContent("Archive entries must not use absolute paths")
    parts = normalized.rstrip("/").split("/")
    if any(part == ".." for part in parts):
        raise InvalidFileContent("Archive entries must not contain '..' path segments")
    if PurePosixPath(parts[-1].lower()).suffix in BLOCKED_ARCHIVE_EXTENSIONS:
        raise InvalidFileContent("Archive contains executable content")


def _check_ratio(total: int, compressed: int) -> None:
    if total > RATIO_CHECK_MIN_BYTES and total / max(compressed, 1) > MAX_COMPRESSION_RATIO:
        raise InvalidFileContent("Archive compression ratio is too high (possible zip bomb)")


def _check_zip(file: BinaryIO, size: int, *, npz: bool) -> None:
    if not _head(file, 4).startswith(b"PK\x03\x04"):
        raise InvalidFileContent("File content does not match a ZIP archive")
    try:
        with zipfile.ZipFile(file) as archive:
            infos = archive.infolist()
            if not infos:
                raise InvalidFileContent("Archive is empty")
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise InvalidFileContent(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries")
            total = 0
            for info in infos:
                _check_member_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise InvalidFileContent("Archive must not contain symbolic links")
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise InvalidFileContent("Archive must contain only regular files and directories")
                if info.flag_bits & 0x1:
                    raise InvalidFileContent("Encrypted archive entries are not accepted")
                total += info.file_size
                if total > MAX_ARCHIVE_UNCOMPRESSED:
                    raise InvalidFileContent("Archive expands beyond the 1 GiB limit")
                if (
                    info.file_size > RATIO_CHECK_MIN_BYTES
                    and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO
                ):
                    raise InvalidFileContent("Archive entry compression ratio is too high (possible zip bomb)")
            _check_ratio(total, size)
            for info in infos:
                if info.is_dir():
                    if npz:
                        raise InvalidFileContent("NumPy .npz archives must contain only .npy arrays")
                    continue
                if npz and not info.filename.lower().endswith(".npy"):
                    raise InvalidFileContent("NumPy .npz archives must contain only .npy arrays")
                with archive.open(info) as member:
                    head = member.read(NPY_MAX_HEADER_BYTES + 12 if npz else 64)
                if looks_executable(head):
                    raise InvalidFileContent("Archive contains executable content")
                if npz:
                    parse_npy_header(head)
    except zipfile.BadZipFile as exc:
        raise InvalidFileContent("ZIP archive is corrupt") from exc
    finally:
        file.seek(0)


class _BoundedReader(io.RawIOBase):
    """Caps the number of decompressed bytes tarfile may pull through (gzip-bomb defence)."""

    def __init__(self, inner: BinaryIO, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer)
        data = self._inner.read(len(view))
        self.consumed += len(data)
        if self.consumed > self._limit:
            raise InvalidFileContent("Archive expands beyond the 1 GiB limit")
        view[: len(data)] = data
        return len(data)


def _check_tar_gz(file: BinaryIO, size: int) -> None:
    if not _head(file, 2).startswith(b"\x1f\x8b"):
        raise InvalidFileContent("File content does not match a gzip-compressed tar archive")
    file.seek(0)
    gz = gzip.GzipFile(fileobj=file, mode="rb")
    bounded = io.BufferedReader(_BoundedReader(gz, MAX_ARCHIVE_UNCOMPRESSED + 64 * 1024 * 1024))  # type: ignore[arg-type]
    try:
        with tarfile.open(fileobj=bounded, mode="r|") as archive:
            count = 0
            total = 0
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_ENTRIES:
                    raise InvalidFileContent(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries")
                _check_member_name(member.name)
                if not (member.isreg() or member.isdir()):
                    raise InvalidFileContent("Archive must contain only regular files and directories")
                total += member.size
                if total > MAX_ARCHIVE_UNCOMPRESSED:
                    raise InvalidFileContent("Archive expands beyond the 1 GiB limit")
                if member.isreg() and member.size:
                    extracted = archive.extractfile(member)
                    if extracted is not None and looks_executable(extracted.read(64)):
                        raise InvalidFileContent("Archive contains executable content")
            if count == 0:
                raise InvalidFileContent("Archive is empty")
            _check_ratio(total, size)
    except (tarfile.TarError, EOFError, OSError, gzip.BadGzipFile) as exc:
        raise InvalidFileContent("tar.gz archive is corrupt") from exc
    finally:
        file.seek(0)


def _sniff(ftype: FileType, file: BinaryIO, size: int) -> None:
    head = _head(file, 512)
    if looks_executable(head):
        raise InvalidFileContent("Executable content is not accepted")
    if head.startswith(b"#!") and not ftype.plain_text:
        raise InvalidFileContent("Scripts are not accepted")
    kind = ftype.kind
    if kind in TEXT_KINDS:
        _check_utf8_text(file)
        if kind in ("csv", "tsv"):
            _check_delimited(file, "," if kind == "csv" else "\t")
    elif kind == "json":
        _check_json(file, size)
    elif kind == "jsonl":
        _check_jsonl(file)
    elif kind == "notebook":
        _check_notebook(file, size)
    elif kind == "pdf":
        if not head.startswith(b"%PDF-"):
            raise InvalidFileContent("File content does not match a PDF document")
    elif kind == "parquet":
        if size < 12 or not head.startswith(b"PAR1") or _tail(file, size, 4) != b"PAR1":
            raise InvalidFileContent("File content does not match a Parquet file")
    elif kind == "npy":
        parse_npy_header(_head(file, NPY_MAX_HEADER_BYTES + 12))
    elif kind in ("zip", "npz"):
        _check_zip(file, size, npz=kind == "npz")
    elif kind == "tar_gz":
        _check_tar_gz(file, size)
    elif kind == "png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidFileContent("File content does not match a PNG image")
    elif kind == "jpeg":
        if not head.startswith(b"\xff\xd8\xff"):
            raise InvalidFileContent("File content does not match a JPEG image")
    else:  # pragma: no cover - FILE_TYPES and this dispatch are kept in sync
        raise UnsupportedFileType()


def validate_lab_upload(
    filename: str | None,
    declared_content_type: str | None,
    fileobj: BinaryIO,
    *,
    max_bytes: int | None = None,
    allowed_kinds: frozenset[str] | set[str] | None = None,
) -> ValidatedLabUpload:
    """Validate an uploaded file; return it spooled, hashed and typed. Raises 413/422 on rejection."""
    limit = get_settings().max_lab_upload_bytes if max_bytes is None else max_bytes
    safe_name = sanitize_filename(filename or "upload")
    extension = detect_extension(safe_name)
    ftype = FILE_TYPES.get(extension)
    if ftype is None:
        allowed = ", ".join(sorted(FILE_TYPES))
        raise UnsupportedFileType(f"Unsupported file type '{extension or '(none)'}'. Allowed: {allowed}")
    if allowed_kinds is not None and ftype.kind not in allowed_kinds:
        raise UnsupportedFileType(f"Files of type '{extension}' are not accepted here")
    declared = normalize_content_type(declared_content_type)
    if declared not in GENERIC_CONTENT_TYPES and declared not in ftype.content_types:
        raise InvalidFileContent(f"Declared content type '{declared[:80]}' does not match extension {extension}")
    file, owned, size, digest = _spool(fileobj, limit)
    try:
        if size == 0:
            raise ValidationFailed("Uploaded file is empty")
        try:
            _sniff(ftype, file, size)
        except (ValidationFailed, PayloadTooLarge):
            raise
        except Exception as exc:  # malformed input must never surface as a 500
            raise InvalidFileContent("File could not be validated") from exc
        file.seek(0)
    except BaseException:
        if owned:
            file.close()
        raise
    return ValidatedLabUpload(
        filename=safe_name,
        extension=extension,
        content_type=ftype.canonical_type,
        size=size,
        sha256=digest,
        kind=ftype.kind,
        file=file,
        owns_file=owned,
    )
