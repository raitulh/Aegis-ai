"""Bounded, deterministic document parsers: bytes → :class:`ParsedDocument` (text + sections + metadata).

Every parser treats its input as untrusted: output text is stripped of NULs, control characters, ANSI
escapes, bidi overrides and zero-width characters; sizes are capped (input bytes, output characters,
PDF pages, CSV rows scanned, JSON nodes visited, notebook cells); corrupt input produces warnings and a
best-effort (possibly empty) document instead of an exception. Script-like content (HTML ``<script>``,
notebook rich outputs, base64 images) is dropped, never rendered.
"""

from __future__ import annotations

import csv
import html
import io
import json
import math
import re
from collections import Counter
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field

from engines.lab.ingestion.detect import DOCUMENT_KINDS

INGESTION_PARSER_VERSION = "ingestion-parsers-1.0.0"

DEFAULT_MAX_CHARS = 1_000_000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 2000
CSV_PREVIEW_ROWS = 20
CSV_STATS_ROWS = 1000
CSV_MAX_COUNTED_ROWS = 2_000_000
CSV_MAX_COLUMNS = 200
CSV_FIELD_LIMIT = 1_000_000
MAX_CELL_CHARS = 80
MAX_TRACKED_DISTINCT = 1000
JSON_MAX_KEY_PATHS = 200
JSON_MAX_NODES = 100_000
JSON_MAX_DEPTH = 32
JSONL_MAX_RECORDS = 1_000_000
JSONL_SUMMARY_RECORDS = 1000
JSONL_PREVIEW_RECORDS = 20
NOTEBOOK_MAX_CELLS = 5000
NOTEBOOK_MAX_OUTPUT_CHARS = 2000
MAX_LINKS = 200
MAX_HEADING_CHARS = 300


class UnsupportedDocumentError(ValueError):
    """Raised when :func:`parse` is asked to parse a kind it does not support."""


class Section(BaseModel):
    heading: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    level: int = Field(default=1, ge=1, le=6)


class ParsedDocument(BaseModel):
    kind: str
    text: str
    title: str | None = None
    sections: list[Section] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


# =============================================================================================
# Text hygiene
# =============================================================================================
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​‎‏‪-‮⁠⁦-⁩﻿]")
_INLINE_WS_RE = re.compile(r"[ \t ]+")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalize newlines and strip ANSI escapes, NULs, control, bidi-override and zero-width characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", text)


def _collapse(text: str) -> str:
    """Collapse runs of inline whitespace and excess blank lines (used for HTML/markup-derived text)."""
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _MANY_NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _decode(data: bytes) -> tuple[str, bool]:
    """UTF-8 decode (BOM stripped); returns ``(text, had_invalid_bytes)``."""
    try:
        return data.decode("utf-8-sig"), False
    except UnicodeDecodeError:
        return data.decode("utf-8-sig", errors="replace"), True


def _short(value: str, limit: int = MAX_CELL_CHARS) -> str:
    value = _INLINE_WS_RE.sub(" ", clean_text(value).replace("\n", " ")).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


class _TextBuilder:
    """Accumulates cleaned text blocks, records section starts and enforces ``max_chars``."""

    def __init__(self, max_chars: int) -> None:
        self._parts: list[str] = []
        self._length = 0
        self._max = max_chars
        self._heads: list[tuple[str, int, int]] = []
        self.truncated = False

    @property
    def length(self) -> int:
        return self._length

    def add(self, text: str, *, sep: str = "\n\n") -> int | None:
        """Append a block; returns the offset where its content starts (None if nothing was added)."""
        if self.truncated:
            return None
        block = clean_text(text).strip("\n")
        if not block.strip():
            return None
        prefix = sep if self._length else ""
        room = self._max - self._length - len(prefix)
        if room <= 0:
            self.truncated = True
            return None
        if len(block) > room:
            block = block[:room]
            self.truncated = True
        start = self._length + len(prefix)
        self._parts.append(prefix + block)
        self._length = start + len(block)
        return start

    def add_section(self, heading: str, level: int, text: str) -> int | None:
        label = _short(heading, MAX_HEADING_CHARS)
        start = self.add(text)
        if start is not None and label:
            self._heads.append((label, max(1, min(6, level)), start))
        return start

    def heading(self, heading: str, level: int = 1) -> None:
        label = _short(heading, MAX_HEADING_CHARS)
        if label:
            self.add_section(label, level, label)

    def build(self) -> tuple[str, list[Section]]:
        text = "".join(self._parts)
        sections: list[Section] = []
        for index, (label, level, start) in enumerate(self._heads):
            end = self._heads[index + 1][2] if index + 1 < len(self._heads) else len(text)
            if end > start:
                sections.append(Section(heading=label, start=start, end=end, level=level))
        return text, sections


