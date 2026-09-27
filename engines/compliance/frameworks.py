"""Framework reference packs and mapping.

Frameworks are versioned *reference data*. Aegis maps organizational controls to framework references by
shared test-type/domain, and clearly labels these as assessment/reference mappings — never legal advice
or certification. Framework control text is paraphrased/summarised reference metadata, not verbatim
reproduction of the source standards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.policy.dsl import DOMAIN_BY_TEST_TYPE

DISCLAIMER = (
    "Reference mapping for assessment purposes only. This does not constitute legal advice, certification, "
    "or a determination of compliance with the referenced framework."
)


@dataclass(frozen=True)
class FrameworkControlRef:
    ref: str
    title: str
    description: str
    group: str
    domains: tuple[str, ...]
    test_types: tuple[str, ...]


@dataclass(frozen=True)
class FrameworkPack:
    key: str
    name: str
    version: str
    version_date: str
    publisher: str
    description: str
    source_url: str
    controls: list[FrameworkControlRef] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


# Concise, paraphrased reference metadata for well-known AI risk frameworks. Domains/test-types drive
# the automatic mapping to organizational controls.
FRAMEWORKS: list[FrameworkPack] = [
    FrameworkPack(
        key="nist-ai-rmf",
        name="NIST AI Risk Management Framework",
        version="1.0",
        version_date="2023-01-26",
        publisher="NIST",
        description="Voluntary framework organised around the Govern, Map, Measure and Manage functions.",
        source_url="https://www.nist.gov/itl/ai-risk-management-framework",
        controls=[
            FrameworkControlRef(
                "GOVERN-1.1",
                "Governance policies established",
                "Legal and policy requirements for AI are understood and documented.",
                "Govern",
                ("governance",),
                ("human_oversight", "custom_rule"),
            ),
            FrameworkControlRef(
                "MAP-1.1",
                "Context and risk framing",
                "Intended purpose and context of use are documented.",
                "Map",
                ("governance",),
                ("custom_rule",),
            ),
            FrameworkControlRef(
                "MEASURE-2.11",
                "Fairness and bias evaluated",
                "Harmful bias and fairness are measured and documented.",
                "Measure",
                ("fairness",),
                ("counterfactual",),
            ),
            FrameworkControlRef(
                "MEASURE-2.7",
                "Security and resilience",
                "AI system security and resilience are evaluated (e.g. adversarial robustness).",
                "Measure",
                ("security",),
                ("prompt_injection",),
            ),
            FrameworkControlRef(
                "MEASURE-2.9",
                "Validity and reliability",
                "Outputs are evaluated for validity and reliability, including factual accuracy.",
                "Measure",
                ("truthfulness",),
                ("groundedness", "source_required"),
            ),
            FrameworkControlRef(
                "MEASURE-2.10",
                "Privacy evaluated",
                "Privacy risks, including data leakage, are assessed.",
                "Measure",
                ("privacy",),
                ("pii_leakage",),
            ),
            FrameworkControlRef(
                "MANAGE-2.2",
                "Mechanisms to sustain value",
                "Controls and human oversight are in place to manage risks.",
                "Manage",
                ("governance", "safety"),
                ("human_oversight", "safety_refusal"),
            ),
        ],
    ),
    FrameworkPack(
        key="nist-genai-profile",
        name="NIST Generative AI Profile",
        version="1.0",
        version_date="2024-07-26",
        publisher="NIST",
        description="Companion profile (NIST AI 600-1) addressing risks specific to generative AI.",
        source_url="https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence",
        controls=[
            FrameworkControlRef(
                "GAI-CBRN",
                "Dangerous capability safeguards",
                "Guardrails against dangerous or harmful information.",
                "Risks",
                ("safety",),
                ("safety_refusal",),
            ),
            FrameworkControlRef(
                "GAI-CONF",
                "Confabulation",
                "Mitigate confabulated (hallucinated) outputs.",
                "Risks",
                ("truthfulness",),
                ("groundedness",),
            ),
            FrameworkControlRef(
                "GAI-DATA-PRIV",
                "Data privacy",
                "Prevent leakage of sensitive or personal data.",
                "Risks",
                ("privacy",),
                ("pii_leakage",),
            ),
            FrameworkControlRef(
                "GAI-INFO-SEC",
                "Information security",
                "Address prompt-injection and related security risks.",
                "Risks",
                ("security",),
                ("prompt_injection",),
            ),
            FrameworkControlRef(
                "GAI-HARM-BIAS",
                "Harmful bias",
                "Address harmful bias and homogenization.",
                "Risks",
                ("fairness",),
                ("counterfactual",),
            ),
        ],
    ),
    FrameworkPack(
        key="owasp-llm",
        name="OWASP Top 10 for LLM Applications",
        version="2025",
        version_date="2024-11-01",
        publisher="OWASP",
        description="Most critical security risks for LLM applications.",
        source_url="https://owasp.org/www-project-top-10-for-large-language-model-applications/",
        controls=[
            FrameworkControlRef(
                "LLM01",
                "Prompt Injection",
                "Manipulating an LLM via crafted inputs, directly or indirectly.",
                "Top 10",
                ("security",),
                ("prompt_injection",),
            ),
            FrameworkControlRef(
                "LLM02",
                "Sensitive Information Disclosure",
                "Exposure of sensitive data in outputs.",
                "Top 10",
                ("privacy",),
                ("pii_leakage",),
            ),
            FrameworkControlRef(
                "LLM06",
                "Excessive Agency",
                "Excessive permissions/autonomy granted to LLM-based agents.",
                "Top 10",
                ("governance",),
                ("human_oversight", "tool_permission", "authorization"),
            ),
            FrameworkControlRef(
                "LLM09",
                "Misinformation",
                "Reliance on inaccurate or fabricated content.",
                "Top 10",
                ("truthfulness",),
                ("groundedness", "source_required"),
            ),
        ],
    ),
    FrameworkPack(
        key="iso-42001",
        name="ISO/IEC 42001 (AI Management System)",
        version="2023",
        version_date="2023-12-01",
        publisher="ISO/IEC",
        description="Conceptual control mapping to the AI management system standard (Annex A themes).",
        source_url="https://www.iso.org/standard/81230.html",
        controls=[
            FrameworkControlRef(
                "A.6.2",
                "AI system impact assessment",
                "Assess impacts of AI systems, including fairness and safety.",
                "Annex A",
                ("fairness", "safety"),
                ("counterfactual", "safety_refusal"),
            ),
            FrameworkControlRef(
                "A.8.3",
                "Data for AI systems",
                "Manage data quality and privacy for AI systems.",
                "Annex A",
                ("privacy",),
                ("pii_leakage",),
            ),
            FrameworkControlRef(
                "A.9.2",
                "Human oversight",
                "Ensure appropriate human oversight of AI systems.",
                "Annex A",
                ("governance",),
                ("human_oversight",),
            ),
            FrameworkControlRef(
                "A.10.4",
                "Performance and accuracy",
                "Monitor performance including output accuracy.",
                "Annex A",
                ("truthfulness",),
                ("groundedness",),
            ),
        ],
    ),
]
FRAMEWORKS_BY_KEY = {f.key: f for f in FRAMEWORKS}


def suggest_mappings(control_domain: str, control_test_type: str, framework: FrameworkPack) -> list[dict[str, Any]]:
    """Suggest framework references for an organizational control by shared test-type/domain."""
    matches: list[dict[str, Any]] = []
    for ref in framework.controls:
        if control_test_type in ref.test_types:
            matches.append(
                {
                    "ref": ref.ref,
                    "confidence": 0.85,
                    "rationale": f"Both address {control_test_type.replace('_', ' ')}.",
                }
            )
        elif control_domain in ref.domains:
            matches.append(
                {"ref": ref.ref, "confidence": 0.6, "rationale": f"Both relate to the {control_domain} domain."}
            )
    return matches


def framework_test_types(framework_key: str) -> set[str]:
    fw = FRAMEWORKS_BY_KEY.get(framework_key)
    return {tt for c in fw.controls for tt in c.test_types} if fw else set()


assert all(tt in DOMAIN_BY_TEST_TYPE for f in FRAMEWORKS for c in f.controls for tt in c.test_types)
