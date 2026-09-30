"""Literature search clients: OpenAlex and arXiv (pure IO — no database access).

``search_literature(query, sources, limit)`` queries every requested source, tolerates per-source
failures (partial results + warnings), merges the result lists round-robin (so no single source dominates
the top of the list) and de-duplicates by DOI, then canonical URL, then normalized title — merging the
metadata of duplicates (e.g. an arXiv preprint and its OpenAlex record). ``search_papers`` returns just the
records (the contract used by the ``paper_search`` tool).

Security:

* only the fixed OpenAlex/arXiv endpoints are called, over HTTPS; every URL (including each redirect hop,
  at most :data:`MAX_REDIRECTS`) must be on ``settings.tool_egress_allowlist_hosts`` and pass
  :func:`~aegis_api.security.ssrf.validate_outbound_url`; redirects are never followed automatically;
* responses are read as a bounded stream (:data:`MAX_RESPONSE_BYTES`); timeouts come from settings;
* arXiv returns Atom XML. ``defusedxml`` is not available, so any payload containing a ``DOCTYPE`` or
  ``ENTITY`` declaration is rejected *before* parsing: without a DTD the standard parser cannot expand
  entities (no billion-laughs / XXE);
* everything returned is untrusted bibliographic data — callers store it as data and never as instructions.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET  # DTD/ENTITY payloads are rejected before parsing
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import structlog
from pydantic import BaseModel, Field

from aegis_api.config import get_settings
from aegis_api.errors import ValidationFailed
from aegis_api.security.ssrf import validate_outbound_url
from engines.lab.sandbox import host_allowed

log = structlog.get_logger("aegis.lab.research.literature")

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
SUPPORTED_SOURCES: tuple[str, ...] = ("openalex", "arxiv")
MAX_LIMIT = 50
MAX_QUERY_CHARS = 500
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_ATTEMPTS = 2
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_AUTHORS = 100
MAX_ABSTRACT_WORDS = 5000
MAX_ABSTRACT_CHARS = 20_000
MAX_ARXIV_TERMS = 12
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"})
USER_AGENT = "aegis-lab/1.0 (literature search)"
RESOLVE_DNS = True  # tests with a mock transport switch DNS resolution off (the host allowlist still applies)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
_ARXIV_ID_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/)(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
)
_ARXIV_VERSION_RE = re.compile(r"v\d+$")
_DECLARATION_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_TITLE_STRIP_RE = re.compile(r"[^0-9a-z]+")
_ARXIV_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

SourceName = Literal["openalex", "arxiv"]
SourceType = Literal["paper", "preprint"]


class LiteratureSourceError(Exception):
    """One literature source failed (network, HTTP status, malformed payload). Never escapes the search."""


class TransientSourceError(LiteratureSourceError):
    """A retryable source failure (timeout, connection error, HTTP 429/5xx)."""


# --- records --------------------------------------------------------------------------------------
class PaperRecord(BaseModel):
    """Normalized bibliographic record from OpenAlex or arXiv (untrusted data)."""

    source_type: SourceType
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    canonical_url: str | None = None
    publication_date: date | None = None
    publication_year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    cited_by_count: int | None = None
    is_retracted: bool = False
    is_peer_reviewed: bool | None = None
    type: str | None = None
    origins: list[str] = Field(default_factory=list)


class SourceOutcome(BaseModel):
    source: str
    count: int = 0
    error: str | None = None
    latency_ms: int = 0


class LiteratureSearchResult(BaseModel):
    query: str
    records: list[PaperRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    outcomes: list[SourceOutcome] = Field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return bool(self.outcomes) and all(o.error is not None for o in self.outcomes)


# --- normalization --------------------------------------------------------------------------------
def normalize_doi(value: Any) -> str | None:
    """``10.x/y`` lower-cased without resolver prefixes; ``None`` when not a DOI."""
    if not isinstance(value, str):
        return None
    doi = value.strip()
    lowered = doi.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            doi = doi[len(prefix) :]
            break
    doi = doi.strip().rstrip(".").lower()
    return doi if _DOI_RE.match(doi) and len(doi) <= 255 else None


def canonical_url(url: Any) -> str | None:
    """Stable form of a URL for de-duplication: https, lower-case host, no fragment, no default port, no
    tracking parameters, no trailing slash; DOI and arXiv links collapse to their resolver form."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if host in ("doi.org", "dx.doi.org"):
        doi = normalize_doi(parts.path.lstrip("/"))
        if doi:
            return f"https://doi.org/{doi}"
    arxiv = arxiv_id_from_url(url)
    if arxiv and host.endswith("arxiv.org"):
        return f"https://arxiv.org/abs/{arxiv}"
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
        ]
    )
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    return urlunsplit(("https", netloc, path, query, ""))[:2000]


