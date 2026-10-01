"use client";
import { Check, Minus } from "lucide-react";
import Link from "next/link";
import { Button, Skeleton } from "@/components/ui/primitives";
import { usePlans } from "@/lib/queries";
import { cn, titleCase } from "@/lib/utils";

/** Plans come from the API's plan catalogue (deployment configuration) — nothing is hard-coded here. */
export function PricingTable() {
  const { data, isPending, isError } = usePlans();
  if (isPending) return <Skeleton className="h-96" />;
  if (isError || !data) return <p className="text-sm">Plan details are unavailable right now. <Link className="text-[var(--color-accent-bright)] hover:underline" href="/contact?kind=sales">Contact us</Link> for pricing.</p>;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {data.plans.map((p) => (
          <div key={p.key} className={cn("flex flex-col rounded-[var(--radius-lg)] border bg-[var(--color-surface)] p-5", p.key === "business" ? "border-[var(--color-accent)]" : "border-[var(--color-border)]")}>
            <h2 className="text-base font-semibold text-[var(--color-text)]">{p.name}</h2>
            <p className="mt-1 text-sm">{p.audience}</p>
            <p className="mt-3 text-xl font-semibold text-[var(--color-text)]">{p.price_display ?? (p.self_serve ? <span className="text-sm font-normal text-[var(--color-text-subtle)]">Pricing set by your provider</span> : "Contact sales")}</p>
            <ul className="mt-4 space-y-1 text-xs">
              {Object.entries(p.limits).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-2">
                  <span>{data.quotas[k] ?? titleCase(k)}</span>
                  <span className="font-mono text-[var(--color-text)]">{v === null ? "Contracted" : v.toLocaleString()}</span>
                </li>
              ))}
              <li className="flex justify-between gap-2">
                <span>Runtime event retention</span>
                <span className="font-mono text-[var(--color-text)]">{p.retention_days ? `${p.retention_days} days` : "Contracted"}</span>
              </li>
            </ul>
            <ul className="mt-4 flex-1 space-y-1.5 text-xs">
              {Object.entries(p.features).map(([k, f]) => (
                <li key={k} className={cn("flex items-start gap-1.5", !f.included && "text-[var(--color-text-subtle)]")}>
                  {f.included && !f.roadmap ? <Check className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-success)]" aria-label="Included" /> : <Minus className="mt-0.5 h-3 w-3 shrink-0" aria-label="Not included" />}
                  <span>
                    {f.label}
                    {f.roadmap ? " (roadmap)" : ""}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs">Support: {p.support}</p>
            <div className="mt-4">
              {p.self_serve ? (
                <Link href="/signup">
                  <Button className="w-full" variant={p.key === "business" ? "primary" : "secondary"}>
                    {p.key === "free" ? "Start free" : "Create a workspace"}
                  </Button>
                </Link>
              ) : (
                <Link href="/contact?kind=sales">
                  <Button className="w-full" variant="secondary">
                    Contact sales
                  </Button>
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>
      {data.roadmap_features.length ? <p className="text-xs">Roadmap items are not available in the current release and are never presented as included.</p> : null}
    </div>
  );
}
