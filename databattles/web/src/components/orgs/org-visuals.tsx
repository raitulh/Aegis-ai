"use client";

import { Building2, Check, GraduationCap, Handshake, Sprout, Users, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { orgAccent } from "./org-ui";

/*
 * Presentational building blocks shared by the organization routes (public pages, join flows, admin
 * console, sponsor dashboard). Nothing here fetches data or holds business logic.
 */

const TYPE_ICONS: Record<string, LucideIcon> = {
  university: GraduationCap,
  club: Users,
  community: Sprout,
  sponsor: Handshake,
  company: Building2,
};

export function OrgTypeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = TYPE_ICONS[type] ?? Building2;
  return <Icon className={className} aria-hidden />;
}

/** Soft radial glow in the organization's own accent colour (decorative; sits behind content). */
export function AccentGlow({ color, className, strength = 20 }: { color?: string | null; className?: string; strength?: number }) {
  const c = orgAccent(color);
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none absolute -z-10", className)}
      style={{ background: `radial-gradient(closest-side, color-mix(in oklab, ${c} ${strength}%, transparent), transparent)` }}
    />
  );
}

/** 1px top edge that fades in and out of the organization's accent colour. */
export function AccentEdge({ color, className }: { color?: string | null; className?: string }) {
  const c = orgAccent(color);
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none absolute inset-x-0 top-0 h-px", className)}
      style={{ background: `linear-gradient(90deg, transparent, ${c} 25%, color-mix(in oklab, ${c} 55%, var(--cyan)) 70%, transparent)` }}
    />
  );
}

/**
 * Restrained success moment: a check that draws itself inside a single pulse ring. Use only on real success.
 * Pass `animated={false}` for an existing success state (e.g. already verified) that isn't a new moment.
 */
export function SuccessMark({ className, animated = true }: { className?: string; animated?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_30%,transparent)]",
        className,
      )}
    >
      {animated ? <span className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring" /> : null}
      <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray={animated ? "24" : undefined} className={animated ? "motion-safe:animate-check" : undefined} />
      </svg>
    </span>
  );
}

/** Status glyph for non-success outcomes (pending, failed, waiting) in the same orbit shape as `SuccessMark`. */
export function StateMark({ icon, tone = "neutral", className }: { icon: ReactNode; tone?: "neutral" | "warning" | "danger" | "info"; className?: string }) {
  const tones = {
    neutral: "bg-surface-2 text-accent-strong ring-border",
    warning: "bg-warning-soft text-warning ring-[color-mix(in_oklab,var(--warning)_30%,transparent)]",
    danger: "bg-danger-soft text-danger ring-[color-mix(in_oklab,var(--danger)_30%,transparent)]",
    info: "bg-info-soft text-info ring-[color-mix(in_oklab,var(--info)_30%,transparent)]",
  }[tone];
  return (
    <span aria-hidden className={cn("relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full ring-1 ring-inset [&_svg]:h-6 [&_svg]:w-6", tones, className)}>
      <span className="absolute -inset-1.5 rounded-full border border-dashed border-border-strong motion-safe:animate-spin-slow" />
      {icon}
    </span>
  );
}

export type StepState = "done" | "current" | "upcoming";

/**
 * Horizontal progress through a short flow (join, accept invite, confirm email). The state of each step is
 * derived by the caller from real data; this only renders it. Text labels carry the meaning, not colour.
 */
