"""Deterministic document-type detection from magic bytes, file extension, content type and a content sniff.

Precedence: strong magic (PDF signature, known binary signatures, NUL bytes, HTML document prologue,
notebook JSON) → file extension → declared content type → content sniff. The declared filename and
content type are client-controlled, so magic bytes always win over them.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Literal

DocumentKind = Literal["pdf", "csv", "tsv", "json", "jsonl", "markdown", "notebook", "html", "text", "unsupported"]
DOCUMENT_KINDS: tuple[str, ...] = (
    "pdf",
    "csv",
    "tsv",
    "json",
    "jsonl",
    "markdown",
    "notebook",
    "html",
    "text",
    "unsupported",
)

#: Number of leading bytes :func:`detect_type` inspects.
DETECT_HEAD_BYTES = 8192

EXTENSION_KINDS: dict[str, DocumentKind] = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdown": "markdown",
    ".ipynb": "notebook",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".txt": "text",
    ".text": "text",
    ".log": "text",
    ".rst": "text",
}
CONTENT_TYPE_KINDS: dict[str, DocumentKind] = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/json": "json",
    "text/json": "json",
    "application/x-ndjson": "jsonl",
    "application/jsonl": "jsonl",
    "application/x-jsonlines": "jsonl",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "application/x-ipynb+json": "notebook",
    "text/html": "html",
    "application/xhtml+xml": "html",
}
UNSUPPORTED_EXTENSIONS = frozenset(
    {
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".zip",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".tar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tiff",
        ".exe",
        ".dll",
        ".so",
        ".bin",
        ".parquet",
        ".npy",
        ".npz",
        ".h5",
        ".hdf5",
        ".pkl",
        ".pickle",
        ".pt",
        ".pth",
        ".onnx",
        ".sqlite",
        ".db",
        ".mp3",
        ".mp4",
        ".wav",
    }
)
BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"PK\x03\x04",
    b"\x1f\x8b",
    b"\x7fELF",
    b"BZh",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"SQLite format 3\x00",
    b"PAR1",
    b"\x89HDF\r\n\x1a\n",
    b"\x93NUMPY",
    b"\xfd7zXZ\x00",
    b"%!PS",
)

_MARKDOWN_HINT = re.compile(
    r"^(?:#{1,6}\s+\S|```|~~~|\s*[-*+]\s+\[[ xX]\]|.*\]\((?:https?://|#|/)[^)]*\))", re.MULTILINE
)
_DELIMITER_KINDS: tuple[tuple[str, DocumentKind], ...] = (("\t", "tsv"), (",", "csv"), (";", "csv"), ("|", "csv"))
_CONTROL_BYTES = frozenset(set(range(0, 9)) | {11, 12} | (set(range(14, 32)) - {27}) | {127})  # ESC allowed (ANSI logs)


def _extension(filename: str | None) -> str:
    if not filename:
        return ""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if "." not in name.strip("."):
        return ""
    return "." + name.rsplit(".", 1)[-1]


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _decode_head(head: bytes) -> str | None:
    """UTF-8 decode tolerating a multi-byte sequence cut off at the end of the head; None if not text."""
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start >= len(head) - 3:
            return head[: exc.start].decode("utf-8", errors="strict")
        return None


def _looks_binary(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return True
    control = sum(1 for byte in head if byte in _CONTROL_BYTES)
    return control / len(head) > 0.05


def _complete_lines(text: str, truncated: bool, limit: int = 20) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if truncated and lines and not text.endswith(("\n", "\r")):
        lines = lines[:-1]
    return lines[:limit]


def _delimited_kind(lines: list[str]) -> DocumentKind | None:
    if len(lines) < 2:
        return None
    for delimiter, kind in _DELIMITER_KINDS:
        try:
            widths = {len(row) for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)}
        except csv.Error:
            continue
        if len(widths) == 1 and widths.pop() >= 2:
            return kind
    return None


def _sniff(head: bytes, truncated: bool) -> DocumentKind:
    text = _decode_head(head)
    if text is None:
        return "unsupported"
    stripped = text.lstrip("﻿ \t\r\n")
    if not stripped:
        return "text"
    if stripped[0] in "{[":
        lines = _complete_lines(stripped, truncated)
        if len(lines) >= 2 and all(line.strip().startswith("{") and line.strip().endswith("}") for line in lines):
            return "jsonl"
        return "json"
    if _MARKDOWN_HINT.search(stripped[:4096]):
        return "markdown"
    delimited = _delimited_kind(_complete_lines(stripped, truncated))
    if delimited:
        return delimited
    return "text"


def detect_type(filename: str | None, content_type: str | None, head: bytes) -> DocumentKind:
    """Detect the document kind of an upload from its name, declared content type and leading bytes."""
    truncated = len(head) > DETECT_HEAD_BYTES
    head = head[:DETECT_HEAD_BYTES]
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if any(head.startswith(signature) for signature in BINARY_SIGNATURES):
        return "unsupported"
    ext = _extension(filename)
    media = _media_type(content_type)
    if ext in UNSUPPORTED_EXTENSIONS or _looks_binary(head):
        return "unsupported"

    lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:2048].lower()
    if lowered.startswith((b"<!doctype html", b"<html")) or (lowered.startswith(b"<?xml") and b"<html" in lowered):
        return "html"
    is_json_object = lowered.startswith(b"{")
    looks_notebook = is_json_object and (b'"nbformat"' in head or (b'"cells"' in head and b'"cell_type"' in head))
    if looks_notebook or (is_json_object and (ext == ".ipynb" or media == "application/x-ipynb+json")):
        return "notebook"

    by_extension = EXTENSION_KINDS.get(ext)
    if by_extension == "pdf" or (by_extension is None and CONTENT_TYPE_KINDS.get(media) == "pdf"):
        return "unsupported"  # declared PDF without the PDF signature
    if by_extension == "notebook":
        return _sniff(head, truncated)
    if by_extension is not None:
        return by_extension
    by_content_type = CONTENT_TYPE_KINDS.get(media)
    if by_content_type is not None and by_content_type != "notebook":
        return by_content_type
    return _sniff(head, truncated)
