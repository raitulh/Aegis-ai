"use client";

import * as RD from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  ArrowRight,
  BookOpen,
  Clock3,
  CornerDownLeft,
  Database,
  FileSearch,
  FolderGit2,
  Gavel,
  GitPullRequest,
  LayoutDashboard,
  Loader2,
  LogOut,
  MessagesSquare,
  Monitor,
  Moon,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  Trophy,
  University,
  User,
  Wind,
  type LucideIcon,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useDebounced, useMe, useSignOut } from "@/lib/hooks";
import { setReducedMotion, useTheme } from "./theme";

type Suggestion = { type: string; title: string; subtitle: string | null; url: string };

const TYPE_ICON: Record<string, LucideIcon> = {
  competition: Trophy,
  dataset: Database,
  project: FolderGit2,
  course: BookOpen,
  organization: University,
  user: User,
  thread: MessagesSquare,
};

const TYPE_LABEL: Record<string, string> = {
  competition: "Competitions",
  dataset: "Datasets",
  project: "Projects",
  course: "Courses",
  organization: "Organizations",
  user: "People",
  thread: "Discussions",
};

const RECENT_KEY = "db-palette-recent";
type Recent = { label: string; href: string; type: string };

function readRecent(): Recent[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const parsed = raw ? (JSON.parse(raw) as Recent[]) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 5) : [];
  } catch {
    return [];
  }
}

function pushRecent(item: Recent) {
  try {
    const next = [item, ...readRecent().filter((r) => r.href !== item.href)].slice(0, 5);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: recents are a convenience only */
  }
}

/** Case-insensitive "all words appear" match for local commands. */
function matches(label: string, q: string) {
  const words = q.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = label.toLowerCase();
  return words.every((w) => hay.includes(w));
}

type Cmd = { id: string; label: string; icon: LucideIcon; hint?: string; href?: string; run?: () => void; keywords?: string };

const itemCls =
  "group flex cursor-pointer items-center gap-3 rounded-[10px] px-3 py-2 text-sm text-fg outline-none transition-colors data-[selected=true]:bg-surface-2 data-[selected=true]:shadow-[inset_0_0_0_1px_var(--border)]";
const groupCls =
  "mt-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[10.5px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.14em] [&_[cmdk-group-heading]]:text-subtle";

