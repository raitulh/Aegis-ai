"use client";
import Link from "next/link";
import { Network } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Card, CardBody, EmptyState } from "@/components/ui/primitives";
import { useSystems } from "@/lib/queries";
import { titleCase } from "@/lib/utils";

export default function AgentsPage() {
  const query = useSystems({ page_size: 100 });
  return (
    <div>
      <PageHeader title="Agents" description="Inspect observable agent traces and tool-action authorization — no chain-of-thought is ever stored." />
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(page) => {
          const agents = page.items.filter((s) => s.system_type === "agent" || s.system_type === "multi_agent");
          return agents.length === 0 ? (
            <EmptyState icon={Network} title="No agent systems" description="Register an agent system and run an audit to capture traces." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {agents.map((s) => (
                <Link key={s.id} href={`/dashboard/agents/${s.id}`}>
                  <Card className="transition-colors hover:border-[var(--color-border-strong)]">
                    <CardBody>
                      <div className="flex items-center gap-2"><Network className="h-4 w-4 text-[var(--color-accent-bright)]" /><span className="font-medium">{s.name}</span></div>
                      <p className="mt-1 text-xs text-[var(--color-text-subtle)]">{titleCase(s.system_type)} · {s.model_name}</p>
                    </CardBody>
                  </Card>
                </Link>
              ))}
            </div>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
