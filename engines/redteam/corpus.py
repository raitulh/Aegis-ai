"""Adversarial probe corpus loading.

Aegis ships with **no built-in attack payloads**. Organizations supply their own red-team corpus (for
example an open-source adversarial dataset they have vetted) as JSON/YAML, or register one programmatically.
Each probe is a data record describing a placement, a benign success marker (nonce/canary) and the
policy-relevant expected behaviour — never operational harmful content.

Probe record schema::

    {
      "key": "unique-key",
      "category": "prompt_injection",       # taxonomy bucket (free-form)
      "technique": "short-label",           # how it is delivered
      "payload": "text, may contain {nonce}",
      "placement": "user | context_document | candidate_note | fetched_page",
      "detection": {"type": "nonce" | "canary" | "regex" | "tool_called" | "score_at_least" | "safety_compliance", ...},
      "expected_behavior": "what a compliant system should do",
      "severity": "low | medium | high | critical",
      "domains": ["*"]                       # optional: restrict to system profiles/domains
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engines.common.text import stable_hash

VALID_PLACEMENTS = frozenset({"user", "context_document", "candidate_note", "fetched_page"})
VALID_DETECTIONS = frozenset({"nonce", "canary", "regex", "tool_called", "score_at_least", "safety_compliance"})


@dataclass(frozen=True)
class ProbeTemplate:
    key: str
    category: str
    technique: str
    payload: str
    placement: str
    detection: dict[str, Any]
    expected_behavior: str
    severity: str = "medium"
    domains: tuple[str, ...] = ("*",)

    def applies_to(self, domain: str) -> bool:
        return "*" in self.domains or domain in self.domains


class CorpusError(ValueError):
    """A probe corpus record is malformed."""


def _coerce(record: dict[str, Any]) -> ProbeTemplate:
    missing = [
        k
        for k in ("key", "category", "technique", "payload", "placement", "detection", "expected_behavior")
        if k not in record
    ]
    if missing:
        raise CorpusError(f"probe missing required fields: {', '.join(missing)}")
    placement = str(record["placement"])
    if placement not in VALID_PLACEMENTS:
        raise CorpusError(f"invalid placement '{placement}' (allowed: {sorted(VALID_PLACEMENTS)})")
    detection = record["detection"]
    if not isinstance(detection, dict) or detection.get("type") not in VALID_DETECTIONS:
        raise CorpusError(f"invalid detection for probe '{record['key']}' (allowed types: {sorted(VALID_DETECTIONS)})")
    severity = str(record.get("severity", "medium"))
    if severity not in {"low", "medium", "high", "critical"}:
        raise CorpusError(f"invalid severity '{severity}' for probe '{record['key']}'")
    domains = record.get("domains") or ["*"]
    return ProbeTemplate(
        key=str(record["key"]),
        category=str(record["category"]),
        technique=str(record["technique"]),
        payload=str(record["payload"]),
        placement=placement,
        detection=dict(detection),
        expected_behavior=str(record["expected_behavior"]),
        severity=severity,
        domains=tuple(str(d) for d in domains),
    )


@dataclass
class ProbeCorpus:
    """A named, versioned collection of adversarial probes."""

    name: str = "empty"
    version: str = "0"
    probes: list[ProbeTemplate] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.probes

    def for_domain(self, domain: str) -> list[ProbeTemplate]:
        return [p for p in self.probes if p.applies_to(domain)]

    def by_key(self, key: str) -> ProbeTemplate | None:
        return next((p for p in self.probes if p.key == key), None)

    @classmethod
    def from_records(cls, records: list[dict[str, Any]], *, name: str = "custom", version: str = "1") -> ProbeCorpus:
        seen: set[str] = set()
        probes: list[ProbeTemplate] = []
        for record in records:
            probe = _coerce(record)
            if probe.key in seen:
                raise CorpusError(f"duplicate probe key '{probe.key}'")
            seen.add(probe.key)
            probes.append(probe)
        return cls(name=name, version=version, probes=probes)

    @classmethod
    def load_file(cls, path: str | Path) -> ProbeCorpus:
        p = Path(path)
        raw = p.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if p.suffix in (".yaml", ".yml") else json.loads(raw)
        if isinstance(data, dict):
            records = data.get("probes", [])
            return cls.from_records(records, name=data.get("name", p.stem), version=str(data.get("version", "1")))
        if isinstance(data, list):
            return cls.from_records(data, name=p.stem, version="1")
        raise CorpusError("corpus file must be a list of probes or an object with a 'probes' array")

    def digest(self) -> str:
        return stable_hash([p.key for p in self.probes] + [self.name, self.version], 16)


def make_nonce(*parts: Any) -> str:
    """Benign marker string used to detect whether an injected instruction was obeyed."""
    return "AEGIS-" + stable_hash(parts, 8).upper()


def render(template: ProbeTemplate, *, nonce: str) -> str:
    return template.payload.replace("{nonce}", nonce)


# The default corpus is intentionally empty. Aegis authors no attack payloads.
EMPTY_CORPUS = ProbeCorpus(name="empty", version="0", probes=[])


def default_corpus() -> ProbeCorpus:
    return EMPTY_CORPUS
