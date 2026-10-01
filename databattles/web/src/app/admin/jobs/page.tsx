"use client";

import { useQuery } from "@tanstack/react-query";
import { Bug, ListChecks, RotateCcw, Search } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, ChoiceFilter, DotBadge, FilterBar, JsonDisclosure, ResultCount, SectionHeading, TableSkeleton } from "@/components/admin/admin-ui";
import type { ErrorRow, JobRow } from "@/components/admin/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, post } from "@/lib/api";
import { formatDateTime, relativeTime, titleCase } from "@/lib/format";
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
      <SectionHeading id="jobs-heading" eyebrow="Queue" title="Background jobs" />
      <FilterBar role="search">
        <ChoiceFilter
          label="Job status"
          value={state.status ?? ""}
          onChange={(v) => setState({ status: v })}
          options={[{ value: "", label: "Any status" }, ...JOB_STATUSES.map((s) => ({ value: s, label: titleCase(s) }))]}
        />
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
          <Input aria-label="Job kind" placeholder="Kind, e.g. score_submission" className="pl-9 font-mono text-[13px]" value={kind} maxLength={48} onChange={(e) => setKind(e.target.value)} />
        </div>
      </FilterBar>
      {jobs.isPending ? (
        <TableSkeleton rows={6} cols={6} />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : jobs.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setKind(""); reset(); }} /> : <EmptyState icon={<ListChecks className="h-5 w-5" />} title="No jobs yet" />
      ) : (
        <>
          <ResultCount total={jobs.data.total} noun="jobs" />
          <Table>
            <THead>
              <tr>
                <TH>Kind</TH>
                <TH>Status</TH>
                <TH className="text-right">Attempts</TH>
                <TH>Created</TH>
                <TH>Finished</TH>
                <TH>Last error</TH>
                <TH className="relative"><span className="sr-only">Actions</span></TH>
              </tr>
            </THead>
            <TBody>
              {jobs.data.items.map((j) => (
                <TR key={j.id} className="align-top">
                  <TD className="whitespace-nowrap font-mono text-xs text-fg">{j.kind}</TD>
                  <TD><DotBadge tone={JOB_TONE[j.status] ?? "neutral"}>{titleCase(j.status)}</DotBadge></TD>
                  <TD className="tabular whitespace-nowrap text-right text-xs text-muted">{j.attempts} / {j.max_attempts}</TD>
                  <TD className="tabular whitespace-nowrap text-xs text-muted" title={formatDateTime(j.created_at)}>{relativeTime(j.created_at)}</TD>
                  <TD className="tabular whitespace-nowrap text-xs text-muted" title={formatDateTime(j.finished_at)}>{j.finished_at ? relativeTime(j.finished_at) : j.run_after && j.status === "queued" ? `runs ${relativeTime(j.run_after)}` : "—"}</TD>
                  <TD className="min-w-48 max-w-sm">
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
    <section id="errors" aria-labelledby="errors-heading" className="mt-14 scroll-mt-28">
      <SectionHeading
        id="errors-heading"
        eyebrow="Exceptions"
        title="Recent errors"
        description="Unhandled exceptions from the API and worker. Match a user’s “Reference” to the request id."
      />
      <FilterBar>
        <ChoiceFilter
          label="Error source"
          value={source}
          onChange={(v) => { setSource(v); setPage(1); }}
          options={[{ value: "", label: "All sources" }, { value: "api", label: "API" }, { value: "worker", label: "Worker" }]}
        />
      </FilterBar>
      {errors.isPending ? (
        <TableSkeleton rows={4} cols={4} />
      ) : errors.isError ? (
        <ErrorState error={errors.error} onRetry={() => errors.refetch()} />
      ) : errors.data.items.length === 0 ? (
        <EmptyState icon={<Bug className="h-5 w-5" />} title="No errors recorded" description={source ? "Nothing from this source." : "Nice."} />
      ) : (
        <>
          <ResultCount total={errors.data.total} noun="errors" />
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
                  <TD className="tabular whitespace-nowrap text-xs text-muted" title={formatDateTime(e.created_at)}>{relativeTime(e.created_at)}</TD>
                  <TD><Badge tone={e.source === "worker" ? "info" : "neutral"}>{titleCase(e.source)}</Badge></TD>
                  <TD className="text-xs">
                    {e.method || e.path ? <span className="block whitespace-nowrap font-mono text-fg">{e.method} {e.path}</span> : null}
                    {e.request_id ? <span className="block font-mono text-[11px] text-subtle">{e.request_id}</span> : null}
                  </TD>
                  <TD className="min-w-56 max-w-md text-xs">
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
      <AdminHeader
        eyebrow="Monitor"
        icon={<ListChecks />}
        title="Jobs & errors"
        description="Background queue state (auto-refreshes every 30 seconds). Failed and dead jobs can be re-queued."
      />
      <Suspense fallback={<TableSkeleton rows={6} cols={6} />}>
        <Jobs />
      </Suspense>
      <Errors />
    </div>
  );
}
