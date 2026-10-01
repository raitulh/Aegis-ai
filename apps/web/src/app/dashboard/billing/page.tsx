"use client";
import { Check, CreditCard, ExternalLink, Minus, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { SectionTitle } from "@/components/ui/display";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Progress } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { useCan, usePlans, useUsage } from "@/lib/queries";
import type { PlanPublic, Quota, Usage } from "@/lib/types";
import { cn, formatDate, titleCase } from "@/lib/utils";

export default function BillingPage() {
  const usage = useUsage();
  return (
    <div>
      <PageHeader title="Usage & billing" description="Usage is counted from an append-only ledger of metered actions and live resource counts. Projections are linear estimates for the current period." />
      <QueryBoundary query={usage}>{(u) => <UsageView usage={u} />}</QueryBoundary>
    </div>
  );
}

function UsageView({ usage }: { usage: Usage }) {
  const can = useCan();
  const plans = usePlans();
  const [busy, setBusy] = useState<string | null>(null);

  async function go(kind: "checkout" | "portal", plan?: string) {
    setBusy(plan ?? kind);
    try {
      const { url } = await api.post<{ url: string }>(kind === "checkout" ? "/billing/checkout" : "/billing/portal", kind === "checkout" ? { plan } : undefined);
      const target = new URL(url);
      if (target.protocol !== "https:") throw new Error("Billing provider returned an insecure URL");
      window.location.assign(target.toString());
    } catch (e) {
      toast.error(errorMessage(e));
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
        <Card>
          <CardHeader>
            <CardTitle>Current plan</CardTitle>
            {usage.sandbox ? <Badge tone="warning">DEMO sandbox</Badge> : null}
          </CardHeader>
          <CardBody className="space-y-3">
            <div>
              <p className="text-2xl font-semibold">{usage.plan.name}</p>
              <p className="text-sm text-[var(--color-text-muted)]">{usage.plan.audience}</p>
            </div>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-[var(--color-text-muted)]">Period</dt>
                <dd>
                  {formatDate(usage.period.start)} – {formatDate(usage.period.end)}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--color-text-muted)]">Subscription</dt>
                <dd>{titleCase(usage.subscription.status)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--color-text-muted)]">Support</dt>
                <dd>{usage.plan.support}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--color-text-muted)]">Runtime event retention</dt>
                <dd>{usage.plan.retention_days ? `${usage.plan.retention_days} days` : "Contracted"}</dd>
              </div>
            </dl>
            {usage.billing_provider === "none" ? (
              <p className="rounded-[var(--radius)] border border-[var(--color-border)] p-2.5 text-xs text-[var(--color-text-muted)]">
                Online billing is not configured on this deployment. Plan changes are applied by your administrator or through a contract.
              </p>
            ) : can("billing:manage") && usage.subscription.provider !== "none" ? (
              <Button variant="secondary" icon={CreditCard} loading={busy === "portal"} onClick={() => go("portal")}>
                Manage billing
              </Button>
            ) : null}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Usage this period</CardTitle>
          </CardHeader>
          <CardBody className="grid gap-4 sm:grid-cols-2">
            {usage.quotas.map((q) => (
              <QuotaMeter key={q.metric} q={q} />
            ))}
          </CardBody>
        </Card>
      </div>

      <div>
        <SectionTitle>Plans</SectionTitle>
        <QueryBoundary query={plans} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
          {(c) => (
            <>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {c.plans.map((p) => (
                  <PlanCard
                    key={p.key}
                    plan={p}
                    current={p.key === usage.plan_key}
                    quotaLabels={c.quotas}
                    action={
                      p.key !== usage.plan_key && can("billing:manage") && usage.self_serve_checkout && p.self_serve && (p.key === "pro" || p.key === "business") ? (
                        <Button size="sm" className="w-full" loading={busy === p.key} onClick={() => go("checkout", p.key)}>
                          Switch to {p.name}
                        </Button>
                      ) : !p.self_serve ? (
                        <Link href="/contact?kind=sales" className="block">
                          <Button size="sm" variant="secondary" className="w-full" icon={ExternalLink}>
                            Contact sales
                          </Button>
                        </Link>
                      ) : null
                    }
                  />
                ))}
              </div>
              {c.roadmap_features.length ? (
                <p className="mt-3 text-xs text-[var(--color-text-subtle)]">
                  On the roadmap (not available in this release): {c.roadmap_features.map((f) => c.features[f] ?? f).join(", ")}.
                </p>
              ) : null}
            </>
          )}
        </QueryBoundary>
      </div>
    </div>
  );
}

function QuotaMeter({ q }: { q: Quota }) {
  const pct = q.percent ?? 0;
  const color = pct >= 100 ? "var(--color-critical)" : pct >= 80 ? "var(--color-high)" : "var(--color-accent)";
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
        <span className="text-[var(--color-text-muted)]">{q.label}</span>
        <span className="font-mono text-xs">
          {q.used.toLocaleString()} / {q.limit === null ? "unlimited" : q.limit.toLocaleString()}
        </span>
      </div>
      {q.limit !== null ? <Progress value={pct} color={color} /> : <div className="h-1.5 rounded-full bg-[var(--color-surface-3)]" />}
      <p className={cn("mt-1 text-[11px]", q.projected_over_limit ? "text-[var(--color-high)]" : "text-[var(--color-text-subtle)]")}>
        {q.periodic ? (q.projected !== null ? <><TrendingUp className="mr-1 inline h-3 w-3" aria-hidden />Projected {q.projected.toLocaleString()} by period end{q.projected_over_limit ? " — over the limit" : ""}</> : "Resets each period") : "Live count"}
      </p>
    </div>
  );
}

function PlanCard({ plan, current, quotaLabels, action }: { plan: PlanPublic; current: boolean; quotaLabels: Record<string, string>; action: React.ReactNode }) {
  return (
    <Card className={cn("flex flex-col", current && "border-[var(--color-accent)]")}>
      <CardBody className="flex flex-1 flex-col gap-3">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">{plan.name}</h3>
          {current ? <Badge tone="accent">Current</Badge> : null}
        </div>
        <p className="text-sm text-[var(--color-text-muted)]">{plan.audience}</p>
        <p className="text-lg font-semibold">{plan.price_display ?? (plan.self_serve ? <span className="text-sm font-normal text-[var(--color-text-subtle)]">Pricing not configured</span> : "Contact sales")}</p>
        <ul className="space-y-1 text-xs">
          {Object.entries(plan.limits).map(([k, v]) => (
            <li key={k} className="flex justify-between gap-2">
              <span className="text-[var(--color-text-muted)]">{quotaLabels[k] ?? titleCase(k)}</span>
              <span className="font-mono">{v === null ? "Contracted" : v.toLocaleString()}</span>
            </li>
          ))}
        </ul>
        <ul className="flex-1 space-y-1 text-xs">
          {Object.entries(plan.features).map(([k, f]) => (
            <li key={k} className={cn("flex items-start gap-1.5", !f.included && "text-[var(--color-text-subtle)]")}>
              {f.included && !f.roadmap ? <Check className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-success)]" aria-label="Included" /> : <Minus className="mt-0.5 h-3 w-3 shrink-0" aria-label="Not included" />}
              <span>
                {f.label}
                {f.roadmap ? " (roadmap)" : ""}
              </span>
            </li>
          ))}
        </ul>
        {action}
      </CardBody>
    </Card>
  );
}
