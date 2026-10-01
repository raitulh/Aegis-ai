"use client";

import { useQuery } from "@tanstack/react-query";
import { History, RotateCcw } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, FilterBar, JsonDisclosure, ResultCount, TableSkeleton } from "@/components/admin/admin-ui";
import type { AuditEntry } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useDebounced } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 50;
const FIELDS = [
  { key: "action", label: "Action prefix", placeholder: "e.g. org. or moderation.hide", max: 64 },
  { key: "target_type", label: "Target type", placeholder: "e.g. user, organization", max: 32 },
  { key: "target_id", label: "Target id", placeholder: "UUID or key", max: 64 },
  { key: "actor_handle", label: "Actor handle", placeholder: "@handle", max: 31 },
] as const;
type FilterKey = (typeof FIELDS)[number]["key"];

function AuditLog() {
  const [state, setState, reset] = useUrlState({ action: "", target_type: "", target_id: "", actor_handle: "", page: "1" });
  const [draft, setDraft] = useState<Record<FilterKey, string>>({
    action: state.action ?? "",
    target_type: state.target_type ?? "",
    target_id: state.target_id ?? "",
    actor_handle: state.actor_handle ?? "",
  });
  const debounced = useDebounced(draft, 400);
  useEffect(() => {
    const patch: Partial<Record<FilterKey, string>> = {};
    let changed = false;
    for (const f of FIELDS) {
      const v = debounced[f.key].trim();
      if (v !== (state[f.key] ?? "")) {
        patch[f.key] = v;
        changed = true;
      }
    }
    if (changed) setState(patch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const page = Math.max(1, Number(state.page) || 1);
  const filters = {
    action: state.action || undefined,
    target_type: state.target_type || undefined,
    target_id: state.target_id || undefined,
    actor_handle: state.actor_handle?.replace(/^@/, "") || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const log = useQuery({ queryKey: ["admin", "audit", filters], queryFn: () => get<Page<AuditEntry>>("/admin/audit", filters) });
  const filtered = FIELDS.some((f) => state[f.key]);

  return (
    <div>
      <AdminHeader eyebrow="Governance" icon={<History />} title="Audit log" description="Every privileged action, newest first. Entries are append-only." />
      <FilterBar role="search" className="grid grid-cols-2 gap-3 p-3 sm:grid lg:grid-cols-4">
        {FIELDS.map((f) => (
          <label key={f.key} className="block min-w-0">
            <span className="text-eyebrow text-subtle">{f.label}</span>
            <Input
              className="mt-1.5 h-9 font-mono text-[13px]"
              value={draft[f.key]}
              placeholder={f.placeholder}
              maxLength={f.max}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            />
          </label>
        ))}
      </FilterBar>
      {filtered ? (
        <div className="-mt-1 mb-3 flex justify-end">
          <Button size="sm" variant="ghost" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => { setDraft({ action: "", target_type: "", target_id: "", actor_handle: "" }); reset(); }}>Clear filters</Button>
        </div>
      ) : null}

      {log.isPending ? (
        <TableSkeleton rows={10} cols={6} />
      ) : log.isError ? (
        <ErrorState error={log.error} onRetry={() => log.refetch()} />
      ) : log.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setDraft({ action: "", target_type: "", target_id: "", actor_handle: "" }); reset(); }} /> : <EmptyState icon={<History className="h-5 w-5" />} title="No audit entries yet" />
      ) : (
        <>
          <ResultCount total={log.data.total} noun="entries" />
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Action · Actor</TH>
                <TH>Target</TH>
                <TH>Reason</TH>
                <TH>Metadata</TH>
              </tr>
            </THead>
            <TBody>
              {log.data.items.map((e) => (
                <TR key={e.id} className="align-top">
                  <TD className="tabular whitespace-nowrap text-xs text-subtle" title={formatDateTime(e.created_at)}>
                    <span className="block font-medium text-fg">{relativeTime(e.created_at)}</span>
                    {formatDateTime(e.created_at)}
                  </TD>
                  <TD className="min-w-44">
                    <button
                      type="button"
                      className="whitespace-nowrap rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11.5px] text-accent-strong transition-colors hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                      title="Filter by this action"
                      onClick={() => { setDraft({ ...draft, action: e.action }); setState({ action: e.action }); }}
                    >
                      {e.action}
                    </button>
                    <div className="relative mt-1.5 flex min-w-0 items-center gap-1.5 text-xs">
                      <span className="sr-only">Actor:</span>
                      {e.actor ? <UserLink user={e.actor} size={18} className="text-xs" /> : <span className="text-xs text-subtle">System</span>}
                    </div>
                  </TD>
                  <TD className="text-xs">
                    {e.target_type ? (
                      <button
                        type="button"
                        className="rounded-md text-left transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                        title="Filter by this target"
                        onClick={() => {
                          setDraft({ ...draft, target_type: e.target_type ?? "", target_id: e.target_id ?? "" });
                          setState({ target_type: e.target_type ?? "", target_id: e.target_id ?? "" });
                        }}
                      >
                        <span className="block text-muted">{e.target_type}</span>
                        {e.target_id ? <span className="block max-w-40 truncate font-mono text-[11px] text-subtle">{e.target_id}</span> : null}
                      </button>
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TD>
                  <TD className="min-w-48 max-w-xs text-xs text-fg"><span className="line-clamp-3 whitespace-pre-wrap">{e.reason ?? <span className="text-subtle">—</span>}</span></TD>
                  <TD><JsonDisclosure value={e.meta} label="Metadata" /></TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={log.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </div>
  );
}

export default function AdminAuditPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={10} cols={6} />}>
      <AuditLog />
    </Suspense>
  );
}
