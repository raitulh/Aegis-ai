"use client";

import { cn } from "@/lib/cn";

/**
 * Success glyph for real completions (lesson complete, quiz passed, challenge verified, course finished).
 * `animate` adds a short pulse and draws the check — pass it only for a success that just happened in this
 * session, never for a state that was merely loaded. Reduced motion shows the finished check instantly.
 */
export function SuccessMark({ animate = false, size = "md", className }: { animate?: boolean; size?: "sm" | "md" | "lg"; className?: string }) {
  const dims = size === "lg" ? "h-14 w-14" : size === "sm" ? "h-8 w-8" : "h-10 w-10";
  const glyph = size === "lg" ? "h-7 w-7" : size === "sm" ? "h-4 w-4" : "h-5 w-5";
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex shrink-0 items-center justify-center rounded-full border border-[color-mix(in_oklab,var(--success)_35%,transparent)] bg-success-soft text-success",
        dims,
        animate && "motion-safe:animate-pop",
        className,
      )}
    >
      {animate ? <span className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring [animation-iteration-count:3]" /> : null}
      <svg viewBox="0 0 24 24" className={glyph} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className={animate ? "motion-safe:animate-check" : undefined} />
      </svg>
    </span>
  );
}

/** Score bar with a marker at the pass mark. Both numbers come from the graded attempt. */
export function ScoreBar({ score, threshold, passed, label }: { score: number; threshold: number; passed: boolean; label: string }) {
  const v = Math.max(0, Math.min(100, score));
  const t = Math.max(0, Math.min(100, threshold));
  return (
    <div className="mt-4 pt-6">
      <div className="relative">
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-surface-3"
          role="progressbar"
          aria-valuenow={Math.round(v)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
        >
          <div
            className={cn("h-full rounded-full transition-[width] duration-700 ease-out-expo", passed ? "bg-success" : "bg-warning")}
            style={{ width: `${v}%` }}
          />
        </div>
        <span aria-hidden className="absolute -top-1 h-4 w-px bg-fg/70" style={{ left: `${t}%` }} />
        <span
          aria-hidden
          className={cn(
            "tabular absolute -top-6 whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted",
            t > 80 ? "-translate-x-full" : t < 20 ? "" : "-translate-x-1/2",
          )}
          style={{ left: `${t}%` }}
        >
          Pass {t}%
        </span>
      </div>
    </div>
  );
}
