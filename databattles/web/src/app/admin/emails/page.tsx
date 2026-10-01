"use client";

import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { Suspense } from "react";

import { AdminHeader, ChoiceFilter, DotBadge, EmailText, FilterBar, JsonDisclosure, ResultCount, TableSkeleton } from "@/components/admin/admin-ui";
import type { EmailRow } from "@/components/admin/types";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 50;
const TONE = { queued: "neutral", sent: "success", failed: "danger" } as const;

function Emails() {
  const [state, setState] = useUrlState({ status: "", page: "1" });
  const page = Math.max(1, Number(state.page) || 1);
  const filters = { status: state.status || undefined, page, page_size: PAGE_SIZE };
  const emails = useQuery({ queryKey: ["admin", "emails", filters], queryFn: () => get<Page<EmailRow>>("/admin/emails", filters), refetchInterval: 30_000 });

  return (
    <div>
      <AdminHeader
        eyebrow="Monitor"
        icon={<Mail />}
        title="Email outbox"
        description="Transactional emails queued by the platform. Failed messages are retried by the worker; the error shows the last attempt."
      />
      <FilterBar>
        <ChoiceFilter
          label="Email status"
          value={state.status ?? ""}
          onChange={(v) => setState({ status: v })}
          options={[
            { value: "", label: "All statuses" },
            { value: "queued", label: "Queued" },
            { value: "sent", label: "Sent" },
            { value: "failed", label: "Failed" },
          ]}
        />
        <p className="text-xs text-subtle sm:ml-auto sm:pr-1.5">Refreshes every 30 seconds</p>
      </FilterBar>
      {emails.isPending ? (
        <TableSkeleton rows={8} cols={6} />
      ) : emails.isError ? (
        <ErrorState error={emails.error} onRetry={() => emails.refetch()} />
      ) : emails.data.items.length === 0 ? (
        <EmptyState icon={<Mail className="h-5 w-5" />} title={state.status ? `No ${state.status} emails` : "No emails yet"} />
      ) : (
        <>
          <ResultCount total={emails.data.total} noun="emails" />
          <Table>
            <THead>
              <tr>
                <TH>Created</TH>
                <TH>To</TH>
                <TH>Template</TH>
                <TH>Subject</TH>
                <TH>Status</TH>
                <TH className="text-right">Attempts</TH>
              </tr>
            </THead>
            <TBody>
              {emails.data.items.map((e) => (
                <TR key={e.id} className="align-top">
                  <TD className="tabular whitespace-nowrap text-xs text-muted" title={formatDateTime(e.created_at)}>{relativeTime(e.created_at)}</TD>
                  <TD className="min-w-44 max-w-56 text-xs text-fg"><EmailText email={e.to} /></TD>
                  <TD className="whitespace-nowrap">
                    <span className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-muted">
                      {e.template}{e.template_version ? <span className="text-subtle">v{e.template_version}</span> : null}
                    </span>
                  </TD>
                  <TD className="min-w-56 max-w-xs text-xs text-fg"><span className="line-clamp-2">{e.subject ?? "—"}</span></TD>
                  <TD className="whitespace-nowrap">
                    <DotBadge tone={TONE[e.status] ?? "neutral"}>{titleCase(e.status)}</DotBadge>
                    {e.sent_at ? <span className="tabular mt-1 block text-[11px] text-subtle" title={formatDateTime(e.sent_at)}>{relativeTime(e.sent_at)}</span> : null}
                  </TD>
                  <TD className="text-right text-xs">
                    <span className="tabular text-muted">{e.attempts}</span>
                    {e.error ? <div className="mt-1 flex justify-end text-left"><JsonDisclosure value={e.error} label="Error" /></div> : null}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={emails.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </div>
  );
}

export default function AdminEmailsPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={8} cols={6} />}>
      <Emails />
    </Suspense>
  );
}
