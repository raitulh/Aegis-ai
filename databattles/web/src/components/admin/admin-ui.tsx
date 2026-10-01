"use client";

import { ChevronRight } from "lucide-react";
import { useState, type HTMLAttributes, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { SegmentedControl } from "@/components/ui/extras";
import { Select } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

/* ------------------------------------------------------------------ Page chrome */

/**
 * Title block for console pages — the page's single `h1`. Compact compared with `PageHeader` because the console
 * already has an identity strip and a section rail. `eyebrow`, `icon` and `meta` are optional additions.
 */
export function AdminHeader({
  title,
  description,
  actions,
  eyebrow,
  icon,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  icon?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="relative isolate mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div
        aria-hidden
        className="pointer-events-none absolute -left-16 -top-12 -z-10 h-40 w-[22rem] max-w-full opacity-80"
        style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
      />
      <div className="min-w-0 animate-rise">
        {eyebrow || icon ? (
          <div className="mb-2.5 flex items-center gap-2">
            {icon ? (
              <span className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>
                {icon}
              </span>
            ) : null}
            {eyebrow ? <p className="text-eyebrow text-accent-strong">{eyebrow}</p> : null}
          </div>
        ) : null}
        <h1 className="text-title text-fg">{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted sm:text-[15px]">{description}</p> : null}
        {meta ? <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-subtle">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2 animate-rise [animation-delay:60ms]">{actions}</div> : null}
    </header>
  );
}

/** Section heading inside a console page: mono eyebrow, `h2` (pass `id` for `aria-labelledby`), copy and an action. */
export function SectionHeading({
  id,
  eyebrow,
  title,
  description,
  action,
  className,
}: {
  id?: string;
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="mb-1.5 text-eyebrow text-subtle">{eyebrow}</p> : null}
        <h2 id={id} className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg">
          {title}
        </h2>
        {description ? <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{description}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}

/**
 * Card header with a configurable heading level. Same look as `CardHeader`, but defaults to `h3` so a card that sits
 * inside a section that already has an `h2` (`SectionHeading`) keeps a correct outline.
 */
export function PanelHeader({
  title,
  description,
  action,
  icon,
  as: Heading = "h3",
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  as?: "h2" | "h3" | "h4";
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b border-border px-5 py-4", className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon ? (
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>
            {icon}
          </span>
        ) : null}
        <div className="min-w-0">
          <Heading className="text-sm font-semibold tracking-[-0.01em] text-fg">{title}</Heading>
          {description ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/**
 * Single-choice filter. Below `lg` (phones and tablets, where the console uses touch-sized controls) it is a native
 * `Select` (40px tall, every option reachable); from `lg` up it is a compact `SegmentedControl` so all options are
 * visible at a glance. Only one of the two is ever displayed (the other is `display: none`, so it is also out of the
 * accessibility tree). Both are bound to the same value and `onChange`.
 */
export function ChoiceFilter<T extends string>({
  label,
  value,
  onChange,
  options,
  selectClassName,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  selectClassName?: string;
}) {
  return (
    <>
      <Select aria-label={label} className={cn("lg:hidden sm:w-44", selectClassName)} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </Select>
      <SegmentedControl label={label} size="sm" value={value} onChange={onChange} options={options} className="hidden lg:inline-flex" />
    </>
  );
}

/** Glass filter bar above a table. Pass `role="search"` when it holds search/filter controls. */
export function FilterBar({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "mb-3 flex flex-col gap-2.5 rounded-[var(--radius-lg)] border border-border surface-glass p-2.5 shadow-card sm:flex-row sm:flex-wrap sm:items-center",
        className,
      )}
      {...props}
    />
  );
}

/** "123 users" line between the filter bar and the table. The count is always a real total from the API. */
export function ResultCount({ total, noun, children }: { total: number; noun: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 px-1">
      <p className="text-xs text-subtle" aria-live="polite">
        <span className="tabular font-medium text-fg">{formatNumber(total)}</span> {noun}
      </p>
      {children}
    </div>
  );
}

/** Table-shaped loading placeholder (header strip + rows) so the layout doesn't jump when data arrives. */
export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card" role="status" aria-label="Loading">
      <div className="flex gap-6 border-b border-border bg-bg-elevated/70 px-4 py-3">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className={cn("h-2.5", i === 0 ? "w-24" : "hidden w-16 sm:block")} />
        ))}
      </div>
      <div className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="flex items-center gap-6 px-4 py-3.5" style={{ opacity: 1 - r * 0.08 }}>
            <Skeleton className="h-6 w-6 shrink-0 rounded-full" />
            <Skeleton className="h-3.5 w-40 max-w-[40%]" />
            {Array.from({ length: Math.max(0, cols - 2) }).map((_, c) => (
              <Skeleton key={c} className="hidden h-3 w-16 sm:block" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Email address that prefers to wrap before the "@" (then anywhere) instead of mid-word. */
export function EmailText({ email, className }: { email: string; className?: string }) {
  const at = email.indexOf("@");
  return (
    <span className={cn("[overflow-wrap:anywhere]", className)}>
      {at > 0 ? (
        <>
          {email.slice(0, at)}
          <wbr />
          {email.slice(at)}
        </>
      ) : (
        email
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ Tiles */

/** Hairline grid of tiles (gap-px over a border-coloured background). Set the column classes via `className`. */
export function TileGrid({ className, children, label }: { className?: string; children: ReactNode; label?: string }) {
  return (
    <div
      aria-label={label}
      role={label ? "group" : undefined}
      className={cn("grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card", className)}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ Status */

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

/** Badge with a leading status dot; the text always names the state (colour is never the only signal). */
export function DotBadge({ tone = "neutral", children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <Badge tone={tone} title={title} className={className}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </Badge>
  );
}

const USER_STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  active: "success",
  suspended: "warning",
  banned: "danger",
  deleted: "neutral",
};

export function UserStatusBadge({ status, title }: { status: string; title?: string }) {
  return (
    <DotBadge tone={USER_STATUS_TONE[status] ?? "neutral"} title={title}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </DotBadge>
  );
}

/** Collapsible JSON viewer for audit metadata and error payloads (rendered as text, never HTML). */
export function JsonDisclosure({ value, label = "Details" }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(false);
  const empty = value === null || value === undefined || (typeof value === "object" && Object.keys(value as object).length === 0);
  if (empty) return <span className="text-xs text-subtle">—</span>;
  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={cn(
          "group inline-flex max-w-full items-center gap-1 rounded-md py-0.5 text-left text-xs font-medium text-muted transition-colors hover:text-fg",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
        )}
      >
        <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-subtle transition-transform duration-200 group-hover:text-muted", open && "rotate-90")} aria-hidden />
        <span className="min-w-0 truncate">{label}</span>
      </button>
      {open ? (
        <pre className="mt-2 max-h-72 max-w-xl overflow-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-3 font-mono text-[11px] leading-relaxed text-fg shadow-[inset_0_1px_2px_rgb(0_0_0/0.12)] animate-slide-down">
          {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

export function HealthPill({ ok, children }: { ok: boolean | "warn"; children: ReactNode }) {
  const tone = ok === true ? "text-success" : ok === "warn" ? "text-warning" : "text-danger";
  return (
    <span className={cn("inline-flex items-center gap-2 text-sm font-medium", tone)}>
      <span className="relative flex h-2.5 w-2.5 items-center justify-center" aria-hidden>
        <span className="absolute inset-0 rounded-full bg-current opacity-25" />
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {children}
    </span>
  );
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0s";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${Math.floor(seconds)}s`;
}
