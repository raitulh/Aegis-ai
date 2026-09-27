import type { ReactNode } from "react";

export function ContentPage({ eyebrow, title, lede, children }: { eyebrow: string; title: string; lede: string; children?: ReactNode }) {
  return (
    <article className="prose-invert">
      <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-accent-bright)]">{eyebrow}</p>
      <h1 className="mt-2 text-4xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-4 text-lg text-[var(--color-text-muted)]">{lede}</p>
      <div className="mt-8 space-y-6 text-[var(--color-text-muted)]">{children}</div>
    </article>
  );
}

export function FeatureRow({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <h3 className="text-base font-semibold text-[var(--color-text)]">{title}</h3>
      <p className="mt-1.5 text-sm">{body}</p>
    </div>
  );
}
