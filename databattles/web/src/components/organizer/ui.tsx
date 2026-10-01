"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Presentational building blocks for the organizer console. They hold no data or behaviour — every value
 * shown in them is supplied by the page from data it already fetches.
 */

/* ------------------------------------------------------------------ panels & section headers */

/**
 * Calm bordered surface for a group of controls. The title is an `h3` because the competition layout owns
 * the `h1` and each manage page's `ManageHeading` is the `h2`.
 */
export function Panel({
  title,
  description,
  icon,
  action,
  children,
  className,
  bodyClassName,
  flush = false,
  tone = "default",
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Body without padding (for hairline lists that run edge to edge). */
  flush?: boolean;
  tone?: "default" | "warning" | "danger" | "accent";
  id?: string;
}) {
  const ring = {
    default: "border-border",
    warning: "border-warning/40",
    danger: "border-danger/40",
    accent: "border-[color-mix(in_oklab,var(--accent)_40%,var(--border))]",
  }[tone];
  return (
    <section id={id} className={cn("@container relative scroll-mt-28 overflow-hidden rounded-[var(--radius-lg)] border bg-surface surface-sheen shadow-card", ring, className)}>
      {title ? (
        <div className="flex flex-col gap-3 border-b border-border px-4 py-3.5 @md:flex-row @md:items-start @md:justify-between @md:px-5">
          <div className="flex min-w-0 items-start gap-3">
            {icon ? (
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>
                {icon}
              </span>
            ) : null}
            <div className="min-w-0">
              <h3 className="text-sm font-semibold tracking-[-0.01em] text-fg">{title}</h3>
              {description ? <div className="mt-0.5 text-xs leading-relaxed text-muted">{description}</div> : null}
            </div>
          </div>
          {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
        </div>
      ) : null}
      {children !== undefined && children !== null ? <div className={cn(flush ? "" : "px-4 py-4 @md:px-5 @md:py-5", bodyClassName)}>{children}</div> : null}
    </section>
  );
}

/** Heading for a block that is not wrapped in a panel (dense tables, feeds). */
export function SectionHeader({
  title,
  description,
  action,
  eyebrow,
  count,
  className,
  id,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  eyebrow?: ReactNode;
  count?: number;
  className?: string;
  id?: string;
}) {
  return (
    <div className={cn("mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="mb-1 text-eyebrow text-subtle">{eyebrow}</p> : null}
        <h3 id={id} className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-fg">
          {title}
          {count !== undefined ? <span className="tabular rounded-full bg-surface-3 px-1.5 py-px text-[11px] font-medium text-muted">{count}</span> : null}
        </h3>
        {description ? <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-muted">{description}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ metric tiles */

/** Gap-px tile grid (the landing metrics pattern). Children are `Tile`s. */
export function TileGrid({ children, className, cols = 4 }: { children: ReactNode; className?: string; cols?: 2 | 3 | 4 | 5 }) {
  const grid = {
    2: "grid-cols-2",
    3: "grid-cols-2 sm:grid-cols-3",
    4: "grid-cols-2 lg:grid-cols-4",
    // Five narrow tiles: reserve two label lines so values line up when a label wraps.
    5: "grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 [&_dt]:min-h-[2.75em]",
  }[cols];
  return (
    // The gradient hairline is a pseudo-element so the <dl> only contains dt/dd groups.
    <dl
      className={cn(
        "relative grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card",
        "before:pointer-events-none before:absolute before:inset-x-6 before:top-0 before:z-10 before:h-px before:bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] before:opacity-50",
        grid,
        className,
      )}
    >
      {children}
    </dl>
  );
}

const TILE_ACCENT = {
  accent: "text-accent-strong",
  cyan: "text-cyan",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  muted: "text-subtle",
} as const;

/** One metric. `value` is always real data supplied by the caller (or "—"). */
export function Tile({
  label,
  value,
  hint,
  icon,
  accent = "accent",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  accent?: keyof typeof TILE_ACCENT;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col bg-surface px-4 py-4 sm:px-5", className)}>
      <dt className="flex min-w-0 items-start gap-1.5 text-eyebrow leading-snug text-subtle">
        {icon ? <span className={cn("mt-px shrink-0 [&_svg]:h-3.5 [&_svg]:w-3.5", TILE_ACCENT[accent])} aria-hidden>{icon}</span> : null}
        <span className="min-w-0">{label}</span>
      </dt>
      <dd className="tabular mt-2.5 min-w-0 text-[1.6rem] font-semibold leading-none tracking-[-0.03em] text-fg">{value}</dd>
      {hint ? <dd className="mt-2 min-w-0 text-xs text-subtle">{hint}</dd> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ toolbars, lists, empties */

/** Glass filter / summary bar that sits above a table. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "mb-3 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-[var(--glass)] px-3 py-2.5 backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Hairline-divided list on a single surface (replaces stacks of bordered cards). */
export function ListSurface({ children, className, as = "ul" }: { children: ReactNode; className?: string; as?: "ul" | "ol" }) {
  const Tag = as;
  return <Tag className={cn("divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card", className)}>{children}</Tag>;
}

/** Compact empty placeholder for inside panels and charts (the full `EmptyState` is for page-level emptiness). */
export function InlineEmpty({ icon, title, description, action, className }: { icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1 rounded-[var(--radius-md)] border border-dashed border-border-strong bg-bg-elevated/40 px-4 py-8 text-center", className)}>
      {icon ? (
        <span className="mb-2 flex h-9 w-9 items-center justify-center rounded-full border border-border bg-surface-2 text-subtle [&_svg]:h-4 [&_svg]:w-4" aria-hidden>
          {icon}
        </span>
      ) : null}
      <p className="text-sm font-medium text-fg">{title}</p>
      {description ? <p className="max-w-sm text-xs leading-relaxed text-muted">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/** Label/value facts in a hairline grid. Values wrap instead of truncating. */
export function FactGrid({ items, className, cols = 2 }: { items: { label: ReactNode; value: ReactNode; icon?: ReactNode }[]; className?: string; cols?: 2 | 3 | 4 }) {
  const grid = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[cols];
  return (
    <dl className={cn("grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border", grid, className)}>
      {items.map((it, i) => (
        <div key={i} className={cn("min-w-0 bg-bg-elevated px-3.5 py-3", cols === 2 && items.length % 2 === 1 && i === items.length - 1 && "sm:col-span-2")}>
          <dt className="flex items-center gap-1.5 text-eyebrow text-subtle [&_svg]:h-3.5 [&_svg]:w-3.5">
            {it.icon}
            {it.label}
          </dt>
          <dd className="mt-1.5 min-w-0 break-words text-sm text-fg">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Small mono label used for the eyebrow of an inline sub-block (e.g. a form inside a panel). */
export function SubHeading({ children, className }: { children: ReactNode; className?: string }) {
  // Joined without tailwind-merge: it would treat `text-eyebrow` and `text-subtle` as conflicting colours.
  return <p className={["text-eyebrow text-subtle", className].filter(Boolean).join(" ")}>{children}</p>;
}

/* ------------------------------------------------------------------ competition window */

/**
 * Where "now" sits in the competition window — derived only from the real start/end (and optional
 * registration close) instants. Renders nothing meaningful when dates are missing.
 */
export function WindowTrack({ startsAt, endsAt, registrationClosesAt, now, formatLabel }: {
  startsAt: string | null | undefined;
  endsAt: string | null | undefined;
  registrationClosesAt?: string | null;
  now: Date;
  formatLabel: (iso: string) => string;
}) {
  const start = startsAt ? new Date(startsAt).getTime() : null;
  const end = endsAt ? new Date(endsAt).getTime() : null;
  if (!start || !end || end <= start) {
    return (
      <div className="rounded-[var(--radius-md)] border border-dashed border-border-strong bg-bg-elevated/40 px-4 py-3 text-xs text-muted">
        Set a start and end time to see the competition window.
      </div>
    );
  }
  const t = now.getTime();
  const pct = Math.max(0, Math.min(100, ((t - start) / (end - start)) * 100));
  const reg = registrationClosesAt ? new Date(registrationClosesAt).getTime() : null;
  const regPct = reg && reg > start && reg < end ? ((reg - start) / (end - start)) * 100 : null;
  const phase = t < start ? "before" : t >= end ? "after" : "during";
  const totalDays = Math.max(1, Math.round((end - start) / 86_400_000));
  const day = Math.min(totalDays, Math.max(1, Math.ceil((t - start) / 86_400_000)));
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-fg">
          {phase === "before" ? "Not started yet" : phase === "after" ? "Window closed" : <>Day <span className="tabular">{day}</span> of <span className="tabular">{totalDays}</span></>}
        </p>
        <p className="tabular font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">{Math.round(pct)}% elapsed</p>
      </div>
      <div
        className="relative h-2 rounded-full bg-surface-3"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label={`${Math.round(pct)}% of the competition window has elapsed`}
      >
        <div className="absolute inset-y-0 left-0 rounded-full bg-brand shadow-[0_0_14px_-2px_color-mix(in_oklab,var(--accent)_70%,transparent)] transition-[width] duration-700 ease-out-expo" style={{ width: `${pct}%` }} />
        {regPct !== null ? (
          <span className="absolute top-1/2 h-3.5 w-px -translate-y-1/2 bg-warning" style={{ left: `${regPct}%` }} aria-hidden title="Registration closes" />
        ) : null}
        {phase === "during" ? (
          <span className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[var(--bg)] bg-fg shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_35%,transparent)]" style={{ left: `${pct}%` }} aria-hidden />
        ) : null}
      </div>
      <div className="mt-2.5 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-subtle">
        <span><span className="font-mono text-[10px] uppercase tracking-[0.12em]">Start</span> <span className="tabular text-muted">{formatLabel(startsAt as string)}</span></span>
        {regPct !== null && registrationClosesAt ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-px bg-warning" aria-hidden />
            <span className="font-mono text-[10px] uppercase tracking-[0.12em]">Registration closes</span> <span className="tabular text-muted">{formatLabel(registrationClosesAt)}</span>
          </span>
        ) : null}
        <span><span className="font-mono text-[10px] uppercase tracking-[0.12em]">End</span> <span className="tabular text-muted">{formatLabel(endsAt as string)}</span></span>
      </div>
    </div>
  );
}
