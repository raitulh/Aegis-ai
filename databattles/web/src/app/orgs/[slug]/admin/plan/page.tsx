"use client";

import { useQuery } from "@tanstack/react-query";
import { CreditCard } from "lucide-react";

import { useAdminOrg } from "@/components/orgs/org-admin-context";
import { EntitlementList, PlanPrice } from "@/components/orgs/org-ui";
import { AdminPageHeader } from "@/components/orgs/org-visuals";
import type { SubscriptionView } from "@/components/orgs/types";
import { Badge } from "@/components/ui/badge";
import { KeyValue } from "@/components/ui/misc";
import { InlineNotice, QueryState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, titleCase } from "@/lib/format";

export default function OrgPlanPage() {
  const org = useAdminOrg();
  const sub = useQuery({ queryKey: ["orgs", org.slug, "subscription"], queryFn: () => get<SubscriptionView>(`/orgs/${org.slug}/subscription`) });

  return (
    <div className="space-y-6">
      <AdminPageHeader eyebrow="Organization" title="Plan" description="What your organization can do on the platform." />
      <QueryState
        query={sub}
        loading={
          <div className="space-y-4" role="status" aria-label="Loading">
            <Skeleton className="h-48 w-full rounded-[var(--radius-xl)]" />
            <Skeleton className="h-64 w-full rounded-[var(--radius-xl)]" />
          </div>
        }
      >
        {(s) => (
          <>
            {s.payment_failed ? (
              <InlineNotice tone="danger" title="A payment failed">Contact the platform team to resolve billing for this organization.</InlineNotice>
            ) : null}

            <section
              aria-labelledby="current-plan"
              className="border-gradient relative isolate overflow-hidden rounded-[var(--radius-xl)] bg-surface surface-sheen shadow-elevated"
            >
              <div aria-hidden className="pointer-events-none absolute -right-16 -top-24 -z-10 h-64 w-[28rem] max-w-full" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
              <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_60%)]" />
              <div className="grid grid-cols-1 gap-6 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-10">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="inline-flex items-center gap-1.5 text-eyebrow text-accent-strong">
                      <CreditCard className="h-3.5 w-3.5" aria-hidden /> Current plan
                    </p>
                    <Badge tone={s.status === "active" ? "success" : "warning"}>{titleCase(s.status)}</Badge>
                  </div>
                  <h2 id="current-plan" className="mt-3 text-[2rem] font-semibold leading-none tracking-[-0.035em] text-fg">{s.plan?.name ?? "No plan"}</h2>
                  <div className="mt-6">
                    <KeyValue
                      items={[
                        { label: "Billing", value: s.provider === "manual" ? "Manual (handled by the platform team)" : titleCase(s.provider) },
                        { label: "Current period ends", value: s.current_period_end ? formatDate(s.current_period_end) : "No end date" },
                      ]}
                    />
                  </div>
                </div>
                {s.plan ? (
                  <div className="min-w-0 rounded-[var(--radius-lg)] border border-border bg-bg-elevated/60 p-4 sm:p-5">
                    <p className="mb-3 text-eyebrow text-subtle">Included</p>
                    <EntitlementList entitlements={s.plan.entitlements} />
                  </div>
                ) : null}
              </div>
            </section>

            <InlineNotice tone="info" title="Changing plans">{s.billing_note} No card details are collected in this app.</InlineNotice>

            {s.available_plans.length ? (
              <section aria-labelledby="plans-heading">
                <div className="mb-3 flex items-end justify-between gap-3">
                  <h2 id="plans-heading" className="text-base font-semibold tracking-[-0.015em] text-fg">Available plans</h2>
                  <span className="text-xs text-subtle">Prices per organization per month</span>
                </div>
                <div className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border shadow-card md:grid-cols-2 xl:grid-cols-3">
                  {s.available_plans.map((p) => {
                    const current = p.key === s.plan?.key;
                    return (
                      <div key={p.key} className={cn("relative flex min-w-0 flex-col p-5", current ? "bg-surface-2" : "bg-surface")}>
                        {current ? <span aria-hidden className="absolute inset-x-0 top-0 h-0.5 bg-brand" /> : null}
                        <div className="flex items-center justify-between gap-2">
                          <h3 className="font-semibold tracking-[-0.01em] text-fg">{p.name}</h3>
                          {current ? <Badge tone="accent">Current</Badge> : null}
                        </div>
                        <div className="mt-3"><PlanPrice cents={p.price_cents_monthly} /></div>
                        {p.description ? <p className="mt-2 text-sm leading-relaxed text-muted">{p.description}</p> : null}
                        <EntitlementList entitlements={p.entitlements} className="mt-5 border-t border-border pt-4" />
                      </div>
                    );
                  })}
                </div>
              </section>
            ) : null}
          </>
        )}
      </QueryState>
    </div>
  );
}
