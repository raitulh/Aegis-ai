"use client";
import { Menu } from "@base-ui-components/react/menu";
import { ChevronDown, LogOut, Menu as MenuIcon, Search, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { CommandPalette } from "@/components/dashboard/command-palette";
import { NAV, type NavGroup } from "@/components/dashboard/nav";
import { NotificationsMenu } from "@/components/dashboard/notifications";
import { Logo } from "@/components/logo";
import { DemoBanner } from "@/components/ui/display";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { api } from "@/lib/api";
import { useCan, useSession } from "@/lib/queries";
import { cn, initials, titleCase } from "@/lib/utils";

function NavList({ groups, pathname, onNavigate }: { groups: NavGroup[]; pathname: string; onNavigate?: () => void }) {
  const isActive = (href: string) => (href === "/dashboard" ? pathname === href : pathname === href || pathname.startsWith(href + "/"));
  return (
    <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 py-4">
      {groups.map((group, gi) => (
        <div key={gi} className={gi > 0 ? "mt-5" : ""}>
          {group.section ? <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-subtle)]">{group.section}</p> : null}
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active = isActive(item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group flex items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-1.5 text-sm transition-colors",
                      active ? "bg-[var(--color-surface-2)] text-[var(--color-text)]" : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]",
                    )}
                  >
                    <item.icon className={cn("h-4 w-4", active && "text-[var(--color-accent-bright)]")} aria-hidden />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function DashboardShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: session } = useSession();
  const can = useCan();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.permission || can(i.permission)) })).filter((g) => g.items.length);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    const onExpired = () => router.push(`/login?next=${encodeURIComponent(window.location.pathname)}&expired=1`);
    window.addEventListener("keydown", onKey);
    window.addEventListener("aegis:unauthenticated", onExpired);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("aegis:unauthenticated", onExpired);
    };
  }, [router]);

  async function logout() {
    await api.post("/auth/logout").catch(() => {});
    router.push("/login");
  }

  const org = session?.organization;

  return (
    <div className="flex min-h-screen bg-[var(--color-bg)]">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] lg:flex">
        <div className="flex h-14 items-center border-b border-[var(--color-border)] px-5">
          <Link href="/dashboard" aria-label="Aegis home" className="rounded-md">
            <Logo />
          </Link>
        </div>
        <NavList groups={groups} pathname={pathname} />
        {org ? (
          <div className="border-t border-[var(--color-border)] px-4 py-3 text-xs">
            <p className="truncate font-medium text-[var(--color-text)]">{org.name}</p>
            <p className="text-[var(--color-text-subtle)]">
              {titleCase(session?.role)} · {titleCase(org.plan)} plan
            </p>
          </div>
        ) : null}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {org && (org.is_demo || org.is_sandbox) ? <DemoBanner sandbox={org.is_sandbox} expiresAt={org.expires_at} /> : null}
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-bg)]/90 px-4 backdrop-blur-xl sm:px-6">
          <button type="button" aria-label="Open navigation" onClick={() => setDrawerOpen(true)} className="rounded-md p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] lg:hidden">
            <MenuIcon className="h-5 w-5" />
          </button>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Search and run commands"
            className="flex flex-1 items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text-subtle)] transition-colors hover:border-[var(--color-border-strong)] sm:max-w-sm"
          >
            <Search className="h-4 w-4" aria-hidden />
            <span>Search or run a command…</span>
            <kbd className="ml-auto hidden rounded border border-[var(--color-border-strong)] px-1.5 py-0.5 font-mono text-[10px] sm:block">⌘K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-2">
            <NotificationsMenu />
            <Menu.Root>
              <Menu.Trigger aria-label="Account menu" className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-sm hover:border-[var(--color-border-strong)] focus-ring">
                <span aria-hidden className="grid h-6 w-6 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[10px] font-semibold text-[var(--color-accent-bright)]">
                  {initials(session?.user.full_name, session?.user.email)}
                </span>
                <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-subtle)]" aria-hidden />
              </Menu.Trigger>
              <Menu.Portal>
                <Menu.Positioner sideOffset={8} align="end">
                  <Menu.Popup className="z-50 w-60 rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface-2)] p-1.5 shadow-[var(--shadow-lg)]">
                    <div className="px-2.5 py-2">
                      <p className="truncate text-sm font-medium">{session?.user.full_name || session?.user.email}</p>
                      <p className="truncate text-xs text-[var(--color-text-subtle)]">
                        {org?.name} · {titleCase(session?.role)}
                      </p>
                    </div>
                    <div className="my-1 h-px bg-[var(--color-border)]" />
                    <Menu.Item render={<Link href="/dashboard/settings" />} className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-[var(--color-text-muted)] outline-none data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-text)]">
                      <Settings className="h-4 w-4" aria-hidden /> Settings
                    </Menu.Item>
                    <Menu.Item onClick={logout} className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-[var(--color-text-muted)] outline-none data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-text)]">
                      <LogOut className="h-4 w-4" aria-hidden /> Sign out
                    </Menu.Item>
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.Root>
          </div>
        </header>

        <main id="main" tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 outline-none sm:px-6 lg:px-8">
          {children}
        </main>
      </div>

      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent title="Navigate" side="left" className="max-w-xs">
          <NavList groups={groups} pathname={pathname} onNavigate={() => setDrawerOpen(false)} />
        </SheetContent>
      </Sheet>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
