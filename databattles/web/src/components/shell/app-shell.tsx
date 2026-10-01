"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bell,
  BookOpen,
  ChevronDown,
  Database,
  FolderGit2,
  Gavel,
  GitPullRequest,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  MessagesSquare,
  Monitor,
  Moon,
  Search,
  Settings,
  Shield,
  ShieldAlert,
  Sparkles,
  Sun,
  Swords,
  Trophy,
  University,
  User,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
import { DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { hasRole, useConfig, useMe, useSignOut } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { CommandPalette } from "./command-palette";
import { useTheme } from "./theme";

const NAV = [
  { href: "/competitions", label: "Competitions", icon: Trophy },
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/learn", label: "Learn", icon: BookOpen },
  { href: "/projects", label: "Projects", icon: FolderGit2 },
  { href: "/open-source", label: "Open source", icon: GitPullRequest },
  { href: "/discussions", label: "Discussions", icon: MessagesSquare },
  { href: "/orgs", label: "Universities", icon: University },
];

/** Routes rendered without the global chrome (focused flows). */
const BARE = ["/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/onboarding"];

function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const next = pref === "dark" ? "light" : pref === "light" ? "system" : "dark";
  const Icon = pref === "dark" ? Moon : pref === "light" ? Sun : Monitor;
  return (
    <Button variant="ghost" size="icon" aria-label={`Theme: ${pref}. Switch to ${next}`} title={`Theme: ${pref}`} onClick={() => setPref(next)}>
      <Icon className="h-4 w-4" />
    </Button>
  );
}

function NotificationBell() {
  const unread = useQuery({ queryKey: qk.unread, queryFn: () => get<{ count: number }>("/notifications/unread-count"), refetchInterval: 60_000 });
  const n = unread.data?.count ?? 0;
  return (
    <Link href="/notifications" className="relative inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] text-muted hover:bg-surface-2 hover:text-fg" aria-label={n ? `Notifications, ${n} unread` : "Notifications"}>
      <Bell className="h-4 w-4" />
      {n > 0 ? (
        <span className="absolute right-1 top-1 min-w-4 rounded-full bg-accent px-1 text-center text-[10px] font-semibold leading-4 text-accent-fg">{n > 99 ? "99+" : n}</span>
      ) : null}
    </Link>
  );
}

function UserMenu() {
  const me = useMe().data;
  const signOut = useSignOut();
  if (!me) return null;
  const managesOrg = me.memberships.some((m) => ["owner", "admin", "manager"].includes(m.role) && m.status === "active");
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="flex items-center gap-2 rounded-full p-0.5 pr-2 hover:bg-surface-2" aria-label="Account menu">
          <Avatar name={me.display_name} src={me.avatar_url} size={30} />
          <ChevronDown className="hidden h-3.5 w-3.5 text-subtle sm:block" />
        </button>
      </MenuTrigger>
      <MenuContent className="w-60">
        <MenuLabel>
          <span className="block truncate text-sm font-medium text-fg">{me.display_name}</span>
          <span className="block truncate">@{me.handle}</span>
        </MenuLabel>
        <MenuSeparator />
        <Link href="/dashboard"><MenuItem><LayoutDashboard className="h-4 w-4" /> Dashboard</MenuItem></Link>
        <Link href={`/u/${me.handle}`}><MenuItem><User className="h-4 w-4" /> Public profile</MenuItem></Link>
        <Link href="/organize"><MenuItem><Swords className="h-4 w-4" /> Organizer tools</MenuItem></Link>
        <Link href="/judge"><MenuItem><Gavel className="h-4 w-4" /> Judging</MenuItem></Link>
        {managesOrg ? <Link href="/orgs/mine"><MenuItem><University className="h-4 w-4" /> My organizations</MenuItem></Link> : null}
        {hasRole(me, "moderator") ? <Link href="/moderation"><MenuItem><ShieldAlert className="h-4 w-4" /> Moderation</MenuItem></Link> : null}
        {hasRole(me, "platform_admin") && me.platform_roles.includes("platform_admin") ? (
          <Link href="/admin"><MenuItem><Shield className="h-4 w-4" /> Admin</MenuItem></Link>
        ) : null}
        <Link href="/settings"><MenuItem><Settings className="h-4 w-4" /> Settings</MenuItem></Link>
        <MenuSeparator />
        <MenuItem onSelect={() => void signOut()}><LogOut className="h-4 w-4" /> Sign out</MenuItem>
      </MenuContent>
    </Menu>
  );
}