export function Stepper({ steps, label, className }: { steps: { label: ReactNode; hint?: ReactNode; state: StepState }[]; label: string; className?: string }) {
  const cols = steps.length === 2 ? "sm:grid-cols-2" : steps.length === 4 ? "sm:grid-cols-4" : "sm:grid-cols-3";
  return (
    <ol aria-label={label} className={cn("grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border", cols, className)}>
      {steps.map((s, i) => {
        const done = s.state === "done";
        const current = s.state === "current";
        return (
          <li
            key={i}
            aria-current={current ? "step" : undefined}
            className={cn("relative flex min-w-0 items-start gap-3 px-4 py-3.5", current ? "bg-surface-2" : "bg-surface")}
          >
            {current ? <span aria-hidden className="absolute inset-x-0 top-0 h-0.5 bg-brand" /> : null}
            <span
              aria-hidden
              className={cn(
                "tabular mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-semibold",
                done
                  ? "bg-success-soft text-success ring-1 ring-inset ring-[color-mix(in_oklab,var(--success)_30%,transparent)]"
                  : current
                    ? "bg-accent-fill text-accent-fg shadow-[0_0_0_4px_color-mix(in_oklab,var(--accent)_18%,transparent)]"
                    : "bg-surface-3 text-subtle",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <div className="min-w-0">
              <p className={cn("text-sm font-medium leading-snug", s.state === "upcoming" ? "text-muted" : "text-fg")}>
                {s.label}
                <span className="sr-only">{done ? " (done)" : current ? " (current step)" : " (not started)"}</span>
              </p>
              {s.hint ? <p className="mt-0.5 text-xs leading-relaxed text-subtle">{s.hint}</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Hairline fact grid (MetaItem children). Borders are drawn per cell, so a partially filled last row
 * never shows an empty filled tile.
 */
export function FactGrid({ children, className, cols = "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6" }: { children: ReactNode; className?: string; cols?: string }) {
  return (
    <div className={cn("overflow-hidden", className)}>
      <dl className={cn("-ml-px -mt-px grid", cols, "[&>*]:border-l [&>*]:border-t [&>*]:border-border [&>*]:px-5 [&>*]:py-4")}>{children}</dl>
    </div>
  );
}

/** Dashboard metric tile for `MetricGrid` — always a real value supplied by the caller. */
export function Metric({ label, value, hint, icon }: { label: ReactNode; value: ReactNode; hint?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="group relative min-w-0 transition-colors duration-300 hover:bg-surface-2/50">
      <dt className="flex min-h-8 items-start gap-2 text-eyebrow text-subtle [&_svg]:mt-px [&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:shrink-0 [&_svg]:text-accent-strong">
        {icon}
        <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
      </dt>
      <dd className="tabular mt-3 text-[1.75rem] font-semibold leading-none tracking-[-0.035em] text-fg">{value}</dd>
      {hint ? <dd className="mt-2 text-xs leading-relaxed text-subtle">{hint}</dd> : null}
    </div>
  );
}

/** Panel of metric tiles separated by hairlines, with a brand edge on top. */
export function MetricGrid({ children, className, cols = "grid-cols-2 lg:grid-cols-3" }: { children: ReactNode; className?: string; cols?: string }) {
  return (
    <div className={cn("relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card", className)}>
      <div aria-hidden className="pointer-events-none absolute inset-x-6 top-0 z-10 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
      <FactGrid cols={cols} className="[&_dl>*]:py-5">
        {children}
      </FactGrid>
    </div>
  );
}

/** Title block for admin console pages — the page's single h1 (the console layout has none). */
export function AdminPageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 animate-rise sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? <p className="text-eyebrow text-accent-strong">{eyebrow}</p> : null}
        <h1 className="mt-1.5 text-[1.5rem] font-semibold leading-tight tracking-[-0.025em] text-fg sm:text-[1.75rem]">{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">{description}</p> : null}
        {meta ? <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-subtle">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

/** Section heading used inside pages (h2 + optional eyebrow, description and action). */
export function SectionTitle({
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
    <div className={cn("mb-4 flex items-end justify-between gap-4", className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="mb-1 text-eyebrow text-subtle">{eyebrow}</p> : null}
        <h2 id={id} className="text-lg font-semibold tracking-[-0.02em] text-fg">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-relaxed text-muted">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** Institutional email domains as mono chips. */
export function DomainChips({ domains, className }: { domains: string[]; className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)}>
      {domains.map((d) => (
        <li key={d} className="inline-flex h-7 max-w-full items-center truncate rounded-md border border-border bg-bg-elevated px-2 font-mono text-xs text-muted">
          @{d}
        </li>
      ))}
    </ul>
  );
}

/**
 * Count of a capped API list (the org detail endpoint returns at most 12 of each). At the cap the real total is
 * unknown, so the value is labelled as the number shown rather than implying there are more.
 */
export function ListCount({ n, cap = 12 }: { n: number; cap?: number }) {
  return (
    <>
      <span className="tabular">{n}</span>
      {n >= cap ? <span className="ml-1 text-xs font-normal text-subtle">shown</span> : null}
    </>
  );
}
