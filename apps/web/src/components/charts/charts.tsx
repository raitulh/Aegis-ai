"use client";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DIMENSIONS, SEVERITY_META } from "@/lib/format";
import { titleCase } from "@/lib/utils";

const AXIS = { stroke: "var(--color-text-subtle)", fontSize: 11 };
const GRID = "var(--color-border)";

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface-3)] px-2.5 py-1.5 text-xs shadow-[var(--shadow)]">
      {label ? <p className="mb-1 font-medium">{titleCase(String(label))}</p> : null}
      {payload.map((p: any, i: number) => (
        <p key={i} style={{ color: p.color || p.fill }}>
          {titleCase(p.name)}: <span className="font-mono">{typeof p.value === "number" ? p.value.toFixed(p.value % 1 ? 1 : 0) : p.value}</span>
        </p>
      ))}
    </div>
  );
}

/** Only dimensions that were actually measured are plotted — an untested dimension is not a score of 0. */
export function TrustPostureRadar({ current, previous }: { current: Record<string, number>; previous?: Record<string, number> }) {
  const measured = DIMENSIONS.filter((d) => current[d] !== undefined);
  const data = measured.map((d) => ({ dimension: titleCase(d), current: current[d], previous: previous?.[d] ?? 0 }));
  if (data.length < 3) {
    return <p className="text-sm text-[var(--color-text-subtle)]">A radar needs at least three measured dimensions; see the scores alongside.</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <RadarChart data={data} outerRadius="62%" margin={{ top: 8, right: 28, bottom: 8, left: 28 }}>
        <PolarGrid stroke={GRID} />
        <PolarAngleAxis dataKey="dimension" tick={{ fill: "var(--color-text-muted)", fontSize: 11 }} />
        {previous && Object.keys(previous).length ? <Radar name="Previous" dataKey="previous" stroke="var(--color-text-subtle)" fill="var(--color-text-subtle)" fillOpacity={0.08} strokeDasharray="3 3" /> : null}
        <Radar name="Current" dataKey="current" stroke="var(--color-accent)" fill="var(--color-accent)" fillOpacity={0.22} strokeWidth={2} />
        <Tooltip content={<ChartTooltip />} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

export function RiskTrendChart({ data }: { data: { date: string; scores: Record<string, number> }[] }) {
  const rows = data.map((d) => ({ date: d.date.slice(5), ...d.scores }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={rows} margin={{ left: -18, right: 8, top: 8 }}>
        <defs>
          {DIMENSIONS.map((d, i) => (
            <linearGradient key={d} id={`grad-${d}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLORS[i]} stopOpacity={0.3} />
              <stop offset="100%" stopColor={COLORS[i]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis domain={[0, 100]} tick={AXIS} tickLine={false} axisLine={false} width={40} />
        <Tooltip content={<ChartTooltip />} />
        {DIMENSIONS.map((d, i) => (
          <Area key={d} type="monotone" dataKey={d} name={d} stroke={COLORS[i]} fill={`url(#grad-${d})`} strokeWidth={1.6} dot={false} connectNulls />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SeverityBar({ counts }: { counts: Record<string, number> }) {
  const data = ["critical", "high", "medium", "low", "info"].map((s) => ({ severity: titleCase(s), value: counts[s] ?? 0, color: SEVERITY_META[s].color }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ left: -20, right: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey="severity" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis allowDecimals={false} tick={AXIS} tickLine={false} axisLine={false} width={36} />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--color-surface-2)" }} />
        <Bar dataKey="value" name="Findings" radius={[4, 4, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MiniSpark({ data, color = "var(--color-accent)" }: { data: number[]; color?: string }) {
  const rows = data.map((v, i) => ({ i, v }));
  return (
    <ResponsiveContainer width="100%" height={40}>
      <AreaChart data={rows} margin={{ top: 2, bottom: 2 }}>
        <defs>
          <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill="url(#spark)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

const COLORS = ["#4c8dff", "#3ecf8e", "#f5c451", "#ff8a4c", "#a78bfa", "#6ba3ff"];

/** Runtime events vs. policy violations over time (real counts from the runtime ledger). */
export function RuntimeTimelineChart({ data, hourly }: { data: { t: string; events: number; violations: number }[]; hourly: boolean }) {
  const rows = data.map((d) => ({
    ...d,
    label: new Date(d.t).toLocaleString(undefined, hourly ? { hour: "2-digit", minute: "2-digit" } : { month: "short", day: "numeric" }),
  }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={rows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...AXIS} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis {...AXIS} tickLine={false} axisLine={false} allowDecimals={false} />
        <Tooltip content={<ChartTooltip />} />
        <Area type="monotone" dataKey="events" name="events" stroke="var(--color-accent)" fill="var(--color-accent)" fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} />
        <Area type="monotone" dataKey="violations" name="violations" stroke="var(--color-high)" fill="var(--color-high)" fillOpacity={0.2} strokeWidth={2} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
