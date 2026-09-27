import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, description, breadcrumbs, actions }: { title: string; description?: string; breadcrumbs?: { label: string; href?: string }[]; actions?: ReactNode }) {
  return (
    <div className="mb-6">
      {breadcrumbs ? (
        <nav className="mb-2 flex items-center gap-1 text-xs text-[var(--color-text-subtle)]">
          {breadcrumbs.map((b, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 ? <ChevronRight className="h-3 w-3" /> : null}
              {b.href ? (
                <Link href={b.href} className="hover:text-[var(--color-text-muted)]">
                  {b.label}
                </Link>
              ) : (
                <span className="text-[var(--color-text-muted)]">{b.label}</span>
              )}
            </span>
          ))}
        </nav>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
          {description ? <p className="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
