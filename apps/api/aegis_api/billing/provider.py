"""Billing-provider abstraction.

Core logic (entitlements, usage ledger, plan limits) never depends on a specific provider. A provider only
turns intents (checkout, portal, cancel) into provider calls and turns signed provider webhooks into
normalized subscription updates. ``none`` means plans are managed by an operator (contracts, invoices
outside the product) — the UI says so instead of showing a fake checkout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from aegis_api.config import get_settings
from aegis_api.errors import AppError, ServiceUnavailable, Unauthorized


@dataclass
class SubscriptionUpdate:
    provider_event_id: str
    type: str
    organization_id: str | None
    plan: str | None = None
    status: str | None = None
    customer_id: str | None = None
    subscription_id: str | None = None
    period_start: int | None = None
    period_end: int | None = None
    cancel_at_period_end: bool | None = None


class BillingNotConfigured(AppError):
    """Self-serve billing is not configured on this deployment."""

    status_code = 501
    code = "billing_not_configured"


class BillingProvider(Protocol):
    name: str

    def checkout_url(self, *, organization_id: str, plan: str, customer_email: str | None) -> str: ...

    def portal_url(self, *, customer_id: str) -> str: ...

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> SubscriptionUpdate | None: ...


class NoBillingProvider:
    name = "none"

    def checkout_url(self, *, organization_id: str, plan: str, customer_email: str | None) -> str:
        raise BillingNotConfigured("Plans are managed by your administrator. Contact sales to change plan.")

    def portal_url(self, *, customer_id: str) -> str:
        raise BillingNotConfigured("No billing portal is configured on this deployment.")

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> SubscriptionUpdate | None:
        raise BillingNotConfigured("No billing provider is configured.")


def verify_stripe_signature(
    secret: str, body: bytes, header: str, tolerance: int = 300, now: int | None = None
) -> bool:
    """Stripe scheme: ``Stripe-Signature: t=<ts>,v1=<hex hmac-sha256(secret, "<ts>.<body>")>`` (+ replay window)."""
    try:
        parts = [p.split("=", 1) for p in header.split(",")]
        timestamp = int(next(v for k, v in parts if k == "t"))
        signatures = [v for k, v in parts if k == "v1"]
    except (StopIteration, ValueError):
        return False
    if abs((now or int(time.time())) - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


class StripeBillingProvider:
    """Stripe Checkout + Billing Portal. Price ids per plan come from ``PLANS_JSON``
    (``{"pro": {"stripe_price_id": "price_..."}}``); nothing is hard-coded."""

    name = "stripe"
    api = "https://api.stripe.com/v1"

    def __init__(self) -> None:
        settings = get_settings()
        if not (settings.stripe_secret_key and settings.stripe_webhook_secret):
            raise BillingNotConfigured("Stripe keys are not configured")
        self.secret_key = settings.stripe_secret_key
        self.webhook_secret = settings.stripe_webhook_secret

    def _price_id(self, plan: str) -> str:
        overrides = json.loads(get_settings().plans_json or "{}")
        price = (overrides.get(plan) or {}).get("stripe_price_id")
        if not price:
            raise BillingNotConfigured(f"No Stripe price is configured for the '{plan}' plan")
        return str(price)

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(f"{self.api}{path}", data=data, auth=(self.secret_key, ""), timeout=15)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("Billing provider is unreachable") from exc
        if response.status_code >= 400:
            raise ServiceUnavailable(f"Billing provider error ({response.status_code})")
        return response.json()

    def checkout_url(self, *, organization_id: str, plan: str, customer_email: str | None) -> str:
        web = get_settings().web_base_url.rstrip("/")
        data = {
            "mode": "subscription",
            "line_items[0][price]": self._price_id(plan),
            "line_items[0][quantity]": "1",
            "success_url": f"{web}/dashboard/billing?checkout=success",
            "cancel_url": f"{web}/dashboard/billing?checkout=cancelled",
            "client_reference_id": organization_id,
            "metadata[organization_id]": organization_id,
            "metadata[plan]": plan,
            "subscription_data[metadata][organization_id]": organization_id,
            "subscription_data[metadata][plan]": plan,
        }
        if customer_email:
            data["customer_email"] = customer_email
        return str(self._post("/checkout/sessions", data)["url"])

    def portal_url(self, *, customer_id: str) -> str:
        web = get_settings().web_base_url.rstrip("/")
        return str(
            self._post("/billing_portal/sessions", {"customer": customer_id, "return_url": f"{web}/dashboard/billing"})[
                "url"
            ]
        )

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> SubscriptionUpdate | None:
        if not verify_stripe_signature(self.webhook_secret, body, headers.get("stripe-signature", "")):
            raise Unauthorized("Invalid billing webhook signature", code="invalid_signature")
        event = json.loads(body)
        obj = (event.get("data") or {}).get("object") or {}
        kind = event.get("type", "")
        meta = obj.get("metadata") or {}
        update = SubscriptionUpdate(
            provider_event_id=str(event.get("id")),
            type=kind,
            organization_id=meta.get("organization_id") or obj.get("client_reference_id"),
            plan=meta.get("plan"),
            customer_id=obj.get("customer"),
        )
        if kind.startswith("customer.subscription."):
            update.subscription_id = obj.get("id")
            update.status = "canceled" if kind.endswith("deleted") else obj.get("status")
            update.period_start = obj.get("current_period_start")
            update.period_end = obj.get("current_period_end")
            update.cancel_at_period_end = obj.get("cancel_at_period_end")
        elif kind == "checkout.session.completed":
            update.subscription_id = obj.get("subscription")
            update.status = "active"
        elif kind == "invoice.payment_failed":
            update.subscription_id = obj.get("subscription")
            update.status = "past_due"
        else:
            return update  # recorded, no state change
        return update


def get_provider() -> BillingProvider:
    if get_settings().billing_provider == "stripe":
        return StripeBillingProvider()
    return NoBillingProvider()
