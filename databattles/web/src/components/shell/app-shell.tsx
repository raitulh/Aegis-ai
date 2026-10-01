"use client";

import * as RD from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bell,
  BookOpen,
  Check,
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
  Sun,
  Swords,
  Trophy,
  University,
  User,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { Logo, LogoMark } from "@/components/brand/logo";
import { Avatar } from "@/components/ui/avatar";
import { DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { AmbientBackground } from "@/components/visual/ambient-background";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { hasRole, useConfig, useMe, useSignOut } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { CommandPalette } from "./command-palette";
import { useTheme } from "./theme";

const NAV = [
  { href: "/competitions", label: "Competitions", icon: Trophy, blurb: "Challenges & leaderboards" },
  { href: "/datasets", label: "Datasets", icon: Database, blurb: "Versioned, licensed data" },
  { href: "/learn", label: "Learn", icon: BookOpen, blurb: "Courses & challenges" },
  { href: "/projects", label: "Projects", icon: FolderGit2, blurb: "Student work" },
  { href: "/open-source", label: "Open source", icon: GitPullRequest, blurb: "Good first issues" },
  { href: "/discussions", label: "Discussions", icon: MessagesSquare, blurb: "Community threads" },
  { href: "/orgs", label: "Universities", icon: University, blurb: "Universities & clubs" },
];

/** Routes rendered without the global chrome (focused flows). */
const BARE = ["/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/onboarding"];

const isActive = (pathname: string, href: string) => pathname === href || pathname.startsWith(href + "/");

/* ------------------------------------------------------------------ Theme */

const THEME_OPTIONS = [
  { value: "dark", label: "Dark", icon: Moon },
  { value: "light", label: "Light", icon: Sun },
  { value: "system", label: "System", icon: Monitor },
] as const;

function ThemeMenu() {
  const { pref, setPref } = useTheme();
  const Current = pref === "dark" ? Moon : pref === "light" ? Sun : Monitor;
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Theme: ${pref}. Change theme`} title={`Theme: ${pref}`}>
          <Current className="h-4 w-4" />
        </Button>
      </MenuTrigger>
      <MenuContent className="w-44">
        <MenuLabel>Appearance</MenuLabel>
        {THEME_OPTIONS.map((o) => (
          <MenuItem key={o.value} onSelect={() => setPref(o.value)}>
            <o.icon className="h-4 w-4" /> {o.label}
            {pref === o.value ? <Check className="ml-auto h-3.5 w-3.5 text-accent-strong!" aria-label="Selected" /> : null}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

function ThemeSegmented() {
  const { pref, setPref } = useTheme();
  return (
    <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-1 rounded-[var(--radius-md)] border border-border bg-bg-elevated p-1">
      {THEME_OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={pref === o.value}
          onClick={() => setPref(o.value)}
          className={cn(
            "flex items-center justify-center gap-1.5 rounded-[8px] py-2 text-[13px] font-medium transition-colors",
            pref === o.value ? "bg-surface-3 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]" : "text-muted hover:text-fg",
          )}
        >
          <o.icon className="h-3.5 w-3.5" /> {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Account */

function NotificationBell() {
  const unread = useQuery({ queryKey: qk.unread, queryFn: () => get<{ count: number }>("/notifications/unread-count"), refetchInterval: 60_000 });
  const n = unread.data?.count ?? 0;
  return (
    <Link
      href="/notifications"
      className="relative inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] text-muted transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
      aria-label={n ? `Notifications, ${n} unread` : "Notifications"}
    >
      <Bell className="h-4 w-4" />
      {n > 0 ? (
        <span className="tabular absolute right-0.5 top-0.5 min-w-4 rounded-full bg-accent-fill px-1 text-center text-[10px] font-semibold leading-4 text-accent-fg shadow-[0_0_0_2px_var(--bg)] animate-pop">
          {n > 99 ? "99+" : n}
        </span>
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
        <button
          className="flex items-center gap-1.5 rounded-full p-0.5 pr-1.5 transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] sm:pr-2"
          aria-label="Account menu"
        >
          <span className="rounded-full bg-brand p-px">
            <Avatar name={me.display_name} src={me.avatar_url} size={28} className="ring-2 ring-bg" />
          </span>
          <ChevronDown className="hidden h-3.5 w-3.5 text-subtle sm:block" />
        </button>
      </MenuTrigger>
      <MenuContent className="w-64">
        <div className="flex items-center gap-3 px-2.5 py-2.5">
          <Avatar name={me.display_name} src={me.avatar_url} size={36} />
          <div className="min-w-0">
            <span className="block truncate text-sm font-medium text-fg">{me.display_name}</span>
            <span className="block truncate text-xs text-subtle">@{me.handle}</span>
          </div>
        </div>
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

/* ------------------------------------------------------------------ Navigation */

/** Desktop links with a pill that glides to the hovered link and rests on the active one. */
function DesktopNav({ pathname }: { pathname: string }) {
  const listRef = useRef<HTMLDivElement>(null);
  const linkRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const [hover, setHover] = useState<number | null>(null);
  const [pill, setPill] = useState<{ left: number; width: number; visible: boolean }>({ left: 0, width: 0, visible: false });
  const activeIdx = NAV.findIndex((n) => isActive(pathname, n.href));
  const target = hover ?? (activeIdx >= 0 ? activeIdx : null);

  const measure = useCallback(() => {
    const el = target === null ? null : linkRefs.current[target];
    if (!el) {
      setPill((p) => ({ ...p, visible: false }));
      return;
    }
    setPill({ left: el.offsetLeft, width: el.offsetWidth, visible: true });
  }, [target]);

  useLayoutEffect(() => {
    measure();
  }, [measure]);

  useEffect(() => {
    const ro = new ResizeObserver(() => measure());
    if (listRef.current) ro.observe(listRef.current);
    return () => ro.disconnect();
  }, [measure]);

  return (
    <nav className="ml-1 hidden min-w-0 lg:block xl:ml-3" aria-label="Main">
      <div ref={listRef} className="relative flex items-center gap-0.5" onPointerLeave={() => setHover(null)}>
        <span
          aria-hidden
          className={cn(
            "absolute top-1/2 h-8 -translate-y-1/2 rounded-[9px] border border-border bg-surface-2/80 shadow-[inset_0_1px_0_var(--hairline-highlight)] transition-[left,width,opacity] duration-300 ease-out-expo",
            pill.visible ? "opacity-100" : "opacity-0",
          )}
          style={{ left: pill.left, width: pill.width }}
        />
        {NAV.map((n, i) => {
          const active = i === activeIdx;
          return (
            <Link
              key={n.href}
              href={n.href}
              ref={(el) => {
                linkRefs.current[i] = el;
              }}
              aria-current={active ? "page" : undefined}
              onPointerEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              className={cn(
                "relative z-10 whitespace-nowrap rounded-[9px] px-2.5 py-1.5 text-[13.5px] font-medium transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)] xl:px-3.5",
                active ? "text-fg" : "text-muted hover:text-fg",
              )}
            >
              {n.label}
              {active ? <span aria-hidden className="absolute inset-x-3 bottom-[3px] h-px bg-brand opacity-70" /> : null}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

function MobileNav({
  open,
  onOpenChange,
  onSearch,
  pathname,
  returnFocusRef,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSearch: () => void;
  pathname: string;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const me = useMe().data;
  const signOut = useSignOut();
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-50 bg-[rgb(3_4_8/0.55)] backdrop-blur-sm data-[state=open]:animate-fade-in lg:hidden" />
        <RD.Content
          // The opener lives outside the dialog (no Radix trigger), so hand focus back to it explicitly.
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            returnFocusRef.current?.focus();
          }}
          className="fixed inset-x-2 top-2 z-50 max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-[var(--radius-xl)] border border-border-strong bg-surface/95 p-4 shadow-elevated backdrop-blur-xl outline-none data-[state=open]:animate-[menu-in_220ms_var(--ease-out)_both] lg:hidden">
          <div className="flex items-center justify-between">
            <Logo />
            <RD.Close className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-fg" aria-label="Close menu">
              <X className="h-5 w-5" />
            </RD.Close>
          </div>
          <RD.Title className="sr-only">Navigation</RD.Title>
          <RD.Description className="sr-only">Browse DataBattles sections and account pages.</RD.Description>

          <button
            type="button"
            onClick={() => {
              onOpenChange(false);
              onSearch();
            }}
            className="mt-4 flex h-11 w-full items-center gap-2.5 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 text-sm text-subtle"
          >
            <Search className="h-4 w-4" /> Search DataBattles…
          </button>

          <nav aria-label="Main mobile" className="mt-4">
            <p className="mb-2 px-1 text-eyebrow text-subtle">Explore</p>
            <ul className="grid grid-cols-1 gap-1 min-[400px]:grid-cols-2">
              {NAV.map((n, i) => {
                const active = isActive(pathname, n.href);
                return (
                  <li key={n.href} className="animate-rise" style={{ animationDelay: `${i * 30}ms` }}>
                    <Link
                      href={n.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 transition-colors",
                        active ? "border-border-strong bg-surface-2 text-fg" : "border-transparent text-muted hover:bg-surface-2 hover:text-fg",
                      )}
                    >
                      <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border", active ? "bg-accent-soft text-accent-strong" : "bg-bg-elevated")}>
                        <n.icon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{n.label}</span>
                        <span className="block truncate text-[11.5px] text-subtle">{n.blurb}</span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          {me ? (
            <div className="mt-5">
              <p className="mb-2 px-1 text-eyebrow text-subtle">You</p>
              <ul className="grid grid-cols-2 gap-1 text-sm">
                {[
                  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
                  { href: `/u/${me.handle}`, label: "Profile", icon: User },
                  { href: "/notifications", label: "Notifications", icon: Bell },
                  { href: "/settings", label: "Settings", icon: Settings },
                ].map((l) => (
                  <li key={l.href}>
                    <Link href={l.href} className="flex items-center gap-2.5 rounded-[var(--radius-md)] px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-fg">
                      <l.icon className="h-4 w-4" /> {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-5">
            <p className="mb-2 px-1 text-eyebrow text-subtle">Appearance</p>
            <ThemeSegmented />
          </div>

          <div className="mt-5 flex gap-2 border-t border-border pt-4">
            {me ? (
              <Button variant="secondary" className="w-full" icon={<LogOut className="h-4 w-4" />} onClick={() => void signOut()}>
                Sign out
              </Button>
            ) : (
              <>
                <LinkButton href="/login" variant="secondary" className="flex-1">Sign in</LinkButton>
                <LinkButton href="/signup" className="flex-1">Get started</LinkButton>
              </>
            )}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

function TopNav({ onSearch }: { onSearch: () => void }) {
  const pathname = usePathname();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className="sticky top-0 z-40 lg:px-4 lg:pt-3">
      <div
        className={cn(
          "mx-auto flex h-14 max-w-7xl items-center gap-2 border-b px-4 transition-[background-color,border-color,box-shadow] duration-300 sm:px-5",
          "lg:rounded-[18px] lg:border lg:px-3 lg:pl-4",
          scrolled
            ? "border-border bg-[var(--glass-strong)] shadow-elevated backdrop-blur-xl backdrop-saturate-150"
            : "border-border bg-[var(--glass)] backdrop-blur-lg lg:border-[color-mix(in_oklab,var(--border)_70%,transparent)] lg:shadow-[inset_0_1px_0_var(--hairline-highlight)]",
        )}
      >
        <Link
          href={me.data ? "/dashboard" : "/"}
          className="flex shrink-0 items-center rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--ring)]"
          aria-label={me.data ? "DataBattles — dashboard" : "DataBattles — home"}
        >
          <span className="sm:hidden"><LogoMark /></span>
          <span className="hidden sm:inline-flex"><Logo /></span>
        </Link>

        <DesktopNav pathname={pathname} />

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={onSearch}
            className="group hidden h-9 items-center gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated/70 pl-3 pr-1.5 text-[13px] text-subtle transition-colors hover:border-border-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] md:flex lg:hidden xl:flex"
            aria-label="Search (Ctrl K)"
          >
            <Search className="h-3.5 w-3.5" /> <span className="pr-6">Search…</span>
            <kbd className="flex h-6 items-center rounded-md border border-border bg-surface-2 px-1.5 font-mono text-[10.5px] text-muted">⌘K</kbd>
          </button>
          <Button variant="ghost" size="icon" className="md:hidden lg:inline-flex xl:hidden" aria-label="Search (Ctrl K)" onClick={onSearch}>
            <Search className="h-4 w-4" />
          </Button>
          <span className="hidden lg:inline-flex"><ThemeMenu /></span>
          {me.isPending ? (
            <span className="skeleton h-8 w-8 rounded-full" />
          ) : me.data ? (
            <>
              <NotificationBell />
              <UserMenu />
            </>
          ) : (
            <>
              <LinkButton href="/login" variant="ghost" size="sm" className="px-2.5 min-[380px]:px-3">Sign in</LinkButton>
              <LinkButton href="/signup" size="sm" className="hidden sm:inline-flex">Get started</LinkButton>
            </>
          )}
          <button
            ref={menuButtonRef}
            className="ml-0.5 inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-md)] text-muted transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] lg:hidden"
            aria-label="Open menu"
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            <MenuIcon className="h-5 w-5" />
          </button>
        </div>
      </div>
      <MobileNav open={open} onOpenChange={setOpen} onSearch={onSearch} pathname={pathname} returnFocusRef={menuButtonRef} />
    </header>
  );
}

/* ------------------------------------------------------------------ Footer */

function FooterColumn({ title, links }: { title: string; links: { href: string; label: string }[] }) {
  return (
    <div>
      <p className="text-eyebrow text-subtle">{title}</p>
      <ul className="mt-4 space-y-2.5 text-sm">
        {links.map((l) => (
          <li key={l.href}>
            <Link href={l.href} className="group inline-flex items-center gap-1 text-muted transition-colors hover:text-fg">
              {l.label}
              <ArrowUpRight className="h-3 w-3 -translate-x-1 opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

const LOOP_WORDS = ["Learn", "Build", "Compete", "Contribute", "Verify", "Showcase"];

function Footer() {
  const config = useConfig().data;
  return (
    <footer className="relative mt-28 overflow-hidden border-t border-border">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-50" />
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 left-1/2 h-80 w-[60rem] max-w-full -translate-x-1/2"
        style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
      />
      <div className="relative mx-auto grid max-w-7xl gap-10 px-4 py-14 sm:grid-cols-2 sm:px-6 lg:grid-cols-[1.4fr_1fr_1fr_1fr] lg:px-8">
        <div>
          <Logo />
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-muted">
            Competitions, datasets, courses and open-source work for universities — with results anyone can verify.
          </p>
          <p className="mt-5 flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono text-[11px] uppercase tracking-[0.12em] text-subtle">
            {LOOP_WORDS.map((w, i) => (
              <span key={w} className="inline-flex items-center gap-1.5">
                {i ? <span aria-hidden className="text-border-strong">→</span> : null}
                {w}
              </span>
            ))}
          </p>
          {config?.demo_mode ? <DemoBadge className="mt-5" /> : null}
        </div>
        <FooterColumn
          title="Platform"
          links={[
            { href: "/competitions", label: "Competitions" },
            { href: "/datasets", label: "Datasets" },
            { href: "/learn", label: "Courses" },
            { href: "/projects", label: "Projects" },
            { href: "/open-source", label: "Open source hub" },
            { href: "/verify", label: "Verify a certificate" },
          ]}
        />
        <FooterColumn
          title="Organizers"
          links={[
            { href: "/organize", label: "Host a competition" },
            { href: "/orgs", label: "Universities & clubs" },
            { href: "/pricing", label: "Plans" },
          ]}
        />
        <FooterColumn
          title="Trust"
          links={[
            { href: "/guidelines", label: "Community guidelines" },
            { href: "/privacy", label: "Privacy" },
            { href: "/terms", label: "Terms" },
          ]}
        />
      </div>
      <div className="relative border-t border-border">
        <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-5 text-xs text-subtle sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
          <span>DataBattles — learn, build and compete in AI.</span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden /> Results backed by evidence, verifiable by anyone.
          </span>
        </div>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------------ Shell */

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
        <AmbientBackground intense />
        {children}
      </main>
    );
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <AmbientBackground />
      <TopNav onSearch={() => setPaletteOpen(true)} />
      <main id="main" className="flex-1">
        {children}
      </main>
      <Footer />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
