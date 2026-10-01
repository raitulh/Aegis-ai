"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BarChart3, Building2, CreditCard, LayoutDashboard, Mail, Settings, Users } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { OrgAdminContext } from "@/components/orgs/org-admin-context";
import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import type { OrgDetail } from "@/components/orgs/types";
import { DemoBadge } from "@/components/ui/badge";
import { Container } from "@/components/ui/page";
import { ErrorState, PermissionDenied, Skeleton, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

const ADMIN_ONLY = new Set(["invites", "departments", "settings", "plan"]);

export default function OrgAdminLayout({ children }: { children: ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const pathname = usePathname();
  const me = useRequireAuth();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`), enabled: Boolean(me.data) });

  if (me.isPending || !me.data) return <Spinner />;
  if (org.isPending) {
    return (
      <Container className="py-8">
        <div role="status" aria-label="Loading">
          <Skeleton className="h-14 w-72" />
          <div className="mt-8 grid gap-6 lg:grid-cols-[220px_1fr]">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-96 w-full" />
          </div>
        </div>
      </Container>
    );
  }
  if (org.isError) return <Container className="py-10"><ErrorState error={org.error} onRetry={() => org.refetch()} /></Container>;

  const o = org.data;
  if (!o.viewer.can_manage_content) {
    return (
      <Container className="py-10">
        <PermissionDenied message={`Only owners, admins and managers of ${o.name} can open its admin area.`} />
      </Container>
    );
  }

  const base = `/orgs/${slug}/admin`;
  const section = pathname.slice(base.length).split("/").filter(Boolean)[0] ?? "";
  const blocked = ADMIN_ONLY.has(section) && !o.viewer.can_manage;

  const nav = [
    { href: base, label: "Dashboard", icon: LayoutDashboard, exact: true, show: true },
    { href: `${base}/members`, label: "Members", icon: Users, show: true },
    { href: `${base}/invites`, label: "Invites", icon: Mail, show: o.viewer.can_manage },
    { href: `${base}/departments`, label: "Departments", icon: Building2, show: o.viewer.can_manage },
    { href: `${base}/settings`, label: "Settings", icon: Settings, show: o.viewer.can_manage },
    { href: `${base}/plan`, label: "Plan", icon: CreditCard, show: o.viewer.can_manage },
  ].filter((i) => i.show);

  return (
    <OrgAdminContext.Provider value={o}>
      <Container className="py-8">
        <div className="flex flex-col gap-4 border-b border-border pb-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={48} />
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-accent-strong">Organization admin</p>
              <p className="truncate text-lg font-semibold text-fg">{o.name}</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <OrgVerificationBadge status={o.verification_status} />
                {o.is_demo ? <DemoBadge /> : null}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-3 text-sm">
            {o.type === "sponsor" ? (
              <Link href={`/orgs/${o.slug}/sponsor`} className="inline-flex items-center gap-1.5 text-muted hover:text-fg">
                <BarChart3 className="h-4 w-4" aria-hidden /> Sponsor dashboard
              </Link>
            ) : null}
            <Link href={`/orgs/${o.slug}`} className="inline-flex items-center gap-1.5 text-muted hover:text-fg">
              Public page <ArrowUpRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[200px_minmax(0,1fr)]">
          <nav aria-label="Organization admin" className="lg:sticky lg:top-20 lg:self-start">
            <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
              {nav.map((item) => {
                const active = item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <li key={item.href} className="shrink-0">
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-2 text-sm transition-colors",
                        active ? "bg-surface-2 font-medium text-fg" : "text-muted hover:bg-surface-2 hover:text-fg",
                      )}
                    >
                      <item.icon className="h-4 w-4" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
            {!o.viewer.can_manage ? (
              <p className="mt-4 hidden text-xs text-subtle lg:block">You’re a manager. Invites, departments, settings and billing are managed by owners and admins.</p>
            ) : null}
          </nav>
          <div className="min-w-0">
            {blocked ? <PermissionDenied message={`Only owners and admins of ${o.name} can open this section.`} /> : children}
          </div>
        </div>
      </Container>
    </OrgAdminContext.Provider>
  );
}
