"use client";

import { useQuery } from "@tanstack/react-query";
import { Bug, ListChecks, RotateCcw } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, JsonDisclosure } from "@/components/admin/admin-ui";
import type { ErrorRow, JobRow } from "@/components/admin/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, post } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useDebounced } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 25;
const JOB_STATUSES = ["queued", "running", "succeeded", "failed", "dead"] as const;
const JOB_TONE: Record<string, "neutral" | "info" | "success" | "warning" | "danger"> = {
  queued: "neutral",
  running: "info",
  succeeded: "success",
  failed: "warning",
  dead: "danger",
};

function Jobs() {
  const [state, setState, reset] = useUrlState({ status: "", kind: "", page: "1" });
  const [kind, setKind] = useState(state.kind ?? "");
  const debouncedKind = useDebounced(kind, 400);
  useEffect(() => {
    if (debouncedKind.trim() !== (state.kind ?? "")) setState({ kind: debouncedKind.trim() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedKind]);
  const page = Math.max(1, Number(state.page) || 1);
  const filters = { status: state.status || undefined, kind: state.kind || undefined, page, page_size: PAGE_SIZE };
  const jobs = useQuery({ queryKey: ["admin", "jobs", filters], queryFn: () => get<Page<JobRow>>("/admin/jobs", filters), refetchInterval: 30_000 });
  const retry = useApiMutation((id: string) => post<{ message: string }>(`/admin/jobs/${id}/retry`), {
    success: (r) => r.message,
    invalidate: [["admin", "jobs"], ["admin", "health"]],
  });
  const filtered = Boolean(state.status || state.kind);

  return (
    <section aria-labelledby="jobs-heading">
      <h2 id="jobs-heading" className="sr-only">Background jobs</h2>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row" role="search">
        <Select aria-label="Job status" className="sm:w-44" value={state.status ?? ""} onChange={(e) => setState({ status: e.target.value })}>
          <option value="">Any status</option>
          {JOB_STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </Select>
        <Input aria-label="Job kind" placeholder="Kind, e.g. score_submission" className="sm:max-w-xs" value={kind} maxLength={48} onChange={(e) => setKind(e.target.value)} />
      </div>
      {jobs.isPending ? (
        <SkeletonRows rows={6} />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : jobs.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setKind(""); reset(); }} /> : <EmptyState icon={<ListChecks className="h-5 w-5" />} title="No jobs yet" />
      ) : (
        <>
          <p className="mb-2 text-sm text-muted" aria-live="polite">{formatNumber(jobs.data.total)} jobs</p>
          <Table>
            <THead>
              <tr>
                <TH>Kind</TH>
                <TH>Status</TH>
                <TH>Attempts</TH>
                <TH>Created</TH>
                <TH>Finished</TH>
                <TH>Last error</TH>
                <TH><span className="sr-only">Actions</span></TH>
              </tr>
            </THead>
            <TBody>
              {jobs.data.items.map((j) => (
                <TR key={j.id} className="align-top">
                  <TD className="font-mono text-xs text-fg">{j.kind}</TD>
                  <TD><Badge tone={JOB_TONE[j.status] ?? "neutral"}>{titleCase(j.status)}</Badge></TD>
                  <TD className="tabular-nums text-muted">{j.attempts} / {j.max_attempts}</TD>
                  <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(j.created_at)}>{relativeTime(j.created_at)}</TD>
                  <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(j.finished_at)}>{j.finished_at ? relativeTime(j.finished_at) : j.run_after && j.status === "queued" ? `runs ${relativeTime(j.run_after)}` : "—"}</TD>
                  <TD className="max-w-sm">
                    {j.last_error ? <JsonDisclosure value={j.last_error} label={j.last_error.slice(0, 60) + (j.last_error.length > 60 ? "…" : "")} /> : <span className="text-xs text-subtle">—</span>}
                  </TD>
                  <TD className="text-right">
                    {j.status === "failed" || j.status === "dead" ? (
                      <Button size="sm" variant="secondary" icon={<RotateCcw className="h-3.5 w-3.5" />} loading={retry.isPending && retry.variables === j.id} disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>
                        Retry
                      </Button>
                    ) : null}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={jobs.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </section>
  );
}

function Errors() {
  const [source, setSource] = useState("");
  const [page, setPage] = useState(1);
  const filters = { source: source || undefined, page, page_size: PAGE_SIZE };
  const errors = useQuery({ queryKey: ["admin", "errors", filters], queryFn: () => get<Page<ErrorRow>>("/admin/errors", filters) });
  return (
    <section id="errors" aria-labelledby="errors-heading" className="mt-10 scroll-mt-24">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="errors-heading" className="text-base font-semibold text-fg">Recent errors</h2>
          <p className="mt-1 text-sm text-muted">Unhandled exceptions from the API and worker. Match a user’s “Reference” to the request id.</p>
        </div>
        <Select aria-label="Error source" className="sm:w-40" value={source} onChange={(e) => { setSource(e.target.value); setPage(1); }}>
          <option value="">All sources</option>
          <option value="api">API</option>
          <option value="worker">Worker</option>
        </Select>
      </div>
      {errors.isPending ? (
        <SkeletonRows rows={4} />
      ) : errors.isError ? (
        <ErrorState error={errors.error} onRetry={() => errors.refetch()} />
      ) : errors.data.items.length === 0 ? (
        <EmptyState icon={<Bug className="h-5 w-5" />} title="No errors recorded" description={source ? "Nothing from this source." : "Nice."} />
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Source</TH>
                <TH>Request</TH>
                <TH>Error</TH>
              </tr>
            </THead>
            <TBody>
              {errors.data.items.map((e) => (
                <TR key={e.id} className="align-top">
                  <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(e.created_at)}>{relativeTime(e.created_at)}</TD>
                  <TD><Badge tone={e.source === "worker" ? "info" : "neutral"}>{titleCase(e.source)}</Badge></TD>
                  <TD className="text-xs">
                    {e.method || e.path ? <span className="block font-mono text-fg">{e.method} {e.path}</span> : null}
                    {e.request_id ? <span className="block font-mono text-[11px] text-subtle">{e.request_id}</span> : null}
                  </TD>
                  <TD className="max-w-md text-xs">
                    <span className="block font-medium text-danger">{e.error_type}</span>
                    <span className="line-clamp-3 break-words text-muted">{e.message}</span>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={errors.data.total} onPage={setPage} />
        </>
      )}
    </section>
  );
}

export default function AdminJobsPage() {
  return (
    <div>
      <AdminHeader title="Jobs & errors" description="Background queue state (auto-refreshes every 30 seconds). Failed and dead jobs can be re-queued." />
      <Suspense fallback={<SkeletonRows rows={6} />}>
        <Jobs />
      </Suspense>
      <Errors />
    </div>
  );
}
