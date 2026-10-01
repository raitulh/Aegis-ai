"use client";
import { Check, ChevronLeft, ChevronRight, Copy, FlaskConical, Lock } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { cn, titleCase } from "@/lib/utils";

export function KeyValue({ items, className }: { items: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("divide-y divide-[var(--color-border)]/70 text-sm", className)}>
      {items.map(([k, v]) => (
        <div key={k} className="flex items-start justify-between gap-4 py-2">
          <dt className="shrink-0 text-[var(--color-text-muted)]">{k}</dt>
          <dd className="min-w-0 text-right text-[var(--color-text)]">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : label}
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)] focus-ring"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-[var(--color-success)]" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

export function CodeBlock({ code, language, className }: { code: string; language?: string; className?: string }) {
  return (
    <div className={cn("relative overflow-hidden rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)]", className)}>
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-subtle)]">{language ?? "code"}</span>
        <CopyButton value={code} label="Copy code" />
      </div>
      <pre className="max-h-[480px] overflow-auto p-3 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function Hash({ value, length = 12 }: { value?: string | null; length?: number }) {
  if (!value) return <span className="text-[var(--color-text-subtle)]">—</span>;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs" title={value}>
      {value.slice(0, length)}…
      <CopyButton value={value} label="Copy hash" />
    </span>
  );
}

export function Pagination({ page, totalPages, onPage }: { page: number; totalPages: number; onPage: (p: number) => void }) {
  if (totalPages <= 1) return null;
  return (
    <nav aria-label="Pagination" className="flex items-center justify-end gap-2 pt-3 text-xs text-[var(--color-text-muted)]">
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 hover:bg-[var(--color-surface-2)] disabled:opacity-40 focus-ring">
        <ChevronLeft className="h-3.5 w-3.5" /> Previous
      </button>
      <span aria-current="page">
        Page {page} of {totalPages}
      </span>
      <button type="button" disabled={page >= totalPages} onClick={() => onPage(page + 1)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 hover:bg-[var(--color-surface-2)] disabled:opacity-40 focus-ring">
        Next <ChevronRight className="h-3.5 w-3.5" />
      </button>
    </nav>
  );
}

/** Explains a plan limit with the actual numbers instead of a bare "upgrade" prompt. */
export function PlanLimitNotice({ error }: { error: unknown }) {
  if (!(error instanceof ApiError) || !error.isPlanLimit) return null;
  const d = (error.details ?? {}) as { metric?: string; used?: number; limit?: number; plan?: string; next_plan?: string; available_on?: string; roadmap?: boolean };
  return (
    <div role="status" className="flex gap-3 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-medium)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-medium)_7%,transparent)] p-3 text-sm">
      <Lock className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-medium)]" aria-hidden />
      <div className="space-y-1">
        <p className="font-medium">{error.message}</p>
        {d.metric && d.limit !== undefined ? (
          <p className="text-[var(--color-text-muted)]">
            {titleCase(d.metric)}: {d.used?.toLocaleString()} of {d.limit.toLocaleString()} used on the {titleCase(d.plan)} plan.
          </p>
        ) : null}
        {d.roadmap ? <p className="text-[var(--color-text-muted)]">This capability is on the roadmap.</p> : null}
        {(d.next_plan || d.available_on) && !d.roadmap ? (
          <Link href="/dashboard/billing" className="text-[var(--color-accent-bright)] hover:underline">
            See usage and plan options ({titleCase(d.next_plan || d.available_on)} includes more capacity)
          </Link>
        ) : null}
      </div>
    </div>
  );
}

export function DemoBanner({ sandbox, expiresAt }: { sandbox: boolean; expiresAt?: string | null }) {
  return (
    <div role="note" className="flex items-center gap-2 border-b border-[color-mix(in_srgb,var(--color-medium)_30%,transparent)] bg-[color-mix(in_srgb,var(--color-medium)_8%,transparent)] px-4 py-1.5 text-xs text-[var(--color-medium)] sm:px-6">
      <FlaskConical className="h-3.5 w-3.5" aria-hidden />
      <span className="font-semibold tracking-wide">DEMO</span>
      <span className="text-[var(--color-text-muted)]">
        Simulated systems and data{sandbox && expiresAt ? ` · sandbox expires ${new Date(expiresAt).toLocaleString()}` : ""}. Nothing here describes a real organization.
      </span>
    </div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-sm font-semibold tracking-tight">{children}</h2>
      {action}
    </div>
  );
}
