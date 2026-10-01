"use client";

import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { Suspense } from "react";

import { AdminHeader, JsonDisclosure } from "@/components/admin/admin-ui";
import type { EmailRow } from "@/components/admin/types";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
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
        title="Email outbox"
        description="Transactional emails queued by the platform. Failed messages are retried by the worker; the error shows the last attempt."
        actions={
          <Select aria-label="Email status" className="w-40" value={state.status ?? ""} onChange={(e) => setState({ status: e.target.value })}>
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="sent">Sent</option>
            <option value="failed">Failed</option>
          </Select>
        }
      />
      {emails.isPending ? (
        <SkeletonRows rows={8} />
      ) : emails.isError ? (
        <ErrorState error={emails.error} onRetry={() => emails.refetch()} />
      ) : emails.data.items.length === 0 ? (
        <EmptyState icon={<Mail className="h-5 w-5" />} title={state.status ? `No ${state.status} emails` : "No emails yet"} />
      ) : (
        <>
          <p className="mb-2 text-sm text-muted" aria-live="polite">{formatNumber(emails.data.total)} emails</p>
          <Table>
            <THead>
              <tr>
                <TH>Created</TH>
                <TH>To</TH>
                <TH>Template</TH>
                <TH>Subject</TH>
                <TH>Status</TH>
                <TH>Attempts</TH>
              </tr>
            </THead>
            <TBody>
              {emails.data.items.map((e) => (
                <TR key={e.id} className="align-top">
                  <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(e.created_at)}>{relativeTime(e.created_at)}</TD>
                  <TD className="max-w-48 break-all text-xs text-fg">{e.to}</TD>
                  <TD className="font-mono text-xs text-muted">{e.template}{e.template_version ? ` v${e.template_version}` : ""}</TD>
                  <TD className="max-w-xs text-xs text-fg"><span className="line-clamp-2">{e.subject ?? "—"}</span></TD>
                  <TD>
                    <Badge tone={TONE[e.status] ?? "neutral"}>{titleCase(e.status)}</Badge>
                    {e.sent_at ? <span className="mt-1 block text-[11px] text-subtle" title={formatDateTime(e.sent_at)}>{relativeTime(e.sent_at)}</span> : null}
                  </TD>
                  <TD className="text-xs">
                    <span className="tabular-nums text-muted">{e.attempts}</span>
                    {e.error ? <div className="mt-1"><JsonDisclosure value={e.error} label="Error" /></div> : null}
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
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <Emails />
    </Suspense>
  );
}
