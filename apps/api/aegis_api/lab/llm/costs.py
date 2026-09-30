"""Price lookup for model calls: organization ``ModelConfig`` → platform ``ModelConfig`` → ``MODEL_PRICING_JSON``.

Prices are never invented: when none is configured the cost is ``None`` (recorded as 0 with
``cost_estimated=True`` and basis "no price configured").
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.lab.llm.schemas import Usage
from aegis_api.lab.models import ModelConfig
from engines.lab.llm_costs import CostResult, Price, TokenCounts, compute_cost, price_from_mapping


@dataclass(frozen=True)
class ModelConfigView:
    """Detached, immutable snapshot of a ``ModelConfig`` row (safe to use after the session closes)."""

    id: str
    organization_id: uuid.UUID | None
    provider_kind: str
    model: str
    tier: str
    task_types: tuple[str, ...] = ()
    priority: int = 100
    enabled: bool = True
    max_output_tokens: int | None = None
    temperature: float | None = None
    input_per_mtok_usd: Decimal | None = None
    output_per_mtok_usd: Decimal | None = None
    cached_input_per_mtok_usd: Decimal | None = None
    context_window: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, row: ModelConfig) -> ModelConfigView:
        return cls(
            id=str(row.id),
            organization_id=row.organization_id,
            provider_kind=row.provider_kind,
            model=row.model,
            tier=row.tier,
            task_types=tuple(row.task_types or ()),
            priority=row.priority if row.priority is not None else 100,
            enabled=bool(row.enabled),
            max_output_tokens=row.max_output_tokens,
            temperature=row.temperature,
            input_per_mtok_usd=row.input_per_mtok_usd,
            output_per_mtok_usd=row.output_per_mtok_usd,
            cached_input_per_mtok_usd=row.cached_input_per_mtok_usd,
            context_window=row.context_window,
            capabilities=dict(row.capabilities or {}),
        )


def price_from_config(row: ModelConfigView) -> Price | None:
    if row.input_per_mtok_usd is None or row.output_per_mtok_usd is None:
        return None
    return price_from_mapping(
        {
            "input_per_mtok": row.input_per_mtok_usd,
            "output_per_mtok": row.output_per_mtok_usd,
            "cached_input_per_mtok": row.cached_input_per_mtok_usd,
        },
        source="org" if row.organization_id is not None else "platform",
    )


def settings_price(
    provider: str, models: Iterable[str | None], pricing: Mapping[str, Any] | None = None
) -> Price | None:
    table = pricing if pricing is not None else get_settings().model_pricing
    for model in models:
        if model:
            found = price_from_mapping(table.get(f"{provider}:{model}"), source="settings")
            if found is not None:
                return found
    return price_from_mapping(table.get(provider), source="settings")


def resolve_price(
    provider: str,
    model: str,
    *,
    configs: Iterable[ModelConfigView] = (),
    model_version: str | None = None,
    pricing: Mapping[str, Any] | None = None,
) -> Price | None:
    """Most specific configured price for ``provider:model`` (organization rows win over platform rows)."""
    names = [m for m in (model, model_version) if m]
    rows = [r for r in configs if r.provider_kind == provider and r.model in names]
    rows.sort(key=lambda r: (r.organization_id is None, names.index(r.model), r.priority))
    for row in rows:
        price = price_from_config(row)
        if price is not None:
            return price
    return settings_price(provider, names, pricing)


def load_configs(db: Session, organization_id: uuid.UUID, *, enabled_only: bool = False) -> list[ModelConfigView]:
    """Organization and platform-wide model configuration rows visible to the tenant session."""
    stmt = select(ModelConfig).where(
        or_(ModelConfig.organization_id == organization_id, ModelConfig.organization_id.is_(None))
    )
    if enabled_only:
        stmt = stmt.where(ModelConfig.enabled.is_(True))
    return [ModelConfigView.of(row) for row in db.scalars(stmt.order_by(ModelConfig.priority, ModelConfig.created_at))]


def call_cost(price: Price | None, usage: Usage, *, local: bool) -> CostResult:
    return compute_cost(
        price,
        TokenCounts(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            thinking_tokens=usage.thinking_tokens,
        ),
        local=local,
    )
