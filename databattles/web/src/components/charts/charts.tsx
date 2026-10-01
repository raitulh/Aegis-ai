"use client";

/**
 * Dependency-free SVG charts. Each chart has an accessible name and a visually hidden data table so the
 * information is available to screen readers, not just as pixels.
 */

import { useId, useMemo, useState } from "react";

import { cn } from "@/lib/cn";
import { formatDate, formatNumber } from "@/lib/format";

type Point = { x: string | number; y: number };

function SrTable({ caption, rows }: { caption: string; rows: [string, string | number][] }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <tbody>
        {rows.map(([k, v], i) => (
          <tr key={i}>
            <th scope="row">{k}</th>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Sparkline({ data, width = 120, height = 32, className, label = "Trend" }: { data: number[]; width?: number; height?: number; className?: string; label?: string }) {
  if (data.length < 2) return <span className="text-xs text-subtle">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - ((v - min) / span) * (height - 4) - 2}`).join(" ");
  return (
    <svg width={width} height={height} className={className} role="img" aria-label={`${label}: from ${formatNumber(data[0], 4)} to ${formatNumber(data[data.length - 1], 4)}`}>
      <polyline points={pts} fill="none" stroke="var(--accent-strong)" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

const LINE_PAD = { l: 48, r: 12, t: 12, b: 24 };

export function LineChart({ data, height = 200, label, yFormat = (v: number) => formatNumber(v, 4), xFormat = (x: string | number) => String(x), className }: {
  data: Point[];
  height?: number;
  label: string;
  yFormat?: (v: number) => string;
  xFormat?: (x: string | number) => string;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const gid = `lc${useId().replace(/:/g, "")}`;
  const width = 640;
  const pad = LINE_PAD;
  const { path, area, xs, ys, min, max } = useMemo(() => {
    const ysv = data.map((d) => d.y);
    const mn = Math.min(...ysv);
    const mx = Math.max(...ysv);
    const span = mx - mn || 1;
    const xsv = data.map((_, i) => pad.l + (data.length === 1 ? 0.5 : i / (data.length - 1)) * (width - pad.l - pad.r));
    const ysp = data.map((d) => pad.t + (1 - (d.y - mn) / span) * (height - pad.t - pad.b));
    const p = xsv.map((x, i) => `${i ? "L" : "M"}${x},${ysp[i]}`).join(" ");
    const a = `${p} L${xsv[xsv.length - 1]},${height - pad.b} L${xsv[0]},${height - pad.b} Z`;
    return { path: p, area: a, xs: xsv, ys: ysp, min: mn, max: mx };
  }, [data, height, pad]);
  if (!data.length) return <p className="py-8 text-center text-sm text-subtle">No data yet.</p>;
  return (
    <div className={cn("relative", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={label} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id={`${gid}-fill`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
          <linearGradient id={`${gid}-stroke`} x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--accent-strong)" />
            <stop offset="100%" stopColor="var(--cyan)" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((f) => {
          const y = pad.t + f * (height - pad.t - pad.b);
          return (
            <g key={f}>
              <line x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke="var(--border)" strokeDasharray="2 5" />
              <text x={pad.l - 6} y={y} textAnchor="end" dominantBaseline="central" className="fill-[var(--fg-subtle)] text-[10px]">
                {yFormat(max - f * (max - min))}
              </text>
            </g>
          );
        })}
        <path d={area} fill={`url(#${gid}-fill)`} />
        <path d={path} fill="none" stroke={`url(#${gid}-stroke)`} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null ? <line x1={xs[hover]} x2={xs[hover]} y1={pad.t} y2={height - pad.b} stroke="var(--border-strong)" /> : null}
        {xs.map((x, i) => (
          <g key={i}>
            <rect x={x - 8} y={pad.t} width={16} height={height - pad.t - pad.b} fill="transparent" onMouseEnter={() => setHover(i)} />
            {hover === i ? <circle cx={x} cy={ys[i]} r={4.5} fill="var(--accent-strong)" stroke="var(--surface)" strokeWidth={2} /> : null}
          </g>
        ))}
        <text x={pad.l} y={height - 6} className="fill-[var(--fg-subtle)] text-[10px]">{xFormat(data[0].x)}</text>
        <text x={width - pad.r} y={height - 6} textAnchor="end" className="fill-[var(--fg-subtle)] text-[10px]">{xFormat(data[data.length - 1].x)}</text>
      </svg>
      {hover !== null ? (
        <div className="tabular pointer-events-none absolute right-2 top-2 rounded-lg border border-border-strong bg-surface-2/95 px-2.5 py-1 text-xs shadow-card backdrop-blur animate-fade-in">
          {xFormat(data[hover].x)} · <span className="font-semibold">{yFormat(data[hover].y)}</span>
        </div>
      ) : null}
      <SrTable caption={label} rows={data.map((d) => [xFormat(d.x), yFormat(d.y)])} />
    </div>
  );
}

export function BarChart({ data, label, height = 180, valueFormat = (v: number) => formatNumber(v) }: { data: { label: string; value: number }[]; label: string; height?: number; valueFormat?: (v: number) => string }) {
  if (!data.length) return <p className="py-8 text-center text-sm text-subtle">No data yet.</p>;
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div>
      <div className="flex items-end gap-1.5" style={{ height }} role="img" aria-label={label}>
        {data.map((d, i) => (
          <div key={i} className="group relative flex h-full flex-1 flex-col justify-end" title={`${d.label}: ${valueFormat(d.value)}`}>
            <div className="rounded-t-[5px] bg-gradient-to-t from-accent/55 to-accent-strong transition-[opacity,filter] duration-200 group-hover:brightness-125" style={{ height: `${(d.value / max) * 100}%`, minHeight: d.value ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-[10px] text-subtle">
        <span>{data[0].label}</span>
        <span>{data[data.length - 1].label}</span>
      </div>
      <SrTable caption={label} rows={data.map((d) => [d.label, valueFormat(d.value)])} />
    </div>
  );
}

export function HBarList({ data, label, valueFormat = (v: number) => formatNumber(v) }: { data: { label: string; value: number }[]; label: string; valueFormat?: (v: number) => string }) {
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <ul className="space-y-2" aria-label={label}>
      {data.map((d, i) => (
        <li key={i} className="text-sm">
          <div className="mb-1 flex justify-between gap-3">
            <span className="truncate text-fg">{d.label}</span>
            <span className="tabular text-muted">{valueFormat(d.value)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-surface-3">
            <div className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out-expo" style={{ width: `${(d.value / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** GitHub-style activity calendar from {"YYYY-MM-DD": count}. */
export function ActivityHeatmap({ counts, days = 182, label = "Activity" }: { counts: Record<string, number>; days?: number; label?: string }) {
  const cells = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const start = new Date(today);
    start.setDate(start.getDate() - days + 1);
    start.setDate(start.getDate() - start.getDay());
    const out: { date: string; count: number }[] = [];
    for (const d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
      const key = d.toISOString().slice(0, 10);
      out.push({ date: key, count: counts[key] ?? 0 });
    }
    return out;
  }, [counts, days]);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const max = Math.max(...cells.map((c) => c.count), 1);
  const weeks = Math.ceil(cells.length / 7);
  const level = (n: number) => (n === 0 ? 0 : Math.min(4, Math.ceil((n / max) * 4)));
  const fills = [
    "var(--surface-3)",
    "color-mix(in oklab, var(--accent) 32%, var(--surface-3))",
    "color-mix(in oklab, var(--accent) 55%, var(--surface-3))",
    "color-mix(in oklab, var(--accent) 78%, var(--surface-3))",
    "var(--accent-strong)",
  ];
  return (
    <div>
      <div className="overflow-x-auto">
        <svg width={weeks * 13} height={7 * 13} role="img" aria-label={`${label}: ${total} contributions in the last ${days} days`}>
          {cells.map((c, i) => (
            <rect key={c.date} x={Math.floor(i / 7) * 13} y={(i % 7) * 13} width={10} height={10} rx={2} fill={fills[level(c.count)]}>
              <title>{`${formatDate(c.date)}: ${c.count}`}</title>
            </rect>
          ))}
        </svg>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-subtle">
        <span>{formatNumber(total)} in the last {days} days</span>
        <span className="flex items-center gap-1">
          Less {fills.map((f, i) => <span key={i} className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: f }} />)} More
        </span>
      </div>
    </div>
  );
}

export function Histogram({ bins, label }: { bins: { start: number; end: number; count: number }[]; label: string }) {
  return <BarChart label={label} data={bins.map((b) => ({ label: formatNumber(b.start, 3), value: b.count }))} />;
}
