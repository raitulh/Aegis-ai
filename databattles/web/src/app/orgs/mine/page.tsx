"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Building2, CalendarDays, Plus, Settings, University } from "lucide-react";
import Link from "next/link";

import { isManagerRole, OrgLogo, RoleBadge, VERIFICATION_METHOD_LABELS } from "@/components/orgs/org-ui";
import { OrgTypeIcon } from "@/components/orgs/org-visuals";
import type { MyMembership } from "@/components/orgs/types";
import { Badge, SelfDeclaredBadge, StatusBadge, VerifiedBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDate, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

function MembershipRow({ m }: { m: MyMembership }) {
  return (
    <li className="group relative flex flex-col gap-3 px-4 py-4 transition-colors duration-200 hover:bg-surface-2/50 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
      <div className="flex min-w-0 flex-1 items-center gap-3.5">
        <OrgLogo name={m.org.name} logoUrl={m.org.logo_url} size={44} />
        <div className="min-w-0">
          <Link href={`/orgs/${m.org.slug}`} className="block truncate font-medium tracking-[-0.01em] text-fg transition-colors hover:text-accent-strong">
            {m.org.name}
          </Link>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-subtle">
            <span className="inline-flex items-center gap-1">
              <OrgTypeIcon type={m.org.type} className="h-3.5 w-3.5" />
              {titleCase(m.org.type)}
            </span>
            <span aria-hidden>·</span>
            <span className="tabular inline-flex items-center gap-1">
              <CalendarDays className="h-3.5 w-3.5" aria-hidden />
              {m.status === "pending" ? "Requested" : "Joined"} {formatDate(m.joined_at)}
            </span>
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
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
      <div className="shrink-0 sm:w-28 sm:text-right">
        {m.status === "active" && isManagerRole(m.role) ? (
          <LinkButton href={`/orgs/${m.org.slug}/admin`} variant="secondary" size="sm" icon={<Settings className="h-4 w-4" />}>
            Manage
          </LinkButton>
        ) : (
          <LinkButton href={`/orgs/${m.org.slug}`} variant="ghost" size="sm" icon={<ArrowUpRight className="h-4 w-4" />}>View</LinkButton>
        )}
      </div>
    </li>
  );
}

function Group({ title, hint, rows }: { title: string; hint: string; rows: MyMembership[] }) {
  if (!rows.length) return null;
  return (
    <section aria-label={title}>
      <div className="mb-2.5 flex items-baseline justify-between gap-3 px-1">
        <h2 className="text-eyebrow text-subtle">
          {title} <span className="tabular text-muted">· {rows.length}</span>
        </h2>
        <p className="hidden text-xs text-subtle sm:block">{hint}</p>
      </div>
      <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        {rows.map((m) => <MembershipRow key={m.org.id} m={m} />)}
      </ul>
    </section>
  );
}

export default function MyOrgsPage() {
  const me = useRequireAuth();
  const memberships = useQuery({
    queryKey: ["orgs", "mine"],
    queryFn: () => get<MyMembership[]>("/orgs/me/memberships"),
    enabled: Boolean(me.data),
  });

  if (me.isPending || !me.data) {
    return (
      <Container size="lg">
        <div className="pb-8 pt-12" role="status" aria-label="Loading">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="mt-4 h-8 w-64" />
          <Skeleton className="mt-3 h-4 w-96 max-w-full" />
        </div>
        <SkeletonRows rows={4} />
      </Container>
    );
  }

  return (
    <Container size="lg" className="pb-20">
      <PageHeader
        eyebrow="Organizations"
        icon={<Building2 />}
        title="My organizations"
        description="Organizations you belong to or have asked to join. Verified memberships appear as verified on your profile; others do not."
        meta={
          memberships.data && memberships.data.length ? (
            <>
              <span className="tabular"><span className="font-medium text-muted">{memberships.data.filter((m) => m.status === "active").length}</span> active</span>
              <span className="tabular"><span className="font-medium text-muted">{memberships.data.filter((m) => m.status === "active" && m.verified).length}</span> verified</span>
              <span className="tabular"><span className="font-medium text-muted">{memberships.data.filter((m) => m.status === "pending").length}</span> pending</span>
            </>
          ) : null
        }
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
            icon={<University />}
            title="You haven’t joined any organizations"
            description="Find your university or club and verify with your institutional email, or ask an admin for an invite."
            action={
              <>
                <LinkButton href="/orgs">Find your organization</LinkButton>
                <LinkButton href="/orgs/new" variant="secondary" icon={<Plus className="h-4 w-4" />}>Create one</LinkButton>
              </>
            }
          />
        }
      >
        {(rows) => (
          <div className="space-y-8">
            <Group title="You manage" hint="Open the admin console to run members, invites and settings." rows={rows.filter((m) => m.status === "active" && isManagerRole(m.role))} />
            <Group title="Member of" hint="Visible to you and each organization. A verified university membership can also appear on your profile." rows={rows.filter((m) => m.status === "active" && !isManagerRole(m.role))} />
            <Group title="Awaiting review" hint="An administrator reviews each request." rows={rows.filter((m) => m.status === "pending")} />
            <Group title="Past memberships" hint="Declined or removed." rows={rows.filter((m) => m.status !== "active" && m.status !== "pending")} />
          </div>
        )}
      </QueryState>
    </Container>
  );
}
