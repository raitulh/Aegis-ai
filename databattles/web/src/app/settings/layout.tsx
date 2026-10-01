"use client";

import { Bell, Lock, Medal, Palette, Plug, User, UserCog, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Container, PageHeader } from "@/components/ui/page";
import { Spinner } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { useRequireAuth } from "@/lib/hooks";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/settings/profile", label: "Profile", icon: User },
  { href: "/settings/account", label: "Account & security", icon: UserCog },
  { href: "/settings/privacy", label: "Privacy", icon: Lock },
  { href: "/settings/notifications", label: "Notifications", icon: Bell },
  { href: "/settings/integrations", label: "Integrations", icon: Plug },
  { href: "/settings/achievements", label: "Achievements", icon: Medal },
  { href: "/settings/appearance", label: "Appearance", icon: Palette },
];

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const me = useRequireAuth();
  const pathname = usePathname();

  return (
    <Container size="lg">
      <PageHeader title="Settings" description="Manage your profile, account security, privacy and preferences." />
      <div className="grid gap-8 pb-16 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Settings" className="-mx-4 overflow-x-auto px-4 lg:mx-0 lg:overflow-visible lg:px-0">
          <ul className="flex gap-1 lg:sticky lg:top-20 lg:flex-col">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(href + "/");
              return (
                <li key={href} className="shrink-0">
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2.5 rounded-[var(--radius-md)] px-3 py-2 text-sm font-medium transition-colors",
                      active ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2/60 hover:text-fg",
                    )}
                  >
                    <Icon className={cn("h-4 w-4", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="min-w-0">{me.isPending || !me.data ? <Spinner label="Loading settings" /> : children}</div>
      </div>
    </Container>
  );
}