function TopNav({ onSearch }: { onSearch: () => void }) {
  const pathname = usePathname();
  const me = useMe();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <X className="h-5 w-5" /> : <MenuIcon className="h-5 w-5" />}
        </button>
        <Link href={me.data ? "/dashboard" : "/"} className="flex items-center gap-2 font-semibold tracking-tight text-fg">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-cyan text-accent-fg">
            <Sparkles className="h-4 w-4" aria-hidden />
          </span>
          <span className="hidden sm:inline">DataBattles</span>
        </Link>
        <nav className="ml-4 hidden items-center gap-0.5 lg:flex" aria-label="Main">
          {NAV.map((n) => {
            const active = pathname === n.href || pathname.startsWith(n.href + "/");
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={cn("rounded-md px-2.5 py-1.5 text-sm transition-colors", active ? "bg-surface-2 text-fg" : "text-muted hover:text-fg")}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-1.5">
          <button onClick={onSearch}
            className="hidden h-9 items-center gap-2 rounded-[var(--radius-md)] border border-border bg-surface px-3 text-sm text-subtle hover:border-border-strong hover:text-fg md:flex"
            aria-label="Search (Ctrl K)">
            <Search className="h-4 w-4" /> Search
            <kbd className="ml-6 rounded border border-border px-1.5 font-mono text-[10px]">⌘K</kbd>
          </button>
          <Button variant="ghost" size="icon" className="md:hidden" aria-label="Search" onClick={onSearch}><Search className="h-4 w-4" /></Button>
          <ThemeToggle />
          {me.isPending ? <span className="skeleton h-8 w-8 rounded-full" /> : me.data ? (
            <>
              <NotificationBell />
              <UserMenu />
            </>
          ) : (
            <>
              <LinkButton href="/login" variant="ghost" size="sm">Sign in</LinkButton>
              <LinkButton href="/signup" size="sm" className="hidden sm:inline-flex">Get started</LinkButton>
            </>
          )}
        </div>
      </div>
      {open ? (
        <nav className="border-t border-border bg-bg px-4 py-3 lg:hidden" aria-label="Main mobile">
          <ul className="grid grid-cols-2 gap-1">
            {NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className="flex items-center gap-2 rounded-md px-3 py-2.5 text-sm text-muted hover:bg-surface-2 hover:text-fg">
                  <n.icon className="h-4 w-4" /> {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
    </header>
  );
}

function Footer() {
  const config = useConfig().data;
  return (
    <footer className="mt-24 border-t border-border">
      <div className="mx-auto grid max-w-7xl gap-8 px-4 py-10 text-sm sm:grid-cols-2 sm:px-6 lg:grid-cols-4 lg:px-8">
        <div>
          <p className="font-semibold text-fg">DataBattles</p>
          <p className="mt-2 text-muted">Learn → build → compete → contribute → verify → showcase.</p>
          {config?.demo_mode ? <DemoBadge className="mt-3" /> : null}
        </div>
        <div>
          <p className="font-medium text-fg">Platform</p>
          <ul className="mt-2 space-y-1.5 text-muted">
            <li><Link href="/competitions" className="hover:text-fg">Competitions</Link></li>
            <li><Link href="/learn" className="hover:text-fg">Courses</Link></li>
            <li><Link href="/open-source" className="hover:text-fg">Open source hub</Link></li>
            <li><Link href="/verify" className="hover:text-fg">Verify a certificate</Link></li>
          </ul>
        </div>
        <div>
          <p className="font-medium text-fg">Organizers</p>
          <ul className="mt-2 space-y-1.5 text-muted">
            <li><Link href="/organize" className="hover:text-fg">Host a competition</Link></li>
            <li><Link href="/orgs" className="hover:text-fg">Universities &amp; clubs</Link></li>
            <li><Link href="/pricing" className="hover:text-fg">Plans</Link></li>
          </ul>
        </div>
        <div>
          <p className="font-medium text-fg">Trust</p>
          <ul className="mt-2 space-y-1.5 text-muted">
            <li><Link href="/guidelines" className="hover:text-fg">Community guidelines</Link></li>
            <li><Link href="/privacy" className="hover:text-fg">Privacy</Link></li>
            <li><Link href="/terms" className="hover:text-fg">Terms</Link></li>
          </ul>
        </div>
      </div>
    </footer>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
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
  const bare = BARE.some((p) => pathname === p || pathname.startsWith(p + "/"));
  if (bare) {
    return (
      <main id="main" className="min-h-dvh">
        {children}
      </main>
    );
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <TopNav onSearch={() => setPaletteOpen(true)} />
      <main id="main" className="flex-1">
        {children}
      </main>
      <Footer />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
