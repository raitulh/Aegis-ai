import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Container({ className, children, size = "xl" }: { className?: string; children: ReactNode; size?: "md" | "lg" | "xl" | "full" }) {
  const w = { md: "max-w-3xl", lg: "max-w-5xl", xl: "max-w-7xl", full: "max-w-none" }[size];
  return <div className={cn("mx-auto w-full px-4 sm:px-6 lg:px-8", w, className)}>{children}</div>;
}

/**
 * Page title block. The single `h1` of a page lives here. `icon` adds a glyph chip beside the eyebrow and
 * `meta` holds compact facts under the description (counts, dates). A soft glow anchors the title.
 */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
  className,
  icon,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  className?: string;
  icon?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className={cn("relative isolate flex flex-col gap-5 pb-8 pt-10 sm:flex-row sm:items-end sm:justify-between sm:pt-12", className)}>
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-10 -z-10 h-56 w-[28rem] max-w-full"
        style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
      />
      <div className="min-w-0 animate-rise">
        {eyebrow || icon ? (
          <div className="mb-3 flex items-center gap-2">
            {icon ? (
              <span className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>
                {icon}
              </span>
            ) : null}
            {eyebrow ? <div className="text-eyebrow text-accent-strong">{eyebrow}</div> : null}
          </div>
        ) : null}
        <h1 className="text-title text-fg">{title}</h1>
        {description ? <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted">{description}</p> : null}
        {meta ? <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-subtle">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2 animate-rise [animation-delay:60ms]">{actions}</div> : null}
    </header>
  );
}

export function Section({
  title,
  description,
  action,
  children,
  className,
  eyebrow,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  eyebrow?: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={cn("py-7", className)}>
      {title ? (
        <div className="mb-5 flex items-end justify-between gap-4">
          <div className="min-w-0">
            {eyebrow ? <div className="mb-1.5 text-eyebrow text-subtle">{eyebrow}</div> : null}
            <h2 className="text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">{title}</h2>
            {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
