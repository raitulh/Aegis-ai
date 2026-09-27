"use client";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Boxes, FileText, Gauge, ScrollText, ShieldAlert, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { SearchResponse } from "@/lib/types";

const QUICK = [
  { label: "Go to Overview", href: "/dashboard", icon: Sparkles },
  { label: "AI Systems", href: "/dashboard/systems", icon: Boxes },
  { label: "New Audit", href: "/dashboard/audits/new", icon: Gauge },
  { label: "Findings", href: "/dashboard/findings", icon: ShieldAlert },
  { label: "Policies", href: "/dashboard/policies", icon: ScrollText },
  { label: "Evidence", href: "/dashboard/evidence", icon: FileText },
];

const TYPE_ICON: Record<string, typeof Boxes> = { system: Boxes, finding: ShieldAlert, policy: ScrollText, control: ScrollText, audit: Gauge };

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResponse | null>(null);

  useEffect(() => {
    if (!query || query.length < 2) {
      setResults(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        setResults(await api.get<SearchResponse>("/search", { q: query }));
      } catch {
        setResults(null);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [query]);

  function go(href: string) {
    onOpenChange(false);
    setQuery("");
    router.push(href);
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-[12vh] backdrop-blur-sm" onClick={() => onOpenChange(false)}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-xl overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] shadow-[var(--shadow-lg)]">
        <Command shouldFilter={!results} loop>
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4">
            <Sparkles className="h-4 w-4 text-[var(--color-text-subtle)]" />
            <Command.Input value={query} onValueChange={setQuery} placeholder="Search systems, findings, policies…" className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-subtle)]" autoFocus />
            <kbd className="rounded border border-[var(--color-border-strong)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-subtle)]">ESC</kbd>
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-sm text-[var(--color-text-subtle)]">No results found.</Command.Empty>
            {!results && (
              <Command.Group heading="Quick actions" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-[var(--color-text-subtle)]">
                {QUICK.map((q) => (
                  <Command.Item key={q.href} onSelect={() => go(q.href)} className="flex cursor-pointer items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-2 text-sm text-[var(--color-text-muted)] data-[selected=true]:bg-[var(--color-surface-2)] data-[selected=true]:text-[var(--color-text)]">
                    <q.icon className="h-4 w-4" />
                    {q.label}
                  </Command.Item>
                ))}
              </Command.Group>
            )}
            {results &&
              Object.entries(results.groups).map(([type, items]) => (
                <Command.Group key={type} heading={type} className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-[var(--color-text-subtle)]">
                  {items.map((item) => {
                    const Icon = TYPE_ICON[item.type] ?? FileText;
                    return (
                      <Command.Item key={item.id} value={item.id + item.title} onSelect={() => go(item.url)} className="flex cursor-pointer items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-2 text-sm text-[var(--color-text-muted)] data-[selected=true]:bg-[var(--color-surface-2)] data-[selected=true]:text-[var(--color-text)]">
                        <Icon className="h-4 w-4" />
                        <span className="flex-1 truncate">{item.title}</span>
                        {item.subtitle ? <span className="text-xs text-[var(--color-text-subtle)]">{item.subtitle}</span> : null}
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ))}
          </Command.List>
        </Command>
      </div>
    </div>
  );
}