def normalize_title(title: Any) -> str:
    """Case-, accent- and punctuation-insensitive title key."""
    if not isinstance(title, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return _WS_RE.sub(" ", _TITLE_STRIP_RE.sub(" ", ascii_only)).strip()


def arxiv_id_from_url(url: Any) -> str | None:
    """Version-less arXiv identifier from an abs/pdf URL."""
    if not isinstance(url, str):
        return None
    match = _ARXIV_ID_RE.search(url)
    return match.group("id") if match else None


def _clean(text: Any, limit: int | None = None) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = "".join(ch for ch in unicodedata.normalize("NFC", text) if ch.isprintable() or ch in "\n\t")
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned[:limit] if limit else cleaned


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def reconstruct_abstract(index: Any, *, max_words: int = MAX_ABSTRACT_WORDS) -> str | None:
    """Rebuild an abstract from OpenAlex's ``abstract_inverted_index`` ({word: [positions]}).

    Positions outside ``[0, max_words)`` are ignored, so a hostile payload cannot force a huge allocation.
    """
    if not isinstance(index, dict) or not index:
        return None
    positions: dict[int, str] = {}
    for word, places in index.items():
        if not isinstance(word, str) or not isinstance(places, list):
            continue
        for place in places:
            if isinstance(place, int) and not isinstance(place, bool) and 0 <= place < max_words:
                positions.setdefault(place, word)
    if not positions:
        return None
    return _clean(" ".join(positions[i] for i in sorted(positions)), MAX_ABSTRACT_CHARS)


# --- OpenAlex -------------------------------------------------------------------------------------
def _openalex_short_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1][:64] or None


def parse_openalex_work(work: Any) -> PaperRecord | None:
    """One OpenAlex ``Work`` → :class:`PaperRecord` (``None`` for records without a title)."""
    if not isinstance(work, dict):
        return None
    title = _clean(work.get("title") or work.get("display_name"), 1000)
    if not title:
        return None
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    doi = normalize_doi(work.get("doi")) or normalize_doi(ids.get("doi"))
    primary = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    host = primary.get("source") if isinstance(primary.get("source"), dict) else {}
    external: dict[str, str] = {}
    openalex_id = _openalex_short_id(work.get("id") or ids.get("openalex"))
    if openalex_id:
        external["openalex"] = openalex_id
    locations = [loc for loc in work.get("locations") or [] if isinstance(loc, dict)]
    for location in [primary, *locations]:
        for key in ("landing_page_url", "pdf_url"):
            arxiv = arxiv_id_from_url(location.get(key))
            if arxiv:
                external.setdefault("arxiv", arxiv)
    for key in ("pmid", "pmcid", "mag"):
        value = ids.get(key)
        if isinstance(value, str | int) and str(value):
            external[key] = _openalex_short_id(str(value)) or str(value)
    crossref_type = work.get("type_crossref") if isinstance(work.get("type_crossref"), str) else None
    oa_type = work.get("type") if isinstance(work.get("type"), str) else None
    is_preprint = oa_type == "preprint" or crossref_type == "posted-content" or host.get("type") == "repository"
    landing = primary.get("landing_page_url") if isinstance(primary.get("landing_page_url"), str) else None
    url = f"https://doi.org/{doi}" if doi else landing or (work.get("id") if isinstance(work.get("id"), str) else None)
    authors: list[str] = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        name = _clean(author.get("display_name"), 200) if isinstance(author, dict) else None
        if name:
            authors.append(name)
        if len(authors) >= MAX_AUTHORS:
            break
    year = work.get("publication_year")
    cited = work.get("cited_by_count")
    return PaperRecord(
        source_type="preprint" if is_preprint else "paper",
        title=title,
        authors=authors,
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        doi=doi,
        url=url,
        canonical_url=canonical_url(url),
        publication_date=_parse_date(work.get("publication_date")),
        publication_year=year if isinstance(year, int) and not isinstance(year, bool) else None,
        venue=_clean(host.get("display_name"), 300),
        publisher=_clean(host.get("host_organization_name"), 300),
        external_ids=external,
        cited_by_count=cited if isinstance(cited, int) and not isinstance(cited, bool) and cited >= 0 else None,
        is_retracted=work.get("is_retracted") is True,
        is_peer_reviewed=None,
        type=crossref_type or oa_type,
        origins=["openalex"],
    )


