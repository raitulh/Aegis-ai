"use client";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Server } from "lucide-react";
import { Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";

type Trust = {
  controls: { area: string; status: string; detail: string }[];
  compliance_roadmap: { framework: string; status: string; note: string }[];
  evidence_signing_key_id: string;
  data_handling: Record<string, string>;
  subprocessors: string;
};

/** Facts served by the API about this deployment. No certification is claimed that has not been attested. */
export function TrustCenter() {
  const { data, isPending, isError } = useQuery({ queryKey: ["public-trust"], queryFn: () => api.get<Trust>("/public/trust"), staleTime: 5 * 60_000 });
  if (isPending) return <Skeleton className="h-96" />;
  if (isError || !data) return <p className="text-sm">Trust information is unavailable right now.</p>;
  return (
    <div className="space-y-10">
      <section aria-labelledby="controls">
        <h2 id="controls" className="text-xl font-semibold text-[var(--color-text)]">
          Security controls
        </h2>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {data.controls.map((c) => (
            <li key={c.area} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
              <p className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
                {c.status === "implemented" ? <CheckCircle2 className="h-4 w-4 text-[var(--color-success)]" aria-hidden /> : <Server className="h-4 w-4 text-[var(--color-medium)]" aria-hidden />}
                {c.area}
                <span className="ml-auto text-xs font-normal text-[var(--color-text-subtle)]">{c.status === "implemented" ? "In the product" : "Your deployment"}</span>
              </p>
              <p className="mt-1.5 text-sm">{c.detail}</p>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="compliance">
        <h2 id="compliance" className="text-xl font-semibold text-[var(--color-text)]">
          Certifications and attestations
        </h2>
        <p className="mt-2 text-sm">We state what has and has not been independently assessed.</p>
        <ul className="mt-4 divide-y divide-[var(--color-border)] rounded-[var(--radius-lg)] border border-[var(--color-border)]">
          {data.compliance_roadmap.map((c) => (
            <li key={c.framework} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
              <CircleDashed className="h-4 w-4 text-[var(--color-text-subtle)]" aria-hidden />
              <span className="font-medium text-[var(--color-text)]">{c.framework}</span>
              <span className="rounded-full border border-[var(--color-border-strong)] px-2 py-0.5 text-xs">{c.status}</span>
              <span className="text-[var(--color-text-subtle)]">{c.note}</span>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="data">
        <h2 id="data" className="text-xl font-semibold text-[var(--color-text)]">
          Data handling
        </h2>
        <dl className="mt-4 space-y-3 text-sm">
          {Object.entries(data.data_handling).map(([k, v]) => (
            <div key={k}>
              <dt className="font-medium capitalize text-[var(--color-text)]">{k.replace(/_/g, " ")}</dt>
              <dd className="mt-0.5">{v}</dd>
            </div>
          ))}
          <div>
            <dt className="font-medium text-[var(--color-text)]">Subprocessors</dt>
            <dd className="mt-0.5">{data.subprocessors}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--color-text)]">Evidence signing key id</dt>
            <dd className="mt-0.5 font-mono text-xs">{data.evidence_signing_key_id}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
