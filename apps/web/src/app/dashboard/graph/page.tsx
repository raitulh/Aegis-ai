"use client";
import { ExternalLink, Network } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Graph2D } from "@/components/graph/graph-2d";
import { LAYERS, TYPE_LABEL, layerOf, nodeColor } from "@/components/graph/layout";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Segmented, Select } from "@/components/ui/forms";
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { useClientValue, usePrefersReducedMotion } from "@/lib/hooks";
import { useGraph, useSystems } from "@/lib/queries";
import type { AssuranceGraph, GraphNode } from "@/lib/types";
import { formatDateTime, titleCase } from "@/lib/utils";

const Graph3D = dynamic(() => import("@/components/graph/graph-3d"), {
  ssr: false,
  loading: () => <div className="h-[70vh] skeleton rounded-[var(--radius-lg)]" />,
});

type View = "map" | "list" | "3d";

// Probed once per page load (each probe would otherwise allocate a WebGL context).
let webglSupport: boolean | undefined;
function supportsWebGL() {
  if (webglSupport === undefined) {
    try {
      const c = document.createElement("canvas");
      webglSupport = !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch {
      webglSupport = false;
    }
  }
  return webglSupport;
}

export default function GraphPage() {
  const systems = useSystems({ page_size: 100 });
  const [systemId, setSystemId] = useState("");
  const [minSeverity, setMinSeverity] = useState("low");
  const [days, setDays] = useState(90);
  const [includeResolved, setIncludeResolved] = useState(false);
  const [view, setView] = useState<View>("map");
  const [selected, setSelected] = useState<string | null>(null);
  const webgl = useClientValue(supportsWebGL, false);
  const reducedMotion = usePrefersReducedMotion();
  const query = useGraph({ system_id: systemId || undefined, min_severity: minSeverity, days, include_resolved: includeResolved });

  return (
    <div>
      <PageHeader
        title="Assurance graph"
        description="How your policies, systems, tests, evidence, findings and fixes connect — built from your actual records. Select a node to see its relationships."
        actions={
          <Segmented<View>
            label="Graph view"
            value={view}
            onChange={setView}
            options={[
              { value: "map", label: "Map" },
              { value: "list", label: "List" },
              { value: "3d", label: "3D", disabled: !webgl },
            ]}
          />
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select aria-label="System" value={systemId} onChange={(e) => setSystemId(e.target.value)} className="h-8 w-52 text-xs">
          <option value="">All systems</option>
          {(systems.data?.items ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <Select aria-label="Minimum finding severity" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)} className="h-8 w-44 text-xs">
          {["info", "low", "medium", "high", "critical"].map((s) => (
            <option key={s} value={s}>
              Findings ≥ {titleCase(s)}
            </option>
          ))}
        </Select>
        <Select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-8 w-36 text-xs">
          {[7, 30, 90, 180, 365].map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
          <input type="checkbox" className="accent-[var(--color-accent)]" checked={includeResolved} onChange={(e) => setIncludeResolved(e.target.checked)} />
          Include resolved findings
        </label>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-[60vh] skeleton rounded-[var(--radius-lg)]" />}>
        {(g) =>
          g.nodes.length === 0 ? (
            <EmptyState icon={Network} title="Nothing to map yet" description="The graph fills in as you register systems, run audits and publish policies." />
          ) : (
            <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
              <div className="min-w-0 space-y-3">
                {view === "map" ? <Graph2D nodes={g.nodes} edges={g.edges} selected={selected} onSelect={setSelected} /> : null}
                {view === "3d" ? (
                  <>
                    <Graph3D nodes={g.nodes} edges={g.edges} selected={selected} onSelect={setSelected} />
                    <p className="text-xs text-[var(--color-text-subtle)]">Drag to orbit, scroll to zoom.{reducedMotion ? " Reduced motion is on: the scene never animates on its own." : ""} The map and list views carry the same information and are keyboard accessible.</p>
                  </>
                ) : null}
                {view === "list" ? <GraphList graph={g} onSelect={setSelected} selected={selected} /> : null}
                <Legend graph={g} />
              </div>
              <NodePanel graph={g} selected={selected} onSelect={setSelected} />
            </div>
          )
        }
      </QueryBoundary>
    </div>
  );
}

function Legend({ graph }: { graph: AssuranceGraph }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--color-text-muted)]">
      {Object.entries(graph.counts).map(([type, count]) => (
        <span key={type} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: nodeColor({ id: "", type: type as GraphNode["type"], label: "" }) }} />
          {TYPE_LABEL[type] ?? titleCase(type)} · {count}
        </span>
      ))}
      <span className="ml-auto text-[var(--color-text-subtle)]">Generated {formatDateTime(graph.generated_at)}</span>
    </div>
  );
}

