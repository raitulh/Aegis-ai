"""Type detection and parsing for knowledge ingestion (PDF, papers, web pages, CSV, JSON, Markdown, notebooks,
plain text). Content is parsed as *data*: notebooks' code cells are extracted as text and never executed, HTML
scripts/styles are dropped, and parsers enforce size limits.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

MAX_TEXT_CHARS = 5_000_000
MAX_CSV_ROWS_PROFILED = 50_000


class ParseError(ValueError):
    """The document could not be parsed as its detected type."""


@dataclass
class ParsedDocument:
    doc_type: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)
    table_profile: dict[str, Any] | None = None


def detect_type(filename: str, data: bytes, declared: str | None = None) -> str:
    name = filename.lower()
    head = data[:2048].lstrip()
    if data.startswith(b"%PDF-"):
        return "pdf"
    if name.endswith(".ipynb"):
        return "notebook"
    if name.endswith((".md", ".markdown")):
        return "markdown"
    if name.endswith(".csv") or (declared or "").startswith("text/csv"):
        return "csv"
    if name.endswith((".json", ".jsonl")) or head[:1] in (b"{", b"["):
        return "json"
    if name.endswith((".html", ".htm")) or head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    if b"\x00" in data[:4096]:
        raise ParseError("Binary content is not supported for ingestion")
    return "text"


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "object"})
    _BLOCK = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title: str | None = None
        self._in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            d = {k: v or "" for k, v in attrs}
            key = d.get("name") or d.get("property")
            if (
                key
                and d.get("content")
                and key.lower()
                in {
                    "citation_title",
                    "citation_author",
                    "citation_doi",
                    "citation_publication_date",
                    "citation_journal_title",
                    "dc.title",
                    "dc.creator",
                    "description",
                    "og:title",
                    "author",
                }
            ):
                self.meta[key.lower()] = (self.meta.get(key.lower(), "") + "; " + d["content"]).strip("; ")
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        self.parts.append(data)


def _decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_TEXT_CHARS:
        raise ParseError(f"Document exceeds {MAX_TEXT_CHARS} characters")
    return text


def _parse_pdf(data: bytes) -> ParsedDocument:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages[:2000]]
        info: dict[str, Any] = dict(reader.metadata or {})
    except Exception as exc:
        raise ParseError(f"PDF could not be parsed: {type(exc).__name__}") from exc
    text = "\n\n".join(pages)
    meta: dict[str, Any] = {k.strip("/").lower(): str(v) for k, v in info.items() if isinstance(v, str | int | float)}
    meta["page_count"] = len(pages)
    sections = [{"page": i + 1, "chars": len(p)} for i, p in enumerate(pages)]
    return ParsedDocument("pdf", text, meta, sections)


def _parse_csv(data: bytes) -> ParsedDocument:
    text = _decode(data)
    dialect: type[csv.Dialect] = csv.excel
    with contextlib.suppress(csv.Error):
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(_take(reader, MAX_CSV_ROWS_PROFILED + 1))
    if not rows:
        raise ParseError("CSV is empty")
    header, body = rows[0], rows[1:]
    columns: list[dict[str, Any]] = []
    for idx, name in enumerate(header):
        values = [r[idx] for r in body if idx < len(r)]
        numeric = [v for v in values if _is_number(v)]
        col: dict[str, Any] = {
            "name": name,
            "non_empty": sum(1 for v in values if v != ""),
            "distinct": len(set(values)),
        }
        if values and len(numeric) >= 0.9 * len([v for v in values if v != ""]) and numeric:
            nums = [float(v) for v in numeric]
            col.update({"type": "numeric", "min": min(nums), "max": max(nums), "mean": sum(nums) / len(nums)})
        else:
            col["type"] = "categorical" if len(set(values)) <= max(20, len(values) // 20) else "text"
        columns.append(col)
    profile = {"columns": columns, "rows_profiled": len(body), "truncated": len(rows) > MAX_CSV_ROWS_PROFILED}
    summary = f"CSV with {len(header)} columns: " + ", ".join(header[:50])
    return ParsedDocument("csv", summary + "\n" + text[:20_000], {"columns": header}, [], profile)


def _parse_json(data: bytes) -> ParsedDocument:
    text = _decode(data)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        try:
            value = [json.loads(ln) for ln in lines]
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON: {exc.msg}") from exc
    meta: dict[str, Any] = {"top_level": type(value).__name__}
    if isinstance(value, dict):
        meta["keys"] = sorted(value)[:100]
        for key in ("title", "name", "doi", "authors", "abstract"):
            if isinstance(value.get(key), str | list):
                meta[key] = value[key]
    elif isinstance(value, list):
        meta["items"] = len(value)
    return ParsedDocument("json", json.dumps(value, indent=1, ensure_ascii=False)[:MAX_TEXT_CHARS], meta)


def _parse_notebook(data: bytes) -> ParsedDocument:
    try:
        nb = json.loads(_decode(data))
    except json.JSONDecodeError as exc:
        raise ParseError("Notebook is not valid JSON") from exc
    cells = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells, list):
        raise ParseError("Notebook has no cells")
    parts: list[str] = []
    sections: list[dict[str, Any]] = []
    for i, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        kind = cell.get("cell_type", "unknown")
        parts.append(f"[{kind} cell {i}]\n{source}")
        sections.append({"cell": i, "type": kind, "chars": len(source)})
    meta = {"cells": len(cells), "kernel": ((nb.get("metadata") or {}).get("kernelspec") or {}).get("name")}
    return ParsedDocument("notebook", "\n\n".join(parts), meta, sections)


def _parse_html(data: bytes) -> ParsedDocument:
    extractor = _TextExtractor()
    try:
        extractor.feed(_decode(data))
        extractor.close()
    except Exception as exc:
        raise ParseError("HTML could not be parsed") from exc
    text = re.sub(r"\n\s*\n+", "\n\n", "".join(extractor.parts)).strip()
    meta: dict[str, Any] = dict(extractor.meta)
    if extractor.title:
        meta.setdefault("title", extractor.title)
    return ParsedDocument("html", text, meta)


def _parse_markdown(data: bytes) -> ParsedDocument:
    text = _decode(data)
    headings = [
        {"level": len(m.group(1)), "title": m.group(2).strip(), "offset": m.start()}
        for m in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.M)
    ]
    meta: dict[str, Any] = {}
    if headings:
        meta["title"] = headings[0]["title"]
    return ParsedDocument("markdown", text, meta, headings)


def parse(doc_type: str, data: bytes) -> ParsedDocument:
    parsers = {
        "pdf": _parse_pdf,
        "csv": _parse_csv,
        "json": _parse_json,
        "notebook": _parse_notebook,
        "html": _parse_html,
        "markdown": _parse_markdown,
        "text": lambda d: ParsedDocument("text", _decode(d)),
    }
    if doc_type not in parsers:
        raise ParseError(f"Unsupported document type '{doc_type}'")
    return parsers[doc_type](data)


def _take(iterable: Any, n: int) -> list[Any]:
    out = []
    for i, item in enumerate(iterable):
        if i >= n:
            break
        out.append(item)
    return out


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return value.strip() != ""
