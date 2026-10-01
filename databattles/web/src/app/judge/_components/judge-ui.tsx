"use client";

import { CheckCircle2, Circle, PencilLine } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/cn";

/**
 * Presentational pieces for the judging workspace. They hold no data fetching or behaviour — every value is
 * supplied by the page from data it already has.
 */

export type EntryStateKind = "submitted" | "draft" | "todo";

export const ENTRY_STATE_LABEL: Record<EntryStateKind, string> = {
  submitted: "Submitted",
  draft: "Draft",
  todo: "Not started",
};

/** Status glyph for a queue entry. The label is always shown next to it, so colour is never the only signal. */
export function EntryStateIcon({ state, className, pop }: { state: EntryStateKind; className?: string; pop?: boolean }) {
  if (state === "submitted") return <CheckCircle2 className={cn("h-4 w-4 shrink-0 text-success", pop && "animate-pop", className)} aria-hidden />;
  if (state === "draft") return <PencilLine className={cn("h-4 w-4 shrink-0 text-warning", className)} aria-hidden />;
  return <Circle className={cn("h-4 w-4 shrink-0 text-subtle", className)} aria-hidden />;
}

/** Small state pill used in the scoring panel header. */
export function StatePill({ state, celebrate }: { state: EntryStateKind; celebrate?: boolean }) {
  const tone = {
    submitted: "bg-success-soft text-success ring-[color-mix(in_oklab,var(--success)_30%,transparent)]",
    draft: "bg-warning-soft text-warning ring-[color-mix(in_oklab,var(--warning)_28%,transparent)]",
    todo: "bg-surface-3/80 text-muted ring-border",
  }[state];
  return (
    <span className={cn("inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs font-medium leading-none ring-1 ring-inset", tone, celebrate && "animate-pop")}>
      {state === "submitted" ? (
        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className={celebrate ? "animate-check" : undefined} />
        </svg>
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      )}
      {ENTRY_STATE_LABEL[state]}
    </span>
  );
}

/** Restrained success moment: a pulse ring and a drawn check, shown only after a real transition to submitted. */
export function SuccessMark({ celebrate, className }: { celebrate: boolean; className?: string }) {
  // `--animate-pulse-ring` loops forever, so the inline iteration count makes the pulse play exactly once.
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_35%,transparent)]",
        celebrate && "animate-pulse-ring",
        className,
      )}
      style={celebrate ? { animationIterationCount: 1 } : undefined}
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2.8} strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className={celebrate ? "animate-check" : undefined} />
      </svg>
    </span>
  );
}

/** Gap-px metric strip (the landing metrics pattern). Children are `SummaryTile`s. */
export function SummaryTiles({ children, className }: { children: ReactNode; className?: string }) {
  return (
    // The decorative hairline lives on the wrapper so the `<dl>` only contains dt/dd groups.
    <div className={cn("relative", className)}>
      <span aria-hidden className="pointer-events-none absolute inset-x-6 top-0 z-10 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-50" />
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card lg:grid-cols-4">{children}</dl>
    </div>
  );
}

const TILE_TONE = {
  accent: "text-accent-strong",
  cyan: "text-cyan",
  success: "text-success",
  warning: "text-warning",
  muted: "text-subtle",
} as const;

/** One metric tile. `value` is always real data supplied by the caller. */
export function SummaryTile({ label, value, hint, icon, tone = "accent" }: { label: ReactNode; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: keyof typeof TILE_TONE }) {
  return (
    <div className="flex min-w-0 flex-col bg-surface px-4 py-4 sm:px-5">
      <dt className="flex min-w-0 items-center gap-1.5 text-eyebrow text-subtle">
        {icon ? <span className={cn("shrink-0 [&_svg]:h-3.5 [&_svg]:w-3.5", TILE_TONE[tone])} aria-hidden>{icon}</span> : null}
        <span className="truncate">{label}</span>
      </dt>
      <dd className="tabular mt-2.5 text-[1.6rem] font-semibold leading-none tracking-[-0.03em] text-fg">{value}</dd>
      {hint ? <dd className="mt-2 truncate text-xs text-subtle">{hint}</dd> : null}
    </div>
  );
}

/** Heading for a block that is not wrapped in a panel. */
export function BlockHeading({ id, title, count, description, action }: { id?: string; title: ReactNode; count?: number; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h2 id={id} className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-fg">
          {title}
          {count !== undefined ? <span className="tabular rounded-full bg-surface-3 px-1.5 py-px text-[11px] font-medium text-subtle">{count}</span> : null}
        </h2>
        {description ? <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-muted">{description}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}

/** Loading placeholder shaped like the judging index (metric strip, assignment rows, aside). */
export function JudgeHomeSkeleton() {
  return (
    <div className="space-y-8" role="status" aria-label="Loading">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-surface px-5 py-4">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="mt-4 h-7 w-12" />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-4" style={{ opacity: 1 - i * 0.15 }}>
              <Skeleton className="h-11 w-11 shrink-0 rounded-full" />
              <div className="min-w-0 flex-1">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="mt-2 h-3 w-1/3" />
              </div>
              <Skeleton className="hidden h-3 w-32 sm:block" />
            </div>
          ))}
        </div>
        <Skeleton className="hidden h-56 rounded-[var(--radius-lg)] lg:block" />
      </div>
    </div>
  );
}

/** Loading placeholder shaped like the review workstation (queue rail, entry, scoring panel). */
export function WorkstationSkeleton() {
  return (
    <div role="status" aria-label="Loading">
      <div className="pb-8 pt-10 sm:pt-12">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="mt-4 h-9 w-2/3 max-w-lg" />
        <Skeleton className="mt-3 h-4 w-1/2 max-w-sm" />
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[256px_minmax(0,1fr)] xl:grid-cols-[272px_minmax(0,1fr)_392px]">
        <div className="space-y-2">
          <Skeleton className="h-16 rounded-[var(--radius-lg)]" />
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-[var(--radius-md)]" />
          ))}
        </div>
        <Skeleton className="h-80 rounded-[var(--radius-lg)]" />
        <Skeleton className="h-[28rem] rounded-[var(--radius-lg)] lg:col-start-2 xl:col-start-3 xl:row-start-1" />
      </div>
    </div>
  );
}
