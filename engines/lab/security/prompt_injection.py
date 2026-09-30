"""Prompt-injection detection, sanitization and untrusted-data fencing.

External documents, web pages, MCP/tool outputs and even stored memories are treated as UNTRUSTED DATA. They
are never concatenated into instructions. Instead they are:

1. scanned (heuristic signals → score/severity; the report is stored with the content's provenance),
2. sanitized (invisible/bidi/tag characters removed, fake role headers neutralized, size-capped),
3. fenced inside a content-addressed boundary that the content itself cannot forge, and placed in the
   RESEARCH DATA / TOOL OUTPUT sections of the prompt, below the system and developer policy sections.

Heuristics cannot catch every attack; they reduce risk and feed policy (e.g. high-score content is
quarantined from durable memory). Authorization never depends on model compliance: tools are gated by the
ToolBroker regardless of what the model asks for.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["none", "low", "medium", "high"]

MAX_UNTRUSTED_CHARS = 60_000

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
_ROLE_HEADER = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:<\|?\s*)?(system|developer|assistant|user|tool)(?:\s*\|?>)?\s*[:>]\s*"
)
_CHAT_TOKENS = re.compile(r"<\|(?:im_start|im_end|system|endoftext|start_header_id|end_header_id|eot_id)\|>", re.I)
_FENCE_FORGERY = re.compile(r"<<<\s*/?\s*(?:END_)?UNTRUSTED_DATA", re.I)

_SIGNALS: list[tuple[str, float, re.Pattern[str]]] = [
    (
        "instruction_override",
        0.45,
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|all|earlier|system|developer)\b[^.\n]{0,40}\b(instructions?|prompts?|rules?|polic(y|ies)|messages?)\b",
            re.I,
        ),
    ),
    (
        "role_reassignment",
        0.35,
        re.compile(
            r"\b(you are now|from now on,? you|act as (an?|the) (unrestricted|jailbroken|developer mode)|pretend (that )?you)\b",
            re.I,
        ),
    ),
    (
        "system_prompt_probe",
        0.35,
        re.compile(
            r"\b(reveal|print|show|output|repeat)\b[^.\n]{0,30}\b(system prompt|hidden instructions|your instructions|developer message)\b",
            re.I,
        ),
    ),
    (
        "tool_invocation",
        0.3,
        re.compile(
            r"\b(call|invoke|execute|run|use)\b[^.\n]{0,30}\b(tool|function|command|shell|api)\b[^.\n]{0,60}\b(with|using)\b",
            re.I,
        ),
    ),
    (
        "exfiltration",
        0.4,
        re.compile(
            r"\b(send|post|upload|exfiltrate|forward|leak)\b[^.\n]{0,60}\b(secret|api[ _-]?key|token|password|credential|environment variables?)\b",
            re.I,
        ),
    ),
    ("markdown_exfil", 0.35, re.compile(r"!\[[^\]]*\]\(https?://[^)\s]+\?[^)\s]*=[^)\s]*\)", re.I)),
    (
        "policy_evasion",
        0.3,
        re.compile(
            r"\b(bypass|disable|turn off|circumvent)\b[^.\n]{0,40}\b(safety|guardrails?|filters?|policy|audit|approval)\b",
            re.I,
        ),
    ),
    (
        "privilege_escalation",
        0.35,
        re.compile(
            r"\b(grant|give|elevate|escalate)\b[^.\n]{0,30}\b(admin|owner|root|permissions?|autonomy|privileges?)\b",
            re.I,
        ),
    ),
    ("chat_template_tokens", 0.4, _CHAT_TOKENS),
    ("fence_forgery", 0.5, _FENCE_FORGERY),
    ("fake_role_header", 0.2, _ROLE_HEADER),
]
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


@dataclass
class InjectionSignal:
    kind: str
    weight: float
    excerpt: str


@dataclass
class InjectionReport:
    score: float
    severity: Severity
    signals: list[InjectionSignal] = field(default_factory=list)
    invisible_chars: int = 0
    truncated: bool = False

    @property
    def suspicious(self) -> bool:
        return self.severity in ("medium", "high")

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "severity": self.severity,
            "signals": [{"kind": s.kind, "weight": s.weight, "excerpt": s.excerpt} for s in self.signals],
            "invisible_chars": self.invisible_chars,
            "truncated": self.truncated,
        }


def _severity(score: float) -> Severity:
    if score >= 0.6:
        return "high"
    if score >= 0.35:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def detect(text: str) -> InjectionReport:
    invisible = len(_INVISIBLE.findall(text))
    normalized = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text))
    signals: list[InjectionSignal] = []
    for kind, weight, pattern in _SIGNALS:
        match = pattern.search(normalized)
        if match:
            signals.append(InjectionSignal(kind, weight, match.group(0)[:160]))
    if invisible >= 3:
        signals.append(InjectionSignal("invisible_characters", 0.25, f"{invisible} invisible/bidi characters"))
    if _BASE64_BLOB.search(normalized):
        signals.append(InjectionSignal("encoded_payload", 0.15, "long base64-like blob"))
    score = 1.0 - _product(1.0 - s.weight for s in signals) if signals else 0.0
    return InjectionReport(score=round(score, 3), severity=_severity(score), signals=signals, invisible_chars=invisible)


def _product(values: Any) -> float:
    out = 1.0
    for v in values:
        out *= v
    return out


def sanitize(text: str, *, max_chars: int = MAX_UNTRUSTED_CHARS) -> tuple[str, bool]:
    """Remove invisible characters, neutralize chat-template tokens / role headers / fence forgeries."""
    cleaned = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text))
    cleaned = "".join(ch for ch in cleaned if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    cleaned = _CHAT_TOKENS.sub("[token removed]", cleaned)
    cleaned = _FENCE_FORGERY.sub("[boundary removed]", cleaned)
    cleaned = _ROLE_HEADER.sub(lambda m: f"[quoted {m.group(1).lower()} label] ", cleaned)
    truncated = len(cleaned) > max_chars
    if truncated:
        cleaned = cleaned[:max_chars] + "\n[... truncated ...]"
    return cleaned, truncated


def wrap_untrusted(
    text: str, *, source: str, source_id: str | None = None, kind: str = "data"
) -> tuple[str, InjectionReport]:
    """Sanitize + fence untrusted content. The boundary nonce is derived from the sanitized content hash, so
    the content cannot contain its own closing boundary (and the rendering stays reproducible)."""
    report = detect(text)
    cleaned, truncated = sanitize(text)
    report.truncated = truncated
    nonce = hashlib.sha256(cleaned.encode()).hexdigest()[:16]
    safe_source = re.sub(r"[^A-Za-z0-9_.:/@-]", "_", source)[:120]
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]", "_", source_id or "")[:80]
    header = (
        f'<<<UNTRUSTED_DATA kind="{kind}" source="{safe_source}" id="{safe_id}" nonce="{nonce}" '
        f'injection_risk="{report.severity}">>>'
    )
    return f'{header}\n{cleaned}\n<<<END_UNTRUSTED_DATA nonce="{nonce}">>>', report
