"use client";
import Link from "next/link";
import { Gauge, Plus } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Pagination } from "@/components/ui/display";
import { Select } from "@/components/ui/forms";
import { Button, EmptyState, Progress, StatusBadge } from "@/components/ui/primitives";
import { useAudits, useCan } from "@/lib/queries";
import { timeAgo, titleCase } from "@/lib/utils";

const STATUSES = ["queued", "running", "completed", "partially_completed", "failed", "cancelled"];

export default function AuditsPage() {
  const can = useCan();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const query = useAudits({ page, page_size: 25, status: status || undefined });
  const newAudit = can("audits:run") ? (
    <Link href="/dashboard/audits/new">
      <Button icon={Plus}>New audit</Button>
    </Link>
  ) : null;
  return (
    <div>
      <PageHeader title="Audits" description="Every assurance run, with live progress, findings and a verifiable evidence chain." actions={newAudit} />
      <div className="mb-3 flex items-center gap-2">
        <label htmlFor="audit-status" className="text-xs text-[var(--color-text-muted)]">
          Status
        </label>
        <Select
          id="audit-status"
          className="w-48"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState
              icon={Gauge}
              title={status ? "No audits with this status" : "No audits yet"}
              description={status ? "Try another status filter." : "Run your first audit to evaluate an AI system against its policies."}
              action={!status ? newAudit : null}
            />
          ) : (
            <>
              <DataTable caption="Audits">
                <THead>
                  <tr>
                    <TH>Audit</TH>
                    <TH>Status</TH>
                    <TH>Progress</TH>
                    <TH>Findings</TH>
                    <TH>Intensity</TH>
                    <TH>Created</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((a) => (
                    <TR key={a.id} href={`/dashboard/audits/${a.id}`}>
                      <TD>
                        <RowLink href={`/dashboard/audits/${a.id}`}>{a.name}</RowLink>
                        {a.kind && a.kind !== "manual" ? <span className="ml-2 text-xs text-[var(--color-text-subtle)]">{titleCase(a.kind)}</span> : null}
                      </TD>
                      <TD>
                        <StatusBadge status={a.status} />
                      </TD>
                      <TD>
                        <div className="flex items-center gap-2">
                          <Progress value={a.progress} className="w-24" />
                          <span className="font-mono text-xs text-[var(--color-text-subtle)]">{a.progress}%</span>
                        </div>
                      </TD>
                      <TD className="font-mono">{a.findings_count}</TD>
                      <TD className="text-[var(--color-text-muted)]">{titleCase(a.intensity)}</TD>
                      <TD className="text-[var(--color-text-subtle)]">{timeAgo(a.created_at)}</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
              <Pagination page={data.meta.page} totalPages={data.meta.total_pages} onPage={setPage} />
            </>
          )
        }
      </QueryBoundary>
    </div>
  );
}
