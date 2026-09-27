"use client";
import Link from "next/link";
import { Gauge, Plus } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Button, EmptyState, Progress, StatusBadge } from "@/components/ui/primitives";
import { useAudits } from "@/lib/queries";
import { timeAgo, titleCase } from "@/lib/utils";

export default function AuditsPage() {
  const query = useAudits({ page_size: 50 });
  return (
    <div>
      <PageHeader title="Audits" description="Every assurance run, with live progress and evidence." actions={<Link href="/dashboard/audits/new"><Button icon={Plus}>New Audit</Button></Link>} />
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState icon={Gauge} title="No audits yet" description="Run your first audit to evaluate an AI system." action={<Link href="/dashboard/audits/new"><Button size="sm">Create Audit</Button></Link>} />
          ) : (
            <DataTable>
              <THead><TR><TH>Audit</TH><TH>Status</TH><TH>Progress</TH><TH>Findings</TH><TH>Intensity</TH><TH>Created</TH></TR></THead>
              <tbody>
                {page.items.map((a) => (
                  <TR key={a.id} onClick={() => (window.location.href = `/dashboard/audits/${a.id}`)}>
                    <TD className="font-medium">{a.name}</TD>
                    <TD><StatusBadge status={a.status} /></TD>
                    <TD><div className="flex items-center gap-2"><Progress value={a.progress} className="w-24" /><span className="font-mono text-xs text-[var(--color-text-subtle)]">{a.progress}%</span></div></TD>
                    <TD className="font-mono">{a.findings_count}</TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(a.intensity)}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{timeAgo(a.created_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
    </div>
  );
}
