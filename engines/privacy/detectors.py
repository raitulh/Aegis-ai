"""PII and secret detection: regex + checksum validation + entropy checks + configurable custom rules.

Detectors favour precision (validated checksums, context keywords) to keep false positives low. Every
match carries its type, span, confidence and a masked preview; raw values are only returned to callers
that explicitly need them (evidence encryption), never logged.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from pydantic import BaseModel


class PIIMatch(BaseModel):
    type: str
    category: str  # contact | identifier | financial | secret | location | person
    start: int
    end: int
    value: str
    masked: str
    confidence: float
    detector: str


@dataclass
class CustomRule:
    name: str
    pattern: str
    category: str = "identifier"
    confidence: float = 0.8
    _compiled: re.Pattern[str] | None = field(default=None, repr=False)

    def compiled(self) -> re.Pattern[str]:
        if self._compiled is None:
            self._compiled = re.compile(self.pattern)
        return self._compiled


def mask_value(value: str, keep: int = 2) -> str:
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}{'•' * max(len(local) - 1, 2)}@{domain}"
    digits = sum(c.isalnum() for c in value)
    if digits <= keep * 2:
        return "•" * len(value)
    shown = value[-keep:]
    return re.sub(r"[A-Za-z0-9]", "•", value[:-keep]) + shown


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def iban_valid(value: str) -> bool:
    iban = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


def ssn_valid(value: str) -> bool:
    area, group, serial = value.split("-")
    return area not in {"000", "666"} and not area.startswith("9") and group != "00" and serial != "0000"


_Validator = Callable[[str], bool] | None

PATTERNS: list[tuple[str, str, re.Pattern[str], float, _Validator]] = [
    ("email", "contact", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 0.97, None),
    (
        "phone",
        "contact",
        re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{1,4}(?:[\s.-]\d{2,4}){2,4}(?!\w)"),
        0.8,
        lambda v: 9 <= sum(c.isdigit() for c in v) <= 15,
    ),
    ("us_ssn", "identifier", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0.9, ssn_valid),
    ("credit_card", "financial", re.compile(r"\b(?:\d[ -]?){13,19}\b"), 0.95, luhn_valid),
    (
        "iban",
        "financial",
        re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"),
        0.95,
        iban_valid,
    ),
    (
        "ipv4",
        "identifier",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        0.6,
        None,
    ),
    (
        "street_address",
        "location",
        re.compile(
            r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct)\b\.?"
        ),
        0.75,
        None,
    ),
    ("account_id", "identifier", re.compile(r"\b(?:LM|ACC|CUST|ACCT)-\d{4,}(?:-\d{2,})?\b"), 0.85, None),
    (
        "passport",
        "identifier",
        re.compile(r"\b(?:passport(?:\s*(?:no|number))?[:#\s]+)([A-Z0-9]{6,9})\b", re.I),
        0.7,
        None,
    ),
    # --- secrets ---
    ("aws_access_key", "secret", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), 0.98, None),
    ("github_token", "secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), 0.98, None),
    ("slack_token", "secret", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), 0.97, None),
    ("google_api_key", "secret", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0.97, None),
    ("openai_key", "secret", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"), 0.95, None),
    ("aegis_key", "secret", re.compile(r"\baeg_(?:live|test)_[A-Za-z0-9]{20,}\b"), 0.99, None),
    ("jwt", "secret", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0.9, None),
    ("private_key", "secret", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), 0.99, None),
    (
        "credential_assignment",
        "secret",
        re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?([^\s'\"]{6,})", re.I),
        0.85,
        None,
    ),
]

NAME_CONTEXT = re.compile(
    r"\b(?:customer|name|candidate|patient|employee|applicant|mr\.?|ms\.?|mrs\.?|dr\.?)\s*:?\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2})\b"
)
HIGH_ENTROPY_TOKEN = re.compile(r"\b[A-Za-z0-9_\-+/]{32,}\b")


class PIIDetector:
    def __init__(
        self,
        *,
        custom_rules: Iterable[CustomRule] = (),
        detect_names: bool = True,
        entropy_threshold: float = 4.2,
        disabled_types: Iterable[str] = (),
    ) -> None:
        self.custom_rules = list(custom_rules)
        self.detect_names = detect_names
        self.entropy_threshold = entropy_threshold
        self.disabled = set(disabled_types)

    def detect(self, text: str) -> list[PIIMatch]:
        if not text:
            return []
        matches: list[PIIMatch] = []
        for type_, category, pattern, confidence, validator in PATTERNS:
            if type_ in self.disabled:
                continue
            for m in pattern.finditer(text):
                group = 1 if m.groups() and m.group(1) else 0
                value = m.group(group)
                if validator and not validator(value):
                    continue
                matches.append(
                    PIIMatch(
                        type=type_,
                        category=category,
                        start=m.start(group),
                        end=m.end(group),
                        value=value,
                        masked=mask_value(value),
                        confidence=confidence,
                        detector="regex+validator" if validator else "regex",
                    )
                )
        if self.detect_names and "person_name" not in self.disabled:
            for m in NAME_CONTEXT.finditer(text):
                matches.append(
                    PIIMatch(
                        type="person_name",
                        category="person",
                        start=m.start(1),
                        end=m.end(1),
                        value=m.group(1),
                        masked=m.group(1)[0] + "•••• " + (m.group(1).split()[-1][0] + "••••"),
                        confidence=0.65,
                        detector="context-heuristic",
                    )
                )
        if "high_entropy_secret" not in self.disabled:
            for m in HIGH_ENTROPY_TOKEN.finditer(text):
                token = m.group(0)
                if (
                    shannon_entropy(token) >= self.entropy_threshold
                    and any(c.isdigit() for c in token)
                    and any(c.isalpha() for c in token)
                ):
                    matches.append(
                        PIIMatch(
                            type="high_entropy_secret",
                            category="secret",
                            start=m.start(),
                            end=m.end(),
                            value=token,
                            masked=mask_value(token, keep=4),
                            confidence=0.6,
                            detector="entropy",
                        )
                    )
        for rule in self.custom_rules:
            for m in rule.compiled().finditer(text):
                matches.append(
                    PIIMatch(
                        type=f"custom:{rule.name}",
                        category=rule.category,
                        start=m.start(),
                        end=m.end(),
                        value=m.group(0),
                        masked=mask_value(m.group(0)),
                        confidence=rule.confidence,
                        detector="custom-rule",
                    )
                )
        return _dedupe(matches)


def _dedupe(matches: list[PIIMatch]) -> list[PIIMatch]:
    """Keep the most specific/confident match for overlapping spans."""
    ordered = sorted(matches, key=lambda m: (m.start, -(m.end - m.start), -m.confidence))
    result: list[PIIMatch] = []
    for m in ordered:
        overlap = next((r for r in result if not (m.end <= r.start or m.start >= r.end)), None)
        if overlap is None:
            result.append(m)
        elif m.confidence > overlap.confidence and (m.end - m.start) >= (overlap.end - overlap.start):
            result[result.index(overlap)] = m
    return sorted(result, key=lambda m: m.start)


def redact(text: str, matches: list[PIIMatch] | None = None, detector: PIIDetector | None = None) -> str:
    """Replace detected values with masked previews (default presentation for evidence and UI)."""
    if not text:
        return text or ""
    if matches is None:
        matches = (detector or DEFAULT_DETECTOR).detect(text)
    out = []
    cursor = 0
    for m in sorted(matches, key=lambda x: x.start):
        if m.start < cursor:
            continue
        out.append(text[cursor : m.start])
        out.append(f"[{m.type.upper()}:{m.masked}]")
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out)


DEFAULT_DETECTOR = PIIDetector()


def summarize(matches: list[PIIMatch]) -> dict[str, int]:
    counts: Counter[str] = Counter(m.type for m in matches)
    return dict(counts)
