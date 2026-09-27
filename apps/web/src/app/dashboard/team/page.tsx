"use client";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, StatusBadge } from "@/components/ui/primitives";
import { useTeam } from "@/lib/queries";
import { initials, timeAgo, titleCase } from "@/lib/utils";

export default function TeamPage() {
  const query = useTeam();
  return (
    <div>
      <PageHeader title="Team" description="Workspace members and their roles. Permissions are enforced server-side." />
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(members) => (
          <DataTable>
            <THead><TR><TH>Member</TH><TH>Email</TH><TH>Role</TH><TH>Status</TH><TH>Last active</TH></TR></THead>
            <tbody>
              {members.map((m) => (
                <TR key={m.membership_id}>
                  <TD><div className="flex items-center gap-2"><span className="grid h-7 w-7 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[10px] font-semibold text-[var(--color-accent-bright)]">{initials(m.name, m.email)}</span><span className="font-medium">{m.name ?? "—"}</span></div></TD>
                  <TD className="text-[var(--color-text-muted)]">{m.email}</TD>
                  <TD><Badge tone="accent">{titleCase(m.role)}</Badge></TD>
                  <TD><StatusBadge status={m.status === "active" ? "resolved" : m.status} /></TD>
                  <TD className="text-[var(--color-text-subtle)]">{m.last_active_at ? timeAgo(m.last_active_at) : "—"}</TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )}
      </QueryBoundary>
    </div>
  );
}
