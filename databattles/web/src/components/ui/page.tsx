import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Container({ className, children, size = "xl" }: { className?: string; children: ReactNode; size?: "md" | "lg" | "xl" | "full" }) {
  const w = { md: "max-w-3xl", lg: "max-w-5xl", xl: "max-w-7xl", full: "max-w-none" }[size];
  return <div className={cn("mx-auto w-full px-4 sm:px-6 lg:px-8", w, className)}>{children}</div>;
}

export function PageHeader({ title, description, actions, eyebrow, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode; className?: string }) {
  return (
    <header className={cn("flex flex-col gap-4 py-8 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        {eyebrow ? <div className="mb-2 text-xs font-medium uppercase tracking-wider text-accent-strong">{eyebrow}</div> : null}
        <h1 className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-sm text-muted sm:text-base">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function Section({ title, description, action, children, className }: { title?: ReactNode; description?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("py-6", className)}>
      {title ? (
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-fg">{title}</h2>
            {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
          </div>
          {action}
        </div>
      ) : null}
      {children}
    </section>
  );
}
