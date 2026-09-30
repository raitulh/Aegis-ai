"""Deterministic source-quality assessment from bibliographic metadata (never from model output).

A transparent additive score: a base value by publication type, adjustments for peer review, DOI,
recognized venue, citation count and domain class (government / academic / publisher / preprint server /
repository / encyclopedia / self-published or social platform / unknown), then caps for preprints and a
hard zero for retracted works. Every applied rule is listed in ``rules_applied`` with its effect, so the
score can be audited and reproduced exactly.
"""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

SOURCE_QUALITY_METHOD = "deterministic-rules-v1"

Tier = Literal["high", "medium", "low", "unknown"]

TYPE_BASE_SCORES: dict[str, float] = {
    "journal-article": 0.7,
    "proceedings": 0.65,
    "book": 0.6,
    "book-chapter": 0.58,
    "report": 0.5,
    "dataset": 0.55,
    "thesis": 0.5,
    "preprint": 0.45,
    "web page": 0.35,
    "other": 0.4,
}
_TYPE_ALIASES: dict[str, str] = {
    "journal-article": "journal-article",
    "journal article": "journal-article",
    "article": "journal-article",
    "journal": "journal-article",
    "paper": "journal-article",
    "proceedings": "proceedings",
    "proceedings-article": "proceedings",
    "conference paper": "proceedings",
    "conference": "proceedings",
    "inproceedings": "proceedings",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "book-chapter": "book-chapter",
    "chapter": "book-chapter",
    "report": "report",
    "technical report": "report",
    "tech-report": "report",
    "dataset": "dataset",
    "thesis": "thesis",
    "dissertation": "thesis",
    "preprint": "preprint",
    "posted-content": "preprint",
    "web page": "web page",
    "webpage": "web page",
    "web_page": "web page",
    "web": "web page",
    "blog": "web page",
    "other": "other",
}