function GraphList({ graph, selected, onSelect }: { graph: AssuranceGraph; selected: string | null; onSelect: (id: string) => void }) {
  const groups = useMemo(() => LAYERS.map((l) => ({ ...l, nodes: graph.nodes.filter((n) => layerOf(n.type) === LAYERS.indexOf(l)) })).filter((g) => g.nodes.length), [graph.nodes]);
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.key} aria-labelledby={`layer-${g.key}`}>
          <h2 id={`layer-${g.key}`} className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-subtle)]">
            {g.label} ({g.nodes.length})
          </h2>
          <ul className="grid gap-1 sm:grid-cols-2">
            {g.nodes.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  aria-pressed={n.id === selected}
                  onClick={() => onSelect(n.id)}
                  className="flex w-full items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] px-2.5 py-1.5 text-left text-sm hover:border-[var(--color-border-strong)] focus-ring aria-pressed:border-[var(--color-accent)]"
                >
                  <span aria-hidden className="h-2 w-2 shrink-0 rounded-full" style={{ background: nodeColor(n) }} />
                  <span className="truncate">{n.label}</span>
                  {n.severity ? <span className="ml-auto text-xs text-[var(--color-text-subtle)]">{n.severity}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function NodePanel({ graph, selected, onSelect }: { graph: AssuranceGraph; selected: string | null; onSelect: (id: string) => void }) {
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const node = selected ? byId.get(selected) : null;
  const relations = useMemo(() => {
    if (!selected) return [];
    return graph.edges
      .filter((e) => e.source === selected || e.target === selected)
      .map((e) => ({ relation: e.relation, outgoing: e.source === selected, other: byId.get(e.source === selected ? e.target : e.source) }))
      .filter((r): r is { relation: string; outgoing: boolean; other: GraphNode } => !!r.other);
  }, [selected, graph.edges, byId]);

  return (
    <Card className="h-fit xl:sticky xl:top-20">
      <CardHeader>
        <CardTitle>{node ? (TYPE_LABEL[node.type] ?? titleCase(node.type)) : "Details"}</CardTitle>
        {node?.href && node.href.startsWith("/") ? (
          <Link href={node.href} className="inline-flex items-center gap-1 text-xs text-[var(--color-accent-bright)] hover:underline">
            Open <ExternalLink className="h-3 w-3" aria-hidden />
          </Link>
        ) : null}
      </CardHeader>
      <CardBody aria-live="polite">
        {!node ? (
          <p className="text-sm text-[var(--color-text-subtle)]">Select a node in the map or list.</p>
        ) : (
          <div className="space-y-3">
            <p className="text-sm font-medium">{node.label}</p>
            <div className="flex flex-wrap gap-1.5">
              {node.severity ? <Badge>{titleCase(String(node.severity))}</Badge> : null}
              {node.status ? <StatusBadge status={String(node.status)} /> : null}
              {typeof node.runtime_mode === "string" ? <Badge>Runtime: {titleCase(node.runtime_mode)}</Badge> : null}
              {typeof node.environment === "string" ? <Badge>{titleCase(node.environment)}</Badge> : null}
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-[var(--color-text-muted)]">{relations.length} relationships</p>
              <ul className="max-h-80 space-y-1 overflow-y-auto text-xs">
                {relations.map((r, i) => (
                  <li key={i}>
                    <button type="button" onClick={() => onSelect(r.other.id)} className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left hover:bg-[var(--color-surface-2)] focus-ring">
                      <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-subtle)]">
                        {r.outgoing ? "" : "← "}
                        {r.relation.replace(/_/g, " ")}
                        {r.outgoing ? " →" : ""}
                      </span>
                      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: nodeColor(r.other) }} />
                      <span className="truncate">{r.other.label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
