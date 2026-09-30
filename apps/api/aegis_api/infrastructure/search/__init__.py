"""Literature/web research adapters. All results are UNTRUSTED data with provenance (URL, retrieval time,
checksum); nothing here decides authority or truth."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from defusedxml import ElementTree

from aegis_api.config import get_settings
from aegis_api.security.ssrf import validate_outbound_url

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
ALLOWED_FETCH_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
)
MAX_REDIRECTS = 3


class SearchError(Exception):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class SearchHit:
    source_type: str
    title: str
    url: str | None
    snippet: str
    authors: list[str] = field(default_factory=list)
    publication_date: str | None = None
    publisher: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    provider: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    trust_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def checksum(self) -> str:
        key = self.doi or self.arxiv_id or self.url or self.title
        return hashlib.sha256(f"{self.source_type}|{key}".encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "checksum": self.checksum}


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(20.0, connect=8.0),
        follow_redirects=False,
        transport=transport,
        headers={"User-Agent": get_settings().research_user_agent},
    )


def _text(el: Any) -> str:
    return re.sub(r"\s+", " ", (el.text or "") if el is not None else "").strip()


class ArxivSearch:
    name = "arxiv"
    BASE = "https://export.arxiv.org/api/query"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        params: dict[str, str | int] = {
            "search_query": f"all:{query[:300]}",
            "start": 0,
            "max_results": max(1, min(limit, 50)),
            "sortBy": "relevance",
        }
        try:
            with _client(self.transport) as c:
                r = c.get(self.BASE, params=params)
        except httpx.HTTPError as exc:
            raise SearchError(f"arXiv unreachable: {type(exc).__name__}", transient=True) from exc
        if r.status_code != 200:
            raise SearchError(f"arXiv returned HTTP {r.status_code}", transient=r.status_code >= 500)
        if len(r.content) > 5 * 1024 * 1024:
            raise SearchError("arXiv response too large")
        root = ElementTree.fromstring(r.content)  # defusedxml: no entity expansion / external entities
        hits: list[SearchHit] = []
        for entry in root.findall(f"{ATOM}entry"):
            abs_url = _text(entry.find(f"{ATOM}id"))
            m = re.search(r"arxiv\.org/abs/([^v\s]+)", abs_url)
            doi_el = entry.find(f"{ARXIV}doi")
            hits.append(
                SearchHit(
                    source_type="preprint",
                    title=_text(entry.find(f"{ATOM}title"))[:1000],
                    url=abs_url or None,
                    snippet=_text(entry.find(f"{ATOM}summary"))[:4000],
                    authors=[_text(a.find(f"{ATOM}name")) for a in entry.findall(f"{ATOM}author")][:50],
                    publication_date=_text(entry.find(f"{ATOM}published"))[:10] or None,
                    publisher="arXiv",
                    doi=_text(doi_el) or None,
                    arxiv_id=m.group(1) if m else None,
                    provider=self.name,
                    trust_metadata={"peer_reviewed": False, "source": "arXiv API"},
                )
            )
        return hits


class CrossrefSearch:
    name = "crossref"
    BASE = "https://api.crossref.org/works"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        params: dict[str, str | int] = {
            "query": query[:300],
            "rows": max(1, min(limit, 50)),
            "select": "DOI,title,author,issued,container-title,publisher,URL,abstract,type",
        }
        try:
            with _client(self.transport) as c:
                r = c.get(self.BASE, params=params)
        except httpx.HTTPError as exc:
            raise SearchError(f"Crossref unreachable: {type(exc).__name__}", transient=True) from exc
        if r.status_code != 200:
            raise SearchError(f"Crossref returned HTTP {r.status_code}", transient=r.status_code >= 500)
        items = ((r.json() or {}).get("message") or {}).get("items") or []
        hits: list[SearchHit] = []
        for it in items:
            parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
            date = "-".join(str(p) for p in parts if p) or None
            abstract = re.sub(r"<[^>]+>", " ", str(it.get("abstract") or ""))
            hits.append(
                SearchHit(
                    source_type="paper"
                    if it.get("type") in ("journal-article", "proceedings-article")
                    else "publication",
                    title=" ".join(it.get("title") or [])[:1000] or "(untitled)",
                    url=it.get("URL"),
                    snippet=re.sub(r"\s+", " ", abstract).strip()[:4000],
                    authors=[f"{a.get('given', '')} {a.get('family', '')}".strip() for a in it.get("author") or []][
                        :50
                    ],
                    publication_date=date,
                    publisher=it.get("publisher") or " ".join(it.get("container-title") or []) or None,
                    doi=it.get("DOI"),
                    provider=self.name,
                    trust_metadata={
                        "registered_doi": bool(it.get("DOI")),
                        "type": it.get("type"),
                        "source": "Crossref API",
                    },
                )
            )
        return hits


@dataclass
class FetchedDocument:
    url: str
    final_url: str
    status: int
    content_type: str
    data: bytes
    sha256: str
    retrieved_at: str
    truncated: bool


def fetch_url(
    url: str, *, max_bytes: int | None = None, transport: httpx.BaseTransport | None = None
) -> FetchedDocument:
    """SSRF-safe GET: every hop is validated, redirects are followed manually (max 3), size-capped."""
    limit = max_bytes or get_settings().research_fetch_max_bytes
    current = validate_outbound_url(url)
    with _client(transport) as c:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with c.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        location = r.headers.get("location")
                        if not location:
                            raise SearchError("redirect without location")
                        current = validate_outbound_url(urljoin(current, location))
                        continue
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if r.status_code != 200:
                        raise SearchError(f"fetch returned HTTP {r.status_code}", transient=r.status_code >= 500)
                    if ctype and not ctype.startswith(ALLOWED_FETCH_TYPES):
                        raise SearchError(f"content type '{ctype}' is not allowed")
                    body = bytearray()
                    truncated = False
                    for chunk in r.iter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            truncated = True
                            del body[limit:]
                            break
                    data = bytes(body)
                    return FetchedDocument(
                        url=url,
                        final_url=current,
                        status=r.status_code,
                        content_type=ctype or "application/octet-stream",
                        data=data,
                        sha256=hashlib.sha256(data).hexdigest(),
                        retrieved_at=datetime.now(UTC).isoformat(),
                        truncated=truncated,
                    )
            except httpx.HTTPError as exc:
                raise SearchError(f"fetch failed: {type(exc).__name__}", transient=True) from exc
    raise SearchError("too many redirects")


def search_providers(transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    available = {"arxiv": ArxivSearch(transport), "crossref": CrossrefSearch(transport)}
    return {name: available[name] for name in get_settings().search_providers if name in available}