def parse_openalex_response(payload: Any) -> list[PaperRecord]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise LiteratureSourceError("OpenAlex returned an unexpected payload")
    return [record for work in payload["results"] if (record := parse_openalex_work(work)) is not None]


# --- arXiv ----------------------------------------------------------------------------------------
def arxiv_search_query(query: str) -> str:
    """``all:term1 AND all:term2 …`` — only word characters reach arXiv's query syntax."""
    terms = _ARXIV_TERM_RE.findall(query)[:MAX_ARXIV_TERMS]
    if not terms:
        raise ValidationFailed("The query has no searchable terms for arXiv")
    return " AND ".join(f"all:{term}" for term in terms)


def _reject_dtd(data: bytes) -> None:
    if _DECLARATION_RE.search(data):
        raise LiteratureSourceError("arXiv response contains a DOCTYPE/ENTITY declaration and was rejected")


def parse_arxiv_feed(data: bytes | str) -> list[PaperRecord]:
    """Parse an arXiv Atom feed. Payloads with DTD/entity declarations are rejected before parsing."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    _reject_dtd(raw)
    try:
        root = ET.fromstring(raw)  # noqa: S314 - no DTD possible (rejected above), so no entity expansion
    except ET.ParseError as exc:
        raise LiteratureSourceError("arXiv returned malformed XML") from exc
    if root.tag != f"{ATOM}feed":
        raise LiteratureSourceError("arXiv returned an unexpected document")
    records: list[PaperRecord] = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = entry.findtext(f"{ATOM}id") or ""
        if "/api/errors" in entry_id:
            message = _clean(entry.findtext(f"{ATOM}summary"), 200) or "query error"
            raise LiteratureSourceError(f"arXiv rejected the query: {message}")
        arxiv_id = arxiv_id_from_url(entry_id)
        title = _clean(entry.findtext(f"{ATOM}title"), 1000)
        if not arxiv_id or not title:
            continue
        authors = [
            name
            for author in entry.findall(f"{ATOM}author")[:MAX_AUTHORS]
            if (name := _clean(author.findtext(f"{ATOM}name"), 200))
        ]
        published = _parse_date(entry.findtext(f"{ATOM}published"))
        doi = normalize_doi(entry.findtext(f"{ARXIV_NS}doi"))
        journal_ref = _clean(entry.findtext(f"{ARXIV_NS}journal_ref"), 300)
        category = entry.find(f"{ARXIV_NS}primary_category")
        url = f"https://arxiv.org/abs/{arxiv_id}"
        external = {"arxiv": arxiv_id}
        if category is not None and category.get("term"):
            external["arxiv_category"] = str(category.get("term"))[:40]
        records.append(
            PaperRecord(
                source_type="preprint",
                title=title,
                authors=authors,
                abstract=_clean(entry.findtext(f"{ATOM}summary"), MAX_ABSTRACT_CHARS),
                doi=doi,
                url=url,
                canonical_url=canonical_url(url),
                publication_date=published,
                publication_year=published.year if published else None,
                venue=journal_ref or "arXiv",
                publisher=None,
                external_ids=external,
                cited_by_count=None,
                is_retracted=False,
                is_peer_reviewed=False if not journal_ref else None,
                type="preprint",
                origins=["arxiv"],
            )
        )
    return records


# --- merging --------------------------------------------------------------------------------------
def _keys(record: PaperRecord) -> list[str]:
    keys: list[str] = []
    if record.doi:
        keys.append(f"doi:{record.doi}")
    if record.external_ids.get("arxiv"):
        keys.append(f"arxiv:{_ARXIV_VERSION_RE.sub('', record.external_ids['arxiv'])}")
    if record.canonical_url:
        keys.append(f"url:{record.canonical_url}")
    title = normalize_title(record.title)
    if len(title) >= 12:
        keys.append(f"title:{title}")
    return keys


def merge_records(primary: PaperRecord, other: PaperRecord) -> PaperRecord:
    """Fill gaps in ``primary`` from ``other``; a published version wins over a preprint."""
    data = primary.model_dump()
    for field in ("abstract", "doi", "url", "publication_date", "publication_year", "venue", "publisher", "type"):
        if data.get(field) in (None, "") and getattr(other, field) not in (None, ""):
            data[field] = getattr(other, field)
    if not data["authors"] and other.authors:
        data["authors"] = list(other.authors)
    data["external_ids"] = {**other.external_ids, **primary.external_ids}
    counts = [c for c in (primary.cited_by_count, other.cited_by_count) if c is not None]
    data["cited_by_count"] = max(counts) if counts else None
    data["is_retracted"] = primary.is_retracted or other.is_retracted
    if primary.source_type == "paper" or other.source_type == "paper":
        data["source_type"] = "paper"
        if primary.source_type == "preprint" and other.source_type == "paper":
            for field in ("venue", "publisher", "type", "url", "doi"):
                if getattr(other, field):
                    data[field] = getattr(other, field)
            data["is_peer_reviewed"] = other.is_peer_reviewed
    data["origins"] = list(dict.fromkeys([*primary.origins, *other.origins]))
    data["canonical_url"] = (
        f"https://doi.org/{data['doi']}" if data.get("doi") else data.get("canonical_url") or canonical_url(data["url"])
    )
    return PaperRecord.model_validate(data)


def merge_result_lists(lists: Sequence[Sequence[PaperRecord]], limit: int) -> list[PaperRecord]:
    """Round-robin interleave (rank 1 of each source, then rank 2 …) and de-duplicate."""
    interleaved: list[PaperRecord] = []
    for position in range(max((len(items) for items in lists), default=0)):
        interleaved.extend(items[position] for items in lists if position < len(items))
    merged: list[PaperRecord] = []
    index: dict[str, int] = {}
    for record in interleaved:
        slot = next((index[key] for key in _keys(record) if key in index), None)
        if slot is None:
            merged.append(record)
            slot = len(merged) - 1
        else:
            merged[slot] = merge_records(merged[slot], record)
        for key in _keys(merged[slot]):
            index.setdefault(key, slot)
    return merged[:limit]


# --- HTTP -----------------------------------------------------------------------------------------
def build_http_client(timeout: float | None = None) -> httpx.Client:
    """The HTTP client used for literature sources (redirects are handled manually)."""
    seconds = timeout or get_settings().tool_default_timeout_seconds
    return httpx.Client(
        timeout=httpx.Timeout(seconds, connect=min(10.0, seconds)),
        follow_redirects=False,
        headers={"user-agent": USER_AGENT, "accept": "application/json, application/atom+xml;q=0.9"},
    )


def check_url(url: str) -> str:
    """Allowlisted HTTPS host + SSRF validation (raises :class:`LiteratureSourceError`)."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise LiteratureSourceError("invalid URL") from exc
    if not host or not host_allowed(host, get_settings().tool_egress_allowlist_hosts):
        raise LiteratureSourceError(f"host '{host or '?'}' is not on the research egress allowlist")
    try:
        return validate_outbound_url(url, allowed_schemes=frozenset({"https"}), resolve=RESOLVE_DNS)
    except ValidationFailed as exc:
        raise LiteratureSourceError(f"URL rejected: {exc.message}") from exc


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise LiteratureSourceError(f"response exceeds {max_bytes} bytes")
    buffer = bytearray()
    for chunk in response.iter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise LiteratureSourceError(f"response exceeds {max_bytes} bytes")
    return bytes(buffer)


