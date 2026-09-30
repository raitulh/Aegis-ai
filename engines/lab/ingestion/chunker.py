"""Deterministic, section- and sentence-aware text chunking with token overlap.

Tokens are approximated as ``ceil(words × 1.3)`` (no tokenizer dependency, stable across platforms).
Chunks never span two sections; within a section, text is split into sentence units (sentences longer
than the budget are split into word pieces), units are packed greedily up to ``target_tokens`` and each
new chunk repeats trailing whole units of the previous chunk worth at most ``overlap_tokens``. Every
chunk's text is an exact slice ``text[char_start:char_end]`` of the input.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from engines.common.text import split_sentences

TOKENS_PER_WORD = 1.3
DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 60

_WORD_RE = re.compile(r"\S+")


class Chunk(BaseModel):
    seq: int = Field(ge=0)
    text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    token_count: int = Field(ge=0)
    heading: str | None = None


def estimate_tokens(text: str) -> int:
    """Approximate token count: ``ceil(words × 1.3)``."""
    words = len(_WORD_RE.findall(text))
    return math.ceil(words * TOKENS_PER_WORD) if words else 0


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    tokens: int


def _coerce_sections(sections: Iterable[Any], length: int) -> list[tuple[int, int, str | None]]:
    out: list[tuple[int, int, str | None]] = []
    for section in sections:
        if isinstance(section, Mapping):
            start, end, heading = section.get("start"), section.get("end"), section.get("heading")
        else:
            start, end, heading = (
                getattr(section, "start", None),
                getattr(section, "end", None),
                getattr(section, "heading", None),
            )
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("every section needs integer 'start' and 'end' offsets")
        start, end = max(0, min(start, length)), max(0, min(end, length))
        if end > start:
            out.append((start, end, str(heading) if heading else None))
    return sorted(out, key=lambda item: (item[0], item[1]))


def _segments(text: str, sections: Sequence[Any] | None) -> list[tuple[int, int, str | None]]:
    length = len(text)
    if not sections:
        return [(0, length, None)]
    segments: list[tuple[int, int, str | None]] = []
    cursor = 0
    for start, end, heading in _coerce_sections(sections, length):
        if start > cursor:
            segments.append((cursor, start, None))
        start = max(start, cursor)
        if end > start:
            segments.append((start, end, heading))
        cursor = max(cursor, end)
    if cursor < length:
        segments.append((cursor, length, None))
    return segments


def _units(text: str, start: int, end: int, target_tokens: int, piece_tokens: int) -> list[_Unit]:
    """Sentence units; a sentence longer than a whole chunk is split into word pieces of ``piece_tokens``."""
    units: list[_Unit] = []
    max_words = max(1, math.floor(piece_tokens / TOKENS_PER_WORD))
    for _sentence, rel_start, rel_end in split_sentences(text[start:end]):
        abs_start, abs_end = start + rel_start, start + rel_end
        words = list(_WORD_RE.finditer(text, abs_start, abs_end))
        if not words:
            continue
        tokens = math.ceil(len(words) * TOKENS_PER_WORD)
        if tokens <= target_tokens:
            units.append(_Unit(abs_start, abs_end, tokens))
            continue
        for offset in range(0, len(words), max_words):
            group = words[offset : offset + max_words]
            units.append(_Unit(group[0].start(), group[-1].end(), math.ceil(len(group) * TOKENS_PER_WORD)))
    return units


def _pack(units: list[_Unit], target_tokens: int, overlap_tokens: int) -> list[list[_Unit]]:
    packed: list[list[_Unit]] = []
    index = 0
    previous: list[_Unit] = []
    while index < len(units):
        carried: list[_Unit] = []
        carried_tokens = 0
        if previous and overlap_tokens > 0:
            for unit in reversed(previous[1:]):  # never repeat the whole previous chunk
                if carried_tokens + unit.tokens > overlap_tokens:
                    break
                carried.insert(0, unit)
                carried_tokens += unit.tokens
            while carried and carried_tokens + units[index].tokens > target_tokens:
                carried_tokens -= carried.pop(0).tokens
        current = list(carried)
        tokens = carried_tokens
        added = 0
        while index < len(units):
            unit = units[index]
            if added and tokens + unit.tokens > target_tokens:
                break
            current.append(unit)
            tokens += unit.tokens
            added += 1
            index += 1
        packed.append(current)
        previous = current
    return packed


def chunk(
    text: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    sections: Sequence[Any] | None = None,
) -> list[Chunk]:
    """Split ``text`` into overlapping chunks of about ``target_tokens`` tokens.

    ``sections`` (objects or mappings with ``start``, ``end`` and ``heading``) are hard boundaries; text not
    covered by any section is chunked on its own with ``heading=None``.
    """
    if target_tokens < 1:
        raise ValueError("target_tokens must be >= 1")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be >= 0 and < target_tokens")
    piece_tokens = min(target_tokens, overlap_tokens) if overlap_tokens > 0 else target_tokens
    chunks: list[Chunk] = []
    for start, end, heading in _segments(text, sections):
        units = _units(text, start, end, target_tokens, piece_tokens)
        for group in _pack(units, target_tokens, overlap_tokens):
            char_start, char_end = group[0].start, group[-1].end
            body = text[char_start:char_end]
            chunks.append(
                Chunk(
                    seq=len(chunks),
                    text=body,
                    char_start=char_start,
                    char_end=char_end,
                    token_count=estimate_tokens(body),
                    heading=heading,
                )
            )
    return chunks
