"""Deterministic metadata and entity extraction (DOIs, arXiv ids, URLs, years, metric mentions, datasets).

Model-assisted extraction (KnowledgeAgent) may add richer relations later; these deterministic signals are
always recorded so provenance does not depend on a model.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+[A-Za-z0-9]")
ARXIV_RE = re.compile(r"\barXiv:\s?(\d{4}\.\d{4,5})(v\d+)?\b|\barxiv\.org/abs/(\d{4}\.\d{4,5})", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]{4,300}")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
METRIC_RE = re.compile(
    r"\b(accuracy|precision|recall|F1|AUC|ROC-AUC|BLEU|ROUGE(-[12L])?|perplexity|MSE|RMSE|MAE|R\^?2|top-[15] accuracy|"
    r"mAP|IoU|latency|throughput|FLOPs|energy|yield|binding affinity|half-life|efficiency)\b",
    re.I,
)
DATASET_RE = re.compile(r"\b([A-Z][A-Za-z0-9-]{1,40}(?:[- ]\d+[A-Za-z]?)?)\s+(dataset|benchmark|corpus)\b")
AUTHORS_RE = re.compile(r"^\s*(?:authors?|by)\s*[:\-]\s*(.+)$", re.I | re.M)


@dataclass
class ExtractedMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    urls: list[str] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "year": self.year,
            "urls": self.urls,
            "entities": self.entities,
        }


def extract_metadata(text: str, hints: dict[str, Any] | None = None) -> ExtractedMetadata:
    hints = hints or {}
    meta = ExtractedMetadata()
    meta.title = str(hints.get("citation_title") or hints.get("title") or hints.get("dc.title") or "").strip() or None
    if not meta.title:
        first = next((ln.strip().lstrip("#").strip() for ln in text.splitlines() if ln.strip()), "")
        meta.title = first[:300] or None
    author_hint = hints.get("citation_author") or hints.get("author") or hints.get("dc.creator")
    if author_hint:
        meta.authors = [a.strip() for a in re.split(r";|\band\b", str(author_hint)) if a.strip()][:50]
    else:
        m = AUTHORS_RE.search(text[:5000])
        if m:
            meta.authors = [a.strip() for a in re.split(r",|;|\band\b", m.group(1)) if a.strip()][:50]
    doi = hints.get("citation_doi") or hints.get("doi")
    m_doi = DOI_RE.search(str(doi)) if doi else DOI_RE.search(text[:20000])
    meta.doi = m_doi.group(0).rstrip(".") if m_doi else None
    m_ax = ARXIV_RE.search(text[:20000])
    if m_ax:
        meta.arxiv_id = m_ax.group(1) or m_ax.group(3)
    year_hint = hints.get("citation_publication_date") or hints.get("creationdate")
    m_year = YEAR_RE.search(str(year_hint)) if year_hint else YEAR_RE.search(text[:3000])
    meta.year = int(m_year.group(0)) if m_year else None
    meta.urls = sorted(set(URL_RE.findall(text)))[:200]
    metrics = Counter(m.group(0).lower() for m in METRIC_RE.finditer(text))
    datasets = Counter(m.group(1) for m in DATASET_RE.finditer(text))
    meta.entities = [{"type": "Metric", "name": k, "mentions": v} for k, v in metrics.most_common(25)] + [
        {"type": "Dataset", "name": k, "mentions": v} for k, v in datasets.most_common(25)
    ]
    return meta
