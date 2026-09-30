"""Paragraph-aware chunking with overlap and stable per-chunk hashes (character offsets preserved)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    char_start: int
    char_end: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def chunk_text(
    text: str, *, target_chars: int = 1_200, overlap_chars: int = 150, max_chunks: int = 5_000
) -> list[TextChunk]:
    if target_chars < 200:
        raise ValueError("target_chars must be >= 200")
    overlap_chars = max(0, min(overlap_chars, target_chars // 3))
    paragraphs = [(m.start(), m.end()) for m in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text)]
    chunks: list[TextChunk] = []
    start: int | None = None
    end = 0
    for p_start, p_end in paragraphs:
        if start is None:
            start = p_start
        if p_end - start > target_chars and end > start:
            chunks.append(_make(text, len(chunks), start, end))
            start = max(start, end - overlap_chars)
            start = _align(text, start)
        if p_end - start > target_chars * 2:
            # A single very long paragraph: hard-split on sentence/whitespace boundaries.
            pos = start
            while p_end - pos > target_chars:
                cut = _split_point(text, pos, pos + target_chars)
                chunks.append(_make(text, len(chunks), pos, cut))
                pos = _align(text, max(pos + 1, cut - overlap_chars))
            start = pos
        end = p_end
        if len(chunks) >= max_chunks:
            break
    if start is not None and end > start and len(chunks) < max_chunks:
        chunks.append(_make(text, len(chunks), start, end))
    return [c for c in chunks if c.text]


def _make(text: str, index: int, start: int, end: int) -> TextChunk:
    raw = text[start:end]
    stripped = raw.strip()
    offset = raw.find(stripped) if stripped else 0
    return TextChunk(index=index, text=stripped, char_start=start + offset, char_end=start + offset + len(stripped))


def _align(text: str, pos: int) -> int:
    while 0 < pos < len(text) and not text[pos - 1].isspace():
        pos += 1
    return pos


def _split_point(text: str, start: int, limit: int) -> int:
    window = text[start:limit]
    for pattern in (r"[.!?]\s", r"\s"):
        matches = list(re.finditer(pattern, window))
        if matches and matches[-1].end() > len(window) // 2:
            return start + matches[-1].end()
    return limit
