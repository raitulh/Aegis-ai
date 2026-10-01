"use client";

import { Check, Copy, UploadCloud } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";
import { useNow } from "@/lib/hooks";

export function CopyButton({ value, label = "Copy", className }: { value: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors duration-200",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
        done ? "border-success/40 bg-success-soft text-success" : "border-border text-muted hover:border-border-strong hover:bg-surface-2 hover:text-fg",
        className,
      )}
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? <Check className="h-3.5 w-3.5 animate-pop" /> : <Copy className="h-3.5 w-3.5" />}
      <span aria-live="polite">{done ? "Copied" : label}</span>
    </button>
  );
}

export function ProgressBar({ value, className, label }: { value: number; className?: string; label?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div
        className="relative h-full rounded-full bg-brand shadow-[0_0_12px_-2px_color-mix(in_oklab,var(--accent)_70%,transparent)] transition-[width] duration-700 ease-out-expo"
        style={{ width: `${v}%` }}
      />
    </div>
  );
}

export function ProgressRing({ value, size = 44, stroke = 4 }: { value: number; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value));
  const gid = `pr-${size}-${stroke}`;
  return (
    <svg width={size} height={size} role="img" aria-label={`${Math.round(v)}% complete`}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent-strong)" />
          <stop offset="1" stopColor="var(--cyan)" />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--surface-3)" strokeWidth={stroke} fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={`url(#${gid})`} strokeWidth={stroke} fill="none" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - v / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`}
        className="transition-[stroke-dashoffset] duration-700 ease-out-expo" />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="fill-[var(--fg)] text-[11px] font-semibold">{Math.round(v)}%</text>
    </svg>
  );
}

/**
 * Live countdown to a UTC deadline, announced politely to screen readers once per minute at most.
 * `variant="blocks"` renders segmented d/h/m/s tiles for hero placements.
 */
