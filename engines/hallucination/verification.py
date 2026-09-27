"""Claim verification against evidence sources.

Pipeline per claim: candidate source sentences → lexical coverage → numeric consistency → negation and
polarity checks → (optional) model NLI judgment → confidence aggregation → status.

Model judgments can refine but never solely determine a status (see ``combine_with_judgment``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from engines.common.text import content_tokens, split_sentences, stable_hash, truncate
from engines.common.types import ClaimStatus
from engines.evaluation.base import ClaimResult, ModelJudgment, RetrievedDoc
from engines.hallucination.claims import ExtractedClaim, parse_numbers

NEGATION = re.compile(r"\b(not|never|no|none|cannot|can't|isn't|aren't|wasn't|doesn't|don't|without)\b", re.I)
FREE_WORDS = re.compile(r"\b(free|no extra cost|at no cost|no charge|included at no)\b", re.I)
PRICE = re.compile(r"(\d+(?:\.\d+)?\s*(?:eur|usd|gbp|€|\$)|(?:€|\$)\s*\d)", re.I)
METHOD = "lexical-numeric-v1"


@dataclass
class SourceSentence:
    doc: RetrievedDoc
    text: str


@dataclass
class Verification:
    status: str
    support: float
    contradiction: float
    reason: str
    best: SourceSentence | None


def _sentences(docs: list[RetrievedDoc]) -> list[SourceSentence]:
    out: list[SourceSentence] = []
    for doc in docs:
        for sentence, _, _ in split_sentences(doc.text):
            out.append(SourceSentence(doc=doc, text=sentence))
    return out


def _numbers_match(claim_numbers: list[float], source_numbers: list[float]) -> bool:
    return all(any(abs(c - s) < 1e-9 for s in source_numbers) for c in claim_numbers)


def verify_claim(claim: ExtractedClaim, docs: list[RetrievedDoc]) -> Verification:
    claim_tokens = set(content_tokens(claim.resolved))
    if not docs or not claim_tokens:
        return Verification(
            ClaimStatus.UNVERIFIABLE, 0.0, 0.0, "No evidence sources were available for this claim.", None
        )
    claim_non_numeric = {t for t in claim_tokens if not re.fullmatch(r"[\d.,]+", t)}
    best_support: tuple[float, SourceSentence | None, str] = (0.0, None, "")
    best_contra: tuple[float, SourceSentence | None, str] = (0.0, None, "")
    for sent in _sentences(docs):
        sent_tokens = set(content_tokens(sent.text))
        if not sent_tokens:
            continue
        coverage = len(claim_non_numeric & sent_tokens) / max(len(claim_non_numeric), 1)
        source_numbers = parse_numbers(sent.text)
        support = coverage
        contradiction = 0.0
        reason = "Source sentence states the same facts."
        if claim.numbers:
            if _numbers_match(claim.numbers, source_numbers):
                support = min(1.0, coverage + 0.25)
                reason = "Numbers in the claim match the source."
            elif source_numbers and coverage >= 0.6:
                # Same subject (high token overlap) but a different figure → likely contradiction.
                contradiction = min(0.95, 0.4 + coverage * 0.5)
                support = coverage * 0.35
                reason = (
                    f"Source states {', '.join(_fmt(n) for n in source_numbers)} where the claim states "
                    f"{', '.join(_fmt(n) for n in claim.numbers)}."
                )
            else:
                support = coverage * 0.6
        if coverage >= 0.6 and bool(NEGATION.search(claim.resolved)) != bool(NEGATION.search(sent.text)):
            contradiction = max(contradiction, 0.6 + (coverage - 0.6) * 0.75)
            reason = "Claim and source differ in negation/polarity."
        if (
            coverage >= 0.4
            and FREE_WORDS.search(claim.resolved)
            and PRICE.search(sent.text)
            and not FREE_WORDS.search(sent.text)
        ):
            contradiction = max(contradiction, 0.85)
            reason = "Claim states it is free; the source states a price."
        if support > best_support[0]:
            best_support = (support, sent, reason if contradiction == 0 else "Source sentence states the same facts.")
        if contradiction > best_contra[0]:
            best_contra = (contradiction, sent, reason)
    # A source that supports the claim outweighs an unrelated sentence that looks contradictory.
    if best_support[0] >= 0.75:
        support, sentence, reason = best_support
        contradiction = 0.0
    elif best_contra[0] >= 0.6:
        contradiction, sentence, reason = best_contra
        support = best_support[0] * 0.4
    else:
        support, sentence, reason = best_support
        contradiction = 0.0
    doc_tokens = set(content_tokens(" ".join(d.title + " " + d.text for d in docs)))
    topical = len(claim_non_numeric & doc_tokens) / max(len(claim_non_numeric), 1)

    if contradiction >= 0.6 and sentence is not None:
        return Verification(ClaimStatus.CONTRADICTED, round(support, 3), round(contradiction, 3), reason, sentence)
    if support >= 0.75:
        return Verification(
            ClaimStatus.SUPPORTED, round(support, 3), 0.0, reason or "Source sentence states the same facts.", sentence
        )
    if support >= 0.5:
        return Verification(
            ClaimStatus.PARTIALLY_SUPPORTED,
            round(support, 3),
            0.0,
            "Source supports part of the claim; some details are not stated.",
            sentence,
        )
    if topical >= 0.3:
        detail = ""
        if claim.numbers:
            detail = f" No source states {', '.join(_fmt(n) for n in claim.numbers)}."
        return Verification(
            ClaimStatus.UNSUPPORTED,
            round(support, 3),
            0.0,
            "Sources about this topic do not contain this statement." + detail,
            sentence,
        )
    return Verification(
        ClaimStatus.UNVERIFIABLE,
        round(support, 3),
        0.0,
        "No available source covers this topic; the claim cannot be verified either way.",
        None,
    )


def combine_with_judgment(v: Verification, judgment: ModelJudgment | None) -> tuple[Verification, str]:
    """Aggregate deterministic verification with an optional NLI judgment (bounded influence)."""
    if judgment is None:
        return v, METHOD
    label = str(judgment.normalized.get("label", "")).lower()
    conf = judgment.confidence or 0.0
    method = f"{METHOD}+model-nli"
    if v.status == ClaimStatus.PARTIALLY_SUPPORTED and label == "entailment" and conf >= 0.8:
        return Verification(
            ClaimStatus.SUPPORTED, max(v.support, 0.75), 0.0, v.reason + " Model NLI agrees (entailment).", v.best
        ), method
    if (
        v.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED)
        and label == "contradiction"
        and conf >= 0.9
        and v.best is not None
    ):
        return Verification(
            ClaimStatus.CONTRADICTED,
            v.support,
            max(v.contradiction, 0.7),
            v.reason + " Model NLI indicates contradiction.",
            v.best,
        ), method
    return v, method


def to_result(claim: ExtractedClaim, v: Verification, method: str, now: datetime) -> ClaimResult:
    return ClaimResult(
        text=claim.text,
        normalized=claim.normalized,
        status=v.status,
        support_confidence=v.support,
        contradiction_confidence=v.contradiction,
        reason=v.reason,
        span_start=claim.start,
        span_end=claim.end,
        source_title=v.best.doc.title if v.best else None,
        source_url=v.best.doc.url if v.best else None,
        source_excerpt=truncate(v.best.text, 500) if v.best else None,
        source_hash=stable_hash(v.best.doc.text) if v.best else None,
        retrieved_at=now if v.best else None,
        verification_method=method,
    )


def _fmt(n: float) -> str:
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:g}"
