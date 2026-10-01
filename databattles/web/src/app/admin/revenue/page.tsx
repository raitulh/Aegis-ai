"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Building2, CreditCard, Layers } from "lucide-react";

import { AdminHeader, SectionHeading } from "@/components/admin/admin-ui";
import type { RevenueView } from "@/components/admin/types";
import { Stat } from "@/components/ui/card";
import { InlineNotice, QueryState, Skeleton, SkeletonStats } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatMoney, formatNumber, titleCase } from "@/lib/format";

/** Share of MRR as a thin bar plus a percentage label (derived from the API's own plan and total figures). */
function Share({ part, total }: { part: number; total: number }) {
  if (!total) return <span className="text-subtle">—</span>;
  const pct = (part / total) * 100;
  return (
    <div className="flex items-center justify-end gap-2.5">
      <div className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-surface-3 sm:block" aria-hidden>
        <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular w-12 text-right text-muted">{pct.toFixed(pct > 0 && pct < 10 ? 1 : 0)}%</span>
    </div>
  );
}

export default function AdminRevenuePage() {
  const revenue = useQuery({ queryKey: ["admin", "revenue"], queryFn: () => get<RevenueView>("/admin/revenue") });
  return (
    <div>
      <AdminHeader eyebrow="Business" icon={<CreditCard />} title="Revenue" description="Subscriptions by plan. Change an organization’s plan from Organizations." />
      <QueryState
        query={revenue}
        loading={
          <div className="space-y-4" role="status" aria-label="Loading">
            <Skeleton className="h-14 w-full rounded-[var(--radius-md)]" />
            <SkeletonStats count={3} className="lg:grid-cols-3" />
            <Skeleton className="h-56 w-full rounded-[var(--radius-lg)]" />
          </div>
        }
      >
        {(r) => {
          const subs = r.plans.reduce((a, p) => a + p.active_subscriptions, 0);
          return (
            <div className="space-y-8">
              <InlineNotice tone="info" title={`${titleCase(r.provider)} billing`}>
                {r.note}
              </InlineNotice>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Stat label="Monthly recurring revenue" value={formatMoney(r.mrr_cents)} icon={<CreditCard className="h-4 w-4" />} hint="Sum of list prices × active subscriptions" />
                <Stat label="Active subscriptions" accent="cyan" value={formatNumber(subs)} icon={<Building2 className="h-4 w-4" />} hint={`Across ${formatNumber(r.plans.filter((p) => p.active_subscriptions > 0).length)} of ${formatNumber(r.plans.length)} plans`} />
                <Stat
                  label="Payment failures"
                  accent={r.payment_failures ? "danger" : "success"}
                  value={formatNumber(r.payment_failures)}
                  icon={<AlertTriangle className="h-4 w-4" />}
                  hint="Subscriptions with a recorded failed payment"
                />
              </div>
              <section aria-labelledby="plans-heading">
                <SectionHeading id="plans-heading" eyebrow="Breakdown" title="By plan" />
                {r.plans.length ? (
                  <Table>
                    <THead>
                      <tr>
                        <TH>Plan</TH>
                        <TH className="text-right">Price / month</TH>
                        <TH className="text-right">Active</TH>
                        <TH className="text-right">MRR</TH>
                        <TH className="text-right">Share of MRR</TH>
                      </tr>
                    </THead>
                    <TBody>
                      {r.plans.map((p) => (
                        <TR key={p.key}>
                          <TD className="whitespace-nowrap">
                            <span className="font-medium text-fg">{p.name}</span>
                            <span className="ml-2 rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-subtle">{p.key}</span>
                          </TD>
                          <TD className="tabular whitespace-nowrap text-right text-muted">{p.price_cents_monthly ? formatMoney(p.price_cents_monthly) : "Free"}</TD>
                          <TD className="tabular text-right">{formatNumber(p.active_subscriptions)}</TD>
                          <TD className="tabular whitespace-nowrap text-right font-medium text-fg">{formatMoney(p.mrr_cents)}</TD>
                          <TD className="text-right text-xs"><Share part={p.mrr_cents} total={r.mrr_cents} /></TD>
                        </TR>
                      ))}
                    </TBody>
                    <tfoot className="border-t border-border-strong bg-bg-elevated/60 text-sm">
                      <tr>
                        <th scope="row" className="px-4 py-3 text-left text-eyebrow font-medium text-subtle">Total</th>
                        <td className="px-4 py-3" />
                        <td className="tabular px-4 py-3 text-right font-medium text-fg">{formatNumber(subs)}</td>
                        <td className="tabular whitespace-nowrap px-4 py-3 text-right font-semibold text-fg">{formatMoney(r.mrr_cents)}</td>
                        <td className="px-4 py-3" />
                      </tr>
                    </tfoot>
                  </Table>
                ) : (
                  <div className="flex flex-col items-center gap-2 rounded-[var(--radius-lg)] border border-dashed border-border-strong px-6 py-10 text-center">
                    <Layers className="h-5 w-5 text-subtle" aria-hidden />
                    <p className="text-sm text-subtle">No plans configured.</p>
                  </div>
                )}
              </section>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
