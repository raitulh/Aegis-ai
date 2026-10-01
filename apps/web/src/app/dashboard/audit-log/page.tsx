"use client";
import { History, ListChecks } from "lucide-react";
import { useEffect, useState } from "react";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Pagination } from "@/components/ui/display";
import { Input, Select } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { useAuditLog, useCan, useJobs } from "@/lib/queries";
import { formatDateTime, titleCase } from "@/lib/utils";

const ACTION_GROUPS = [
  { value: "", label: "All actions" },
  { value: "auth.", label: "Sign-in & sessions" },
  { value: "team.", label: "Members & roles" },
  { value: "api_key.", label: "API keys" },
  { value: "policy.", label: "Policies" },
  { value: "runtime.", label: "Runtime" },
  { value: "finding.", label: "Findings" },
  { value: "audit.", label: "Audits" },
  { value: "evidence.", label: "Evidence" },
  { value: "settings.", label: "Settings" },
  { value: "billing.", label: "Billing" },
];

export default function AuditLogPage() {
  const can = useCan();
  const [tab, setTab] = useState("activity");
  return (
    <div>
      <PageHeader title="Audit log" description="Who did what, when — every security-relevant change in this workspace. Entries are append-only." />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-5">
          <TabTrigger value="activity">Activity</TabTrigger>
          {can("jobs:read") ? <TabTrigger value="jobs">Background jobs</TabTrigger> : null}
        </TabsList>
        <TabPanel value="activity">{tab === "activity" ? <ActivityTab /> : null}</TabPanel>
        <TabPanel value="jobs">{tab === "jobs" ? <JobsTab /> : null}</TabPanel>
      </Tabs>
    </div>
  );
}

function ActivityTab() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      setTerm(resource.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [resource]);
  const query = useAuditLog({ page, page_size: 50, action: action || undefined, resource_type: term || undefined });

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <Select
          aria-label="Action"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
          className="h-8 w-52 text-xs"
        >
          {ACTION_GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </Select>
        <Input aria-label="Resource type" placeholder="Resource type (e.g. runtime_policy)" value={resource} onChange={(e) => setResource(e.target.value)} className="h-8 w-64 text-xs" />
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState icon={History} title="No entries" description={action || term ? "Nothing matches these filters." : "Changes to this workspace will be recorded here."} />
          ) : (
            <>
              <DataTable caption="Audit log">
                <THead>
                  <tr>
                    <TH>When</TH>
                    <TH>Actor</TH>
                    <TH>Action</TH>
                    <TH>Resource</TH>
                    <TH>Request</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((e) => (
                    <TR key={e.id}>
                      <TD className="whitespace-nowrap text-xs text-[var(--color-text-muted)]">{formatDateTime(e.created_at)}</TD>
                      <TD className="max-w-48 truncate text-sm">{e.actor ?? "System"}</TD>
                      <TD>
                        <span className="font-mono text-xs">{e.action}</span>
                      </TD>
                      <TD className="text-xs text-[var(--color-text-muted)]">
                        {titleCase(e.resource_type)}
                        {e.resource_id ? <span className="ml-1 font-mono text-[var(--color-text-subtle)]">{e.resource_id.slice(0, 8)}</span> : null}
                      </TD>
                      <TD className="font-mono text-[11px] text-[var(--color-text-subtle)]">{e.request_id?.slice(0, 12) ?? "—"}</TD>
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

function JobsTab() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const query = useJobs({ page, page_size: 50, status: status || undefined });
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Select
          aria-label="Job status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
          className="h-8 w-44 text-xs"
        >
          <option value="">All statuses</option>
          {["queued", "running", "succeeded", "retrying", "failed", "dead"].map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
        <p className="text-xs text-[var(--color-text-subtle)]">Transient failures are retried with backoff; permanent failures and exhausted retries are dead-lettered for an operator to redrive.</p>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState icon={ListChecks} title="No background jobs" description="Audits, red-team runs, webhooks and maintenance work appear here as they run." />
          ) : (
            <>
              <DataTable caption="Background jobs">
                <THead>
                  <tr>
                    <TH>Job</TH>
                    <TH>Status</TH>
                    <TH>Attempts</TH>
                    <TH>Duration</TH>
                    <TH>Error</TH>
                    <TH>Started</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((j) => (
                    <TR key={j.id}>
                      <TD className="font-mono text-xs">{j.job}</TD>
                      <TD>
                        <StatusBadge status={j.status} />
                      </TD>
                      <TD className="font-mono text-xs">
                        {j.attempts}/{j.max_attempts}
                      </TD>
                      <TD className="font-mono text-xs text-[var(--color-text-muted)]">{j.duration_ms !== null ? `${(j.duration_ms / 1000).toFixed(1)}s` : "—"}</TD>
                      <TD className="max-w-64 text-xs">
                        {j.error_class ? <Badge>{j.error_class}</Badge> : null}
                        {j.error ? <p className="mt-0.5 truncate text-[var(--color-text-subtle)]" title={j.error}>{j.error}</p> : j.error_class ? null : "—"}
                      </TD>
                      <TD className="whitespace-nowrap text-xs text-[var(--color-text-muted)]">{formatDateTime(j.created_at)}</TD>
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
