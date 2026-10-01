"use client";
import { Download, Search, ShieldAlert, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Pagination } from "@/components/ui/display";
import { Input, Select } from "@/components/ui/forms";
import { Badge, Button, EmptyState, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { api, download, errorMessage } from "@/lib/api";
import { useCan, useFindings, useInvalidate, useSystems } from "@/lib/queries";
import { cn, formatDate, timeAgo, titleCase } from "@/lib/utils";

const SELECTS: { param: string; label: string; options: string[] }[] = [
  { param: "severity", label: "Severity", options: ["critical", "high", "medium", "low", "info"] },
  { param: "status", label: "Status", options: ["open", "triaged", "in_remediation", "fixed", "retesting", "resolved", "accepted_risk", "false_positive"] },
  { param: "source", label: "Source", options: ["audit", "redteam", "runtime", "regression", "manual"] },
  { param: "category", label: "Category", options: ["fairness", "hallucination", "groundedness", "privacy", "safety", "prompt_injection", "policy", "agent_action", "runtime_policy"] },
];
const SORTS = [
  { value: "risk", label: "Highest risk" },
  { value: "sla", label: "SLA due soonest" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "number", label: "Number" },
];
const BULK_STATUSES = ["triaged", "in_remediation", "fixed", "false_positive"];
const FILTER_KEYS = ["severity", "status", "source", "category", "system_id", "q", "open_only", "sort", "audit_id"];

export default function FindingsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const can = useCan();
  const invalidate = useInvalidate();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<{ status?: string; priority?: string } | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");
  const systems = useSystems({ page_size: 100 });

  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""]).filter(([, v]) => v)) as Record<string, string>, [params]);
  const query = useFindings({ ...filters, page, page_size: 50 });
  const systemName = useMemo(() => new Map((systems.data?.items ?? []).map((s) => [s.id, s.name])), [systems.data]);

  function setFilter(param: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(param, value);
    else next.delete(param);
    setPage(1);
    setSelected(new Set());
    const qs = next.toString();
    router.replace(`/dashboard/findings${qs ? `?${qs}` : ""}`);
  }

  useEffect(() => {
    const t = setTimeout(() => {
      if ((params.get("q") ?? "") !== q.trim()) setFilter("q", q.trim());
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce only on input changes
  }, [q]);

  const items = query.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((f) => selected.has(f.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((f) => f.id)));
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function applyBulk() {
    if (!bulk) return;
    try {
      const r = await api.post<{ updated: string[]; failed: { id: string; error: string; message?: string }[] }>("/findings/bulk", { ids: [...selected], ...bulk });
      if (r.failed.length) toast.warning(`${r.updated.length} updated, ${r.failed.length} skipped: ${r.failed[0].message ?? r.failed[0].error}`);
      else toast.success(`${r.updated.length} finding${r.updated.length === 1 ? "" : "s"} updated`);
      setSelected(new Set());
      invalidate("findings");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function exportFindings(format: "csv" | "json") {
    try {
      await download("/findings/export", { method: "GET", params: { format, status: filters.status, system_id: filters.system_id, audit_id: filters.audit_id } });
    } catch (e) {
      toast.error(errorMessage(e, "Export failed"));
    }
  }

  const activeFilters = Object.keys(filters).filter((k) => k !== "sort");
  const canWrite = can("findings:write");

  return (
    <div>
      <PageHeader
        title="Findings"
        description="Evidence-backed risks across your AI systems, deduplicated per system and ranked by an explainable risk score."
        actions={
          <>
            <Button variant="secondary" size="sm" icon={Download} onClick={() => exportFindings("csv")}>
              CSV
            </Button>
            <Button variant="secondary" size="sm" icon={Download} onClick={() => exportFindings("json")}>
              JSON
            </Button>
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-60">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-subtle)]" aria-hidden />
          <Input aria-label="Search findings" placeholder="Search title" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 pl-8 text-xs" />
        </div>
        {SELECTS.map((f) => (
          <Select key={f.param} aria-label={f.label} value={filters[f.param] ?? ""} onChange={(e) => setFilter(f.param, e.target.value)} className="h-8 w-auto text-xs">
            <option value="">{f.label}: all</option>
            {f.options.map((o) => (
              <option key={o} value={o}>
                {titleCase(o)}
              </option>
            ))}
          </Select>
        ))}
        <Select aria-label="System" value={filters.system_id ?? ""} onChange={(e) => setFilter("system_id", e.target.value)} className="h-8 w-auto max-w-48 text-xs">
          <option value="">System: all</option>
          {(systems.data?.items ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
          <input type="checkbox" checked={filters.open_only === "true"} onChange={(e) => setFilter("open_only", e.target.checked ? "true" : "")} className="accent-[var(--color-accent)]" />
          Open only
        </label>
        <Select aria-label="Sort" value={filters.sort ?? "risk"} onChange={(e) => setFilter("sort", e.target.value === "risk" ? "" : e.target.value)} className="ml-auto h-8 w-auto text-xs">
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              Sort: {s.label}
            </option>
          ))}
        </Select>
        {activeFilters.length ? (
          <button
            type="button"
            onClick={() => {
              setQ("");
              router.replace("/dashboard/findings");
            }}
            className="inline-flex items-center gap-1 text-xs text-[var(--color-accent-bright)] hover:underline"
          >
            <X className="h-3 w-3" /> Clear filters
          </button>
        ) : null}
      </div>

      {selected.size > 0 && canWrite ? (
        <div role="region" aria-label="Bulk actions" className="mb-3 flex flex-wrap items-center gap-2 rounded-[var(--radius)] border border-[var(--color-accent)]/40 bg-[var(--color-accent-dim)]/40 px-3 py-2 text-sm">
          <span className="font-medium">{selected.size} selected</span>
          <Select aria-label="Set status" className="h-8 w-auto text-xs" value="" onChange={(e) => e.target.value && setBulk({ status: e.target.value })}>
            <option value="">Set status…</option>
            {BULK_STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
          <Select aria-label="Set priority" className="h-8 w-auto text-xs" value="" onChange={(e) => e.target.value && setBulk({ priority: e.target.value })}>
            <option value="">Set priority…</option>
            {["p1", "p2", "p3", "p4"].map((p) => (
              <option key={p} value={p}>
                {p.toUpperCase()}
              </option>
            ))}
          </Select>
          <span className="text-xs text-[var(--color-text-subtle)]">Risk acceptance needs an individual justification and expiry — open each finding.</span>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </div>
      ) : null}

      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            activeFilters.length ? (
              <EmptyState icon={Search} title="No findings match these filters" description="Clear or widen the filters to see more." />
            ) : (
              <EmptyState icon={ShieldAlert} title="No findings yet" description="Findings appear when audits, red-team runs or runtime policies detect a risk — each one is linked to its evidence." />
            )
          ) : (
            <>
              <DataTable caption="Findings">
                <THead>
                  <tr>
                    {canWrite ? (
                      <TH className="w-8">
                        <input type="checkbox" aria-label="Select all findings on this page" checked={allSelected} onChange={toggleAll} className="relative z-10 accent-[var(--color-accent)]" />
                      </TH>
                    ) : null}
                    <TH>Finding</TH>
                    <TH>System</TH>
                    <TH>Severity</TH>
                    <TH>Risk</TH>
                    <TH>Status</TH>
                    <TH>SLA</TH>
                    <TH>Last seen</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((f) => {
                    const overdue = f.sla_due_at && new Date(f.sla_due_at) < new Date() && !["resolved", "accepted_risk", "false_positive"].includes(f.status);
                    return (
                      <TR key={f.id} href={`/dashboard/findings/${f.id}`} selected={selected.has(f.id)}>
                        {canWrite ? (
                          <TD className="w-8">
                            <input type="checkbox" aria-label={`Select finding ${f.number}`} checked={selected.has(f.id)} onChange={() => toggle(f.id)} className="relative z-10 accent-[var(--color-accent)]" />
                          </TD>
                        ) : null}
                        <TD className="max-w-sm">
                          <div className="flex items-baseline gap-2">
                            <span className="font-mono text-xs text-[var(--color-text-subtle)]">#{f.number}</span>
                            {/* Stretched link lives in the title cell so the checkbox stays independently clickable. */}
                            <span className="line-clamp-1 [&_a]:after:absolute [&_a]:after:inset-0">
                              <RowLink href={`/dashboard/findings/${f.id}`}>{f.title}</RowLink>
                            </span>
                          </div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-[var(--color-text-subtle)]">
                            {titleCase(f.category)}
                            {f.source && f.source !== "audit" ? <Badge>{titleCase(f.source)}</Badge> : null}
                            {f.priority ? <Badge tone="accent">{f.priority.toUpperCase()}</Badge> : null}
                            {f.control_ref ? <span className="font-mono">{f.control_ref}</span> : null}
                          </div>
                        </TD>
                        <TD className="text-[var(--color-text-muted)]">{systemName.get(f.system_id) ?? "—"}</TD>
                        <TD>
                          <SeverityBadge severity={f.severity} />
                        </TD>
                        <TD>
                          <RiskBadge level={f.risk_level} />
                        </TD>
                        <TD>
                          <StatusBadge status={f.status} />
                        </TD>
                        <TD className={cn("text-xs", overdue ? "font-medium text-[var(--color-critical)]" : "text-[var(--color-text-subtle)]")}>
                          {f.sla_due_at ? (overdue ? `Overdue · ${formatDate(f.sla_due_at)}` : formatDate(f.sla_due_at)) : "—"}
                        </TD>
                        <TD className="text-[var(--color-text-subtle)]">{timeAgo(f.last_seen_at ?? f.created_at)}</TD>
                      </TR>
                    );
                  })}
                </tbody>
              </DataTable>
              <div className="flex items-center justify-between">
                <p className="pt-3 text-xs text-[var(--color-text-subtle)]">{data.meta.total.toLocaleString()} findings</p>
                <Pagination
                  page={data.meta.page}
                  totalPages={data.meta.total_pages}
                  onPage={(p) => {
                    setPage(p);
                    setSelected(new Set());
                  }}
                />
              </div>
            </>
          )
        }
      </QueryBoundary>

      <ConfirmDialog
        open={!!bulk}
        onOpenChange={(o) => !o && setBulk(null)}
        tone="primary"
        title={bulk?.status ? `Set ${selected.size} findings to “${titleCase(bulk.status)}”?` : `Set priority on ${selected.size} findings?`}
        description={
          bulk?.status
            ? "Each finding is validated against its lifecycle on its own. Findings that cannot make this transition are skipped and reported; every change is recorded in the finding history and the audit log."
            : `Priority ${bulk?.priority?.toUpperCase()} will be applied to every selected finding.`
        }
        confirmLabel="Apply"
        onConfirm={applyBulk}
      />
    </div>
  );
}
