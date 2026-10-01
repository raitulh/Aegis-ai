"use client";

import { Activity, Building2, CreditCard, Flag, History, ListChecks, Mail, ShieldAlert, Users, Wrench } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/page";
import { PermissionDenied, Spinner } from "@/components/ui/states";
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

export default function AdminLayout({ children }: { children: ReactNode }) {
  const me = useRequireAuth();
  const pathname = usePathname();

  if (me.isPending || !me.data) return <Spinner />;
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
    <Container className="py-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-fg">Platform console</p>
          <Badge tone={isAdmin ? "accent" : "info"}>{isAdmin ? "Platform admin" : "Moderator"}</Badge>
        </div>
        <Link href="/moderation" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
          <ShieldAlert className="h-4 w-4" aria-hidden /> Moderation queue
        </Link>
      </div>
      <div className="grid gap-6 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Admin" className="lg:sticky lg:top-20 lg:self-start">
          <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
            {items.map((item) => {
              const active = item.key === "" ? pathname === "/admin" : pathname === item.href || pathname.startsWith(item.href + "/");
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
        </nav>
        <div className="min-w-0">
          {allowed ? children : <PermissionDenied message="This section is available to platform administrators only." />}
        </div>
      </div>
    </Container>
  );
}
