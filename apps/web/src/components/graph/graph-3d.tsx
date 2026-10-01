"use client";
import { Line, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useMemo } from "react";
import { useClientValue } from "@/lib/hooks";
import type { GraphEdge, GraphNode } from "@/lib/types";
import { layerOf, neighbours, nodeColor } from "./layout";

// getComputedStyle returns a live object; cache it so the external-store snapshot is stable.
let cachedStyles: CSSStyleDeclaration | null = null;
function rootStyles() {
  cachedStyles ??= getComputedStyle(document.documentElement);
  return cachedStyles;
}

/** Resolve `var(--token)` colors to concrete values WebGL can use. */
function useResolvedColor() {
  const styles = useClientValue<CSSStyleDeclaration | null>(rootStyles, null);
  return (value: string) => {
    const m = /^var\((--[\w-]+)\)$/.exec(value);
    return (m && styles?.getPropertyValue(m[1]).trim()) || "#8189a0";
  };
}

/**
 * Optional 3D view of the same graph: layers become concentric rings (governance at the centre, fixes at
 * the edge). Renders on demand only (no idle animation loop) and never auto-rotates.
 */
export default function Graph3D({ nodes, edges, selected, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; selected: string | null; onSelect: (id: string | null) => void }) {
  const color = useResolvedColor();
  const positions = useMemo(() => {
    const byLayer = new Map<number, GraphNode[]>();
    for (const n of nodes) {
      const l = layerOf(n.type);
      byLayer.set(l, [...(byLayer.get(l) ?? []), n]);
    }
    const out = new Map<string, [number, number, number]>();
    for (const [layer, list] of byLayer) {
      const radius = 1.2 + layer * 1.35;
      list.forEach((n, i) => {
        const angle = (i / list.length) * Math.PI * 2 + layer * 0.4;
        out.set(n.id, [Math.cos(angle) * radius, (layer - 3) * 0.55, Math.sin(angle) * radius]);
      });
    }
    return out;
  }, [nodes]);
  const focus = useMemo(() => (selected ? neighbours(selected, edges) : null), [selected, edges]);

  return (
    <div className="h-[70vh] overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-bg)]">
      <Canvas frameloop="demand" camera={{ position: [0, 9, 13], fov: 50 }} dpr={[1, 2]} onPointerMissed={() => onSelect(null)} aria-label="3D assurance graph (use the 2D map or list for keyboard access)">
        <ambientLight intensity={0.8} />
        <pointLight position={[10, 12, 10]} intensity={60} />
        {edges.map((e, i) => {
          const a = positions.get(e.source);
          const b = positions.get(e.target);
          if (!a || !b) return null;
          const active = focus ? e.source === selected || e.target === selected : false;
          return <Line key={i} points={[a, b]} color={active ? color("var(--color-accent-bright)") : color("var(--color-border-strong)")} lineWidth={active ? 2 : 1} transparent opacity={focus ? (active ? 1 : 0.08) : 0.35} />;
        })}
        {nodes.map((n) => {
          const p = positions.get(n.id);
          if (!p) return null;
          const dim = focus && !focus.has(n.id);
          return (
            <mesh
              key={n.id}
              position={p}
              scale={n.id === selected ? 1.6 : 1}
              onClick={(ev) => {
                ev.stopPropagation();
                onSelect(n.id === selected ? null : n.id);
              }}
            >
              <sphereGeometry args={[n.type === "system" || n.type === "agent" ? 0.22 : 0.13, 20, 20]} />
              <meshStandardMaterial color={color(nodeColor(n))} transparent opacity={dim ? 0.15 : 1} />
            </mesh>
          );
        })}
        <OrbitControls enableDamping={false} makeDefault />
      </Canvas>
    </div>
  );
}
