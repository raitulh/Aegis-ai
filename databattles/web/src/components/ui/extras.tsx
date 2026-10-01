"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Keyboard key hint. */
export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-surface-2 px-1 font-mono text-[10px] text-muted", className)}>
      {children}
    </kbd>
  );
}

/**
 * Pill-style single-choice control for view switches and small filter sets (a radiogroup, not tabs —
 * use `Tabs`/`NavTabs` when it switches page sections).
 */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  label,
  size = "md",
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; icon?: ReactNode; count?: number }[];
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-0.5 [scrollbar-width:none]", className)}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-[8px] font-medium transition-[background-color,color,box-shadow] duration-200 [&_svg]:h-3.5 [&_svg]:w-3.5",
              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]",
              size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-[13px]",
              on ? "bg-surface-3 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),0_1px_2px_rgb(0_0_0/0.2)]" : "text-muted hover:text-fg",
            )}
          >
            {o.icon}
            {o.label}
            {o.count !== undefined ? <span className="tabular rounded-full bg-surface-2 px-1.5 text-[10.5px] text-subtle">{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/** Compact fact for hero/meta rows: icon, label (eyebrow) and value. */
export function MetaItem({ icon, label, children, className }: { icon?: ReactNode; label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="flex items-center gap-1.5 text-eyebrow text-subtle [&_svg]:h-3.5 [&_svg]:w-3.5">
        {icon}
        {label}
      </dt>
      <dd className="mt-1.5 truncate text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

/** Leaderboard rank with podium styling for 1–3 (the number is always shown, so colour is never the only signal). */
export function RankBadge({ rank, size = "md", className }: { rank: number | null | undefined; size?: "sm" | "md" | "lg"; className?: string }) {
  const podium =
    rank === 1
      ? "bg-[linear-gradient(135deg,#fde68a,#f59e0b)] text-black shadow-[0_0_18px_-4px_#f59e0b]"
      : rank === 2
        ? "bg-[linear-gradient(135deg,#f1f5f9,#94a3b8)] text-black shadow-[0_0_14px_-5px_#cbd5e1]"
        : rank === 3
          ? "bg-[linear-gradient(135deg,#fdba74,#c2410c)] text-black shadow-[0_0_14px_-5px_#ea580c]"
          : "bg-surface-3 text-muted";
  const dims = size === "sm" ? "h-6 w-6 text-[11px]" : size === "lg" ? "h-10 w-10 text-sm" : "h-7 w-7 text-xs";
  return (
    <span className={cn("tabular inline-flex shrink-0 items-center justify-center rounded-full font-mono font-semibold", dims, podium, className)} aria-label={rank ? `Rank ${rank}` : "Unranked"}>
      {rank ?? "—"}
    </span>
  );
}
