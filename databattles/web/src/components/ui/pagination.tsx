"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { Button } from "./button";

/** Compact page list: first, last, current ±1, with ellipses. */
function pageList(page: number, pages: number): (number | "…")[] {
  const set = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages));
  const sorted = [...set].sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  sorted.forEach((p, i) => {
    if (i && p - sorted[i - 1] > 1) out.push("…");
    out.push(p);
  });
  return out;
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <nav className="mt-8 flex flex-col items-center justify-between gap-3 text-sm text-muted sm:flex-row" aria-label="Pagination">
      <span className="tabular text-xs">
        Page <span className="font-medium text-fg">{page}</span> of {pages} · {formatNumber(total)} results
      </span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} icon={<ChevronLeft className="h-4 w-4" />}>
          Previous
        </Button>
        <ol className="hidden items-center gap-1 sm:flex">
          {pageList(page, pages).map((p, i) =>
            p === "…" ? (
              <li key={`gap-${i}`} className="px-1 text-subtle" aria-hidden>…</li>
            ) : (
              <li key={p}>
                <button
                  type="button"
                  onClick={() => onPage(p)}
                  aria-current={p === page ? "page" : undefined}
                  aria-label={`Page ${p}`}
                  className={cn(
                    "tabular h-8 min-w-8 rounded-[var(--radius-sm)] px-2 text-[13px] font-medium transition-colors",
                    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                    p === page ? "bg-accent-soft text-accent-strong ring-1 ring-inset ring-[color-mix(in_oklab,var(--accent)_30%,transparent)]" : "text-muted hover:bg-surface-2 hover:text-fg",
                  )}
                >
                  {p}
                </button>
              </li>
            ),
          )}
        </ol>
        <Button variant="ghost" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </nav>
  );
}
