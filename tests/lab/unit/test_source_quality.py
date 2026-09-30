"""Deterministic source-quality assessment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engines.lab.source_quality import (
    SOURCE_QUALITY_METHOD,
    SourceMetadata,
    assess_source,
    classify_domain,
    normalize_domain,
)


def assess(**kwargs):
    return assess_source(SourceMetadata(**kwargs))


def test_peer_reviewed_journal_on_publisher_domain_is_high():
    trust = assess(
        url="https://www.nature.com/articles/s41586-020-2649-2",
        doi="10.1038/s41586-020-2649-2",
        type="journal-article",
        venue="Nature",
        is_peer_reviewed=True,
        cited_by_count=1200,
    )
    assert trust.tier == "high"
    assert trust.score >= 0.9
    assert trust.flags == []
    assert trust.domain == "nature.com"
    assert trust.domain_class == "publisher"
    assert trust.method == SOURCE_QUALITY_METHOD == "deterministic-rules-v1"
    assert any(rule.startswith("peer reviewed") for rule in trust.rules_applied)


def test_retracted_is_zero_regardless_of_other_signals():
    trust = assess(
        url="https://www.nature.com/articles/x",
        doi="10.1038/x",
        type="journal-article",
        is_peer_reviewed=True,
        cited_by_count=100_000,
        is_retracted=True,
    )
    assert trust.score == 0.0
    assert trust.tier == "low"
    assert "retracted" in trust.flags
    assert trust.rules_applied[-1] == "retracted: score forced to 0"


def test_retracted_with_no_other_metadata():
    trust = assess(is_retracted=True)
    assert trust.score == 0.0 and "retracted" in trust.flags


def test_arxiv_preprint_flagged_and_capped():
    trust = assess(
        url="https://arxiv.org/abs/2301.01234",
        doi="10.48550/arXiv.2301.01234",
        type="preprint",
        cited_by_count=10_000,
        venue="NeurIPS",
    )
    assert "preprint" in trust.flags
    assert trust.score <= 0.6
    assert trust.domain_class == "preprint_server"
    assert any("capped" in rule for rule in trust.rules_applied)


def test_preprint_domain_implies_preprint_flag_even_without_type():
    trust = assess(url="https://www.biorxiv.org/content/10.1101/2020.01.01.123456v1")
    assert "preprint" in trust.flags


def test_peer_reviewed_preprint_is_not_capped():
    trust = assess(url="https://arxiv.org/abs/1", type="preprint", is_peer_reviewed=True, cited_by_count=10_000)
    assert trust.score > 0.6


@pytest.mark.parametrize(
    ("url", "domain_class"),
    [
        ("https://www.nih.gov/research", "government"),
        ("https://www.gov.uk/guidance", "government"),
        ("https://www.who.int/publications/i/item/1", "government"),
        ("https://cs.stanford.edu/paper.pdf", "academic"),
        ("https://www.ox.ac.uk/research", "academic"),
        ("https://proceedings.neurips.cc/paper/2020", "publisher"),
        ("https://github.com/org/repo", "repository"),
        ("https://doi.org/10.1000/x", "index"),
        ("https://en.wikipedia.org/wiki/Test", "encyclopedia"),
        ("https://someone.medium.com/post", "self_published"),
        ("https://x.com/user/status/1", "self_published"),
        ("https://random-blog.example", "unknown"),
        ("http://192.168.1.10/paper", "ip_host"),
        ("http://localhost:8000/x", "ip_host"),
    ],
)
def test_domain_classes(url, domain_class):
    assert classify_domain(normalize_domain(url)) == domain_class


def test_self_published_web_page_is_low():
    trust = assess(url="https://medium.com/@someone/my-theory", type="web page")
    assert trust.tier == "low"
    assert "self_published" in trust.flags


def test_unknown_domain_flag():
    trust = assess(url="https://totally-unknown.example/article", type="web page")
    assert "unknown_domain" in trust.flags
    assert trust.tier == "low"


def test_government_web_page_is_medium():
    trust = assess(url="https://www.cdc.gov/page", source_type="web_page")
    assert trust.work_type == "web page"
    assert trust.tier == "medium"


def test_ip_host_and_insecure_transport_flags():
    trust = assess(url="http://10.0.0.1/paper.pdf", type="report")
    assert {"ip_host", "insecure_transport"} <= set(trust.flags)


def test_insufficient_metadata_is_unknown_tier():
    trust = assess()
    assert (trust.score, trust.tier) == (0.3, "unknown")
    assert trust.flags == ["insufficient_metadata"]


def test_citation_bonus_is_monotonic_and_capped():
    scores = [assess(type="journal-article", cited_by_count=count).score for count in (0, 10, 100, 1000, 10**6)]
    assert scores == sorted(scores)
    assert scores[-1] - scores[0] <= 0.1 + 1e-9


def test_doi_normalization_and_invalid_doi():
    good = assess(type="journal-article", doi="https://doi.org/10.1000/abc")
    assert any(rule.startswith("DOI present") for rule in good.rules_applied)
    bad = assess(type="journal-article", doi="not-a-doi")
    assert "invalid_doi" in bad.flags


@pytest.mark.parametrize(
    ("venue", "recognized"),
    [
        ("Nature", True),
        ("Nature Communications", True),
        ("Advances in Neural Information Processing Systems (NeurIPS)", True),
        ("Proceedings of ACL 2023", True),
        ("IEEE Transactions on Pattern Analysis and Machine Intelligence", True),
        ("Journal of Computer Science", False),
        ("Oracle Conference", False),
        ("International Journal of Science and Nature Studies", False),
    ],
)
def test_venue_recognition(venue, recognized):
    trust = assess(type="journal-article", venue=venue)
    assert any("recognized venue" in rule for rule in trust.rules_applied) is recognized


def test_recognized_publisher():
    trust = assess(type="book", publisher="Cambridge University Press")
    assert any("recognized venue" in rule for rule in trust.rules_applied)


def test_implausible_publication_year():
    assert "implausible_year" in assess(type="journal-article", publication_year=1200).flags
    assert (
        "implausible_year"
        in assess_source(SourceMetadata(type="journal-article", publication_year=2031), current_year=2026).flags
    )
    assert (
        "implausible_year"
        not in assess_source(SourceMetadata(type="journal-article", publication_year=2027), current_year=2026).flags
    )


def test_type_aliases_and_unknown_type():
    assert assess(type="proceedings-article").work_type == "proceedings"
    assert assess(type="posted-content").work_type == "preprint"
    trust = assess(type="hologram", url="https://nature.com/x")
    assert trust.work_type is None
    assert "unknown_type" in trust.flags


def test_assessment_is_deterministic_and_bounded():
    meta = SourceMetadata(url="https://pnas.org/x", type="journal-article", is_peer_reviewed=True, cited_by_count=50)
    first, second = assess_source(meta), assess_source(meta)
    assert first == second
    assert 0.0 <= first.score <= 1.0


def test_model_output_fields_are_not_accepted():
    with pytest.raises(ValidationError):
        SourceMetadata(url="https://x.org", llm_quality_score=0.99)  # type: ignore[call-arg]


def test_normalize_domain():
    assert normalize_domain("HTTPS://User:pw@WWW.Example.ORG.:443/x") == "example.org"
    assert normalize_domain("sub.example.org") == "sub.example.org"
    assert normalize_domain("") is None
    assert normalize_domain("http://[::1") is None
