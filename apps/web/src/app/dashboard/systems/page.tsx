"use client";
import { Boxes, Plus, Search } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { NewSystemDialog } from "@/components/dashboard/new-system-dialog";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Pagination } from "@/components/ui/display";
import { Input, Select } from "@/components/ui/forms";
import { Badge, Button, EmptyState } from "@/components/ui/primitives";
import { useCan, useSystems } from "@/lib/queries";
import { titleCase } from "@/lib/utils";

const TIER_TONE: Record<string, string> = { critical: "var(--color-critical)", high: "var(--color-high)", limited: "var(--color-medium)", minimal: "var(--color-info)" };

export default function SystemsPage() {
  const can = useCan();
  const router = useRouter();
  const search = useSearchParams();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [environment, setEnvironment] = useState("");
  const query = useSystems({ page, page_size: 25, q: term || undefined, environment: environment || undefined });

  // ⌘K "Add an AI system" deep link (?new=1) opens the dialog; closing it clears the parameter.
  const deepLink = search.get("new") === "1" && can("systems:write");

  useEffect(() => {
    const t = setTimeout(() => {
      setTerm(q.trim());
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const add = can("systems:write") ? (
    <Button icon={Plus} onClick={() => setOpen(true)}>
      Add AI system
    </Button>
  ) : null;

  return (
    <div>
      <PageHeader title="AI systems" description="The models and agents you continuously assure. Each system has its own audits, runtime mode and evidence chain." actions={add} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-subtle)]" aria-hidden />
          <Input aria-label="Search systems" placeholder="Search by name" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
        </div>
        <Select
          aria-label="Environment"
          className="w-44"
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All environments</option>
          {["development", "staging", "production"].map((env) => (
            <option key={env} value={env}>
              {titleCase(env)}
            </option>
          ))}
        </Select>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            term || environment ? (
              <EmptyState icon={Search} title="No systems match" description="Clear the search or environment filter." />
            ) : (
              <EmptyState icon={Boxes} title="Register your first AI system" description="Connect a model provider or endpoint, then run audits and stream runtime events against it." action={add} />
            )
          ) : (
            <>
              <DataTable caption="AI systems">
                <THead>
                  <tr>
                    <TH>Name</TH>
                    <TH>Type</TH>
                    <TH>Environment</TH>
                    <TH>Risk tier</TH>
                    <TH>Model</TH>
                    <TH>Version</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((s) => (
                    <TR key={s.id} href={`/dashboard/systems/${s.id}`}>
                      <TD>
                        <div className="flex items-center gap-2">
                          <RowLink href={`/dashboard/systems/${s.id}`}>{s.name}</RowLink>
                          {s.is_demo ? <Badge tone="warning">DEMO</Badge> : null}
                        </div>
                      </TD>
                      <TD className="text-[var(--color-text-muted)]">{titleCase(s.system_type)}</TD>
                      <TD className="text-[var(--color-text-muted)]">{titleCase(s.environment)}</TD>
                      <TD>
                        <span className="inline-flex items-center gap-1.5 text-xs">
                          <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: TIER_TONE[s.risk_tier] }} />
                          {titleCase(s.risk_tier)}
                        </span>
                      </TD>
                      <TD className="font-mono text-xs text-[var(--color-text-muted)]">{s.model_name ?? "—"}</TD>
                      <TD className="font-mono text-xs">{s.version}</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
              <Pagination page={data.meta.page} totalPages={data.meta.total_pages} onPage={setPage} />
            </>
          )
        }
      </QueryBoundary>
      <NewSystemDialog
        open={open || deepLink}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o && deepLink) router.replace("/dashboard/systems");
        }}
      />
    </div>
  );
}
