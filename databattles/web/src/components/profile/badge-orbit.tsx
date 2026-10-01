"use client";

import { useId } from "react";

import { cn } from "@/lib/cn";
import { BadgeIcon } from "./badge-icon";

/**
 * Achievement constellation: the badges a person actually holds, placed on an orbit around their real total.
 * Purely decorative (`aria-hidden`) — always render an accessible list of the same badges next to it.
 */
export function BadgeOrbit({
  badges,
  total,
  label = "badges",
  className,
}: {
  badges: { id: string; icon: string; color?: string | null }[];
  total: number;
  label?: string;
  className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const shown = badges.slice(0, 8);
  const n = shown.length;
  const pos = (i: number) => {
    const a = (i / Math.max(n, 1)) * Math.PI * 2 - Math.PI / 2;
    return { x: 50 + Math.cos(a) * 40, y: 50 + Math.sin(a) * 40 };
  };
  return (
    <div className={cn("relative aspect-square w-full max-w-[248px]", className)} aria-hidden>
      <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" fill="none">
        <defs>
          <radialGradient id={`${id}-core`} cx="50%" cy="50%" r="50%">
            <stop offset="0" stopColor="var(--accent)" stopOpacity="0.28" />
            <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={`${id}-line`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--accent-strong)" />
            <stop offset="1" stopColor="var(--cyan)" />
          </linearGradient>
        </defs>
        <circle cx="50" cy="50" r="30" fill={`url(#${id}-core)`} />
        <circle cx="50" cy="50" r="40" stroke="var(--border-strong)" strokeWidth="0.4" />
        <circle cx="50" cy="50" r="21" stroke="var(--border)" strokeWidth="0.35" />
        {shown.map((b, i) => {
          const p = pos(i);
          return <line key={b.id} x1="50" y1="50" x2={p.x} y2={p.y} stroke={`url(#${id}-line)`} strokeOpacity="0.4" strokeWidth="0.35" strokeDasharray="0.8 1.4" />;
        })}
      </svg>
      <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full motion-safe:animate-spin-slow" fill="none">
        <circle cx="50" cy="50" r="47" stroke="var(--border)" strokeWidth="0.3" strokeDasharray="0.6 1.8" />
        <circle cx="97" cy="50" r="0.9" fill="var(--cyan)" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tabular text-[1.75rem] font-semibold leading-none tracking-[-0.035em] text-fg">{total}</span>
        <span className="mt-1 text-eyebrow text-subtle">{label}</span>
      </div>
      {shown.map((b, i) => {
        const p = pos(i);
        return (
          <span
            key={b.id}
            className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-surface shadow-card animate-pop"
            style={{ left: `${p.x}%`, top: `${p.y}%`, animationDelay: `${120 + i * 60}ms` }}
          >
            <BadgeIcon icon={b.icon} color={b.color} size={34} />
          </span>
        );
      })}
    </div>
  );
}
