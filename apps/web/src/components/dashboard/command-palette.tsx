"use client";
import { Command } from "cmdk";
import { Boxes, FileCheck2, FileText, Gauge, Plus, Radar, ScrollText, ShieldAlert, ShieldCheck, UserPlus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ALL_NAV_ITEMS } from "@/components/dashboard/nav";
import { api } from "@/lib/api";
import { useCan } from "@/lib/queries";
import type { SearchResponse } from "@/lib/types";

const ACTIONS = [
  { label: "Run an audit", href: "/dashboard/audits/new", icon: Gauge, permission: "audits:run", keywords: "start test evaluate" },
  { label: "Add an AI system", href: "/dashboard/systems?new=1", icon: Plus, permission: "systems:write", keywords: "register connect endpoint" },
  { label: "Create a runtime policy", href: "/dashboard/policies?tab=runtime&new=1", icon: ScrollText, permission: "policies:write", keywords: "policy studio rule" },
  { label: "Review pending approvals", href: "/dashboard/runtime?tab=approvals", icon: Radar, permission: "runtime:read", keywords: "approve deny enforce" },
  { label: "Verify evidence integrity", href: "/dashboard/evidence?tab=verify", icon: ShieldCheck, permission: "evidence:read", keywords: "hash chain tamper package" },
  { label: "Open critical findings", href: "/dashboard/findings?severity=critical&open_only=true", icon: ShieldAlert, keywords: "issues high risk" },
  { label: "Invite a member", href: "/dashboard/team?invite=1", icon: UserPlus, permission: "team:manage", keywords: "team user role" },
];

const TYPE_ICON: Record<string, typeof Boxes> = { system: Boxes, finding: ShieldAlert, policy: ScrollText, control: ScrollText, audit: Gauge, evidence: FileCheck2 };
const HEADING =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-[var(--color-text-subtle)]";
const ITEM =
  "flex cursor-pointer items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-2 text-sm text-[var(--color-text-muted)] data-[selected=true]:bg-[var(--color-surface-2)] data-[selected=true]:text-[var(--color-text)]";

/** ⌘K: navigation, permission-aware actions and live search. Only internal routes are ever navigated to. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const can = useCan();
  const [query, setQuery] = useState("");
  const [rawResults, setResults] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const results = query.trim().length < 2 ? null : rawResults;

  useEffect(() => {
    requestRef.current?.abort();
    if (query.trim().length < 2) return;
    const controller = new AbortController();
    requestRef.current = controller;
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const r = await api.get<SearchResponse>("/search", { q: query.trim() }, controller.signal);
        if (!controller.signal.aborted) setResults(r);
      } catch {
        if (!controller.signal.aborted) setResults(null);
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 180);
    return () => {
      clearTimeout(t);
      controller.abort();
    };
  }, [query]);

  function close() {
    onOpenChange(false);
    setQuery("");
    setResults(null);
  }

  function go(href: string) {
    if (!href.startsWith("/")) return; // never navigate off-site from search data
    close();
    router.push(href);
  }

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(o) => (o ? onOpenChange(true) : close())}
      label="Command palette"
      shouldFilter={!results}
      loop
      overlayClassName="fixed inset-0 z-[60] bg-black/60 backdrop-blur-[2px]"
      contentClassName="fixed left-1/2 top-[12vh] z-[61] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] shadow-[var(--shadow-lg)]"
    >
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4">
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder="Search systems, findings, policies, audits — or type a command"
          aria-label="Search or run a command"
          className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-subtle)]"
        />
        <kbd className="rounded border border-[var(--color-border-strong)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-subtle)]">ESC</kbd>
      </div>
      <Command.List className="max-h-[22rem] overflow-y-auto p-2">
        <Command.Empty className="px-3 py-6 text-center text-sm text-[var(--color-text-subtle)]">{searching ? "Searching…" : "No results."}</Command.Empty>
        {results ? (
          Object.entries(results.groups).map(([type, items]) => (
            <Command.Group key={type} heading={type} className={HEADING}>
              {items.map((item) => {
                const Icon = TYPE_ICON[item.type] ?? FileText;
                return (
                  <Command.Item key={item.id} value={item.id + item.title} onSelect={() => go(item.url)} className={ITEM}>
                    <Icon className="h-4 w-4" aria-hidden />
                    <span className="flex-1 truncate">{item.title}</span>
                    {item.subtitle ? <span className="text-xs text-[var(--color-text-subtle)]">{item.subtitle}</span> : null}
                  </Command.Item>
                );
              })}
            </Command.Group>
          ))
        ) : (
          <>
            <Command.Group heading="Actions" className={HEADING}>
              {ACTIONS.filter((a) => !a.permission || can(a.permission)).map((a) => (
                <Command.Item key={a.href} value={`${a.label} ${a.keywords}`} onSelect={() => go(a.href)} className={ITEM}>
                  <a.icon className="h-4 w-4" aria-hidden />
                  {a.label}
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Go to" className={HEADING}>
              {ALL_NAV_ITEMS.filter((i) => !i.permission || can(i.permission)).map((i) => (
                <Command.Item key={i.href} value={`${i.label} ${i.keywords ?? ""}`} onSelect={() => go(i.href)} className={ITEM}>
                  <i.icon className="h-4 w-4" aria-hidden />
                  {i.label}
                </Command.Item>
              ))}
            </Command.Group>
          </>
        )}
      </Command.List>
    </Command.Dialog>
  );
}
