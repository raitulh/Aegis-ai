"""Claim extraction and normalisation (deterministic)."""

from __future__ import annotations

import re

from pydantic import BaseModel

from engines.common.text import split_sentences

NON_CLAIM_PREFIXES = (
    "i can't",
    "i cannot",
    "i'm not able",
    "i am not able",
    "i don't have",
    "i do not have",
    "please",
    "here is",
    "here's",
    "sure",
    "thanks",
    "thank you",
    "let me",
    "if you",
    "you can reach",
    "hello",
    "hi ",
    "summary:",
    "candidate assessment",
    "score:",
    "recommendation:",
    "source:",
)
HEDGES = re.compile(r"\b(might|may|possibly|perhaps|i think|i believe|likely|probably|it seems)\b", re.I)
PRONOUN_START = re.compile(r"^(it|they|he|she|the company|this company|the firm)\b", re.I)
FACT_SIGNAL = re.compile(
    r"(\d|\b(is|are|was|were|has|have|had|includes?|costs?|employs?|operates?|serves?|founded|located|"
    r"offers?|provides?|requires?|receives?|processed|led|holds?|published|grew|reached|fell|doubled|expect)\b)",
    re.I,
)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")
WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "hundred": 100,
    "thousand": 1000,
    "million": 1_000_000,
    "twice": 2,
    "double": 2,
    "triple": 3,
}


class ExtractedClaim(BaseModel):
    text: str
    normalized: str
    resolved: str  # with pronoun subject resolved
    start: int
    end: int
    numbers: list[float]
    hedged: bool


def parse_numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw in NUMBER.findall(text):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in WORD_NUMBERS and word not in ("one",):
            values.append(float(WORD_NUMBERS[word]))
    return values


def normalize_claim(text: str) -> str:
    text = text.lower()
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    text = re.sub(r"[^\w\s.%€$-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


_SUBJECT_VERB = re.compile(
    r"^(.*?)\s+(?:is|are|was|were|has|have|had|employs?|operates?|serves?|offers?|provides?|includes?|"
    r"costs?|was founded|were founded|will|holds?|published|grew|reached|fell|doubled)\b",
    re.I,
)
_PRONOUNS = {"it", "they", "he", "she", "this", "that", "these", "those"}


def _subject(sentence: str) -> str | None:
    """Return the sentence's subject noun phrase (the text before its first verb), if concrete."""
    match = _SUBJECT_VERB.match(sentence)
    if not match:
        return None
    phrase = match.group(1).strip()
    head = re.sub(r"^(the|a|an|this)\s+", "", phrase, flags=re.I)
    if not head or phrase.lower() in _PRONOUNS or len(phrase.split()) > 5:
        return None
    return phrase


def extract_claims(text: str, *, min_words: int = 4) -> list[ExtractedClaim]:
    claims: list[ExtractedClaim] = []
    subject: str | None = None
    for sentence, start, _end in split_sentences(text):
        clean = sentence.strip()
        lowered = clean.lower()
        if not clean or clean.endswith("?"):
            continue
        # Strip leading labels like "Summary:" while keeping the remainder as a candidate claim.
        label = re.match(r"^(summary|note|answer)\s*:\s*", clean, re.I)
        if label:
            clean = clean[label.end() :]
            start += label.end()
            lowered = clean.lower()
        if any(lowered.startswith(p) for p in NON_CLAIM_PREFIXES):
            continue
        if len(clean.split()) < min_words or not FACT_SIGNAL.search(clean):
            continue
        found_subject = _subject(clean)
        if found_subject:
            subject = found_subject
        resolved = clean
        starts_with_pronoun = bool(re.match(r"^(it|they|he|she)\b", clean, re.I))
        if starts_with_pronoun and subject and not found_subject:
            resolved = re.sub(r"^(it|they|he|she)\b", subject, clean, count=1, flags=re.I)
        claims.append(
            ExtractedClaim(
                text=clean,
                normalized=normalize_claim(resolved),
                resolved=resolved,
                start=start,
                end=start + len(clean),
                numbers=parse_numbers(clean),
                hedged=bool(HEDGES.search(clean)),
            )
        )
    return claims
