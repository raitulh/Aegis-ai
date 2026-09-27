"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  Boxes,
  ChevronDown,
  FileBarChart,
  FileText,
  FlaskConical,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Network,
  Plug,
  Search,
  Settings,
  ShieldAlert,
  ScrollText,
  Users,
} from "lucide-react";
import { Logo } from "@/components/logo";
import { CommandPalette } from "@/components/dashboard/command-palette";
import { NotificationsMenu } from "@/components/dashboard/notifications";
import { Menu } from "@base-ui-components/react/menu";
import { useSession } from "@/lib/queries";
import { api } from "@/lib/api";
import { cn, initials } from "@/lib/utils";

const NAV: { section?: string; items: { href: string; label: string; icon: typeof Gauge }[] }[] = [
  {
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { href: "/dashboard/systems", label: "AI Systems", icon: Boxes },
      { href: "/dashboard/audits", label: "Audits", icon: Gauge },
      { href: "/dashboard/findings", label: "Findings", icon: ShieldAlert },
      { href: "/dashboard/policies", label: "Policies", icon: ScrollText },
      { href: "/dashboard/red-team", label: "Red Team", icon: FlaskConical },
      { href: "/dashboard/evidence", label: "Evidence", icon: FileText },
      { href: "/dashboard/agents", label: "Agents", icon: Network },
      { href: "/dashboard/monitoring", label: "Monitoring", icon: Activity },
      { href: "/dashboard/reports", label: "Reports", icon: FileBarChart },
    ],
  },
  {
    section: "Developer",
    items: [
      { href: "/dashboard/api-keys", label: "API Keys", icon: KeyRound },
      { href: "/dashboard/integrations", label: "Integrations", icon: Plug },
    ],
  },
  {
    section: "Workspace",
    items: [
      { href: "/dashboard/team", label: "Team", icon: Users },
      { href: "/dashboard/settings", label: "Settings", icon: Settings },
    ],
  },
];

export function DashboardShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: session } = useSession();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [env, setEnv] = useState("production");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function logout() {
    await api.post("/auth/logout").catch(() => {});
    router.push("/login");
  }

  const isActive = (href: string) => (href === "/dashboard" ? pathname === href : pathname.startsWith(href));

  return (
    <div className="flex min-h-screen bg-[var(--color-bg)]">
      {/* Sidebar */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] lg:flex">
        <div className="flex h-16 items-center border-b border-[var(--color-border)] px-5">
          <Link href="/dashboard" className="focus-ring rounded-md">
            <Logo />
          </Link>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {NAV.map((group, gi) => (
            <div key={gi} className={gi > 0 ? "mt-6" : ""}>
              {group.section ? <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-subtle)]">{group.section}</p> : null}
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "group flex items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-2 text-sm transition-colors",
                        isActive(item.href)
                          ? "bg-[var(--color-surface-2)] text-[var(--color-text)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]",
                      )}
                    >
                      <item.icon className={cn("h-4 w-4", isActive(item.href) && "text-[var(--color-accent-bright)]")} />
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        {session?.organization.is_demo ? (
          <div className="m-3 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-accent)_35%,transparent)] bg-[var(--color-accent-dim)] px-3 py-2 text-xs text-[var(--color-accent-bright)]">
            Simulated workspace — data is simulated.
          </div>
        ) : null}
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-bg)]/85 px-4 backdrop-blur-xl sm:px-6">
          <button onClick={() => setPaletteOpen(true)} className="flex flex-1 items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-subtle)] transition-colors hover:border-[var(--color-border-strong)] sm:max-w-sm focus-ring">
            <Search className="h-4 w-4" />
            <span>Search…</span>
            <kbd className="ml-auto hidden rounded border border-[var(--color-border-strong)] px-1.5 py-0.5 font-mono text-[10px] sm:block">⌘K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-2">
            <EnvSelector value={env} onChange={setEnv} />
            <NotificationsMenu />
            <Menu.Root>
              <Menu.Trigger className="flex items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-sm hover:border-[var(--color-border-strong)] focus-ring">
                <span className="grid h-6 w-6 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[10px] font-semibold text-[var(--color-accent-bright)]">
                  {initials(session?.user.full_name, session?.user.email)}
                </span>
                <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-subtle)]" />
              </Menu.Trigger>
              <Menu.Portal>
                <Menu.Positioner sideOffset={8} align="end">
                  <Menu.Popup className="z-50 w-56 rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface-2)] p-1.5 shadow-[var(--shadow-lg)]">
                    <div className="px-2.5 py-2">
                      <p className="truncate text-sm font-medium">{session?.user.full_name || session?.user.email}</p>
                      <p className="truncate text-xs text-[var(--color-text-subtle)]">{session?.organization.name}</p>
                    </div>
                    <div className="my-1 h-px bg-[var(--color-border)]" />
                    <Menu.Item render={<Link href="/dashboard/settings" />} className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-[var(--color-text-muted)] outline-none data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-text)]">
                      <Settings className="h-4 w-4" /> Settings
                    </Menu.Item>
                    <Menu.Item onClick={logout} className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-[var(--color-text-muted)] outline-none data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-text)]">
                      <LogOut className="h-4 w-4" /> Sign out
                    </Menu.Item>
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.Root>
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>

        {/* Mobile bottom nav */}
        <nav className="sticky bottom-0 z-30 flex items-center justify-around border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-2 py-1.5 lg:hidden">
          {NAV[0].items.slice(0, 5).map((item) => (
            <Link key={item.href} href={item.href} className={cn("flex flex-col items-center gap-0.5 rounded-md px-2 py-1 text-[10px]", isActive(item.href) ? "text-[var(--color-accent-bright)]" : "text-[var(--color-text-subtle)]")}>
              <item.icon className="h-4.5 w-4.5" />
              {item.label.split(" ")[0]}
            </Link>
          ))}
        </nav>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}

function EnvSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Menu.Root>
      <Menu.Trigger className="hidden items-center gap-1.5 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] sm:flex focus-ring">
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
        {value}
        <ChevronDown className="h-3 w-3" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={8} align="end">
          <Menu.Popup className="z-50 w-40 rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface-2)] p-1.5 shadow-[var(--shadow-lg)]">
            {["production", "staging", "development"].map((e) => (
              <Menu.Item key={e} onClick={() => onChange(e)} className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm capitalize text-[var(--color-text-muted)] outline-none data-[highlighted]:bg-[var(--color-surface-3)] data-[highlighted]:text-[var(--color-text)]">
                {e}
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