class _Result:
    def __init__(self, kind: str, max_chars: int) -> None:
        self.kind = kind
        self.builder = _TextBuilder(max_chars)
        self.title: str | None = None
        self.metadata: dict[str, Any] = {}
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        if message not in self.warnings and len(self.warnings) < 50:
            self.warnings.append(message)

    def finish(self, data_len: int) -> ParsedDocument:
        text, sections = self.builder.build()
        if self.builder.truncated:
            self.warn("output truncated to the character limit")
        metadata = {**self.metadata, "chars": len(text), "bytes": data_len, "parser_version": INGESTION_PARSER_VERSION}
        title = _short(self.title, MAX_HEADING_CHARS) if self.title else None
        return ParsedDocument(
            kind=self.kind,
            text=text,
            title=title or None,
            sections=sections,
            metadata=metadata,
            warnings=self.warnings,
            truncated=self.builder.truncated,
        )


# =============================================================================================
# PDF
# =============================================================================================
def _parse_pdf(data: bytes, res: _Result) -> None:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        res.warn("PDF support unavailable (pypdf not installed)")
        return
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                decrypted = int(reader.decrypt(""))
            except Exception:
                decrypted = 0
            if not decrypted:
                res.warn("encrypted PDF: text could not be extracted")
                res.metadata["encrypted"] = True
                return
        pages = reader.pages
        page_count = len(pages)
    except Exception as exc:
        res.warn(f"corrupt or unreadable PDF: {type(exc).__name__}")
        return
    res.metadata["pages"] = page_count
    try:
        info = reader.metadata
        if info is not None and info.title:
            res.title = str(info.title)
    except Exception:
        res.warn("PDF metadata could not be read")
    parsed = empty = 0
    for number in range(1, page_count + 1):
        if number > MAX_PDF_PAGES:
            res.warn(f"only the first {MAX_PDF_PAGES} pages were parsed")
            break
        if res.builder.truncated:
            break
        try:
            page_text = pages[number - 1].extract_text() or ""
        except Exception as exc:
            res.warn(f"page {number}: text extraction failed ({type(exc).__name__})")
            continue
        parsed += 1
        if res.builder.add_section(f"Page {number}", 1, page_text) is None:
            empty += 1
    res.metadata["pages_parsed"] = parsed
    if page_count and empty == parsed:
        res.warn("no extractable text (the PDF may be scanned images)")


