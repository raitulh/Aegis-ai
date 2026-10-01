"""Plan catalogue: features and quotas per plan.

Limits are product configuration, not code paths: every limit and feature can be overridden per deployment
with ``PLANS_JSON`` and per workspace with a contracted subscription override. Prices are never hard-coded —
``price_display`` is shown only when the operator configures it; otherwise the UI says pricing is not
configured (or "Contact sales" for Enterprise).

``None`` as a limit means *unlimited (contracted)*.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from aegis_api.config import get_settings

# Metered quotas (per billing period unless noted) and live-count quotas.
QUOTAS = {
    "systems": "AI systems (active)",
    "seats": "Members and pending invitations",
    "audit_run": "Audit runs per month",
    "redteam_run": "Red-team campaigns per month",
    "runtime_event": "Runtime events per month",
    "evidence_export": "Evidence package exports per month",
}
FEATURES = {
    "runtime_enforcement": "Runtime Guard enforce mode (block / require approval)",
    "continuous_assurance": "Scheduled and change-triggered audits",
    "advanced_redteam": "Adaptive multi-turn red-team campaigns",
    "webhooks": "Signed outbound webhooks",
    "evidence_export": "Signed evidence package export",
    "custom_retention": "Workspace-defined retention periods",
    "sso": "SAML / OIDC single sign-on",
    "scim": "SCIM provisioning",
}
# Features that are part of the commercial model but not yet implemented in this release. They are shown as
# roadmap items and never presented as available.
ROADMAP_FEATURES = {"sso", "scim"}
LEGACY_ALIASES = {"developer": "pro", "team": "business"}


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    name: str
    audience: str
    limits: dict[str, int | None]
    features: dict[str, bool]
    retention_days: int | None
    support: str
    price_display: str | None = None
    self_serve: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "audience": self.audience,
            "limits": self.limits,
            "features": {
                k: {"included": v, "roadmap": k in ROADMAP_FEATURES, "label": FEATURES[k]}
                for k, v in self.features.items()
            },
            "retention_days": self.retention_days,
            "support": self.support,
            "price_display": self.price_display,
            "self_serve": self.self_serve,
        }


_DEFAULTS: dict[str, PlanDefinition] = {
    "free": PlanDefinition(
        key="free",
        name="Free",
        audience="Individual developers evaluating the full assurance loop",
        limits={
            "systems": 2,
            "seats": 3,
            "audit_run": 25,
            "redteam_run": 3,
            "runtime_event": 10_000,
            "evidence_export": 10,
        },
        features={
            "runtime_enforcement": False,
            "continuous_assurance": False,
            "advanced_redteam": False,
            "webhooks": False,
            "evidence_export": True,
            "custom_retention": False,
            "sso": False,
            "scim": False,
        },
        retention_days=14,
        support="Community",
    ),
    "pro": PlanDefinition(
        key="pro",
        name="Pro",
        audience="AI startups, small teams and consultants",
        limits={
            "systems": 10,
            "seats": 10,
            "audit_run": 300,
            "redteam_run": 50,
            "runtime_event": 500_000,
            "evidence_export": 200,
        },
        features={
            "runtime_enforcement": True,
            "continuous_assurance": True,
            "advanced_redteam": False,
            "webhooks": True,
            "evidence_export": True,
            "custom_retention": False,
            "sso": False,
            "scim": False,
        },
        retention_days=90,
        support="Email",
    ),
    "business": PlanDefinition(
        key="business",
        name="Business",
        audience="AI platform, security and engineering organizations",
        limits={
            "systems": 50,
            "seats": 75,
            "audit_run": 3_000,
            "redteam_run": 500,
            "runtime_event": 10_000_000,
            "evidence_export": 2_000,
        },
        features={
            "runtime_enforcement": True,
            "continuous_assurance": True,
            "advanced_redteam": True,
            "webhooks": True,
            "evidence_export": True,
            "custom_retention": True,
            "sso": False,
            "scim": False,
        },
        retention_days=365,
        support="Priority",
    ),
    "enterprise": PlanDefinition(
        key="enterprise",
        name="Enterprise",
        audience="Regulated and large organizations",
        limits=dict.fromkeys(QUOTAS),
        features={
            "runtime_enforcement": True,
            "continuous_assurance": True,
            "advanced_redteam": True,
            "webhooks": True,
            "evidence_export": True,
            "custom_retention": True,
            "sso": True,
            "scim": True,
        },
        retention_days=None,
        support="Dedicated, contractual SLA",
        price_display="Contact sales",
        self_serve=False,
    ),
}
PLAN_ORDER = ["free", "pro", "business", "enterprise"]


@lru_cache(maxsize=4)
def _catalogue(raw: str) -> dict[str, PlanDefinition]:
    try:
        overrides = json.loads(raw or "{}")
    except ValueError:
        overrides = {}
    plans = dict(_DEFAULTS)
    if isinstance(overrides, dict):
        for key, patch in overrides.items():
            if key not in plans or not isinstance(patch, dict):
                continue
            base = plans[key]
            plans[key] = replace(
                base,
                limits={**base.limits, **(patch.get("limits") or {})},
                features={**base.features, **(patch.get("features") or {})},
                retention_days=patch.get("retention_days", base.retention_days),
                price_display=patch.get("price_display", base.price_display),
                support=patch.get("support", base.support),
            )
    return plans


def catalogue() -> dict[str, PlanDefinition]:
    return _catalogue(get_settings().plans_json)


def normalise(plan_key: str | None) -> str:
    key = (plan_key or "free").lower()
    key = LEGACY_ALIASES.get(key, key)
    return key if key in _DEFAULTS else "free"


def get_plan(plan_key: str | None) -> PlanDefinition:
    return catalogue()[normalise(plan_key)]


def next_plan(plan_key: str) -> str | None:
    idx = PLAN_ORDER.index(normalise(plan_key))
    return PLAN_ORDER[idx + 1] if idx + 1 < len(PLAN_ORDER) else None
