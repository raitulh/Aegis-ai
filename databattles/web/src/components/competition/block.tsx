import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * A titled block inside the competition pages: icon chip, mono eyebrow, section title and an optional action.
 * No card chrome — the content decides whether it needs a surface.
 */
export function Block({
  id,
  eyebrow,
  title,
  description,
  icon,
  action,
  children,
  className,
  as: Tag = "section",
}: {
  id?: string;
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div";
}) {
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <Tag id={id} aria-labelledby={Tag === "section" ? headingId : undefined} className={cn("scroll-mt-20", className)}>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          {icon ? (
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)] [&_svg]:h-4 [&_svg]:w-4" aria-hidden>
              {icon}
            </span>
          ) : null}
          <div className="min-w-0">
            {eyebrow ? <p className="text-eyebrow text-subtle">{eyebrow}</p> : null}
            <h2 id={headingId} className="mt-1 flex flex-wrap items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg sm:text-xl">
              {title}
            </h2>
            {description ? <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{description}</p> : null}
          </div>
        </div>
        {action ? <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
      </div>
      {children}
    </Tag>
  );
}

/** Small uppercase label for asides and panels. */
export function PanelLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("flex items-center gap-1.5 font-mono text-[11px] uppercase leading-4 tracking-[0.14em] text-subtle [&_svg]:h-3.5 [&_svg]:w-3.5", className)}>{children}</p>;
}