# =============================================================================================
# CSV / TSV
# =============================================================================================
class _ColumnStats:
    __slots__ = ("counts", "distinct_capped", "empty", "maximum", "minimum", "name", "non_empty", "numeric", "total")

    def __init__(self, name: str) -> None:
        self.name = name
        self.non_empty = 0
        self.empty = 0
        self.numeric = 0
        self.total = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.counts: Counter[str] = Counter()
        self.distinct_capped = False

    def update(self, raw: str) -> None:
        value = raw.strip()
        if not value:
            self.empty += 1
            return
        self.non_empty += 1
        number = _to_number(value)
        if number is not None:
            self.numeric += 1
            self.total += number
            self.minimum = number if self.minimum is None else min(self.minimum, number)
            self.maximum = number if self.maximum is None else max(self.maximum, number)
        key = _short(value)
        if key in self.counts or len(self.counts) < MAX_TRACKED_DISTINCT:
            self.counts[key] += 1
        else:
            self.distinct_capped = True

    @property
    def kind(self) -> str:
        if self.non_empty == 0:
            return "empty"
        if self.numeric == self.non_empty:
            return "numeric"
        return "text" if self.numeric == 0 else "mixed"

    def as_dict(self) -> dict[str, Any]:
        top = sorted(self.counts.items(), key=lambda item: (-item[1], item[0]))[:3]
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.kind,
            "non_empty": self.non_empty,
            "empty": self.empty,
            "distinct": None if self.distinct_capped else len(self.counts),
        }
        if self.numeric:
            out.update(
                {
                    "min": _round(self.minimum),
                    "max": _round(self.maximum),
                    "mean": _round(self.total / self.numeric),
                }
            )
        if self.kind != "numeric":
            out["top_values"] = [{"value": value, "count": count} for value, count in top]
        return out

    def describe(self) -> str:
        stats = self.as_dict()
        parts = [f"- {self.name}: {stats['type']}; {self.non_empty} non-empty, {self.empty} empty"]
        if self.numeric:
            parts.append(f"min {_fmt(stats['min'])}, max {_fmt(stats['max'])}, mean {_fmt(stats['mean'])}")
        if stats.get("top_values"):
            distinct = stats["distinct"] if stats["distinct"] is not None else f"{MAX_TRACKED_DISTINCT}+"
            top = ", ".join(f"{item['value']} ({item['count']})" for item in stats["top_values"])
            parts.append(f"{distinct} distinct; top: {top}")
        return "; ".join(parts)


def _to_number(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None) -> float | None:
    return None if value is None else float(f"{value:.6g}")


def _fmt(value: Any) -> str:
    return f"{value:.6g}" if isinstance(value, float) else str(value)


def _sniff_delimiter(first_line: str, default: str) -> str:
    counts = {delimiter: first_line.count(delimiter) for delimiter in (default, ",", ";", "\t", "|")}
    best = max(counts.items(), key=lambda item: (item[1], item[0] == default))
    return best[0] if best[1] > 0 else default


_DELIMITER_NAMES = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}


def _parse_delimited(data: bytes, res: _Result, default_delimiter: str) -> None:
    stream = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", errors="replace", newline="")
    first_line = stream.readline()
    stream.seek(0)
    delimiter = _sniff_delimiter(first_line, default_delimiter)
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(CSV_FIELD_LIMIT)
    try:
        _scan_delimited(csv.reader(stream, delimiter=delimiter), res, delimiter)
    finally:
        csv.field_size_limit(previous_limit)


def _scan_delimited(reader: Any, res: _Result, delimiter: str) -> None:
    rows = 0
    header: list[str] | None = None
    preview: list[list[str]] = []
    stats: list[_ColumnStats] = []
    capped = False
    try:
        for row in reader:
            if header is None:
                if len(row) > CSV_MAX_COLUMNS:
                    res.warn(f"only the first {CSV_MAX_COLUMNS} of {len(row)} columns are described")
                header = [_short(cell) or f"column_{index + 1}" for index, cell in enumerate(row[:CSV_MAX_COLUMNS])]
                stats = [_ColumnStats(name) for name in header]
                continue
            if not any(cell.strip() for cell in row):
                continue
            rows += 1
            if rows <= CSV_STATS_ROWS:
                for index, column in enumerate(stats):
                    column.update(row[index] if index < len(row) else "")
            if rows <= CSV_PREVIEW_ROWS:
                preview.append([_short(cell) for cell in row[: len(header)]])
            if rows >= CSV_MAX_COUNTED_ROWS:
                capped = True
                res.warn(f"row counting stopped after {CSV_MAX_COUNTED_ROWS} rows")
                break
    except csv.Error as exc:
        res.warn(f"malformed delimited data near data row {rows + 1}: {_short(str(exc))}")
    if header is None:
        res.warn("empty table")
        res.metadata.update({"rows": 0, "columns": 0, "column_names": [], "delimiter": delimiter})
        return
    name = _DELIMITER_NAMES.get(delimiter, repr(delimiter))
    column_stats = [column.as_dict() for column in stats]
    res.metadata.update(
        {
            "rows": rows,
            "rows_capped": capped,
            "columns": len(header),
            "column_names": header,
            "delimiter": delimiter,
            "column_stats": column_stats,
            "stats_rows": min(rows, CSV_STATS_ROWS),
            "preview_rows": len(preview),
        }
    )
    count = f"at least {rows}" if capped else str(rows)
    summary = (
        f"Table ({name}-separated) with {count} data rows and {len(header)} columns.\n"
        f"Columns: {', '.join(f'{c.name} ({c.kind})' for c in stats)}"
    )
    res.builder.add_section("Table summary", 1, summary)
    scope = f" (first {CSV_STATS_ROWS} rows)" if rows > CSV_STATS_ROWS else ""
    res.builder.add_section(
        "Column statistics", 1, f"Column statistics{scope}:\n" + "\n".join(c.describe() for c in stats)
    )
    if preview:
        lines = [" | ".join(header)] + [" | ".join(row) for row in preview]
        res.builder.add_section("Preview", 1, f"Preview (first {len(preview)} rows):\n" + "\n".join(lines))


