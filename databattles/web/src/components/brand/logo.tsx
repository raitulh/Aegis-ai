import { useId } from "react";

import { cn } from "@/lib/cn";

/**
 * DataBattles mark: a data core with an orbiting node — the platform loop (learn → build → compete →
 * verify) drawn as a single orbit. The front arc passes over the core for depth.
 */
export function LogoMark({ size = 28, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden className={cn("shrink-0", className)}>
      <defs>
        <linearGradient id={`${id}-e`} x1="4" y1="6" x2="28" y2="26" gradientUnits="userSpaceOnUse">
          <stop stopColor="#b9abff" />
          <stop offset="0.55" stopColor="#6d93ff" />
          <stop offset="1" stopColor="#3ddcf2" />
        </linearGradient>
        <radialGradient id={`${id}-c`} cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(14.6 14.4) rotate(50) scale(6.5)">
          <stop stopColor="#ffffff" />
          <stop offset="0.35" stopColor="#b9abff" />
          <stop offset="1" stopColor="#6a4df5" />
        </radialGradient>
        <linearGradient id={`${id}-t`} x1="16" y1="1" x2="16" y2="31" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1a1f2e" />
          <stop offset="1" stopColor="#0a0c13" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill={`url(#${id}-t)`} />
      <rect x="1.5" y="1.5" width="29" height="29" rx="8.5" stroke="rgb(169 152 255 / 0.28)" />
      <ellipse cx="16" cy="16" rx="11.5" ry="5" transform="rotate(-28 16 16)" stroke={`url(#${id}-e)`} strokeOpacity="0.55" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="4.4" fill={`url(#${id}-c)`} />
      <path d="M26.15 10.61 A11.5 5 -28 0 1 5.85 21.39" stroke={`url(#${id}-e)`} strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="24.73" cy="9.42" r="2.1" fill="#3ddcf2" />
      <circle cx="24.73" cy="9.42" r="3.4" fill="#3ddcf2" fillOpacity="0.18" />
    </svg>
  );
}

export function Logo({ className, showWordmark = true }: { className?: string; showWordmark?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      {showWordmark ? (
        <span className="text-[15px] font-semibold tracking-[-0.02em] text-fg">
          Data<span className="text-muted">Battles</span>
        </span>
      ) : null}
    </span>
  );
}
