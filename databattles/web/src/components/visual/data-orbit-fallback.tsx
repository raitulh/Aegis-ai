import { useId } from "react";

import { cn } from "@/lib/cn";
import { ORBIT_CONCEPTS } from "./orbit-concepts";

const CX = 300;
const CY = 300;
const RX = 212;
const RY = 74;
const TILT = (-8 * Math.PI) / 180;

function onEllipse(angle: number, rx = RX, ry = RY) {
  const x = Math.cos(angle) * rx;
  const y = Math.sin(angle) * ry;
  return {
    x: CX + x * Math.cos(TILT) - y * Math.sin(TILT),
    y: CY + x * Math.sin(TILT) + y * Math.cos(TILT),
  };
}

/**
 * 2.5D SVG rendition of the Data Orbit. It is the server-rendered first paint, the reduced-motion view and
 * the fallback when WebGL is unavailable or the GPU context is lost — so it has to stand on its own.
 */
export function DataOrbitFallback({
  className,
  active,
  onActive,
  theme = "dark",
}: {
  className?: string;
  active?: number | null;
  onActive?: (i: number | null) => void;
  theme?: "dark" | "light";
}) {
  const id = useId().replace(/:/g, "");
  const n = ORBIT_CONCEPTS.length;
  const nodes = ORBIT_CONCEPTS.map((c, i) => ({
    ...c,
    color: theme === "light" ? c.colorLight : c.color,
    ...onEllipse((i / n) * Math.PI * 2 - Math.PI / 2),
  }));
  const tiltDeg = (TILT * 180) / Math.PI;
  return (
    <svg viewBox="0 0 600 600" className={cn("h-full w-full", className)} aria-hidden fill="none">
      <defs>
        <radialGradient id={`${id}-glow`} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0.38" />
          <stop offset="0.45" stopColor="var(--accent)" stopOpacity="0.08" />
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-core`} cx="38%" cy="34%" r="70%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.3" stopColor="#c9bcff" />
          <stop offset="0.75" stopColor="#5b3df5" />
          <stop offset="1" stopColor="#2b1d86" />
        </radialGradient>
        <linearGradient id={`${id}-ring`} x1="0" x2="1">
          <stop offset="0" stopColor="var(--accent-strong)" stopOpacity="0.15" />
          <stop offset="0.5" stopColor="var(--accent-strong)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--cyan)" stopOpacity="0.2" />
        </linearGradient>
      </defs>

      <circle cx={CX} cy={CY} r="250" fill={`url(#${id}-glow)`} />

      {/* Orbits */}
      <ellipse cx={CX} cy={CY} rx="282" ry="104" transform={`rotate(-4 ${CX} ${CY})`} stroke="var(--border-strong)" strokeDasharray="3 7" />
      <ellipse cx={CX} cy={CY} rx={RX} ry={RY} transform={`rotate(${tiltDeg} ${CX} ${CY})`} stroke={`url(#${id}-ring)`} strokeWidth="1.4" />
      <ellipse
        cx={CX}
        cy={CY}
        rx={RX}
        ry={RY}
        transform={`rotate(${tiltDeg} ${CX} ${CY})`}
        stroke="var(--cyan)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="34 917"
        className="motion-safe:animate-[orbit-flow_9s_linear_infinite]"
      />
      <ellipse cx={CX} cy={CY} rx="128" ry="50" transform={`rotate(22 ${CX} ${CY})`} stroke="var(--border-strong)" />

      {/* Spokes */}
      {nodes.map((nd, i) => (
        <line
          key={nd.key}
          x1={CX}
          y1={CY}
          x2={nd.x}
          y2={nd.y}
          stroke={nd.color}
          strokeOpacity={active === i ? 0.8 : 0.22}
          strokeWidth={active === i ? 1.5 : 1}
          className="transition-[stroke-opacity] duration-300"
        />
      ))}

      {/* Core */}
      <g className="origin-center [transform-box:fill-box] motion-safe:animate-spin-slow">
        <polygon points="300,248 345,274 345,326 300,352 255,326 255,274" stroke="var(--cyan)" strokeOpacity="0.35" />
        <polygon points="300,262 333,281 333,319 300,338 267,319 267,281" stroke="var(--accent-strong)" strokeOpacity="0.3" />
      </g>
      <circle cx={CX} cy={CY} r="34" fill={`url(#${id}-core)`} />
      <circle cx={CX} cy={CY} r="34" stroke="#ffffff" strokeOpacity="0.25" />

      {/* Concept nodes */}
      {nodes.map((nd, i) => {
        const isActive = active === i;
        const labelAbove = nd.y <= CY;
        return (
          <g
            key={nd.key}
            onPointerEnter={onActive ? () => onActive(i) : undefined}
            onPointerLeave={onActive ? () => onActive(null) : undefined}
            className="motion-safe:animate-float"
            style={{ animationDelay: `${-i * 1.1}s`, transformBox: "fill-box" }}
          >
            <circle cx={nd.x} cy={nd.y} r={isActive ? 20 : 14} fill={nd.color} fillOpacity="0.14" className="transition-all duration-300" />
            <circle cx={nd.x} cy={nd.y} r={isActive ? 8 : 6} fill="var(--bg)" stroke={nd.color} strokeWidth="2" className="transition-all duration-300" />
            <text
              x={nd.x}
              y={labelAbove ? nd.y - 24 : nd.y + 34}
              textAnchor="middle"
              className="fill-[var(--fg)] font-mono text-[12px] tracking-[0.14em]"
              fillOpacity={isActive ? 1 : 0.8}
            >
              <tspan className="fill-[var(--fg-subtle)]">{String(i + 1).padStart(2, "0")} </tspan>
              {nd.label.toUpperCase()}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