export function Countdown({ to, prefix, className, variant = "inline" }: { to: string | null | undefined; prefix?: string; className?: string; variant?: "inline" | "blocks" }) {
  const now = useNow(1000);
  if (!to) return null;
  const ms = new Date(to).getTime() - now.getTime();
  if (ms <= 0) return <span className={className}>Closed</span>;
  const d = Math.floor(ms / 86400000);
  const h = Math.floor((ms % 86400000) / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const text = d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
  if (variant === "blocks") {
    const parts: [number, string][] = d > 0 ? [[d, "days"], [h, "hrs"], [m, "min"]] : [[h, "hrs"], [m, "min"], [s, "sec"]];
    return (
      <span className={cn("inline-flex items-stretch gap-1.5", className)} aria-label={`${prefix ?? ""}${text}`} role="timer" aria-live="off">
        {parts.map(([n, unit]) => (
          <span key={unit} className="flex min-w-[3.1rem] flex-col items-center rounded-[var(--radius-md)] border border-border bg-bg-elevated/80 px-2 py-1.5 shadow-[inset_0_1px_0_var(--hairline-highlight)]">
            <span className="tabular text-lg font-semibold leading-tight tracking-[-0.02em] text-fg">{String(n).padStart(2, "0")}</span>
            <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">{unit}</span>
          </span>
        ))}
      </span>
    );
  }
  return (
    <span className={cn("tabular-nums", className)} aria-live="off">
      {prefix}
      {text}
    </span>
  );
}

/** Cover art generated from a style key — no external images needed. */
const COVERS: Record<string, string> = {
  aurora: "radial-gradient(120% 90% at 10% 10%, #8b6dff 0%, transparent 55%), radial-gradient(90% 90% at 90% 20%, #22d3ee 0%, transparent 50%), linear-gradient(135deg, #111526, #0b0e18)",
  nebula: "radial-gradient(100% 80% at 80% 0%, #f472b6 0%, transparent 50%), radial-gradient(100% 100% at 0% 100%, #6366f1 0%, transparent 55%), linear-gradient(135deg, #150f24, #0c0a16)",
  circuit: "repeating-linear-gradient(90deg, rgb(52 211 153 / 0.12) 0 1px, transparent 1px 22px), repeating-linear-gradient(0deg, rgb(52 211 153 / 0.12) 0 1px, transparent 1px 22px), linear-gradient(135deg, #07251d, #0a1512)",
  dunes: "radial-gradient(140% 70% at 50% 110%, #f59e0b 0%, transparent 55%), radial-gradient(120% 60% at 20% 0%, #fb7185 0%, transparent 50%), linear-gradient(180deg, #2a1407, #140b06)",
  mono: "linear-gradient(135deg, rgb(255 255 255 / 0.06) 25%, transparent 25%, transparent 50%, rgb(255 255 255 / 0.06) 50%, rgb(255 255 255 / 0.06) 75%, transparent 75%), linear-gradient(135deg, #1a1d24, #0f1116)",
  sunrise: "radial-gradient(90% 90% at 50% 100%, #fbbf24 0%, transparent 55%), radial-gradient(120% 100% at 50% 0%, #60a5fa 0%, transparent 60%), linear-gradient(180deg, #102037, #0b1220)",
};

/**
 * Generated cover art from the API's `cover_style` key. Layers a faint data grid, a top sheen and grain over
 * the gradient for depth; `interactive` adds a slow zoom on hover when placed inside a `group` link.
 */
export function Cover({ style = "aurora", className, children, interactive = false }: { style?: string; className?: string; children?: ReactNode; interactive?: boolean }) {
  return (
    <div className={cn("relative isolate overflow-hidden", className)}>
      <div
        aria-hidden
        className={cn("absolute inset-0 -z-10", interactive && "transition-transform duration-700 ease-out-expo group-hover:scale-[1.06]")}
        style={{ background: COVERS[style] ?? COVERS.aurora, backgroundSize: style === "mono" ? "28px 28px, cover" : undefined }}
      />
      <div
        aria-hidden
        className="absolute inset-0 -z-10 opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent_85%)]"
        style={{
          backgroundImage: "linear-gradient(rgb(255 255 255 / 0.07) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255 / 0.07) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      />
      <div aria-hidden className="absolute inset-0 -z-10 bg-[linear-gradient(180deg,rgb(255_255_255/0.08),transparent_40%,rgb(0_0_0/0.25))]" />
      {children}
    </div>
  );
}

/** Drag-and-drop / click file picker. Validation feedback before upload; the server validates again. */
export function FileDrop({
  accept,
  maxBytes,
  onFile,
  disabled,
  hint,
  progress,
}: {
  accept?: string;
  maxBytes?: number;
  onFile: (file: File) => void;
  disabled?: boolean;
  hint?: ReactNode;
  progress?: number | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);

  const pick = (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (maxBytes && file.size > maxBytes) return setError(`File is ${formatBytes(file.size)} — the limit is ${formatBytes(maxBytes)}.`);
    if (accept) {
      const exts = accept.split(",").map((a) => a.trim().toLowerCase());
      if (!exts.some((e) => file.name.toLowerCase().endsWith(e))) return setError(`Choose a ${accept} file.`);
    }
    setName(file.name);
    onFile(file);
  };

  return (
    <div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          pick(e.dataTransfer.files?.[0]);
        }}
        className={cn(
          "group/drop flex w-full flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border border-dashed px-6 py-9 text-center transition-[border-color,background-color,box-shadow] duration-200 disabled:opacity-50",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
          drag
            ? "border-accent bg-accent-soft shadow-[0_0_0_4px_color-mix(in_oklab,var(--accent)_12%,transparent)]"
            : "border-border-strong bg-bg-elevated/40 hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border-strong))] hover:bg-surface-2",
        )}
      >
        <span className={cn("flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface-2 transition-transform duration-300", drag ? "scale-110 text-accent-strong" : "text-muted group-hover/drop:-translate-y-0.5")}>
          <UploadCloud className="h-5 w-5" aria-hidden />
        </span>
        <span className="text-sm font-medium text-fg">{name ?? "Drop a file here or click to browse"}</span>
        {hint ? <span className="text-xs text-subtle">{hint}</span> : null}
      </button>
      <input ref={inputRef} type="file" accept={accept} className="sr-only" tabIndex={-1} onChange={(e) => pick(e.target.files?.[0] ?? undefined)} />
      {progress !== null && progress !== undefined ? <ProgressBar className="mt-3" value={progress * 100} label="Upload progress" /> : null}
      {error ? <p role="alert" className="mt-2 text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}

export function KeyValue({ items }: { items: { label: ReactNode; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-4 text-sm sm:grid-cols-2">
      {items.map((it, i) => (
        <div key={i} className="flex min-w-0 flex-col border-l border-border pl-3">
          <dt className="text-eyebrow text-subtle">{it.label}</dt>
          <dd className="mt-1 min-w-0 break-words text-fg">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