def fetch(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """GET with manual, re-validated redirects, bounded body and one retry on transient failures."""
    target = f"{url}?{urlencode(params)}" if params else url
    last_error: TransientSourceError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _fetch_once(client, target, max_bytes)
        except TransientSourceError as exc:
            last_error = exc
        if attempt + 1 < MAX_ATTEMPTS:
            sleep(1.0 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _fetch_once(client: httpx.Client, target: str, max_bytes: int) -> bytes:
    current = target
    for _hop in range(MAX_REDIRECTS + 1):
        current = check_url(current)
        try:
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise LiteratureSourceError("redirect without a Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code in RETRY_STATUSES:
                    raise TransientSourceError(f"HTTP {response.status_code}")
                if response.status_code != 200:
                    raise LiteratureSourceError(f"HTTP {response.status_code}")
                return _read_bounded(response, max_bytes)
        except httpx.TimeoutException as exc:
            raise TransientSourceError("request timed out") from exc
        except httpx.HTTPError as exc:
            raise TransientSourceError(f"network error ({type(exc).__name__})") from exc
    raise LiteratureSourceError(f"more than {MAX_REDIRECTS} redirects")


def search_openalex(client: httpx.Client, query: str, limit: int, **kwargs: Any) -> list[PaperRecord]:
    params: dict[str, Any] = {"search": query, "per-page": limit}
    mailto = get_settings().openalex_mailto
    if mailto:
        params["mailto"] = mailto
    body = fetch(client, OPENALEX_WORKS_URL, params, **kwargs)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise LiteratureSourceError("OpenAlex returned invalid JSON") from exc
    return parse_openalex_response(payload)[:limit]


def search_arxiv(client: httpx.Client, query: str, limit: int, **kwargs: Any) -> list[PaperRecord]:
    params = {"search_query": arxiv_search_query(query), "start": 0, "max_results": limit, "sortBy": "relevance"}
    return parse_arxiv_feed(fetch(client, ARXIV_QUERY_URL, params, **kwargs))[:limit]


SEARCHERS: dict[str, Callable[..., list[PaperRecord]]] = {"openalex": search_openalex, "arxiv": search_arxiv}


def validate_sources(sources: Iterable[str] | None) -> list[str]:
    """Requested source names (defaults to ``settings.paper_search_source_list``), validated and de-duplicated."""
    names = [s.strip().lower() for s in (sources if sources is not None else get_settings().paper_search_source_list)]
    names = list(dict.fromkeys(n for n in names if n))
    unknown = [n for n in names if n not in SUPPORTED_SOURCES]
    if unknown:
        raise ValidationFailed(
            f"Unknown literature source(s): {', '.join(unknown)}. Supported: {', '.join(SUPPORTED_SOURCES)}"
        )
    if not names:
        raise ValidationFailed("At least one literature source is required")
    return names


def validate_query(query: str) -> str:
    cleaned = _clean(query)
    if not cleaned:
        raise ValidationFailed("A search query is required")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise ValidationFailed(f"The search query must be at most {MAX_QUERY_CHARS} characters")
    return cleaned


def search_literature(
    query: str,
    sources: Sequence[str] | None = None,
    limit: int = 20,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LiteratureSearchResult:
    """Query each source (sequentially, bounded), tolerate per-source failures, merge and de-duplicate."""
    text = validate_query(query)
    names = validate_sources(sources)
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise ValidationFailed(f"limit must be between 1 and {MAX_LIMIT}")
    own_client = client is None
    http = client or build_http_client()
    lists: list[list[PaperRecord]] = []
    outcomes: list[SourceOutcome] = []
    warnings: list[str] = []
    try:
        for name in names:
            started = time.perf_counter()
            try:
                records = SEARCHERS[name](http, text, int(limit), sleep=sleep)
                lists.append(records)
                outcomes.append(
                    SourceOutcome(
                        source=name, count=len(records), latency_ms=int((time.perf_counter() - started) * 1000)
                    )
                )
            except (LiteratureSourceError, ValidationFailed) as exc:
                message = exc.message if isinstance(exc, ValidationFailed) else str(exc)
                warnings.append(f"{name}: {message}")
                outcomes.append(
                    SourceOutcome(source=name, error=message, latency_ms=int((time.perf_counter() - started) * 1000))
                )
                log.warning("literature_source_failed", source=name, error=message)
    finally:
        if own_client:
            http.close()
    return LiteratureSearchResult(
        query=text, records=merge_result_lists(lists, int(limit)), warnings=warnings, outcomes=outcomes
    )


def search_papers(
    query: str,
    sources: Sequence[str] | None = None,
    limit: int = 20,
    *,
    client: httpx.Client | None = None,
) -> list[PaperRecord]:
    """Records only (per-source failures are logged and tolerated)."""
    return search_literature(query, sources, limit, client=client).records


def citation_string(record: PaperRecord) -> str:
    """Short human-readable citation (``Authors (Year). Title. Venue. doi``)."""
    authors = record.authors[:3]
    who = ", ".join(authors) + (" et al." if len(record.authors) > 3 else "") if authors else "Unknown authors"
    year = record.publication_year or (record.publication_date.year if record.publication_date else None)
    parts = [f"{who} ({year if year else 'n.d.'}). {record.title}."]
    if record.venue:
        parts.append(f"{record.venue}.")
    if record.doi:
        parts.append(f"https://doi.org/{record.doi}")
    elif record.url:
        parts.append(record.url)
    return " ".join(parts)[:2000]