# =============================================================================================
# JSON / JSONL
# =============================================================================================
def _json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if value is None:
        return "null"
    return "string"


class _KeySummary:
    def __init__(self) -> None:
        self.paths: dict[str, tuple[set[str], int]] = {}
        self.nodes = 0
        self.capped = False

    def visit(self, root: Any) -> None:
        stack: list[tuple[str, Any, int]] = [("$", root, 0)]
        while stack:
            path, value, depth = stack.pop()
            self.nodes += 1
            if self.nodes > JSON_MAX_NODES:
                self.capped = True
                return
            if path != "$":
                types, count = self.paths.get(path, (set(), 0))
                if path in self.paths or len(self.paths) < JSON_MAX_KEY_PATHS:
                    types.add(_json_type(value))
                    self.paths[path] = (types, count + 1)
                else:
                    self.capped = True
            if depth >= JSON_MAX_DEPTH:
                continue
            if isinstance(value, dict):
                for key in reversed(list(value)):
                    child = f"{path}.{_short(str(key), 60)}" if path != "$" else _short(str(key), 60)
                    stack.append((child, value[key], depth + 1))
            elif isinstance(value, list):
                child = f"{path}[]" if path != "$" else "[]"
                for item in reversed(value[:1000]):
                    stack.append((child, item, depth + 1))

    def render(self) -> str:
        lines = [
            f"- {path} ({'|'.join(sorted(types))}) ×{count}"
            for path, (types, count) in sorted(self.paths.items(), key=lambda item: item[0])
        ]
        if self.capped:
            lines.append("- … (key summary truncated)")
        return "\n".join(lines)

    def as_metadata(self) -> list[dict[str, Any]]:
        return [
            {"path": path, "types": sorted(types), "count": count}
            for path, (types, count) in sorted(self.paths.items(), key=lambda item: item[0])
        ]


