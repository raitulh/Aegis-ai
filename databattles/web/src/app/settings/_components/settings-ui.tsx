"use client";

import { AlertTriangle, Check, CircleAlert, Loader2, Zap } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Presentational building blocks shared by every settings page: a page heading (the layout owns the single
 * `h1`, so these are `h2`/`h3`), calm grouped sections, hairline-divided rows, a save-state indicator and a
 * visually separated danger zone. No data fetching or logic lives here.
 */

export function SettingsPageHeading({
  icon,
  title,
  description,
  actions,
}: {
  icon: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 border-b border-border pb-6 animate-rise">
      <div className="flex min-w-0 items-start gap-3.5">
        <span
          aria-hidden
          className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border-strong bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)] [&_svg]:h-[18px] [&_svg]:w-[18px]"
        >
          <span className="absolute inset-0 rounded-xl bg-[radial-gradient(circle_at_30%_20%,var(--accent-soft),transparent_70%)]" />
          <span className="relative">{icon}</span>
        </span>
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-[-0.025em] text-fg sm:text-[1.375rem]">{title}</h2>
          {description ? <div className="mt-1 max-w-xl text-sm leading-relaxed text-muted">{description}</div> : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2.5 sm:pl-[3.375rem]">{actions}</div> : null}
    </div>
  );
}

/**
 * A titled group of controls. The heading sits on the canvas; the controls live in one quiet surface.
 * `flush` removes the body padding for hairline-divided lists of `SettingsRow`s.
 */
export function SettingsSection({
  title,
  description,
  action,
  children,
  footer,
  flush = false,
  className,
  bodyClassName,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  flush?: boolean;
  className?: string;
  bodyClassName?: string;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cn("scroll-mt-28", className)}>
      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="min-w-0 max-w-2xl flex-1">
          <h3 id={id} className="text-[15px] font-semibold tracking-[-0.015em] text-fg">{title}</h3>
          {description ? <div className="mt-1 text-[13px] leading-relaxed text-muted">{description}</div> : null}
        </div>
        {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
      </div>
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        <div className={cn(flush ? "divide-y divide-border" : "space-y-5 p-5 sm:p-6", bodyClassName)}>{children}</div>
        {footer ? (
          <div className="flex flex-col gap-3 border-t border-border bg-bg-elevated/50 px-5 py-3.5 text-xs text-subtle sm:flex-row sm:items-center sm:justify-between sm:px-6">
            {footer}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** One line in a grouped list: label and helper text on the left, a control on the right. */
export function SettingsRow({
  icon,
  title,
  description,
  children,
  control,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  control?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-3.5 px-5 py-4 sm:px-6", className)}>
      {icon ? (
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-border bg-surface-2 text-muted shadow-[inset_0_1px_0_var(--hairline-highlight)] [&_svg]:h-4 [&_svg]:w-4"
        >
          {icon}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-fg">{title}</div>
        {description ? <div className="mt-0.5 text-xs leading-relaxed text-muted">{description}</div> : null}
        {children}
      </div>
      {control ? <div className="flex shrink-0 items-center gap-2">{control}</div> : null}
    </div>
  );
}

type SaveState = "idle" | "pending" | "success" | "error";

/** Live save indicator for pages that save on every change (reads the mutation status; never invents it). */
export function SaveStatus({ state, idleLabel = "Changes save instantly" }: { state: SaveState; idleLabel?: string }) {
  return (
    <span
      aria-live="polite"
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors duration-200",
        state === "success" && "border-success/30 bg-success-soft text-success",
        state === "error" && "border-danger/30 bg-danger-soft text-danger",
        state === "pending" && "border-border-strong bg-surface-2 text-fg",
        state === "idle" && "border-border bg-surface/60 text-muted",
      )}
    >
      {state === "pending" ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-accent-strong" aria-hidden />
      ) : state === "success" ? (
        <Check className="h-3.5 w-3.5 animate-pop" aria-hidden />
      ) : state === "error" ? (
        <CircleAlert className="h-3.5 w-3.5" aria-hidden />
      ) : (
        <Zap className="h-3.5 w-3.5 text-accent-strong" aria-hidden />
      )}
      {state === "pending" ? "Saving…" : state === "success" ? "Saved" : state === "error" ? "Not saved" : idleLabel}
    </span>
  );
}

/** Destructive actions, separated from everything else by a labelled danger rule and a danger-tinted surface. */
export function DangerZone({ children, description }: { children: ReactNode; description?: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="scroll-mt-28 pt-6">
      <div className="mb-3 flex items-center gap-3">
        <h3 id={id} className="inline-flex shrink-0 items-center gap-1.5 text-eyebrow text-danger">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
          Danger zone
        </h3>
        <span aria-hidden className="h-px flex-1 bg-[linear-gradient(90deg,color-mix(in_oklab,var(--danger)_45%,transparent),transparent)]" />
      </div>
      {description ? <p className="mb-3 text-[13px] text-muted">{description}</p> : null}
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-danger/35 bg-[color-mix(in_oklab,var(--danger)_4%,var(--surface))] shadow-card">
        {children}
      </div>
    </section>
  );
}

/**
 * Enlarges the tap target of a compact switch to 36px without changing the control itself: a `<label>`
 * forwards clicks on its padding to the button it contains.
 */
export function HitArea({ children, className }: { children: ReactNode; className?: string }) {
  return <label className={cn("inline-flex h-9 min-w-9 cursor-pointer items-center justify-center", className)}>{children}</label>;
}

/** Skeleton shaped like a settings page (heading, then two grouped sections). */
export function SettingsSkeleton({ sections = 2, rows = 3 }: { sections?: number; rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-10">
      <div className="flex items-start gap-3.5 border-b border-border pb-6">
        <div className="skeleton h-10 w-10 rounded-xl" />
        <div className="flex-1">
          <div className="skeleton h-5 w-40" />
          <div className="skeleton mt-2.5 h-3.5 w-2/3 max-w-sm" />
        </div>
      </div>
      {Array.from({ length: sections }).map((_, s) => (
        <div key={s}>
          <div className="skeleton h-4 w-32" />
          <div className="skeleton mt-2 h-3 w-1/2 max-w-xs" />
          <div className="mt-3 divide-y divide-border rounded-[var(--radius-lg)] border border-border bg-surface">
            {Array.from({ length: rows }).map((__, r) => (
              <div key={r} className="flex items-center gap-3 px-5 py-4" style={{ opacity: 1 - r * 0.12 }}>
                <div className="flex-1">
                  <div className="skeleton h-3.5 w-1/3" />
                  <div className="skeleton mt-2 h-3 w-2/3" />
                </div>
                <div className="skeleton h-6 w-11 rounded-full" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
