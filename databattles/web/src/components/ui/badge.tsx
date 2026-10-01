import type { ReactNode } from "react";
import { BadgeCheck, FlaskConical } from "lucide-react";

import { cn } from "@/lib/cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info" | "outline";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-3/80 text-muted ring-border",
  accent: "bg-accent-soft text-accent-strong ring-[color-mix(in_oklab,var(--accent)_28%,transparent)]",
  success: "bg-success-soft text-success ring-[color-mix(in_oklab,var(--success)_26%,transparent)]",
  warning: "bg-warning-soft text-warning ring-[color-mix(in_oklab,var(--warning)_26%,transparent)]",
  danger: "bg-danger-soft text-danger ring-[color-mix(in_oklab,var(--danger)_26%,transparent)]",
  info: "bg-info-soft text-info ring-[color-mix(in_oklab,var(--info)_26%,transparent)]",
  outline: "bg-transparent text-muted ring-border-strong",
};

export function Badge({ tone = "neutral", children, className, icon, title }: { tone?: Tone; children: ReactNode; className?: string; icon?: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-full px-2 text-[11.5px] font-medium leading-none ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** Statuses that represent work in motion get a softly pulsing dot (colour is never the only signal — the label says it). */
const LIVE = new Set(["active", "validating", "scoring", "queued"]);

const STATUS: Record<string, { tone: Tone; label: string }> = {
  draft: { tone: "neutral", label: "Draft" },
  upcoming: { tone: "info", label: "Upcoming" },
  active: { tone: "success", label: "Active" },
  ended: { tone: "warning", label: "Ended · results pending" },
  completed: { tone: "accent", label: "Completed" },
  archived: { tone: "neutral", label: "Archived" },
  queued: { tone: "neutral", label: "Queued" },
  validating: { tone: "info", label: "Validating" },
  scoring: { tone: "info", label: "Scoring" },
  scored: { tone: "success", label: "Scored" },
  failed: { tone: "danger", label: "Failed" },
  rejected: { tone: "danger", label: "Rejected" },
  canceled: { tone: "neutral", label: "Canceled" },
  pending: { tone: "warning", label: "Pending" },
  verified: { tone: "success", label: "Verified" },
  unverified: { tone: "neutral", label: "Unverified" },
  published: { tone: "success", label: "Published" },
  open: { tone: "warning", label: "Open" },
  valid: { tone: "success", label: "Valid" },
  revoked: { tone: "danger", label: "Revoked" },
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: status };
  return (
    <Badge tone={s.tone} className={className}>
      <span className="relative flex h-1.5 w-1.5" aria-hidden>
        {LIVE.has(status) ? <span className="absolute inset-0 rounded-full bg-current opacity-60 motion-safe:animate-[ping_2.2s_cubic-bezier(0,0,0.2,1)_infinite]" /> : null}
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {s.label}
    </Badge>
  );
}

/** Marks seeded, synthetic demo content so it is never mistaken for live data. */
export function DemoBadge({ className }: { className?: string }) {
  return (
    <Badge tone="warning" className={className} icon={<FlaskConical className="h-3 w-3" aria-hidden />} title="Synthetic demo data created by the seed script">
      Demo data
    </Badge>
  );
}

export function VerifiedBadge({ label = "Verified", title }: { label?: string; title?: string }) {
  return (
    <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" aria-hidden />} title={title}>
      {label}
    </Badge>
  );
}

export function SelfDeclaredBadge() {
  return (
    <Badge tone="outline" title="Provided by the user and not verified by the platform">
      Self-declared
    </Badge>
  );
}
