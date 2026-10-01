"""Plans and entitlements. Payments are pluggable; this deployment ships a manual provider only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.org import Plan

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
def plans(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"key": p.key, "name": p.name, "description": p.description, "price_cents_monthly": p.price_cents_monthly,
             "entitlements": p.entitlements} for p in db.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order))]


@router.get("/provider")
def provider() -> dict[str, Any]:
    return {"provider": "manual", "checkout_available": False,
            "message": "Plan changes are processed manually by the platform team. No card details are collected by this app."}
