import type { GraphEdge, GraphNode } from "@/lib/types";

/**
 * Deterministic layered layout for the assurance graph. Columns follow the assurance chain
 * (govern → system → test → evidence → finding → fix) so the picture reads left to right.
 * Pure function: same input, same positions — no physics, no randomness.
 */
export const LAYERS: { key: string; label: string; types: GraphNode["type"][] }[] = [
  { key: "govern", label: "Policies & controls", types: ["runtime_policy", "policy", "control"] },
  { key: "systems", label: "Systems & agents", types: ["system", "agent"] },
  { key: "surface", label: "Models & tools", types: ["model", "tool"] },
  { key: "tests", label: "Audits", types: ["test"] },
  { key: "evidence", label: "Evidence", types: ["evidence"] },
  { key: "findings", label: "Findings", types: ["finding"] },
  { key: "fix", label: "Remediation & retests", types: ["remediation", "retest"] },
];

export const TYPE_LABEL: Record<string, string> = {
  runtime_policy: "Runtime policy",
  policy: "Compliance policy",
  control: "Control",
  system: "System",
  agent: "Agent",
  model: "Model",
  tool: "Tool",
  test: "Audit",
  evidence: "Evidence",
  finding: "Finding",
  remediation: "Remediation",
  retest: "Retest",
};

export const TYPE_COLOR: Record<string, string> = {
  runtime_policy: "var(--color-accent-bright)",
  policy: "var(--color-accent)",
  control: "var(--color-info)",
  system: "var(--color-text)",
  agent: "var(--color-text)",
  model: "var(--color-text-muted)",
  tool: "var(--color-text-muted)",
  test: "var(--color-low)",
  evidence: "var(--color-success)",
  finding: "var(--color-high)",
  remediation: "var(--color-medium)",
  retest: "var(--color-success)",
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--color-critical)",
  high: "var(--color-high)",
  medium: "var(--color-medium)",
  low: "var(--color-low)",
  info: "var(--color-info)",
};

export function nodeColor(n: GraphNode): string {
  if (n.type === "finding" && n.severity) return SEVERITY_COLOR[n.severity] ?? TYPE_COLOR.finding;
  return TYPE_COLOR[n.type] ?? "var(--color-text-muted)";
}

export type Positioned = GraphNode & { x: number; y: number; layer: number };

export function layerOf(type: string): number {
  const i = LAYERS.findIndex((l) => l.types.includes(type as GraphNode["type"]));
  return i === -1 ? LAYERS.length - 1 : i;
}

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

export function layout(nodes: GraphNode[], opts: { columnWidth?: number; rowHeight?: number; padding?: number } = {}) {
  const { columnWidth = 196, rowHeight = 30, padding = 24 } = opts;
  const all: GraphNode[][] = LAYERS.map(() => []);
  for (const n of nodes) all[layerOf(n.type)].push(n);
  // Empty layers take no space: columns are packed left to right in chain order.
  const present = LAYERS.map((_, i) => i).filter((i) => all[i].length);
  const columns = present.map((i) => all[i]);
  for (const col of columns) {
    col.sort((a, b) => {
      const s = (SEVERITY_RANK[a.severity ?? ""] ?? 9) - (SEVERITY_RANK[b.severity ?? ""] ?? 9);
      if (s) return s;
      if (a.type !== b.type) return a.type.localeCompare(b.type);
      return a.label.localeCompare(b.label);
    });
  }
  const rows = Math.max(1, ...columns.map((c) => c.length));
  const height = padding * 2 + 28 + rows * rowHeight;
  const positioned = new Map<string, Positioned>();
  columns.forEach((col, column) => {
    // Center short columns vertically so edges stay readable.
    const offset = ((rows - col.length) * rowHeight) / 2;
    col.forEach((n, i) => {
      positioned.set(n.id, { ...n, layer: present[column], x: padding + column * columnWidth, y: padding + 28 + offset + i * rowHeight + rowHeight / 2 });
    });
  });
  const headers = present.map((i, column) => ({ key: LAYERS[i].key, label: LAYERS[i].label, x: padding + column * columnWidth }));
  return { positioned, headers, width: padding * 2 + Math.max(0, columns.length - 1) * columnWidth + 170, height, columnWidth, padding };
}

/** Node ids directly connected to `id` (both directions). */
export function neighbours(id: string, edges: GraphEdge[]): Set<string> {
  const out = new Set<string>([id]);
  for (const e of edges) {
    if (e.source === id) out.add(e.target);
    if (e.target === id) out.add(e.source);
  }
  return out;
}