def _parse_json(data: bytes, res: _Result) -> None:
    text, invalid = _decode(data)
    if invalid:
        res.warn("invalid UTF-8 sequences were replaced")
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as exc:
        res.warn(f"invalid JSON ({type(exc).__name__}); indexed as plain text")
        res.metadata["valid_json"] = False
        res.builder.add(text)
        return
    summary = _KeySummary()
    summary.visit(value)
    top_type = _json_type(value)
    res.metadata.update({"valid_json": True, "top_level_type": top_type, "key_paths": summary.as_metadata()})
    if isinstance(value, dict):
        res.metadata["keys"] = len(value)
        head = f"JSON object with {len(value)} top-level keys."
        title = value.get("title") or value.get("name")
        if isinstance(title, str):
            res.title = title
    elif isinstance(value, list):
        res.metadata["items"] = len(value)
        head = f"JSON array with {len(value)} items."
    else:
        head = f"JSON {top_type} value."
    rendered = summary.render()
    res.builder.add_section("Structure", 1, head + ("\nKey paths:\n" + rendered if rendered else ""))
    try:
        pretty = json.dumps(value, indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        pretty = text
    res.builder.add_section("Content", 1, pretty)


def _parse_jsonl(data: bytes, res: _Result) -> None:
    text, invalid = _decode(data)
    if invalid:
        res.warn("invalid UTF-8 sequences were replaced")
    summary = _KeySummary()
    records = bad = 0
    preview: list[str] = []
    capped = False
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if records + bad >= JSONL_MAX_RECORDS:
            capped = True
            res.warn(f"only the first {JSONL_MAX_RECORDS} lines were read")
            break
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            bad += 1
            continue
        records += 1
        if records <= JSONL_SUMMARY_RECORDS:
            summary.visit(record)
        if records <= JSONL_PREVIEW_RECORDS:
            preview.append(json.dumps(record, ensure_ascii=False, separators=(", ", ": ")))
    if bad:
        res.warn(f"{bad} invalid JSON lines were skipped")
    res.metadata.update(
        {"rows": records, "invalid_lines": bad, "rows_capped": capped, "key_paths": summary.as_metadata()}
    )
    rendered = summary.render()
    head = f"JSON Lines with {records} records" + (f" ({bad} invalid lines skipped)." if bad else ".")
    res.builder.add_section("Structure", 1, head + ("\nKey paths:\n" + rendered if rendered else ""))
    if preview:
        res.builder.add_section(
            "Records",
            1,
            f"First {len(preview)} records:\n" + "\n".join(preview),
        )


# =============================================================================================
# Markdown
# =============================================================================================
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(?P<body>.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)
_ATX_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*#*[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(?P<char>=+|-+)[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_DANGEROUS_BLOCK_RE = re.compile(
    r"<(script|style|noscript|iframe|svg|template|object|embed)\b[^>]*>.*?(?:</\1\s*>|\Z)", re.IGNORECASE | re.DOTALL
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_DATA_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\(\s*data:[^)]*\)", re.IGNORECASE)
_BASE64_BLOB_RE = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=\s]{64,}")


def _strip_markup(block: str) -> str:
    block = _DATA_IMAGE_RE.sub(lambda m: f"[image: {m.group('alt')}]" if m.group("alt") else "[image]", block)
    block = _BASE64_BLOB_RE.sub("[embedded data removed]", block)
    block = _DANGEROUS_BLOCK_RE.sub("", block)
    block = _HTML_COMMENT_RE.sub("", block)
    block = _HTML_TAG_RE.sub("", block)
    return html.unescape(block)


def _markdown_into(res: _Result, source: str, *, counters: dict[str, int]) -> None:
    lines = clean_text(source).split("\n")
    buffer: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if buffer:
            res.builder.add(_strip_markup("\n".join(buffer)))
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            buffer.append(line)
            if (
                fence_match
                and fence_match.group("fence")[0] == fence[0]
                and len(fence_match.group("fence")) >= len(fence)
            ):
                res.builder.add("\n".join(buffer))
                buffer.clear()
                fence = None
            index += 1
            continue
        if fence_match:
            flush()
            fence = fence_match.group("fence")
            counters["code_blocks"] = counters.get("code_blocks", 0) + 1
            buffer.append(line)
            index += 1
            continue
        atx = _ATX_RE.match(line)
        if atx:
            flush()
            heading = _strip_markup(atx.group("text") or "").strip()
            if heading:
                level = len(atx.group("hashes"))
                res.builder.heading(heading, level)
                counters["headings"] = counters.get("headings", 0) + 1
                if level == 1 and res.title is None:
                    res.title = heading
            index += 1
            continue
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        setext = _SETEXT_RE.match(nxt)
        if line.strip() and setext and not _continues_paragraph(buffer) and not _is_list_item(line):
            flush()
            heading = _strip_markup(line).strip()
            level = 1 if setext.group("char").startswith("=") else 2
            res.builder.heading(heading, level)
            counters["headings"] = counters.get("headings", 0) + 1
            if level == 1 and res.title is None:
                res.title = heading
            index += 2
            continue
        buffer.append(line)
        index += 1
    if fence is not None and buffer:
        res.builder.add("\n".join(buffer))
        buffer.clear()
    flush()


def _continues_paragraph(buffer: list[str]) -> bool:
    """True when the current line continues a multi-line paragraph (then ``---`` is not a setext heading)."""
    return len(buffer) > 0 and bool(buffer[-1].strip())


def _is_list_item(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line))


