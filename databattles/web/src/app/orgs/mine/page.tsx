"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Settings, University } from "lucide-react";
import Link from "next/link";

import { isManagerRole, OrgLogo, RoleBadge, VERIFICATION_METHOD_LABELS } from "@/components/orgs/org-ui";
import type { MyMembership } from "@/components/orgs/types";
import { Badge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState, SkeletonRows, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDate, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

export default function MyOrgsPage() {
  const me = useRequireAuth();
  const memberships = useQuery({
    queryKey: ["orgs", "mine"],
    queryFn: () => get<MyMembership[]>("/orgs/me/memberships"),
    enabled: Boolean(me.data),
  });

  if (me.isPending || !me.data) return <Spinner />;

  return (
    <Container size="lg">
      <PageHeader
        eyebrow="Organizations"
        title="My organizations"
        description="Organizations you belong to or have asked to join. Verified memberships appear as verified on your profile; others do not."
        actions={
          <>
            <LinkButton href="/orgs" variant="secondary">Browse</LinkButton>
            <LinkButton href="/orgs/new" icon={<Plus className="h-4 w-4" />}>Create organization</LinkButton>
          </>
        }
      />
      <QueryState
        query={memberships}
        loading={<SkeletonRows rows={4} />}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState
            icon={<University className="h-5 w-5" />}
            title="You haven’t joined any organizations"
            description="Find your university or club and verify with your institutional email, or ask an admin for an invite."
            action={<LinkButton href="/orgs">Find your organization</LinkButton>}
          />
        }
      >
        {(rows) => (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {rows.map((m) => (
              <li key={m.org.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <OrgLogo name={m.org.name} logoUrl={m.org.logo_url} size={40} />
                  <div className="min-w-0">
                    <Link href={`/orgs/${m.org.slug}`} className="block truncate font-medium text-fg hover:text-accent-strong">
                      {m.org.name}
                    </Link>
                    <p className="text-xs text-subtle">
                      {titleCase(m.org.type)} · {m.status === "pending" ? "Requested" : "Joined"} {formatDate(m.joined_at)}
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {m.status === "pending" ? <StatusBadge status="pending" /> : <RoleBadge role={m.role} />}
                  {m.status === "active" ? (
                    m.verified ? (
                      <VerifiedBadge label="Verified member" title={VERIFICATION_METHOD_LABELS[m.verification_method] ?? m.verification_method} />
                    ) : (
                      <SelfDeclaredBadge />
                    )
                  ) : null}
                  {m.org.verification_status !== "verified" ? <Badge tone="outline">Org unverified</Badge> : null}
                </div>
                {m.status === "active" && isManagerRole(m.role) ? (
                  <LinkButton href={`/orgs/${m.org.slug}/admin`} variant="secondary" size="sm" icon={<Settings className="h-4 w-4" />}>
                    Manage
                  </LinkButton>
                ) : (
                  <LinkButton href={`/orgs/${m.org.slug}`} variant="ghost" size="sm">View</LinkButton>
                )}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Container>
  );
}
