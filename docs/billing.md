# Plans, usage and billing

## Plans are configuration

Plans (`free`, `pro`, `business`, `enterprise`) define **limits** (quotas) and **features**. Defaults live in `apps/api/aegis_api/billing/plans.py`; any deployment can override them with `PLANS_JSON`, and a contracted subscription can override them per workspace. **Prices are never hard-coded**: `price_display` is shown only when the operator configures it; otherwise the UI says pricing is not configured (Enterprise shows "Contact sales"). Legacy keys `developer` and `team` map to `pro` and `business`.

| Default | Free | Pro | Business | Enterprise |
| --- | --- | --- | --- | --- |
| AI systems | 2 | 10 | 50 | contracted |
| Members + pending invitations | 3 | 10 | 75 | contracted |
| Audit runs / period | 25 | 300 | 3,000 | contracted |
| Red-team campaigns / period | 3 | 50 | 500 | contracted |
| Runtime events / period | 10,000 | 500,000 | 10,000,000 | contracted |
| Evidence exports / period | 10 | 200 | 2,000 | contracted |
| Runtime enforcement | – | ✓ | ✓ | ✓ |
| Continuous assurance | – | ✓ | ✓ | ✓ |
| Adaptive red team | – | – | ✓ | ✓ |
| Webhooks | – | ✓ | ✓ | ✓ |
| Custom retention | – | – | ✓ | ✓ |
| Runtime event retention | 14 days | 90 days | 365 days | contracted |
| SSO / SCIM | roadmap | roadmap | roadmap | roadmap |

SSO and SCIM are listed in the commercial model but **not implemented** in this release; they are always shown as roadmap and never presented as available. DEMO sandboxes run on `pro` (and say so) so the full loop can be explored.

Example override:

```bash
PLANS_JSON='{"pro": {"price_display": "$99 / month", "limits": {"audit_run": 500}}, "free": {"features": {"webhooks": true}}}'
```

## Enforcement

| Metric / feature | Where it is checked |
| --- | --- |
| `systems` | registering a system |
| `seats` | inviting a member |
| `audit_run` | creating an audit (including scheduled and change-triggered runs) |
| `redteam_run`, `advanced_redteam` | starting a red-team campaign |
| `runtime_event` | batch ingestion (`POST /runtime/events`). The synchronous `POST /runtime/check` is **never** refused for quota reasons — a security control must keep answering. |
| `evidence_export` | exporting a signed package |
| `runtime_enforcement` | switching a system to enforce (an enforce system on a plan without it is evaluated in audit mode) |
| `continuous_assurance`, `webhooks`, `custom_retention` | the corresponding features |

A refusal is HTTP 403 with code `plan_limit_exceeded` (or `feature_not_in_plan`) and details: `metric`, `used`, `limit`, `plan`, `next_plan`. The console explains the numbers instead of showing a generic upgrade prompt.

## Usage ledger

Metered actions are written to `usage_events`, an append-only table (database trigger) with the source of each event. `GET /usage` returns the plan, the billing period, each quota's usage and remaining capacity, and a linear projection to the end of the period once enough of the period has elapsed. Usage alerts fire at 50 / 75 / 90 / 100 % of a limit (notifications and the `usage.threshold` webhook).

## Payments

`BILLING_PROVIDER=none` (default) means plans are assigned by the operator or by contract; the console says so and offers no checkout. With `BILLING_PROVIDER=stripe` (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`):

- `POST /billing/checkout {plan}` and `POST /billing/portal` return provider URLs (permission `billing:manage`).
- `POST /billing/webhooks/stripe` verifies the `Stripe-Signature` (timestamped HMAC, replay window), records every event once (idempotent by event id) and applies subscription changes to the workspace plan.

The Stripe integration is implemented against the documented webhook format and covered by signature and idempotency tests; it has **not** been exercised against a live Stripe account in this repository. Validate it in Stripe test mode before taking payments.