def _parse_markdown(data: bytes, res: _Result) -> None:
    text, invalid = _decode(data)
    if invalid:
        res.warn("invalid UTF-8 sequences were replaced")
    text = _HTML_COMMENT_RE.sub("", _DANGEROUS_BLOCK_RE.sub("", clean_text(text)))
    front = _FRONT_MATTER_RE.match(text)
    if front:
        for line in front.group("body").split("\n"):
            key, _, value = line.partition(":")
            if key.strip().lower() == "title" and value.strip():
                res.title = value.strip().strip("'\"")
        text = text[front.end() :]
        res.metadata["front_matter"] = True
    counters: dict[str, int] = {}
    _markdown_into(res, text, counters=counters)
    res.metadata.update({"headings": counters.get("headings", 0), "code_blocks": counters.get("code_blocks", 0)})


# =============================================================================================
# Notebooks
# =============================================================================================
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _joined(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    return value if isinstance(value, str) else ""


def _parse_notebook(data: bytes, res: _Result) -> None:
    text, _ = _decode(data)
    try:
        notebook = json.loads(text)
    except (ValueError, RecursionError):
        notebook = None
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        res.warn("not a valid Jupyter notebook; parsed as JSON")
        _parse_json(data, res)
        return
    meta = _as_dict(notebook.get("metadata"))
    kernelspec = _as_dict(meta.get("kernelspec"))
    language_info = _as_dict(meta.get("language_info"))
    language = str(kernelspec.get("language") or language_info.get("name") or "python")
    language = re.sub(r"[^A-Za-z0-9_+-]", "", language)[:20] or "python"
    if isinstance(meta.get("title"), str):
        res.title = meta["title"]
    counts = {"markdown": 0, "code": 0, "raw": 0}
    dropped = 0
    counters: dict[str, int] = {}
    cells = notebook["cells"]
    if len(cells) > NOTEBOOK_MAX_CELLS:
        res.warn(f"only the first {NOTEBOOK_MAX_CELLS} cells were parsed")
    for cell in cells[:NOTEBOOK_MAX_CELLS]:
        if not isinstance(cell, dict):
            continue
        cell_type = str(cell.get("cell_type", ""))
        source = _joined(cell.get("source"))
        if cell_type == "markdown":
            counts["markdown"] += 1
            _markdown_into(res, source, counters=counters)
        elif cell_type == "code":
            counts["code"] += 1
            if source.strip():
                res.builder.add(f"```{language}\n{source.rstrip()}\n```")
            for output in _as_list(cell.get("outputs")):
                rendered, rich = _render_output(output)
                dropped += rich
                if rendered:
                    res.builder.add("Output:\n" + rendered[:NOTEBOOK_MAX_OUTPUT_CHARS])
        elif cell_type == "raw":
            counts["raw"] += 1
            res.builder.add(source)
    nbformat = notebook.get("nbformat")
    res.metadata.update(
        {
            "cells": sum(counts.values()),
            "code_cells": counts["code"],
            "markdown_cells": counts["markdown"],
            "raw_cells": counts["raw"],
            "language": language,
            "nbformat": nbformat if isinstance(nbformat, int) else None,
            "dropped_outputs": dropped,
            "headings": counters.get("headings", 0),
        }
    )


def _render_output(output: Any) -> tuple[str, int]:
    """Text of one notebook output (stream / text/plain / error); counts dropped rich (non-text) payloads."""
    if not isinstance(output, dict):
        return "", 0
    kind = output.get("output_type")
    if kind == "stream":
        return clean_text(_joined(output.get("text"))).strip(), 0
    if kind == "error":
        return clean_text(f"{output.get('ename', 'Error')}: {output.get('evalue', '')}").strip(), 0
    if kind in {"execute_result", "display_data", "update_display_data"}:
        payload = _as_dict(output.get("data"))
        rich = sum(1 for key in payload if key != "text/plain")
        return clean_text(_joined(payload.get("text/plain"))).strip(), rich
    return "", 0


# =============================================================================================
# HTML
# =============================================================================================
_SKIP_TAGS = frozenset({"script", "style", "noscript", "iframe", "svg", "template", "object", "embed", "canvas"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "li",
        "ul",
        "ol",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "br",
        "hr",
        "blockquote",
        "pre",
        "form",
        "figure",
        "figcaption",
        "dl",
        "dt",
        "dd",
        "body",
        "caption",
        "address",
        "details",
        "summary",
    }
)
_HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}
_HTML_WS_RE = re.compile(r"\s+")


