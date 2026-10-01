"use client";
import { FileBarChart } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { EmptyState, StatusBadge } from "@/components/ui/primitives";
import { useAudits } from "@/lib/queries";
import { timeAgo } from "@/lib/utils";

export default function ReportsPage() {
  const query = useAudits({ page_size: 100 });
  return (
    <div>
      <PageHeader title="Reports" description="Evidence-indexed assurance reports for completed audits. Reports describe test results; they are not a certification." />
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) => {
          const done = page.items.filter((a) => ["completed", "partially_completed"].includes(a.status));
          return done.length === 0 ? (
            <EmptyState icon={FileBarChart} title="No reports yet" description="Reports are generated automatically when an audit completes." />
          ) : (
            <DataTable caption="Reports">
              <THead><tr><TH>Audit</TH><TH>Status</TH><TH>Findings</TH><TH>Completed</TH></tr></THead>
              <tbody>
                {done.map((a) => (
                  <TR key={a.id} href={`/dashboard/reports/${a.id}`}>
                    <TD><RowLink href={`/dashboard/reports/${a.id}`}>{a.name}</RowLink></TD>
                    <TD><StatusBadge status={a.status} /></TD>
                    <TD className="font-mono">{a.findings_count}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{timeAgo(a.completed_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
