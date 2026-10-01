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
      className={cn("inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted hover:text-fg", className)}
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? "Copied" : label}
    </button>
  );
}

export function ProgressBar({ value, className, label }: { value: number; className?: string; label?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-3", className)} role="progressbar" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-[width] duration-500" style={{ width: `${v}%` }} />
    </div>
  );
}

export function ProgressRing({ value, size = 44, stroke = 4 }: { value: number; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value));
  return (
    <svg width={size} height={size} role="img" aria-label={`${Math.round(v)}% complete`}>
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--surface-3)" strokeWidth={stroke} fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--accent)" strokeWidth={stroke} fill="none" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - v / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="fill-[var(--fg)] text-[11px] font-semibold">{Math.round(v)}%</text>
    </svg>
  );
}

/** Live countdown to a UTC deadline, announced politely to screen readers once per minute at most. */
export function Countdown({ to, prefix, className }: { to: string | null | undefined; prefix?: string; className?: string }) {
  const now = useNow(1000);
  if (!to) return null;
  const ms = new Date(to).getTime() - now.getTime();
  if (ms <= 0) return <span className={className}>Closed</span>;
  const d = Math.floor(ms / 86400000);
  const h = Math.floor((ms % 86400000) / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const text = d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
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

export function Cover({ style = "aurora", className, children }: { style?: string; className?: string; children?: ReactNode }) {
  return (
    <div className={cn("relative overflow-hidden", className)} style={{ background: COVERS[style] ?? COVERS.aurora, backgroundSize: style === "mono" ? "28px 28px, cover" : undefined }}>
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
          "flex w-full flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border-2 border-dashed px-6 py-8 text-center transition-colors disabled:opacity-50",
          drag ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-surface-2",
        )}
      >
        <UploadCloud className="h-6 w-6 text-muted" aria-hidden />
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
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
      {items.map((it, i) => (
        <div key={i} className="flex flex-col">
          <dt className="text-xs text-subtle">{it.label}</dt>
          <dd className="mt-0.5 text-fg">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
