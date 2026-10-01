"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Building2, CreditCard, Info } from "lucide-react";

import { AdminHeader } from "@/components/admin/admin-ui";
import type { RevenueView } from "@/components/admin/types";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { InlineNotice, QueryState, Skeleton } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatMoney, formatNumber, titleCase } from "@/lib/format";

export default function AdminRevenuePage() {
  const revenue = useQuery({ queryKey: ["admin", "revenue"], queryFn: () => get<RevenueView>("/admin/revenue") });
  return (
    <div>
      <AdminHeader title="Revenue" description="Subscriptions by plan. Change an organization’s plan from Organizations." />
      <QueryState
        query={revenue}
        loading={<div className="space-y-4" role="status" aria-label="Loading"><div className="grid gap-3 sm:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full" />)}</div><Skeleton className="h-48 w-full" /></div>}
      >
        {(r) => {
          const subs = r.plans.reduce((a, p) => a + p.active_subscriptions, 0);
          return (
            <div className="space-y-6">
              <InlineNotice tone="info" title={<span className="inline-flex items-center gap-1.5"><Info className="h-4 w-4" aria-hidden />{titleCase(r.provider)} billing</span>}>
                {r.note}
              </InlineNotice>
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Monthly recurring revenue" value={formatMoney(r.mrr_cents)} icon={<CreditCard className="h-4 w-4" />} hint="Sum of list prices × active subscriptions" />
                <Stat label="Active subscriptions" value={formatNumber(subs)} icon={<Building2 className="h-4 w-4" />} />
                <Stat label="Payment failures" value={formatNumber(r.payment_failures)} icon={<AlertTriangle className="h-4 w-4" />} hint="Subscriptions with a recorded failed payment" />
              </div>
              <Card>
                <CardHeader title="By plan" />
                <CardBody>
                  {r.plans.length ? (
                    <Table>
                      <THead>
                        <tr>
                          <TH>Plan</TH>
                          <TH className="text-right">Price / month</TH>
                          <TH className="text-right">Active</TH>
                          <TH className="text-right">MRR</TH>
                        </tr>
                      </THead>
                      <TBody>
                        {r.plans.map((p) => (
                          <TR key={p.key}>
                            <TD>
                              <span className="font-medium text-fg">{p.name}</span>
                              <span className="ml-2 font-mono text-xs text-subtle">{p.key}</span>
                            </TD>
                            <TD className="text-right tabular-nums">{p.price_cents_monthly ? formatMoney(p.price_cents_monthly) : "Free"}</TD>
                            <TD className="text-right tabular-nums">{formatNumber(p.active_subscriptions)}</TD>
                            <TD className="text-right tabular-nums">{formatMoney(p.mrr_cents)}</TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  ) : (
                    <p className="py-6 text-center text-sm text-subtle">No plans configured.</p>
                  )}
                </CardBody>
              </Card>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