function ItemIcon({ icon: Icon, tone = "neutral" }: { icon: LucideIcon; tone?: "neutral" | "accent" }) {
  return (
    <span
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border transition-colors",
        tone === "accent" ? "bg-accent-soft text-accent-strong" : "bg-bg-elevated text-subtle group-data-[selected=true]:text-accent-strong",
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
    </span>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-surface-2 px-1 font-mono text-[10px] text-muted">{children}</kbd>;
}

/** Commands relevant to the page you are on (e.g. the tabs of the competition you're viewing). */
function contextualCommands(pathname: string): { title: string; cmds: Cmd[] } | null {
  const comp = pathname.match(/^\/competitions\/([^/]+)/);
  if (comp && comp[1] !== "new") {
    const base = `/competitions/${comp[1]}`;
    return {
      title: "This competition",
      cmds: [
        { id: "ctx-overview", label: "Overview", icon: Trophy, href: base },
        { id: "ctx-data", label: "Data", icon: Database, href: `${base}/data` },
        { id: "ctx-lb", label: "Leaderboard", icon: ShieldCheck, href: `${base}/leaderboard` },
        { id: "ctx-subs", label: "My submissions", icon: FileSearch, href: `${base}/submissions` },
        { id: "ctx-team", label: "Team", icon: User, href: `${base}/team` },
        { id: "ctx-disc", label: "Discussion", icon: MessagesSquare, href: `${base}/discussion` },
      ],
    };
  }
  const course = pathname.match(/^\/learn\/([^/]+)/);
  if (course && course[1] !== "authoring") {
    return { title: "This course", cmds: [{ id: "ctx-course", label: "Course overview", icon: BookOpen, href: `/learn/${course[1]}` }] };
  }
  const org = pathname.match(/^\/orgs\/([^/]+)/);
  if (org && !["new", "mine", "join", "verify-email"].includes(org[1])) {
    return {
      title: "This organization",
      cmds: [
        { id: "ctx-org", label: "Organization page", icon: University, href: `/orgs/${org[1]}` },
        { id: "ctx-org-join", label: "Join this organization", icon: Plus, href: `/orgs/${org[1]}/join` },
      ],
    };
  }
  return null;
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const me = useMe().data;
  const signOut = useSignOut();
  const { setPref } = useTheme();
  const [q, setQ] = useState("");
  const [recent, setRecent] = useState<Recent[]>([]);
  const dq = useDebounced(q, 180);
  const suggestions = useQuery({
    queryKey: ["suggest", dq],
    queryFn: () => get<Suggestion[]>("/search/suggest", { q: dq }),
    enabled: open && dq.trim().length >= 2,
    staleTime: 30_000,
  });

  // Remember what had focus when the palette opened so closing it returns focus there (it has no Radix trigger).
  const restoreFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (open) {
      restoreFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setRecent(readRecent());
    }
  }, [open]);

  const close = () => {
    onOpenChange(false);
    setQ("");
  };

  const go = (href: string, label: string, type = "page") => {
    pushRecent({ href, label, type });
    close();
    router.push(href);
  };

  const navigate: Cmd[] = useMemo(
    () =>
      [
        { id: "dash", label: "Dashboard", href: "/dashboard", icon: LayoutDashboard, auth: true },
        { id: "comps", label: "Browse competitions", href: "/competitions", icon: Trophy },
        { id: "data", label: "Browse datasets", href: "/datasets", icon: Database },
        { id: "learn", label: "Courses", href: "/learn", icon: BookOpen },
        { id: "projects", label: "Student projects", href: "/projects", icon: FolderGit2 },
        { id: "issues", label: "Good first issues", href: "/open-source/issues", icon: GitPullRequest },
        { id: "disc", label: "Discussions", href: "/discussions", icon: MessagesSquare },
        { id: "orgs", label: "Universities & clubs", href: "/orgs", icon: University },
        { id: "verify", label: "Verify a certificate", href: "/verify", icon: ShieldCheck },
        { id: "judge", label: "Judging", href: "/judge", icon: Gavel, auth: true },
        { id: "settings", label: "Settings", href: "/settings", icon: Settings, auth: true },
      ].filter((a) => !a.auth || me),
    [me],
  );

  const create: Cmd[] = useMemo(
    () =>
      me
        ? [
            { id: "new-disc", label: "New discussion", href: "/discussions/new", icon: Plus },
            { id: "new-project", label: "New project", href: "/projects/new", icon: Plus },
            { id: "new-dataset", label: "New dataset", href: "/datasets/new", icon: Plus },
            { id: "new-comp", label: "Host a competition", href: "/organize/new", icon: Plus },
          ]
        : [],
    [me],
  );

  const prefs: Cmd[] = [
    { id: "theme-dark", label: "Theme: Dark", icon: Moon, run: () => setPref("dark"), keywords: "appearance mode" },
    { id: "theme-light", label: "Theme: Light", icon: Sun, run: () => setPref("light"), keywords: "appearance mode" },
    { id: "theme-system", label: "Theme: Match system", icon: Monitor, run: () => setPref("system"), keywords: "appearance mode" },
    {
      id: "motion",
      label: "Toggle reduced motion",
      icon: Wind,
      run: () => setReducedMotion(document.documentElement.dataset.motion !== "reduced"),
      keywords: "animation accessibility",
    },
    ...(me ? [{ id: "signout", label: "Sign out", icon: LogOut, run: () => void signOut(), keywords: "log out" }] : []),
  ];

  const ctx = contextualCommands(pathname);
  const query = q.trim();
  const filter = (list: Cmd[]) => list.filter((c) => matches(`${c.label} ${c.keywords ?? ""}`, query));
  const grouped = useMemo(() => {
    const out: Record<string, Suggestion[]> = {};
    for (const s of suggestions.data ?? []) (out[s.type] ??= []).push(s);
    return out;
  }, [suggestions.data]);

  const runCmd = (c: Cmd) => {
    if (c.href) go(c.href, c.label);
    else {
      close();
      c.run?.();
    }
  };

  const renderCmds = (title: string, list: Cmd[]) => {
    const shown = filter(list);
    if (!shown.length) return null;
    return (
      <Command.Group heading={title} className={groupCls}>
        {shown.map((c) => (
          <Command.Item key={c.id} value={c.id} onSelect={() => runCmd(c)} className={itemCls}>
            <ItemIcon icon={c.icon} />
            <span className="min-w-0 flex-1 truncate">{c.label}</span>
            {c.href ? <ArrowRight className="h-3.5 w-3.5 text-subtle opacity-0 transition-opacity group-data-[selected=true]:opacity-100" aria-hidden /> : null}
          </Command.Item>
        ))}
      </Command.Group>
    );
  };

  const searching = dq.trim().length >= 2;

  return (
    <RD.Root
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setQ("");
      }}
    >
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-50 bg-[rgb(3_4_8/0.6)] backdrop-blur-[6px] data-[state=open]:animate-fade-in" />
        <RD.Content
          onCloseAutoFocus={(e) => {
            const el = restoreFocus.current;
            if (el && el.isConnected) {
              e.preventDefault();
              el.focus();
            }
          }}
          className="fixed left-1/2 top-[10vh] z-50 w-[calc(100vw-1.5rem)] max-w-2xl -translate-x-1/2 overflow-hidden rounded-[var(--radius-xl)] border border-border-strong bg-surface/95 shadow-elevated outline-none backdrop-blur-xl data-[state=open]:animate-[menu-in_200ms_var(--ease-out)_both]">
          <RD.Title className="sr-only">Search and commands</RD.Title>
          <RD.Description className="sr-only">Search competitions, datasets, people and more, or jump to a page.</RD.Description>
          <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
          <Command shouldFilter={false} label="Command palette" className="flex flex-col">
            <div className="flex items-center gap-3 border-b border-border px-4">
              {suggestions.isFetching ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent-strong" aria-hidden />
              ) : (
                <Search className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
              )}
              <Command.Input
                value={q}
                onValueChange={setQ}
                placeholder="Search competitions, datasets, courses, people…"
                className="h-14 w-full bg-transparent text-[15px] text-fg outline-none placeholder:text-subtle"
              />
              <Kbd>esc</Kbd>
            </div>
            <Command.List className="max-h-[min(60vh,520px)] overflow-y-auto overscroll-contain p-2">
              <Command.Empty className="px-3 py-10 text-center text-sm text-subtle">
                {searching ? (suggestions.isFetching ? "Searching…" : "No matches. Try a different keyword.") : "No commands match."}
              </Command.Empty>

              {Object.entries(grouped).map(([type, items]) => (
                <Command.Group key={type} heading={TYPE_LABEL[type] ?? type} className={groupCls}>
                  {items.map((s) => {
                    const Icon = TYPE_ICON[s.type] ?? FileSearch;
                    return (
                      <Command.Item key={s.url} value={s.url} onSelect={() => go(s.url, s.title, s.type)} className={itemCls}>
                        <ItemIcon icon={Icon} tone="accent" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate">{s.title}</span>
                          {s.subtitle ? <span className="block truncate text-xs text-subtle">{s.subtitle}</span> : null}
                        </span>
                        <span className="hidden text-[11px] capitalize text-subtle sm:inline">{s.type}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ))}

              {searching ? (
                <Command.Group heading="Search" className={groupCls}>
                  <Command.Item value={`search:${dq}`} onSelect={() => go(`/search?q=${encodeURIComponent(dq)}`, `Search “${dq}”`, "search")} className={itemCls}>
                    <ItemIcon icon={FileSearch} />
                    <span className="min-w-0 flex-1 truncate">See all results for “{dq}”</span>
                    <CornerDownLeft className="h-3.5 w-3.5 text-subtle" aria-hidden />
                  </Command.Item>
                </Command.Group>
              ) : null}

              {!query && recent.length ? (
                <Command.Group heading="Recent" className={groupCls}>
                  {recent.map((r) => (
                    <Command.Item key={`recent-${r.href}`} value={`recent-${r.href}`} onSelect={() => go(r.href, r.label, r.type)} className={itemCls}>
                      <ItemIcon icon={Clock3} />
                      <span className="min-w-0 flex-1 truncate">{r.label}</span>
                      <span className="hidden text-[11px] capitalize text-subtle sm:inline">{r.type}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              ) : null}

              {ctx ? renderCmds(ctx.title, ctx.cmds) : null}
              {renderCmds("Navigate", navigate)}
              {renderCmds("Create", create)}
              {renderCmds("Preferences", prefs)}
            </Command.List>
            <div className="hidden items-center justify-between gap-4 border-t border-border bg-bg-elevated/50 px-4 py-2.5 text-[11px] text-subtle sm:flex">
              <span className="flex items-center gap-3">
                <span className="flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate</span>
                <span className="flex items-center gap-1"><Kbd>↵</Kbd> open</span>
                <span className="flex items-center gap-1"><Kbd>esc</Kbd> close</span>
              </span>
              <span className="flex items-center gap-1"><Kbd>⌘</Kbd><Kbd>K</Kbd> anywhere</span>
            </div>
          </Command>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
