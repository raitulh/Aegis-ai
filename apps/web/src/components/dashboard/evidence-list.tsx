"use client";
import { useState } from "react";
import { ChevronDown, FileText, Lock } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import { cn, titleCase } from "@/lib/utils";
import type { Evidence } from "@/lib/types";

export function EvidenceList({ items }: { items: Evidence[] }) {
  const [open, setOpen] = useState<string | null>(items[0]?.id ?? null);
  return (
    <div className="space-y-2">
      {items.map((e) => (
        <div key={e.id} className="overflow-hidden rounded-[var(--radius)] border border-[var(--color-border)]">
          <button onClick={() => setOpen(open === e.id ? null : e.id)} className="flex w-full items-center gap-2 bg-[var(--color-surface)] px-3 py-2.5 text-left hover:bg-[var(--color-surface-2)]">
            <FileText className="h-4 w-4 shrink-0 text-[var(--color-text-subtle)]" />
            <span className="flex-1 text-sm font-medium">{e.title}</span>
            {e.sensitive ? <Lock className="h-3.5 w-3.5 text-[var(--color-medium)]" /> : null}
            <Badge>{titleCase(e.kind)}</Badge>
            <span className="hidden text-xs capitalize text-[var(--color-text-subtle)] sm:inline">{e.confidence_level}</span>
            <ChevronDown className={cn("h-4 w-4 text-[var(--color-text-subtle)] transition-transform", open === e.id && "rotate-180")} />
          </button>
          {open === e.id ? (
            <div className="border-t border-[var(--color-border)] bg-[var(--color-bg)]/40 p-3">
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-[var(--color-text-muted)]">{JSON.stringify(e.content, null, 2)}</pre>
              {e.confidence_reasons.length ? <p className="mt-2 text-[10px] text-[var(--color-text-subtle)]">Confidence: {e.confidence_reasons.join(" · ")}</p> : null}
              <p className="mt-1 font-mono text-[10px] text-[var(--color-text-subtle)]">hash {e.content_hash.slice(0, 24)}… · chain {e.chain_hash.slice(0, 16)}…</p>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
