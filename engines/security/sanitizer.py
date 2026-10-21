"""Real-time payload sanitizer and indirect prompt injection guard.

Evaluates and sanitizes tool arguments and tool outputs to prevent indirect prompt
injections (e.g. malicious context documents, manipulated web scraping results, or
database records trying to hijack the agent loop).

Pure Python: no DB, FastAPI, or app services dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Delimiter tags commonly used to hijack LLM system / developer prompts
SYSTEM_DELIMITER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<\s*\|?\s*im_start\s*\|?\s*>system", re.I),
    re.compile(r"<\s*\|?\s*im_end\s*\|?\s*>", re.I),
    re.compile(r"\[\s*INST\s*\]|\s*\[\s*/\s*INST\s*\]", re.I),
    re.compile(r"<<\s*SYS\s*>>|\s*<<\s*/\s*SYS\s*>>", re.I),
    re.compile(r"<\s*system\s*>|<\s*/\s*system\s*>", re.I),
    re.compile(r"<\s*admin\s*>|<\s*/\s*admin\s*>", re.I),
    re.compile(r"-{3,}\s*(?:BEGIN|START)?\s*SYSTEM\s*(?:PROMPT|INSTRUCTIONS)?\s*-{3,}", re.I),
]

# Explicit role-hijack / instruction override directives
INSTRUCTION_OVERRIDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:ignore|disregard|forget|bypass)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules|guidelines)\b",
        re.I,
    ),
    re.compile(r"\b(?:new|updated)\s+(?:instructions|guidelines|system\s+prompt)\s*:\b", re.I),
    re.compile(
        r"\byou\s+are\s+now\s+(?:operating\s+in|acting\s+as|in)\s+(?:developer\s+mode|unrestricted\s+mode|DAN|jailbreak)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:override|disable|deactivate)\s+(?:safety|content|security)\s+(?:protocols|filters|guardrails)\b", re.I
    ),
    re.compile(r"\bfrom\s+now\s+on\s*,\s*you\s+(?:must|will)\s+(?:respond|act|obey)\b", re.I),
]

# Data exfiltration commands embedded in untrusted external context
DATA_EXFILTRATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:exfiltrate|send|post|transmit|forward)\s+(?:the\s+)?(?:secret|conversation|history|prompt|api\s*key|token|canary)\s+(?:to|towards)\s+https?://",
        re.I,
    ),
    re.compile(
        r"!\[.*?\]\s*\(\s*https?://[^\s)]+?(?:\?|&)(?:token|leak|data|key)=", re.I
    ),  # Markdown image pingback leak
]

# Zero-width / invisible characters used to evade tokenizers
INVISIBLE_CHAR_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF\u2060\u200E\u200F]")


@dataclass(frozen=True)
class ThreatSignal:
    category: str  # delimiter_injection | instruction_override | exfiltration | invisible_evasion
    pattern: str
    sample: str
    severity: str  # high | critical | medium


@dataclass
class SanitizationResult:
    threats_detected: bool
    threats: list[ThreatSignal] = field(default_factory=list)
    invisible_chars_stripped: int = 0
    delimiters_neutralized: int = 0

    @property
    def highest_severity(self) -> str | None:
        if not self.threats:
            return None
        severities = {t.severity for t in self.threats}
        if "critical" in severities:
            return "critical"
        if "high" in severities:
            return "high"
        return "medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "threats_detected": self.threats_detected,
            "threat_count": len(self.threats),
            "highest_severity": self.highest_severity,
            "invisible_chars_stripped": self.invisible_chars_stripped,
            "delimiters_neutralized": self.delimiters_neutralized,
            "threats": [
                {
                    "category": t.category,
                    "pattern": t.pattern,
                    "sample": t.sample[:120],
                    "severity": t.severity,
                }
                for t in self.threats
            ],
        }


def inspect_payload(text: str) -> SanitizationResult:
    """Inspect text for indirect prompt injection without modifying it."""
    threats: list[ThreatSignal] = []

    # 1. Delimiter checks
    for pat in SYSTEM_DELIMITER_PATTERNS:
        match = pat.search(text)
        if match:
            threats.append(
                ThreatSignal(
                    category="delimiter_injection",
                    pattern=pat.pattern,
                    sample=match.group(0),
                    severity="high",
                )
            )

    # 2. Instruction override checks
    for pat in INSTRUCTION_OVERRIDE_PATTERNS:
        match = pat.search(text)
        if match:
            threats.append(
                ThreatSignal(
                    category="instruction_override",
                    pattern=pat.pattern,
                    sample=match.group(0),
                    severity="critical",
                )
            )

    # 3. Exfiltration checks
    for pat in DATA_EXFILTRATION_PATTERNS:
        match = pat.search(text)
        if match:
            threats.append(
                ThreatSignal(
                    category="exfiltration",
                    pattern=pat.pattern,
                    sample=match.group(0),
                    severity="critical",
                )
            )

    # 4. Invisible evasion
    invisible_matches = len(INVISIBLE_CHAR_PATTERN.findall(text))
    if invisible_matches > 0:
        threats.append(
            ThreatSignal(
                category="invisible_evasion",
                pattern="invisible_unicode_chars",
                sample=f"{invisible_matches} zero-width character(s)",
                severity="medium",
            )
        )

    return SanitizationResult(
        threats_detected=len(threats) > 0,
        threats=threats,
        invisible_chars_stripped=invisible_matches,
    )


def sanitize_payload(text: str) -> tuple[str, SanitizationResult]:
    """Sanitize text by neutralizing delimiter tags and stripping invisible zero-width chars."""
    inspection = inspect_payload(text)

    # 1. Strip invisible characters
    sanitized = INVISIBLE_CHAR_PATTERN.sub("", text)
    invisible_count = len(text) - len(sanitized)

    # 2. Neutralize system delimiter tags so LLMs process them purely as benign data
    delimiters_count = 0
    for pat in SYSTEM_DELIMITER_PATTERNS:
        matches = len(pat.findall(sanitized))
        if matches:
            delimiters_count += matches
            sanitized = pat.sub("[SANITIZED_DELIMITER]", sanitized)

    result = SanitizationResult(
        threats_detected=inspection.threats_detected,
        threats=inspection.threats,
        invisible_chars_stripped=invisible_count,
        delimiters_neutralized=delimiters_count,
    )
    return sanitized, result


def sanitize_tool_data(
    tool_name: str,
    arguments: dict[str, Any],
    result_data: Any,
) -> dict[str, Any]:
    """Audit and sanitize tool inputs and outputs during agent execution."""
    arg_threats: list[dict[str, Any]] = []
    sanitized_args: dict[str, Any] = {}

    for k, v in arguments.items():
        if isinstance(v, str):
            clean_val, res = sanitize_payload(v)
            sanitized_args[k] = clean_val
            if res.threats_detected:
                arg_threats.append({"param": k, "threats": res.to_dict()})
        else:
            sanitized_args[k] = v

    output_threat: dict[str, Any] | None = None
    sanitized_output = result_data
    if isinstance(result_data, str):
        clean_res, res = sanitize_payload(result_data)
        sanitized_output = clean_res
        if res.threats_detected:
            output_threat = res.to_dict()
    elif isinstance(result_data, dict):
        sanitized_dict = {}
        nested_threats = []
        for k, v in result_data.items():
            if isinstance(v, str):
                clean_v, res = sanitize_payload(v)
                sanitized_dict[k] = clean_v
                if res.threats_detected:
                    nested_threats.append({"field": k, "threats": res.to_dict()})
            else:
                sanitized_dict[k] = v
        sanitized_output = sanitized_dict
        if nested_threats:
            output_threat = {"nested_threats": nested_threats}

    has_threats = bool(arg_threats or output_threat)
    return {
        "tool_name": tool_name,
        "threats_detected": has_threats,
        "argument_threats": arg_threats,
        "output_threat": output_threat,
        "sanitized_arguments": sanitized_args,
        "sanitized_output": sanitized_output,
    }
