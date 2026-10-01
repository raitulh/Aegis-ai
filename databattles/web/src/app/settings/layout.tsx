"use client";

import { Bell, ExternalLink, Lock, Medal, Palette, Plug, Settings, User, UserCog, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { cn } from "@/lib/cn";
import { useRequireAuth } from "@/lib/hooks";
import { SettingsSkeleton } from "./_components/settings-ui";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/settings/profile", label: "Profile", icon: User },
  { href: "/settings/account", label: "Account & security", icon: UserCog },
  { href: "/settings/privacy", label: "Privacy", icon: Lock },
  { href: "/settings/notifications", label: "Notifications", icon: Bell },
  { href: "/settings/integrations", label: "Integrations", icon: Plug },
  { href: "/settings/achievements", label: "Achievements", icon: Medal },
  { href: "/settings/appearance", label: "Appearance", icon: Palette },
];

/** Desktop rail grouping (presentation only — every NAV entry appears exactly once). */
const GROUPS: { label: string; hrefs: string[] }[] = [
  { label: "Identity", hrefs: ["/settings/profile", "/settings/account"] },
  { label: "Visibility", hrefs: ["/settings/privacy", "/settings/achievements"] },
  { label: "Preferences", hrefs: ["/settings/notifications", "/settings/appearance"] },
  { label: "Connections", hrefs: ["/settings/integrations"] },
];

const isActive = (pathname: string, href: string) => pathname === href || pathname.startsWith(href + "/");

function SettingsNav({ pathname }: { pathname: string }) {
  const scroller = useRef<HTMLUListElement>(null);

  // Keep the active pill in view in the horizontally scrolling mobile list (no effect on desktop: it isn't scrollable).
  useEffect(() => {
    const list = scroller.current;
    const el = list?.querySelector<HTMLElement>("[aria-current='page']");
    if (!list || !el || list.scrollWidth <= list.clientWidth) return;
    list.scrollTo({ left: Math.max(0, el.offsetLeft - list.clientWidth / 2 + el.clientWidth / 2) });
  }, [pathname]);

  return (
    <nav
      aria-label="Settings"
      className={cn(
        "sticky top-14 z-30 -mx-4 min-w-0 self-start border-b border-border bg-[var(--glass-strong)] px-4 py-2 backdrop-blur-xl sm:-mx-6 sm:px-6",
        "lg:top-24 lg:z-auto lg:mx-0 lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none",
      )}
    >
      {/* Mobile & tablet: scrollable pills. */}
      <ul ref={scroller} className="-mx-1 flex gap-1.5 overflow-x-auto px-1 py-0.5 [scrollbar-width:none] lg:hidden">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <li key={href} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-[13px] font-medium transition-[background-color,border-color,color] duration-200",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                  active
                    ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border-strong))] bg-accent-soft text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]"
                    : "border-border bg-surface/70 text-muted hover:border-border-strong hover:text-fg",
                )}
              >
                <Icon className={cn("h-3.5 w-3.5", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>

      {/* Desktop: grouped left rail. */}
      <div className="hidden space-y-6 lg:block">
        {GROUPS.map((g) => (
          <div key={g.label}>
            <p id={`settings-nav-${g.label.toLowerCase()}`} className="mb-1.5 px-3 text-eyebrow text-subtle">{g.label}</p>
            <ul aria-labelledby={`settings-nav-${g.label.toLowerCase()}`} className="space-y-0.5">
              {g.hrefs.map((href) => {
                const item = NAV.find((n) => n.href === href);
                if (!item) return null;
                const { label, icon: Icon } = item;
                const active = isActive(pathname, href);
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "group relative flex h-9 items-center gap-2.5 rounded-[var(--radius-md)] px-3 text-sm font-medium transition-[background-color,color,box-shadow] duration-200",
                        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                        active
                          ? "bg-surface-2 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),inset_0_0_0_1px_var(--border)]"
                          : "text-muted hover:bg-surface-2/60 hover:text-fg",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "absolute inset-y-2 left-0 w-[2px] rounded-full bg-brand transition-opacity duration-200",
                          active ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <Icon className={cn("h-4 w-4 transition-colors", active ? "text-accent-strong" : "text-subtle group-hover:text-muted")} aria-hidden />
                      {label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const me = useRequireAuth();
  const pathname = usePathname();

  return (
    <Container size="lg">
      <PageHeader
        eyebrow="Your account"
        icon={<Settings />}
        title="Settings"
        description="Manage your profile, account security, privacy and preferences."
        meta={
          me.data ? (
            <span className="inline-flex min-w-0 items-center gap-2">
              <Avatar name={me.data.display_name} src={me.data.avatar_url} size={20} />
              <span className="truncate font-medium text-muted">{me.data.display_name}</span>
              <span className="font-mono text-subtle">@{me.data.handle}</span>
            </span>
          ) : null
        }
        actions={
          me.data ? (
            <LinkButton href={`/u/${me.data.handle}`} variant="secondary" size="sm" className="h-9" icon={<ExternalLink className="h-3.5 w-3.5" aria-hidden />}>
              View public profile
            </LinkButton>
          ) : null
        }
      />
      <div className="grid grid-cols-1 gap-6 pb-20 lg:grid-cols-[13.5rem_minmax(0,1fr)] lg:gap-12">
        <SettingsNav pathname={pathname} />
        <div className="min-w-0">{me.isPending || !me.data ? <SettingsSkeleton /> : children}</div>
      </div>
    </Container>
  );
}
