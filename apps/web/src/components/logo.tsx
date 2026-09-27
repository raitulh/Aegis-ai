import { cn } from "@/lib/utils";

/** Aegis wordmark: a shield formed from an assurance network node. Simple, original, no robot imagery. */
export function Logo({ className, showText = true }: { className?: string; showText?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden>
        <path d="M16 2.5 27 7v9c0 7.2-4.7 11.6-11 13.5C9.7 27.6 5 23.2 5 16V7l11-4.5Z" stroke="url(#lg)" strokeWidth="1.6" fill="rgba(76,141,255,0.08)" />
        <circle cx="16" cy="15" r="2.4" fill="#4c8dff" />
        <path d="M16 15 10 10M16 15l6-5M16 15v7.5" stroke="#4c8dff" strokeWidth="1.2" opacity="0.7" />
        <circle cx="10" cy="10" r="1.4" fill="#3ecf8e" />
        <circle cx="22" cy="10" r="1.4" fill="#6ba3ff" />
        <circle cx="16" cy="22.5" r="1.4" fill="#f5c451" />
        <defs>
          <linearGradient id="lg" x1="5" y1="2" x2="27" y2="30" gradientUnits="userSpaceOnUse">
            <stop stopColor="#6ba3ff" />
            <stop offset="1" stopColor="#3ecf8e" />
          </linearGradient>
        </defs>
      </svg>
      {showText ? <span className="text-[15px] font-semibold tracking-tight">AEGIS<span className="text-[var(--color-text-subtle)]"> AI</span></span> : null}
    </span>
  );
}
