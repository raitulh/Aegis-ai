"use client";

import { Activity, Building2, CreditCard, Flag, History, ListChecks, Mail, ScrollText, Shield, ShieldAlert, Users, Wrench } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/page";
import { PermissionDenied, Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { hasRole, useRequireAuth } from "@/lib/hooks";

/** Sections moderators may open; everything else is platform-admin only. */
const MODERATOR_SECTIONS = new Set(["", "users"]);

const NAV = [
  { key: "", href: "/admin", label: "Overview", icon: Activity },
  { key: "users", href: "/admin/users", label: "Users", icon: Users },
  { key: "orgs", href: "/admin/orgs", label: "Organizations", icon: Building2 },
  { key: "audit", href: "/admin/audit", label: "Audit log", icon: History },
  { key: "jobs", href: "/admin/jobs", label: "Jobs & errors", icon: ListChecks },
  { key: "emails", href: "/admin/emails", label: "Emails", icon: Mail },
  { key: "flags", href: "/admin/flags", label: "Feature flags", icon: Flag },
  { key: "revenue", href: "/admin/revenue", label: "Revenue", icon: CreditCard },
  { key: "tools", href: "/admin/tools", label: "Tools", icon: Wrench },
];

/** Desktop rail grouping (presentation only — every visible NAV entry appears exactly once). */
const GROUPS: { label: string; keys: string[] }[] = [
  { label: "Monitor", keys: ["", "jobs", "emails"] },
  { label: "People", keys: ["users", "orgs"] },
  { label: "Governance", keys: ["audit", "flags"] },
  { label: "Business", keys: ["revenue"] },
  { label: "Operate", keys: ["tools"] },
];

type NavItem = (typeof NAV)[number];

function isActive(item: NavItem, pathname: string) {
  return item.key === "" ? pathname === "/admin" : pathname === item.href || pathname.startsWith(item.href + "/");
}

function AdminNav({ items, pathname }: { items: NavItem[]; pathname: string }) {
  const pillsRef = useRef<HTMLUListElement>(null);
  // Same order as the grouped desktop rail.
  const ordered = GROUPS.flatMap((g) => g.keys)
    .map((k) => items.find((i) => i.key === k))
    .filter((i): i is NavItem => Boolean(i));
  // Keep the active pill in view in the horizontally scrolling row (only that row scrolls, never the page).
  useEffect(() => {
    const row = pillsRef.current;
    const active = row?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!row || !active || row.scrollWidth <= row.clientWidth) return;
    row.scrollLeft += active.getBoundingClientRect().left - row.getBoundingClientRect().left - 16;
  }, [pathname]);

  return (
    <nav
      aria-label="Admin"
      className={cn(
        "sticky top-14 z-30 -mx-4 min-w-0 self-start border-b border-border bg-[var(--glass-strong)] px-4 py-2 backdrop-blur-xl sm:-mx-6 sm:px-6",
        "lg:top-24 lg:z-auto lg:mx-0 lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none",
      )}
    >
      {/* Mobile & tablet: one scrollable row of pills. */}
      <ul ref={pillsRef} className="-mx-1 flex gap-1.5 overflow-x-auto px-1 py-0.5 [scrollbar-width:none] lg:hidden">
        {ordered.map((item) => {
          const active = isActive(item, pathname);
          return (
            <li key={item.href} className="shrink-0">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-[13px] font-medium transition-[background-color,border-color,color] duration-200",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                  active
                    ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border-strong))] bg-accent-soft text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]"
                    : "border-border bg-surface/70 text-muted hover:border-border-strong hover:text-fg",
                )}
              >
                <item.icon className={cn("h-3.5 w-3.5", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>

      {/* Desktop: grouped rail. */}
      <div className="hidden space-y-6 lg:block">
        {GROUPS.map((g) => {
          const groupItems = g.keys.map((k) => items.find((i) => i.key === k)).filter((i): i is NavItem => Boolean(i));
          if (!groupItems.length) return null;
          return (
            <div key={g.label}>
              <p className="mb-1.5 px-3 text-eyebrow text-subtle" aria-hidden>{g.label}</p>
              <ul className="space-y-0.5">
                {groupItems.map((item) => {
                  const active = isActive(item, pathname);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "group relative flex h-9 items-center gap-2.5 rounded-[var(--radius-md)] px-3 text-sm transition-[background-color,color,box-shadow] duration-200",
                          "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                          active
                            ? "bg-surface-2 font-medium text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),inset_0_0_0_1px_var(--border)]"
                            : "text-muted hover:bg-surface-2/60 hover:text-fg",
                        )}
                      >
                        <span
                          aria-hidden
                          className={cn("absolute inset-y-2 left-0 w-[2px] rounded-full bg-brand transition-opacity duration-200", active ? "opacity-100" : "opacity-0")}
                        />
                        <item.icon className={cn("h-4 w-4 shrink-0 transition-colors", active ? "text-accent-strong" : "text-subtle group-hover:text-muted")} aria-hidden />
                        <span className="truncate">{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
        <p className="flex items-start gap-2 border-t border-border px-3 pt-4 text-xs leading-relaxed text-subtle">
          <ScrollText className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          Privileged actions are recorded in the audit log.
        </p>
      </div>
    </nav>
  );
}

/** Who is operating the console, with the role that scopes it. */
function IdentityStrip({ name, handle, avatarUrl, isAdmin }: { name: string; handle: string; avatarUrl: string | null; isAdmin: boolean }) {
  return (
    <div className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card animate-rise">
      <div aria-hidden className="pointer-events-none absolute inset-x-6 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-5">
        <div className="order-1 flex min-w-0 flex-1 items-center gap-2.5 md:flex-none">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-soft text-accent-strong" aria-hidden>
            <Shield className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-eyebrow text-accent-strong">Platform console</p>
            <p className="hidden truncate text-xs text-subtle sm:block">Operations, people and governance for DataBattles</p>
          </div>
        </div>
        <span aria-hidden className="order-2 hidden h-8 w-px bg-border md:block" />
        <div className="order-4 flex min-w-0 basis-full items-center gap-2.5 border-t border-border pt-3 md:order-3 md:basis-auto md:border-0 md:pt-0">
          <Avatar name={name} src={avatarUrl} size={26} />
          <div className="min-w-0 leading-tight">
            <p className="truncate text-sm font-medium text-fg">
              <span className="sr-only">Signed in as </span>
              {name}
            </p>
            <p className="truncate font-mono text-[11px] text-subtle">@{handle}</p>
          </div>
          <Badge tone={isAdmin ? "accent" : "info"} className="ml-auto md:ml-0">{isAdmin ? "Platform admin" : "Moderator"}</Badge>
        </div>
        <Link
          href="/moderation"
          className={cn(
            "relative order-3 ml-auto inline-flex h-9 shrink-0 items-center gap-1.5 rounded-[var(--radius-md)] border border-border bg-surface-2 px-3 text-[13px] font-medium text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)] transition-colors hover:border-border-strong hover:bg-surface-3 md:order-4",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
          )}
        >
          <ShieldAlert className="h-4 w-4 text-warning" aria-hidden />
          <span>Moderation<span className="max-sm:sr-only"> queue</span></span>
        </Link>
      </div>
    </div>
  );
}

/** Console-shaped placeholder while the viewer loads (strip, rail and content blocks). */
function ConsoleSkeleton() {
  return (
    <Container className="pb-20 pt-6 sm:pt-8">
      <div role="status" aria-label="Loading admin console">
        <Skeleton className="h-[58px] w-full rounded-[var(--radius-lg)]" />
        <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[13.5rem_minmax(0,1fr)] lg:gap-10">
          <div className="hidden space-y-2 lg:block">
            {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
          </div>
          <div className="space-y-4">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-4 w-80 max-w-full" />
            <Skeleton className="mt-6 h-40 w-full rounded-[var(--radius-lg)]" />
            <Skeleton className="h-64 w-full rounded-[var(--radius-lg)]" />
          </div>
        </div>
      </div>
    </Container>
  );
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  const me = useRequireAuth();
  const pathname = usePathname();

  if (me.isPending || !me.data) return <ConsoleSkeleton />;
  const isAdmin = me.data.platform_roles.includes("platform_admin");
  const isModerator = hasRole(me.data, "moderator");
  if (!isModerator) {
    return (
      <Container className="py-10">
        <PermissionDenied message="The admin console is available to platform administrators and moderators only." />
      </Container>
    );
  }

  const section = pathname.replace(/^\/admin\/?/, "").split("/")[0] ?? "";
  const allowed = isAdmin || MODERATOR_SECTIONS.has(section);
  const items = NAV.filter((n) => isAdmin || MODERATOR_SECTIONS.has(n.key));

  return (
    <Container className="pb-20 pt-6 sm:pt-8">
      <IdentityStrip name={me.data.display_name} handle={me.data.handle} avatarUrl={me.data.avatar_url} isAdmin={isAdmin} />
      <div className="mt-4 grid grid-cols-1 gap-6 lg:mt-8 lg:grid-cols-[13.5rem_minmax(0,1fr)] lg:gap-10">
        <AdminNav items={items} pathname={pathname} />
        <div className="min-w-0 pt-2 lg:pt-0">
          {allowed ? children : <PermissionDenied message="This section is available to platform administrators only." />}
        </div>
      </div>
    </Container>
  );
}
