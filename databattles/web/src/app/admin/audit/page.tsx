"use client";

import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AdminHeader, JsonDisclosure } from "@/components/admin/admin-ui";
import type { AuditEntry } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, NoResults, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
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
      <AdminHeader title="Audit log" description="Every privileged action, newest first. Entries are append-only." />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="search">
        {FIELDS.map((f) => (
          <label key={f.key} className="block text-xs font-medium text-muted">
            {f.label}
            <Input
              className="mt-1"
              value={draft[f.key]}
              placeholder={f.placeholder}
              maxLength={f.max}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            />
          </label>
        ))}
      </div>
      {filtered ? (
        <div className="mb-3">
          <Button size="sm" variant="ghost" onClick={() => { setDraft({ action: "", target_type: "", target_id: "", actor_handle: "" }); reset(); }}>Clear filters</Button>
        </div>
      ) : null}

      {log.isPending ? (
        <SkeletonRows rows={10} />
      ) : log.isError ? (
        <ErrorState error={log.error} onRetry={() => log.refetch()} />
      ) : log.data.items.length === 0 ? (
        filtered ? <NoResults onReset={() => { setDraft({ action: "", target_type: "", target_id: "", actor_handle: "" }); reset(); }} /> : <EmptyState icon={<History className="h-5 w-5" />} title="No audit entries yet" />
      ) : (
        <>
          <p className="mb-2 text-sm text-muted" aria-live="polite">{formatNumber(log.data.total)} entries</p>
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Action</TH>
                <TH>Actor</TH>
                <TH>Target</TH>
                <TH>Reason</TH>
                <TH>Metadata</TH>
              </tr>
            </THead>
            <TBody>
              {log.data.items.map((e) => (
                <TR key={e.id} className="align-top">
                  <TD className="whitespace-nowrap text-xs text-muted" title={formatDateTime(e.created_at)}>
                    <span className="block text-fg">{relativeTime(e.created_at)}</span>
                    {formatDateTime(e.created_at)}
                  </TD>
                  <TD>
                    <button type="button" className="font-mono text-xs text-accent-strong hover:underline" title="Filter by this action" onClick={() => { setDraft({ ...draft, action: e.action }); setState({ action: e.action }); }}>
                      {e.action}
                    </button>
                  </TD>
                  <TD>{e.actor ? <UserLink user={e.actor} size={20} /> : <span className="text-xs text-subtle">System</span>}</TD>
                  <TD className="text-xs">
                    {e.target_type ? (
                      <button
                        type="button"
                        className="text-left hover:text-accent-strong"
                        title="Filter by this target"
                        onClick={() => {
                          setDraft({ ...draft, target_type: e.target_type ?? "", target_id: e.target_id ?? "" });
                          setState({ target_type: e.target_type ?? "", target_id: e.target_id ?? "" });
                        }}
                      >
                        <span className="block text-muted">{e.target_type}</span>
                        {e.target_id ? <span className="block max-w-48 truncate font-mono text-[11px] text-subtle">{e.target_id}</span> : null}
                      </button>
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TD>
                  <TD className="max-w-xs text-xs text-fg"><span className="line-clamp-3 whitespace-pre-wrap">{e.reason ?? <span className="text-subtle">—</span>}</span></TD>
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
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <AuditLog />
    </Suspense>
  );
}
