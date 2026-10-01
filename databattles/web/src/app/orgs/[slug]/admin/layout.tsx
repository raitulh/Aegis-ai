"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BarChart3, Building2, ChevronRight, CreditCard, LayoutDashboard, Mail, Settings, Users } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { OrgAdminContext } from "@/components/orgs/org-admin-context";
import { OrgLogo, OrgVerificationBadge, ROLE_LABELS } from "@/components/orgs/org-ui";
import { AccentEdge } from "@/components/orgs/org-visuals";
import type { OrgDetail } from "@/components/orgs/types";
import { DemoBadge } from "@/components/ui/badge";
import { Container } from "@/components/ui/page";
import { ErrorState, PermissionDenied, Skeleton, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

const ADMIN_ONLY = new Set(["invites", "departments", "settings", "plan"]);

/** Rail grouping is presentational only — visibility of each item is decided by `show` below. */
const GROUP_OF: Record<string, string> = {
  Dashboard: "Overview",
  Members: "People",
  Invites: "People",
  Departments: "People",
  Settings: "Organization",
  Plan: "Organization",
};

const pillCls =
  "flex h-9 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-[13px] font-medium transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";
const railCls =
  "group relative flex h-9 items-center gap-2.5 rounded-[var(--radius-md)] px-3 text-sm transition-[background-color,color,box-shadow] duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]";

export default function OrgAdminLayout({ children }: { children: ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const pathname = usePathname();
  const me = useRequireAuth();
  const org = useQuery({ queryKey: qk.org(slug), queryFn: () => get<OrgDetail>(`/orgs/${slug}`), enabled: Boolean(me.data) });
  const pills = useRef<HTMLUListElement>(null);

  // Keep the active pill in view in the horizontally scrolling mobile nav (desktop rail isn't scrollable).
  useEffect(() => {
    const list = pills.current;
    const el = list?.querySelector<HTMLElement>("[aria-current='page']");
    if (!list || !el || list.scrollWidth <= list.clientWidth) return;
    list.scrollTo({ left: Math.max(0, el.offsetLeft - list.clientWidth / 2 + el.clientWidth / 2) });
  }, [pathname, org.isSuccess]);

  if (me.isPending || !me.data) return <Spinner />;
  if (org.isPending) {
    return (
      <Container className="py-8">
        <div role="status" aria-label="Loading">
          <Skeleton className="h-4 w-72" />
          <div className="mt-6 grid gap-6 lg:grid-cols-[232px_minmax(0,1fr)] lg:gap-8">
            <div className="flex gap-2 overflow-hidden lg:flex-col">
              <Skeleton className="hidden h-40 w-full rounded-[var(--radius-lg)] lg:block" />
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-28 shrink-0 rounded-full lg:w-full lg:rounded-[var(--radius-md)]" />)}
            </div>
            <div className="space-y-4">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-8 w-1/3" />
              <Skeleton className="h-28 w-full rounded-[var(--radius-xl)]" />
              <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
            </div>
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
  const isActive = (item: (typeof nav)[number]) => (item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(item.href + "/"));
  const groups = [...new Set(nav.map((i) => GROUP_OF[i.label] ?? "Organization"))];

  return (
    <OrgAdminContext.Provider value={o}>
      <Container className="pb-20">
        <div className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <nav aria-label="Breadcrumb" className="min-w-0 text-sm text-subtle">
            <ol className="flex min-w-0 items-center gap-1.5">
              <li className="shrink-0"><Link href="/orgs" className="transition-colors hover:text-fg">Organizations</Link></li>
              <li aria-hidden className="shrink-0"><ChevronRight className="h-3.5 w-3.5" /></li>
              <li className="min-w-0 truncate"><Link href={`/orgs/${o.slug}`} className="transition-colors hover:text-fg">{o.name}</Link></li>
              <li aria-hidden className="shrink-0"><ChevronRight className="h-3.5 w-3.5" /></li>
              <li className="shrink-0 text-muted">Admin</li>
            </ol>
          </nav>
          <div className="flex shrink-0 flex-wrap gap-x-4 gap-y-2 text-sm">
            {o.type === "sponsor" ? (
              <Link href={`/orgs/${o.slug}/sponsor`} className="inline-flex items-center gap-1.5 text-muted transition-colors hover:text-fg">
                <BarChart3 className="h-4 w-4" aria-hidden /> Sponsor dashboard
              </Link>
            ) : null}
            <Link href={`/orgs/${o.slug}`} className="inline-flex items-center gap-1.5 text-muted transition-colors hover:text-fg">
              Public page <ArrowUpRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[232px_minmax(0,1fr)] lg:gap-8">
          <aside className="min-w-0 space-y-4 lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:self-start lg:overflow-y-auto lg:pb-4 lg:[scrollbar-width:thin]">
            {/* Identity: compact row on mobile, card in the desktop rail. */}
            <div className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-3.5 shadow-card">
              <AccentEdge color={o.accent_color} className="inset-x-4" />
              <div className="flex min-w-0 items-center gap-3 lg:flex-col lg:items-start">
                <OrgLogo name={o.name} logoUrl={o.logo_url} accentColor={o.accent_color} size={40} />
                <div className="min-w-0">
                  <p className="text-eyebrow text-accent-strong">Organization admin</p>
                  <p className="mt-0.5 line-clamp-2 text-sm font-semibold leading-snug tracking-[-0.01em] text-fg" title={o.name}>{o.name}</p>
                </div>
              </div>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <OrgVerificationBadge status={o.verification_status} />
                {o.is_demo ? <DemoBadge /> : null}
              </div>
              <dl className="mt-3 hidden grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-xs lg:grid">
                <div className="min-w-0 bg-bg-elevated/70 px-2.5 py-2">
                  <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Members</dt>
                  <dd className="tabular mt-0.5 truncate font-medium text-fg">{compactNumber(o.member_count)}</dd>
                </div>
                <div className="min-w-0 bg-bg-elevated/70 px-2.5 py-2">
                  <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Plan</dt>
                  <dd className="mt-0.5 truncate font-medium text-fg">{o.plan_key ? titleCase(o.plan_key) : "—"}</dd>
                </div>
                <div className="col-span-2 min-w-0 bg-bg-elevated/70 px-2.5 py-2">
                  <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Your role</dt>
                  <dd className="mt-0.5 truncate font-medium text-fg">{o.viewer.role ? ROLE_LABELS[o.viewer.role] : "Platform staff"}</dd>
                </div>
              </dl>
            </div>

            {/* Mobile & tablet: one scrollable row of pills. */}
            <nav aria-label="Organization admin" className="lg:hidden">
              <ul ref={pills} className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:-mx-6 sm:px-6">
                {nav.map((item) => {
                  const active = isActive(item);
                  return (
                    <li key={item.href} className="shrink-0">
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          pillCls,
                          active
                            ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] bg-accent-soft text-fg"
                            : "border-border bg-surface text-muted hover:border-border-strong hover:text-fg",
                        )}
                      >
                        <item.icon className={cn("h-3.5 w-3.5", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>

            {/* Desktop: grouped rail. */}
            <nav aria-label="Organization admin" className="hidden lg:block">
              <div className="space-y-5">
                {groups.map((g) => (
                  <div key={g}>
                    <p className="mb-1.5 px-3 text-eyebrow text-subtle" aria-hidden>{g}</p>
                    <ul className="space-y-0.5">
                      {nav
                        .filter((item) => (GROUP_OF[item.label] ?? "Organization") === g)
                        .map((item) => {
                          const active = isActive(item);
                          return (
                            <li key={item.href}>
                              <Link
                                href={item.href}
                                aria-current={active ? "page" : undefined}
                                className={cn(
                                  railCls,
                                  active ? "bg-surface-2 font-medium text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]" : "text-muted hover:bg-surface-2/70 hover:text-fg",
                                )}
                              >
                                <span aria-hidden className={cn("absolute inset-y-2 left-0 w-0.5 rounded-full bg-brand transition-opacity duration-200", active ? "opacity-100" : "opacity-0")} />
                                <item.icon className={cn("h-4 w-4 shrink-0", active ? "text-accent-strong" : "text-subtle group-hover:text-muted")} aria-hidden />
                                <span className="truncate">{item.label}</span>
                              </Link>
                            </li>
                          );
                        })}
                    </ul>
                  </div>
                ))}
              </div>
              {!o.viewer.can_manage ? (
                <p className="mt-5 rounded-[var(--radius-md)] border border-border bg-surface/60 px-3 py-2.5 text-xs leading-relaxed text-subtle">
                  You’re a manager. Invites, departments, settings and billing are managed by owners and admins.
                </p>
              ) : null}
            </nav>
          </aside>
          <div className="min-w-0">
            {blocked ? (
              <>
                <h1 className="sr-only">{o.name} admin</h1>
                <PermissionDenied message={`Only owners and admins of ${o.name} can open this section.`} />
              </>
            ) : children}
          </div>
        </div>
      </Container>
    </OrgAdminContext.Provider>
  );
}
