import { describe, expect, it } from "vitest";
import type { GraphNode } from "@/lib/types";
import { layerOf, layout, neighbours } from "./layout";

const nodes: GraphNode[] = [
  { id: "system:1", type: "system", label: "Support bot" },
  { id: "finding:2", type: "finding", label: "#2 Leak", severity: "medium" },
  { id: "finding:1", type: "finding", label: "#1 Injection", severity: "critical" },
  { id: "runtime_policy:1", type: "runtime_policy", label: "No exfiltration" },
];

describe("graph layout", () => {
  it("places node types in assurance-chain order", () => {
    expect(layerOf("runtime_policy")).toBeLessThan(layerOf("system"));
    expect(layerOf("system")).toBeLessThan(layerOf("finding"));
    expect(layerOf("unknown")).toBe(6);
  });

  it("is deterministic and sorts findings by severity", () => {
    const a = layout(nodes);
    const b = layout([...nodes].reverse());
    for (const [id, n] of a.positioned) {
      expect(b.positioned.get(id)).toMatchObject({ x: n.x, y: n.y });
    }
    const critical = a.positioned.get("finding:1")!;
    const medium = a.positioned.get("finding:2")!;
    expect(critical.y).toBeLessThan(medium.y);
    expect(critical.x).toBe(medium.x);
    expect(b.positioned.get("finding:1")!.y).toBe(critical.y);
  });

  it("finds neighbours in both directions", () => {
    const n = neighbours("system:1", [
      { source: "finding:1", target: "system:1", relation: "affects" },
      { source: "system:1", target: "model:x", relation: "uses_model" },
      { source: "a", target: "b", relation: "x" },
    ]);
    expect([...n].sort()).toEqual(["finding:1", "model:x", "system:1"]);
  });
});
