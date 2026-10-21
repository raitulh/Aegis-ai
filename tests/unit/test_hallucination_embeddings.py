"""Unit tests for claim verification augmented with vector embeddings."""

from __future__ import annotations

from engines.common.types import ClaimStatus
from engines.evaluation.base import RetrievedDoc
from engines.hallucination.claims import extract_claims
from engines.hallucination.verification import verify_claim
from engines.providers.embeddings import HashEmbedder


def test_verify_claim_with_hash_embedder():
    claims = extract_claims("Aegis provides security and governance safeguards for multi-agent systems.")
    assert len(claims) >= 1
    claim = claims[0]

    docs = [
        RetrievedDoc(
            doc_id="doc-1",
            title="Aegis Overview",
            text="Aegis provides security and governance safeguards for multi-agent systems.",
        )
    ]

    embedder = HashEmbedder(dim=128)
    res = verify_claim(claim, docs, embedder=embedder)
    assert res.status in (ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED)
    assert res.support > 0.0
    assert res.best is not None


def test_verify_claim_unsupported_with_embedder():
    claims = extract_claims("The spacecraft visited Jupiter in the summer of 1984.")
    assert len(claims) >= 1
    claim = claims[0]

    docs = [
        RetrievedDoc(
            doc_id="doc-2",
            title="Cooking Recipe",
            text="Mix two cups of flour with milk and sugar.",
        )
    ]

    embedder = HashEmbedder(dim=128)
    res = verify_claim(claim, docs, embedder=embedder)
    assert res.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.UNVERIFIABLE)