#: Domain classes (suffix match on the registrable host). Order matters: first match wins.
GOVERNMENT_SUFFIXES = ("gov", "mil", "gov.uk", "gc.ca", "gov.au", "europa.eu", "who.int", "un.org", "oecd.org")
ACADEMIC_SUFFIXES = ("edu", "ac.uk", "ac.jp", "edu.au", "ac.nz", "ac.in", "edu.cn", "ac.kr", "ethz.ch", "mpg.de")
PUBLISHER_DOMAINS = (
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "cell.com",
    "thelancet.com",
    "nejm.org",
    "bmj.com",
    "jamanetwork.com",
    "pnas.org",
    "acm.org",
    "ieee.org",
    "aclanthology.org",
    "openreview.net",
    "neurips.cc",
    "nips.cc",
    "mlr.press",
    "jmlr.org",
    "plos.org",
    "royalsocietypublishing.org",
    "tandfonline.com",
    "sagepub.com",
    "oup.com",
    "cambridge.org",
    "iop.org",
    "aps.org",
    "acs.org",
    "annualreviews.org",
    "biomedcentral.com",
    "frontiersin.org",
    "mdpi.com",
    "elifesciences.org",
    "aaai.org",
    "ijcai.org",
    "cvf.com",
    "thecvf.com",
    "usenix.org",
)
PREPRINT_DOMAINS = (
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "chemrxiv.org",
    "ssrn.com",
    "osf.io",
    "psyarxiv.com",
    "preprints.org",
    "researchsquare.com",
    "techrxiv.org",
    "eartharxiv.org",
)
REPOSITORY_DOMAINS = (
    "github.com",
    "gitlab.com",
    "zenodo.org",
    "figshare.com",
    "huggingface.co",
    "kaggle.com",
    "paperswithcode.com",
    "dataverse.org",
    "dryad.org",
    "datadryad.org",
)
INDEX_DOMAINS = (
    "doi.org",
    "crossref.org",
    "openalex.org",
    "semanticscholar.org",
    "dblp.org",
    "europepmc.org",
    "scholar.google.com",
)
ENCYCLOPEDIA_DOMAINS = ("wikipedia.org", "britannica.com", "scholarpedia.org")
SELF_PUBLISHED_DOMAINS = (
    "medium.com",
    "substack.com",
    "blogspot.com",
    "wordpress.com",
    "tumblr.com",
    "dev.to",
    "hashnode.dev",
    "ghost.io",
    "quora.com",
    "reddit.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "youtube.com",
    "tiktok.com",
    "instagram.com",
    "stackexchange.com",
    "stackoverflow.com",
)
#: Venue names recognized when the whole (normalized) venue string equals them.
EXACT_VENUES = frozenset(
    {
        "nature",
        "science",
        "cell",
        "the lancet",
        "lancet",
        "new england journal of medicine",
        "the new england journal of medicine",
        "nejm",
        "jama",
        "bmj",
        "pnas",
        "proceedings of the national academy of sciences",
        "elife",
        "jmlr",
        "journal of machine learning research",
    }
)
#: Venue families recognized when the venue string starts with them.
VENUE_PREFIXES = ("ieee transactions", "acm transactions", "physical review", "nature ", "plos ", "annual review")
#: Conference names/acronyms recognized as whole words anywhere in the venue string.
VENUE_WORDS = (
    "neurips",
    "neural information processing systems",
    "icml",
    "international conference on machine learning",
    "iclr",
    "international conference on learning representations",
    "acl",
    "emnlp",
    "naacl",
    "cvpr",
    "iccv",
    "eccv",
    "aaai",
    "ijcai",
    "kdd",
    "sigir",
)
RECOGNIZED_PUBLISHERS = (
    "springer",
    "elsevier",
    "wiley",
    "ieee",
    "acm",
    "oxford university press",
    "cambridge university press",
    "american physical society",
    "american chemical society",
    "royal society",
    "cell press",
    "public library of science",
    "mit press",
    "nature portfolio",
    "american association for the advancement of science",
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

DOMAIN_ADJUSTMENTS: dict[str, float] = {
    "government": 0.15,
    "academic": 0.1,
    "publisher": 0.1,
    "preprint_server": 0.0,
    "repository": -0.05,
    "index": 0.0,
    "encyclopedia": -0.05,
    "self_published": -0.2,
    "ip_host": -0.15,
    "unknown": -0.05,
}
PREPRINT_CAP = 0.6


class SourceMetadata(BaseModel):
    """Bibliographic metadata of a research source (as fetched from OpenAlex/Crossref/arXiv or the page)."""

    model_config = ConfigDict(extra="forbid")

    source_type: str | None = Field(default=None, description="paper|preprint|web_page|dataset|upload|book|other")
    url: str | None = None
    doi: str | None = None
    venue: str | None = None
    publisher: str | None = None
    type: str | None = Field(default=None, description="journal-article|preprint|proceedings|book|web page|…")
    is_peer_reviewed: bool | None = None
    is_retracted: bool | None = None
    cited_by_count: int | None = Field(default=None, ge=0)
    publication_year: int | None = None
    domain: str | None = None


class TrustMetadata(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    tier: Tier
    flags: list[str]
    rules_applied: list[str]
    method: str = SOURCE_QUALITY_METHOD
    domain: str | None = None
    domain_class: str | None = None
    work_type: str | None = None


def normalize_domain(value: str | None) -> str | None:
    """Lower-cased host without scheme, credentials, port, trailing dot or a leading ``www.``."""
    if not value:
        return None
    raw = value.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = urlsplit(raw).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def classify_domain(host: str | None) -> str | None:
    """Return the domain class of ``host`` (see :data:`DOMAIN_ADJUSTMENTS`) or None without a host."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return "ip_host"
    except ValueError:
        pass
    if host == "localhost" or "." not in host:
        return "ip_host"
    for name, suffixes in (
        ("government", GOVERNMENT_SUFFIXES),
        ("academic", ACADEMIC_SUFFIXES),
        ("preprint_server", PREPRINT_DOMAINS),
        ("publisher", PUBLISHER_DOMAINS),
        ("index", INDEX_DOMAINS),
        ("repository", REPOSITORY_DOMAINS),
        ("encyclopedia", ENCYCLOPEDIA_DOMAINS),
        ("self_published", SELF_PUBLISHED_DOMAINS),
    ):
        if _matches(host, suffixes):
            return name
    return "unknown"


def _work_type(meta: SourceMetadata) -> str | None:
    for candidate in (meta.type, meta.source_type):
        if candidate:
            key = candidate.strip().lower()
            mapped = _TYPE_ALIASES.get(key) or _TYPE_ALIASES.get(key.replace("_", " "))
            if mapped:
                return mapped
    return None


def _has_word(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _venue_recognized(meta: SourceMetadata) -> bool:
    venue = re.sub(r"\s+", " ", (meta.venue or "").strip().lower())
    publisher = re.sub(r"\s+", " ", (meta.publisher or "").strip().lower())
    if venue and (
        venue in EXACT_VENUES or venue.startswith(VENUE_PREFIXES) or any(_has_word(venue, word) for word in VENUE_WORDS)
    ):
        return True
    return bool(publisher) and any(_has_word(publisher, name) for name in RECOGNIZED_PUBLISHERS)


def _tier(score: float) -> Tier:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def assess_source(meta: SourceMetadata, *, current_year: int | None = None) -> TrustMetadata:
    """Score a source in ``[0, 1]`` with deterministic rules; see the module docstring for the method."""
    flags: list[str] = []
    rules: list[str] = []
    host = normalize_domain(meta.domain) or normalize_domain(meta.url)
    domain_class = classify_domain(host)
    work_type = _work_type(meta)
    doi = (meta.doi or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")
    has_doi = bool(_DOI_RE.match(doi))

    if not any((host, has_doi, work_type, meta.venue, meta.publisher)):
        flags.append("insufficient_metadata")
        if meta.is_retracted:
            flags.append("retracted")
            rules.append("retracted: score forced to 0")
            return TrustMetadata(score=0.0, tier="low", flags=flags, rules_applied=rules)
        rules.append("insufficient metadata: neutral 0.3")
        return TrustMetadata(score=0.3, tier="unknown", flags=flags, rules_applied=rules)

    base_type = work_type or "other"
    score = TYPE_BASE_SCORES[base_type]
    rules.append(f"type {base_type}: base {score:.2f}")
    if work_type is None:
        flags.append("unknown_type")

    is_preprint = base_type == "preprint" or domain_class == "preprint_server"
    if meta.is_peer_reviewed is True:
        score += 0.1
        rules.append("peer reviewed: +0.10")
    elif meta.is_peer_reviewed is False:
        score -= 0.05
        rules.append("not peer reviewed: -0.05")
        flags.append("not_peer_reviewed")
    if has_doi:
        score += 0.05
        rules.append("DOI present: +0.05")
    elif meta.doi:
        flags.append("invalid_doi")
        rules.append("malformed DOI: +0.00")
    if _venue_recognized(meta):
        score += 0.05
        rules.append("recognized venue/publisher: +0.05")
    if meta.cited_by_count:
        bonus = round(0.1 * min(1.0, math.log10(1 + meta.cited_by_count) / 3.0), 4)
        score += bonus
        rules.append(f"cited by {meta.cited_by_count}: +{bonus:.2f}")
    if domain_class is not None:
        adjustment = DOMAIN_ADJUSTMENTS[domain_class]
        score += adjustment
        rules.append(f"domain {host} ({domain_class}): {adjustment:+.2f}")
        if domain_class == "self_published":
            flags.append("self_published")
        elif domain_class == "unknown":
            flags.append("unknown_domain")
        elif domain_class == "ip_host":
            flags.append("ip_host")
        elif domain_class == "encyclopedia":
            flags.append("tertiary_source")
        elif domain_class == "repository":
            flags.append("repository")
    if meta.url and meta.url.strip().lower().startswith("http://"):
        score -= 0.02
        flags.append("insecure_transport")
        rules.append("plain http URL: -0.02")
    year = meta.publication_year
    if year is not None and (year < 1600 or (current_year is not None and year > current_year + 1)):
        score -= 0.1
        flags.append("implausible_year")
        rules.append(f"implausible publication year {year}: -0.10")
    if is_preprint:
        flags.append("preprint")
        if meta.is_peer_reviewed is not True and score > PREPRINT_CAP:
            score = PREPRINT_CAP
            rules.append(f"preprint without peer review: capped at {PREPRINT_CAP:.2f}")
    score = round(min(1.0, max(0.0, score)), 4)
    if meta.is_retracted:
        flags.append("retracted")
        rules.append("retracted: score forced to 0")
        score = 0.0
    return TrustMetadata(
        score=score,
        tier="low" if meta.is_retracted else _tier(score),
        flags=list(dict.fromkeys(flags)),
        rules_applied=rules,
        domain=host,
        domain_class=domain_class,
        work_type=work_type,
    )
