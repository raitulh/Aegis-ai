"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

export function AdminHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
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
    <Badge tone={USER_STATUS_TONE[status] ?? "neutral"} title={title}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

/** Collapsible JSON viewer for audit metadata and error payloads (rendered as text, never HTML). */
export function JsonDisclosure({ value, label = "Details" }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(false);
  const empty = value === null || value === undefined || (typeof value === "object" && Object.keys(value as object).length === 0);
  if (empty) return <span className="text-xs text-subtle">—</span>;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-fg"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
        {label}
      </button>
      {open ? (
        <pre className="mt-2 max-h-72 max-w-xl overflow-auto rounded-md border border-border bg-bg-elevated p-2 font-mono text-[11px] leading-relaxed text-fg">
          {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

export function HealthPill({ ok, children }: { ok: boolean | "warn"; children: ReactNode }) {
  const tone = ok === true ? "text-success" : ok === "warn" ? "text-warning" : "text-danger";
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", tone)}>
      <span className="h-2 w-2 rounded-full bg-current" aria-hidden />
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
