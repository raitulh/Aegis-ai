import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function Metric({ label, value, icon: Icon, hint, tone }: { label: string; value: string | number; icon?: LucideIcon; hint?: string; tone?: string }) {
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--color-text-muted)]">{label}</span>
        {Icon ? <Icon className="h-4 w-4 text-[var(--color-text-subtle)]" /> : null}
      </div>
      <p className="mt-2 font-mono text-2xl font-semibold" style={{ color: tone }}>
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-[var(--color-text-subtle)]">{hint}</p> : null}
    </div>
  );
}

export function ScoreDelta({ current, previous }: { current: number; previous?: number }) {
  if (previous === undefined || previous === 0) return null;
  const delta = current - previous;
  const positive = delta >= 0;
  return (
    <span className={cn("text-xs font-medium", positive ? "text-[var(--color-success)]" : "text-[var(--color-high)]")}>
      {positive ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}
