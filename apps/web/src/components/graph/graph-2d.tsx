"use client";
import { useMemo } from "react";
import type { GraphEdge, GraphNode } from "@/lib/types";
import { layout, neighbours, nodeColor } from "./layout";

/**
 * Layered SVG view of the assurance graph. Every node is keyboard-focusable (Enter/Space selects);
 * selecting a node highlights its direct relationships and dims the rest.
 */
export function Graph2D({ nodes, edges, selected, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; selected: string | null; onSelect: (id: string | null) => void }) {
  const { positioned, headers, width, height, padding } = useMemo(() => layout(nodes), [nodes]);
  const focus = useMemo(() => (selected ? neighbours(selected, edges) : null), [selected, edges]);

  return (
    <div className="max-h-[70vh] overflow-auto rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg)]">
      <svg width={width} height={height} role="group" aria-label={`Assurance graph with ${nodes.length} nodes and ${edges.length} relationships`} className="block select-none" onClick={() => onSelect(null)}>
        {headers.map((h) => (
          <text key={h.key} x={h.x} y={padding} className="fill-[var(--color-text-subtle)] text-[10px] font-semibold uppercase tracking-wider" aria-hidden>
            {h.label}
          </text>
        ))}
        <g aria-hidden>
          {edges.map((e, i) => {
            const a = positioned.get(e.source);
            const b = positioned.get(e.target);
            if (!a || !b) return null;
            const [from, to] = a.x <= b.x ? [a, b] : [b, a];
            const active = focus ? focus.has(e.source) && focus.has(e.target) && (e.source === selected || e.target === selected) : false;
            const mx = (from.x + to.x) / 2;
            return (
              <path
                key={i}
                d={`M ${from.x + 6} ${from.y} C ${mx} ${from.y}, ${mx} ${to.y}, ${to.x - 6} ${to.y}`}
                fill="none"
                stroke={active ? "var(--color-accent-bright)" : "var(--color-border-strong)"}
                strokeOpacity={focus ? (active ? 0.95 : 0.12) : 0.5}
                strokeWidth={active ? 1.6 : 1}
              />
            );
          })}
        </g>
        {[...positioned.values()].map((n) => {
          const dim = focus && !focus.has(n.id);
          const isSel = n.id === selected;
          return (
            <g
              key={n.id}
              transform={`translate(${n.x}, ${n.y})`}
              tabIndex={0}
              role="button"
              aria-pressed={isSel}
              aria-label={`${n.type.replace("_", " ")}: ${n.label}${n.severity ? `, ${n.severity}` : ""}${n.status ? `, ${n.status}` : ""}`}
              onClick={(ev) => {
                ev.stopPropagation();
                onSelect(isSel ? null : n.id);
              }}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  onSelect(isSel ? null : n.id);
                }
              }}
              className="cursor-pointer outline-none [&:focus-visible>circle]:stroke-[var(--color-accent-bright)] [&:focus-visible>circle]:[stroke-width:3]"
              opacity={dim ? 0.25 : 1}
            >
              <circle r={isSel ? 7 : 5.5} fill={nodeColor(n)} stroke={isSel ? "var(--color-accent-bright)" : "var(--color-bg)"} strokeWidth={2} />
              <text x={11} y={4} className="fill-[var(--color-text-muted)] text-[11px]">
                {n.label.length > 24 ? `${n.label.slice(0, 23)}…` : n.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
