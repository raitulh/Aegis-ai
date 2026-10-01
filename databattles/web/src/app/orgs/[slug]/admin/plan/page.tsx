"use client";

import { useQuery } from "@tanstack/react-query";
import { CreditCard } from "lucide-react";

import { useAdminOrg } from "@/components/orgs/org-admin-context";
import { EntitlementList, PlanPrice } from "@/components/orgs/org-ui";
import type { SubscriptionView } from "@/components/orgs/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
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
      <div>
        <h1 className="text-xl font-semibold text-fg">Plan</h1>
        <p className="mt-1 text-sm text-muted">What your organization can do on the platform.</p>
      </div>
      <QueryState query={sub} loading={<div className="space-y-4" role="status" aria-label="Loading"><Skeleton className="h-40 w-full" /><Skeleton className="h-60 w-full" /></div>}>
        {(s) => (
          <>
            {s.payment_failed ? (
              <InlineNotice tone="danger" title="A payment failed">Contact the platform team to resolve billing for this organization.</InlineNotice>
            ) : null}
            <Card>
              <CardHeader
                title={<span className="inline-flex items-center gap-2"><CreditCard className="h-4 w-4 text-subtle" aria-hidden />Current plan</span>}
                action={<Badge tone={s.status === "active" ? "success" : "warning"}>{titleCase(s.status)}</Badge>}
              />
              <CardBody className="space-y-5">
                <p className="text-2xl font-semibold text-fg">{s.plan?.name ?? "No plan"}</p>
                <KeyValue
                  items={[
                    { label: "Billing", value: s.provider === "manual" ? "Manual (handled by the platform team)" : titleCase(s.provider) },
                    { label: "Current period ends", value: s.current_period_end ? formatDate(s.current_period_end) : "No end date" },
                  ]}
                />
                {s.plan ? (
                  <div>
                    <p className="mb-2 text-xs text-subtle">Included</p>
                    <EntitlementList entitlements={s.plan.entitlements} />
                  </div>
                ) : null}
              </CardBody>
            </Card>

            <InlineNotice tone="info" title="Changing plans">{s.billing_note} No card details are collected in this app.</InlineNotice>

            {s.available_plans.length ? (
              <section aria-labelledby="plans-heading">
                <h2 id="plans-heading" className="mb-3 text-base font-semibold text-fg">Available plans</h2>
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {s.available_plans.map((p) => {
                    const current = p.key === s.plan?.key;
                    return (
                      <div key={p.key} className={cn("flex flex-col rounded-[var(--radius-lg)] border bg-surface p-5", current ? "border-accent" : "border-border")}>
                        <div className="flex items-center justify-between gap-2">
                          <h3 className="font-semibold text-fg">{p.name}</h3>
                          {current ? <Badge tone="accent">Current</Badge> : null}
                        </div>
                        <div className="mt-2"><PlanPrice cents={p.price_cents_monthly} /></div>
                        {p.description ? <p className="mt-2 text-sm text-muted">{p.description}</p> : null}
                        <EntitlementList entitlements={p.entitlements} className="mt-4" />
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