class _HTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.blocks: list[tuple[int, str]] = []  # (heading level or 0, text)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._current: list[str] = []
        self._in_title = False
        self._heading_level = 0
        self._pre_depth = 0

    def _flush(self) -> None:
        text = _collapse("".join(self._current))
        self._current = []
        if text:
            self.blocks.append((self._heading_level, text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag in _HEADING_TAGS:
            self._flush()
            self._heading_level = _HEADING_TAGS[tag]
        elif tag == "br":
            self._current.append("\n")
        elif tag in _BLOCK_TAGS:
            self._flush()
        if tag == "a" and len(self.links) < MAX_LINKS:
            href = next((value for name, value in attrs if name.lower() == "href" and value), None)
            if href and href.strip().lower().startswith(("http://", "https://")):
                link = _short(href.strip(), 2000)
                if link not in self.links:
                    self.links.append(link)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag == "title":
            self._in_title = False
        elif tag in _HEADING_TAGS:
            self._flush()
            self._heading_level = 0
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        elif self._pre_depth:
            self._current.append(data)
        else:
            self._current.append(_HTML_WS_RE.sub(" ", data))

    def close(self) -> None:
        super().close()
        self._flush()


def _parse_html(data: bytes, res: _Result) -> None:
    text, invalid = _decode(data)
    if invalid:
        res.warn("invalid UTF-8 sequences were replaced")
    extractor = _HTMLExtractor()
    try:
        extractor.feed(clean_text(text))
        extractor.close()
    except Exception as exc:  # html.parser is lenient; guard against pathological input anyway
        res.warn(f"HTML parsing stopped early ({type(exc).__name__})")
        extractor._flush()
    title = _collapse("".join(extractor.title_parts))
    headings = 0
    for level, block in extractor.blocks:
        if level:
            headings += 1
            res.builder.heading(block, level)
            if not title and level == 1:
                title = block
        else:
            res.builder.add(block)
    res.title = title or None
    res.metadata.update({"headings": headings, "links": extractor.links})


# =============================================================================================
# Plain text
# =============================================================================================
def _parse_text(data: bytes, res: _Result) -> None:
    text, invalid = _decode(data)
    if invalid:
        res.warn("invalid UTF-8 sequences were replaced")
    text = clean_text(text)
    res.metadata["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    res.builder.add(text)


_PARSERS: dict[str, Callable[[bytes, _Result], None]] = {
    "pdf": _parse_pdf,
    "csv": lambda data, res: _parse_delimited(data, res, ","),
    "tsv": lambda data, res: _parse_delimited(data, res, "\t"),
    "json": _parse_json,
    "jsonl": _parse_jsonl,
    "markdown": _parse_markdown,
    "notebook": _parse_notebook,
    "html": _parse_html,
    "text": _parse_text,
}


def parse(
    kind: str,
    data: bytes,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ParsedDocument:
    """Parse ``data`` of the given detected ``kind`` into bounded, cleaned text with sections and metadata.

    Raises :class:`UnsupportedDocumentError` for ``"unsupported"`` or unknown kinds. Corrupt input never
    raises: it yields warnings and whatever text could be recovered.
    """
    if kind not in _PARSERS:
        known = ", ".join(k for k in DOCUMENT_KINDS if k != "unsupported")
        raise UnsupportedDocumentError(f"unsupported document kind '{kind}' (supported: {known})")
    if max_chars < 1 or max_bytes < 1:
        raise ValueError("max_chars and max_bytes must be positive")
    res = _Result(kind, max_chars)
    size = len(data)
    if size > max_bytes:
        if kind in {"pdf", "notebook", "json"}:
            res.warn(f"document exceeds the {max_bytes}-byte parse limit and was not parsed")
            return res.finish(size)
        res.warn(f"input truncated to the first {max_bytes} bytes")
        data = data[:max_bytes]
    _PARSERS[kind](data, res)
    return res.finish(size)
