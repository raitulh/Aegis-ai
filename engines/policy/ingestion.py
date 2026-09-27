"""Policy document ingestion: extract → normalise → chunk, preserving page/section provenance.

Supports PDF (pypdf), DOCX (python-docx) and TXT/Markdown. Every chunk records its page number, section
number, heading, character span and a content hash so every extracted requirement can be traced back to
an exact source location.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from engines.common.text import normalize_whitespace, stable_hash

SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+(.{2,120})$")
HEADING_MD = re.compile(r"^(#{1,4})\s+(.+)$")


@dataclass
class Page:
    number: int
    text: str


@dataclass
class Chunk:
    index: int
    text: str
    page_number: int | None
    section: str | None
    heading: str | None
    char_start: int
    char_end: int

    @property
    def content_hash(self) -> str:
        return stable_hash(self.text)


@dataclass
class ExtractedDocument:
    filename: str
    text: str
    pages: list[Page]
    page_count: int
    chunks: list[Chunk] = field(default_factory=list)


def extract_pages(filename: str, data: bytes, extension: str) -> list[Page]:
    if extension == ".pdf":
        return _extract_pdf(data)
    if extension == ".docx":
        return [Page(number=1, text=_extract_docx(data))]
    text = data.decode("utf-8", errors="replace")
    if extension == ".md":
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return [Page(number=1, text=text)]


def _extract_pdf(data: bytes) -> list[Page]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[Page] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            pages.append(Page(number=i, text=page.extract_text() or ""))
        except Exception:
            pages.append(Page(number=i, text=""))
    return pages or [Page(number=1, text="")]


def _extract_docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    lines: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name if para.style else "") or ""
        if style.startswith("Heading"):
            lines.append(f"\n## {text}")
        else:
            lines.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    out: list[str] = []
    for part in parts:
        cleaned = normalize_whitespace(part)
        if len(cleaned) < 3:
            continue
        # Break very long paragraphs on sentence boundaries to bound chunk size.
        if len(cleaned) > 1200:
            buf = ""
            for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
                if len(buf) + len(sentence) > 1000 and buf:
                    out.append(buf.strip())
                    buf = ""
                buf += sentence + " "
            if buf.strip():
                out.append(buf.strip())
        else:
            out.append(cleaned)
    return out


def chunk_document(filename: str, pages: list[Page]) -> ExtractedDocument:
    chunks: list[Chunk] = []
    full: list[str] = []
    cursor = 0
    idx = 0
    current_section: str | None = None
    current_heading: str | None = None
    for page in pages:
        for raw_line in re.split(r"\n(?=\s*\d+(?:\.\d+)*\s+[A-Z])", page.text):
            for para in _split_paragraphs(raw_line):
                md = HEADING_MD.match(para)
                sec = SECTION_RE.match(para)
                if md:
                    current_heading = md.group(2).strip()
                    para = current_heading
                elif sec:
                    current_section = sec.group(1)
                    current_heading = sec.group(2).strip()
                start = cursor
                chunks.append(
                    Chunk(
                        index=idx,
                        text=para,
                        page_number=page.number,
                        section=current_section,
                        heading=current_heading,
                        char_start=start,
                        char_end=start + len(para),
                    )
                )
                full.append(para)
                cursor += len(para) + 1
                idx += 1
    return ExtractedDocument(
        filename=filename,
        text="\n".join(full),
        pages=pages,
        page_count=len(pages),
        chunks=chunks,
    )


def ingest(filename: str, data: bytes, extension: str) -> ExtractedDocument:
    return chunk_document(filename, extract_pages(filename, data, extension))
