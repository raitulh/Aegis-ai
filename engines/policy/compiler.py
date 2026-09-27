"""Policy compiler: natural-language policy → requirements → executable controls, with provenance.

Deterministic pipeline:
  1. Detect normative statements (must / must not / shall / required to / prohibited …) in each chunk.
  2. Normalise and classify each requirement into a control domain + test type using weighted keyword
     signals. Ambiguous or unclassifiable requirements are flagged ``needs_human_review``.
  3. Generate a stable control id (e.g. FAIR-003), a default threshold, severity and required evidence.
  4. Retain exact source provenance (document, page, section, excerpt, hash) on every requirement.

An optional model can be used to *refine* low-confidence classifications; it never invents requirements
that were not detected deterministically in the source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.common.text import normalize_whitespace, stable_hash
from engines.common.types import Severity, TestType
from engines.policy.dsl import DOMAIN_BY_TEST_TYPE, ControlDSL, PolicyDSL
from engines.policy.ingestion import Chunk

COMPILER_VERSION = "1.1.0"

MUST = re.compile(
    r"\b(must not|must never|shall not|may not|must|shall|is required to|are required to|required to|requires?|require|has to|have to|is prohibited from|are prohibited from|prohibited|is forbidden|forbidden|should not|should|will not|will)\b",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(must not|must never|shall not|may not|prohibited|forbidden|is prohibited from|are prohibited from|should not|will not|never|no )\b",
    re.I,
)

# domain signal -> (test_type, keyword weights)
DOMAIN_SIGNALS: dict[str, tuple[str, dict[str, float]]] = {
    "fairness": (
        TestType.COUNTERFACTUAL,
        {
            "protected": 3,
            "discriminat": 3,
            "bias": 3,
            "gender": 2,
            "race": 2,
            "ethnic": 2,
            "age": 1.5,
            "disability": 2,
            "religion": 2,
            "fair": 2,
            "equal": 1.5,
            "demographic": 2,
            "characteristic": 1.5,
        },
    ),
    "privacy": (
        TestType.PII_LEAKAGE,
        {
            "personal information": 3,
            "personal data": 3,
            "pii": 3,
            "confidential": 2,
            "sensitive": 1.5,
            "privacy": 2,
            "disclose": 1.5,
            "expose": 1.5,
            "leak": 2,
            "credential": 2,
            "secret": 2,
            "identifier": 1.5,
        },
    ),
    "truthfulness": (
        TestType.GROUNDEDNESS,
        {
            "accurate": 2,
            "accuracy": 2,
            "factual": 2.5,
            "hallucin": 3,
            "unsupported": 2,
            "verifi": 2,
            "cite": 2,
            "source": 2,
            "evidence": 1.5,
            "ground": 2,
            "mislead": 2,
        },
    ),
    "safety": (
        TestType.SAFETY_REFUSAL,
        {
            "harmful": 3,
            "unsafe": 2.5,
            "dangerous": 2,
            "abuse": 1.5,
            "illegal": 2,
            "self-harm": 3,
            "violence": 2,
            "hate": 2,
            "harassment": 2,
            "refuse": 1.5,
            "safety": 1.5,
        },
    ),
    "governance": (
        TestType.HUMAN_OVERSIGHT,
        {
            "human review": 3,
            "human oversight": 3,
            "human approval": 3,
            "reviewed by": 2,
            "sign-off": 2,
            "approv": 2,
            "oversight": 2.5,
            "escalat": 1.5,
            "final decision": 2,
            "authoriz": 1.5,
            "accountab": 1.5,
        },
    ),
    "security": (
        TestType.PROMPT_INJECTION,
        {
            "injection": 3,
            "manipulat": 2,
            "jailbreak": 3,
            "adversarial": 2,
            "override": 2,
            "exfiltrat": 2.5,
            "prompt": 1.5,
            "circumvent": 2,
            "bypass": 2,
        },
    ),
}

DOMAIN_PREFIX = {
    "fairness": "FAIR",
    "privacy": "PRIV",
    "truthfulness": "TRUTH",
    "safety": "SAFE",
    "governance": "HUM",
    "security": "SEC",
}
DEFAULT_SEVERITY = {
    "fairness": Severity.HIGH,
    "privacy": Severity.HIGH,
    "safety": Severity.CRITICAL,
    "truthfulness": Severity.MEDIUM,
    "governance": Severity.HIGH,
    "security": Severity.HIGH,
}
DEFAULT_THRESHOLD: dict[str, dict[str, float]] = {
    TestType.COUNTERFACTUAL: {"max_delta": 0.05},
    TestType.GROUNDEDNESS: {"max_unsupported_rate": 0.0},
}
REQUIRED_EVIDENCE: dict[str, list[str]] = {
    TestType.COUNTERFACTUAL: ["prompt", "model_output", "metric_result"],
    TestType.GROUNDEDNESS: ["model_output", "source", "evaluator_result"],
    TestType.PII_LEAKAGE: ["prompt", "model_output"],
    TestType.HUMAN_OVERSIGHT: ["trace"],
    TestType.SAFETY_REFUSAL: ["prompt", "model_output"],
    TestType.PROMPT_INJECTION: ["prompt", "model_output"],
}


@dataclass
class ExtractedRequirement:
    key: str
    text: str
    normalized: str
    modality: str  # must | must_not | should | may
    domain: str
    test_type: str
    confidence: float
    needs_human_review: bool
    page_number: int | None
    section: str | None
    heading: str | None
    source_excerpt: str
    source_hash: str
    chunk_index: int
    signals: dict[str, float] = field(default_factory=dict)


@dataclass
class CompileResult:
    policy_id: str
    version: str
    requirements: list[ExtractedRequirement]
    controls: list[ControlDSL]
    report: dict[str, Any]

    def to_dsl(self, name: str) -> PolicyDSL:
        return PolicyDSL(id=self.policy_id, name=name, version=self.version, controls=self.controls)


def _modality(sentence: str) -> str:
    m = MUST.search(sentence)
    if not m:
        return "should"
    token = m.group(1).lower()
    if NEGATIVE.search(token) or token in ("should not", "will not"):
        return "must_not"
    if token in ("should",):
        return "should"
    if token in ("may",):
        return "may"
    return "must"


def _classify(text: str) -> tuple[str, str, float, dict[str, float]]:
    low = text.lower()
    scores: dict[str, float] = {}
    hits: dict[str, float] = {}
    for domain, (_test, weights) in DOMAIN_SIGNALS.items():
        score = 0.0
        for keyword, weight in weights.items():
            if keyword in low:
                score += weight
                hits[f"{domain}:{keyword}"] = weight
        if score:
            scores[domain] = score
    if not scores:
        return "governance", TestType.CUSTOM_RULE, 0.25, hits
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_domain, top_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - runner) / top_score if top_score else 0
    confidence = min(0.95, 0.4 + 0.12 * top_score + 0.2 * margin)
    test_type = DOMAIN_SIGNALS[top_domain][0]
    # Refine governance test type: authorization vs oversight vs tool permission
    if top_domain == "governance":
        if "tool" in low or "permission" in low:
            test_type = TestType.TOOL_PERMISSION
        elif "authoriz" in low or "authenticat" in low:
            test_type = TestType.AUTHORIZATION
    return top_domain, test_type, round(confidence, 3), hits


class PolicyCompiler:
    def __init__(self, judge: Any | None = None) -> None:
        self.judge = judge

    def extract_requirements(self, policy_key: str, chunks: list[Chunk]) -> list[ExtractedRequirement]:
        requirements: list[ExtractedRequirement] = []
        counter = 0
        seen_hashes: set[str] = set()
        for chunk in chunks:
            for sentence in _split_sentences(chunk.text):
                if not MUST.search(sentence) or len(sentence.split()) < 4:
                    continue
                normalized = normalize_whitespace(sentence)
                h = stable_hash(normalized)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                domain, test_type, confidence, signals = self._classify_with_optional_judge(normalized)
                counter += 1
                requirements.append(
                    ExtractedRequirement(
                        key=f"{policy_key}-R{counter:03d}",
                        text=sentence,
                        normalized=normalized,
                        modality=_modality(sentence),
                        domain=domain,
                        test_type=test_type,
                        confidence=confidence,
                        needs_human_review=confidence < 0.5 or test_type == TestType.CUSTOM_RULE,
                        page_number=chunk.page_number,
                        section=chunk.section,
                        heading=chunk.heading,
                        source_excerpt=chunk.text[:600],
                        source_hash=chunk.content_hash,
                        chunk_index=chunk.index,
                        signals=signals,
                    )
                )
        return requirements

    def _classify_with_optional_judge(self, text: str) -> tuple[str, str, float, dict[str, float]]:
        domain, test_type, confidence, signals = _classify(text)
        if confidence >= 0.5 or self.judge is None:
            return domain, test_type, confidence, signals
        judgment = self.judge.judge(
            "policy_extraction",
            "Classify this policy requirement into one domain and test_type.\n"
            f"Requirement: {text}\n"
            f"Allowed test_types: {sorted(DOMAIN_BY_TEST_TYPE)}\n"
            'Answer JSON: {"domain": "...", "test_type": "...", "confidence": 0-1}',
            prompt_version="policy-classify-v1",
        )
        if judgment and judgment.normalized.get("test_type") in DOMAIN_BY_TEST_TYPE:
            tt = str(judgment.normalized["test_type"])
            return (
                DOMAIN_BY_TEST_TYPE[tt],
                tt,
                min(0.85, float(judgment.confidence or 0.6)),
                {**signals, "model_refined": 1.0},
            )
        return domain, test_type, confidence, signals

    def build_controls(self, requirements: list[ExtractedRequirement]) -> list[ControlDSL]:
        controls: list[ControlDSL] = []
        per_domain: dict[str, int] = {}
        for req in requirements:
            prefix = DOMAIN_PREFIX.get(req.domain, "GOV")
            per_domain[prefix] = per_domain.get(prefix, 0) + 1
            control_id = f"{prefix}-{per_domain[prefix]:03d}"
            controls.append(
                ControlDSL(
                    id=control_id,
                    requirement=req.normalized,
                    name=(req.heading or req.normalized)[:120],
                    test_type=req.test_type,
                    severity=DEFAULT_SEVERITY.get(req.domain, Severity.MEDIUM),
                    automation="manual"
                    if req.test_type == TestType.CUSTOM_RULE and req.needs_human_review
                    else "automated",
                    threshold=dict(DEFAULT_THRESHOLD.get(req.test_type, {})),
                    required_evidence=list(REQUIRED_EVIDENCE.get(req.test_type, ["model_output"])),
                    needs_human_review=req.needs_human_review,
                    source={
                        "requirement_key": req.key,
                        "page": req.page_number,
                        "section": req.section,
                        "excerpt_hash": req.source_hash,
                    },
                )
            )
        return controls

    def compile(self, policy_key: str, version: str, chunks: list[Chunk]) -> CompileResult:
        requirements = self.extract_requirements(policy_key, chunks)
        controls = self.build_controls(requirements)
        domains: dict[str, int] = {}
        for req in requirements:
            domains[req.domain] = domains.get(req.domain, 0) + 1
        report = {
            "compiler_version": COMPILER_VERSION,
            "chunks_scanned": len(chunks),
            "requirements_extracted": len(requirements),
            "controls_generated": len(controls),
            "needs_human_review": sum(1 for r in requirements if r.needs_human_review),
            "by_domain": domains,
            "average_confidence": round(sum(r.confidence for r in requirements) / len(requirements), 3)
            if requirements
            else 0.0,
            "model_assisted": self.judge is not None,
        }
        return CompileResult(
            policy_id=policy_key, version=version, requirements=requirements, controls=controls, report=report
        )


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n", text) if s.strip()]
