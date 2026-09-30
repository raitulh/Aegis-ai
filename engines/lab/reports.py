"""Research report assembly and citation validation.

The platform assembles the factual sections deterministically from stored records; the ReportAgent only adds
narrative. ``validate_citations`` ensures every ``[EV:<id>]`` citation in model-written text refers to a
known evidence id; unknown citations are stripped and reported (the model cannot invent evidence).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from engines.lab.verification.claims import overclaiming_terms

SECTIONS = (
    "Executive Summary",
    "Research Question",
    "Methodology",
    "Literature",
    "Hypotheses",
    "Experiments",
    "Results",
    "Failures",
    "Evolution",
    "Verification",
    "Limitations",
    "Evidence",
    "Reproducibility",
    "Appendix",
)
_CITATION = re.compile(r"\[EV:([A-Za-z0-9-]{8,64})\]")


@dataclass
class CitationCheck:
    text: str
    cited: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    uncited_numeric_sentences: int = 0
    overclaiming: list[str] = field(default_factory=list)


def validate_citations(text: str, known_ids: Iterable[str]) -> CitationCheck:
    known = set(known_ids)
    cited: list[str] = []
    unknown: list[str] = []

    def repl(match: re.Match[str]) -> str:
        ev = match.group(1)
        if ev in known:
            cited.append(ev)
            return match.group(0)
        unknown.append(ev)
        return "[citation removed: unknown evidence]"

    cleaned = _CITATION.sub(repl, text)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    uncited = sum(1 for s in sentences if re.search(r"\d", s) and not _CITATION.search(s))
    return CitationCheck(
        text=cleaned,
        cited=sorted(set(cited)),
        unknown=sorted(set(unknown)),
        uncited_numeric_sentences=uncited,
        overclaiming=overclaiming_terms(cleaned),
    )


def render_markdown(title: str, sections: dict[str, str], *, metadata: dict[str, Any] | None = None) -> str:
    lines = [f"# {title}", ""]
    for key, value in (metadata or {}).items():
        lines.append(f"- **{key}**: {value}")
    if metadata:
        lines.append("")
    for name in SECTIONS:
        body = sections.get(name)
        if body is None:
            continue
        lines += [f"## {name}", "", body.strip() or "_No content._", ""]
    return "\n".join(lines).rstrip() + "\n"
