"""Small, dependency-free text utilities used across engines."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "which",
        "who",
        "whom",
        "whose",
        "what",
        "when",
        "where",
        "why",
        "how",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "them",
        "his",
        "her",
        "our",
        "their",
        "your",
        "my",
        "me",
        "us",
        "do",
        "does",
        "did",
        "done",
        "not",
        "no",
        "nor",
        "so",
        "than",
        "too",
        "very",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "must",
        "will",
        "shall",
        "has",
        "have",
        "had",
        "having",
        "into",
        "about",
        "over",
        "under",
        "after",
        "before",
        "again",
        "further",
        "once",
        "only",
        "own",
        "same",
        "such",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "any",
        "all",
        "per",
        "via",
    ]
)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Return sentences with character offsets into the original text."""
    out: list[tuple[str, int, int]] = []
    for block_match in re.finditer(r"[^\n]+", text):
        block = block_match.group(0)
        base = block_match.start()
        cursor = 0
        for piece in _SENTENCE_SPLIT.split(block):
            idx = block.find(piece, cursor)
            if idx < 0:
                idx = cursor
            stripped = piece.strip()
            if stripped:
                start = base + idx + (len(piece) - len(piece.lstrip()))
                out.append((stripped, start, start + len(stripped)))
            cursor = idx + len(piece)
    return out


def stable_hash(value: Any, length: int = 64) -> str:
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()[:length]


def stable_unit(*parts: Any) -> float:
    """Deterministic pseudo-random number in [0, 1) derived from the inputs."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def cosine_counts(a: Counter[str], b: Counter[str]) -> float:
    common = set(a) & set(b)
    num = sum(a[t] * b[t] for t in common)
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def truncate(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
