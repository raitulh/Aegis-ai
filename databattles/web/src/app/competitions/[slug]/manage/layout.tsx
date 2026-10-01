"use client";

import {
  BarChart3,
  CalendarClock,
  ClipboardList,
  FlaskConical,
  Gavel,
  LayoutDashboard,
  Megaphone,
  Settings2,
  Trophy,
  UserCog,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { useCompetitionDetail, useManage } from "@/components/organizer/shared";
import { DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { NotFoundState, PermissionDenied, Skeleton, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";

/** Console sections grouped by job. Visibility rules are unchanged; grouping is presentational only. */
function navItems(slug: string, scoringMode: string) {
  const base = `/competitions/${slug}/manage`;
  return [
    { group: "Run", href: base, label: "Overview", icon: LayoutDashboard, exact: true },
    { group: "Run", href: `${base}/participants`, label: "Participants", icon: Users },
    { group: "Run", href: `${base}/submissions`, label: "Submissions", icon: ClipboardList, hidden: scoringMode !== "automatic" },
    { group: "Run", href: `${base}/announcements`, label: "Announcements", icon: Megaphone },
    { group: "Configure", href: `${base}/settings`, label: "Settings", icon: Settings2 },
    { group: "Configure", href: `${base}/evaluation`, label: "Evaluation", icon: FlaskConical, hidden: scoringMode !== "automatic" },
    { group: "Configure", href: `${base}/judging`, label: "Judging", icon: Gavel, hidden: scoringMode !== "judged" },
    { group: "Configure", href: `${base}/schedule`, label: "Schedule & awards", icon: CalendarClock },
    { group: "Configure", href: `${base}/people`, label: "Staff & sponsors", icon: UserCog },
    { group: "Outcomes", href: `${base}/results`, label: "Results & certificates", icon: Trophy },
    { group: "Outcomes", href: `${base}/analytics`, label: "Analytics", icon: BarChart3 },
  ].filter((i) => !i.hidden);
}

const SCORING_LABEL: Record<string, string> = { automatic: "Automatic scoring", judged: "Judged", none: "No scoring" };

function ManageNav({ slug, scoringMode }: { slug: string; scoringMode: string }) {
  const pathname = usePathname();
  const items = navItems(slug, scoringMode);
  const isActive = (i: (typeof items)[number]) => (i.exact ? pathname === i.href : pathname === i.href || pathname.startsWith(i.href + "/"));
  const groups = [...new Set(items.map((i) => i.group))];
  // Keep the active pill visible in the horizontal row (adjusts only that row's scroll, never the page).
  const pillsRef = useRef<HTMLUListElement>(null);
  useEffect(() => {
    const row = pillsRef.current;
    const active = row?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!row || !active || row.scrollWidth <= row.clientWidth) return;
    row.scrollLeft += active.getBoundingClientRect().left - row.getBoundingClientRect().left - 16;
  }, [pathname]);
  return (
    <>
      {/* Mobile & tablet: one scrollable row of pills. */}
      <nav aria-label="Manage competition" className="lg:hidden">
        <ul ref={pillsRef} className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:-mx-6 sm:px-6">
          {items.map((i) => {
            const active = isActive(i);
            return (
              <li key={i.href} className="shrink-0">
                <Link
                  href={i.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex h-9 items-center gap-2 whitespace-nowrap rounded-full border px-3.5 text-[13px] font-medium transition-colors duration-200",
                    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
                    active
                      ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] bg-accent-soft text-fg"
                      : "border-border bg-surface text-muted hover:border-border-strong hover:text-fg",
                  )}
                >
                  <i.icon className={cn("h-3.5 w-3.5", active ? "text-accent-strong" : "text-subtle")} aria-hidden />
                  {i.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Desktop: grouped rail. */}
      <nav aria-label="Manage competition" className="hidden lg:block">
        <div className="space-y-5">
          {groups.map((g) => (
            <div key={g}>
              <p className="mb-1.5 px-3 text-eyebrow text-subtle">{g}</p>
              <ul className="space-y-0.5">
                {items
                  .filter((i) => i.group === g)
                  .map((i) => {
                    const active = isActive(i);
                    return (
                      <li key={i.href}>
                        <Link
                          href={i.href}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "group relative flex items-center gap-2.5 rounded-[var(--radius-md)] px-3 py-2 text-sm transition-colors duration-200",
                            "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                            active ? "bg-surface-2 font-medium text-fg shadow-[inset_0_1px_0_var(--hairline-highlight)]" : "text-muted hover:bg-surface-2/70 hover:text-fg",
                          )}
                        >
                          <span
                            aria-hidden
                            className={cn(
                              "absolute inset-y-2 left-0 w-0.5 rounded-full bg-brand transition-opacity duration-200",
                              active ? "opacity-100" : "opacity-0",
                            )}
                          />
                          <i.icon className={cn("h-4 w-4 shrink-0", active ? "text-accent-strong" : "text-subtle group-hover:text-muted")} aria-hidden />
                          <span className="truncate">{i.label}</span>
                        </Link>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}

/** Who/what is being managed: stays in view in the sticky rail (desktop) and above the pills (mobile). */
function IdentityCard({ title, status, lifecycle, scoringMode, isDemo, configVersion }: {
  title: string;
  status: string;
  lifecycle: string;
  scoringMode: string;
  isDemo: boolean;
  configVersion?: number;
}) {
  return (
    <div className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-3.5 shadow-card">
      <div aria-hidden className="pointer-events-none absolute inset-x-4 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
      <p className="flex items-center gap-1.5 text-eyebrow text-accent-strong">
        <span className="relative flex h-1.5 w-1.5" aria-hidden>
          <span className="absolute inset-0 rounded-full bg-brand" />
        </span>
        Organizer console
      </p>
      <p className="mt-2 line-clamp-2 text-sm font-semibold leading-snug tracking-[-0.01em] text-fg" title={title}>{title}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={status} />
        {isDemo ? <DemoBadge /> : null}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border text-xs">
        <div className="min-w-0 bg-bg-elevated/70 px-2.5 py-2">
          <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Lifecycle</dt>
          <dd className="mt-0.5 truncate font-medium text-fg">{titleCase(lifecycle)}</dd>
        </div>
        <div className="min-w-0 bg-bg-elevated/70 px-2.5 py-2">
          <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Rules</dt>
          <dd className="tabular mt-0.5 truncate font-medium text-fg">{configVersion ? `v${configVersion}` : "—"}</dd>
        </div>
        <div className="col-span-2 min-w-0 bg-bg-elevated/70 px-2.5 py-2">
          <dt className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-subtle">Scoring</dt>
          <dd className="mt-0.5 truncate font-medium text-fg">{SCORING_LABEL[scoringMode] ?? titleCase(scoringMode)}</dd>
        </div>
      </dl>
    </div>
  );
}

function MobileIdentity({ status, lifecycle, scoringMode, isDemo }: { status: string; lifecycle: string; scoringMode: string; isDemo: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <p className="text-eyebrow text-accent-strong">Organizer console</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusBadge status={status} />
        {isDemo ? <DemoBadge /> : null}
        <span className="text-xs text-subtle">
          {titleCase(lifecycle)} · {SCORING_LABEL[scoringMode] ?? titleCase(scoringMode)}
        </span>
      </div>
    </div>
  );
}

export default function ManageLayout({ children }: { children: ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const detail = useCompetitionDetail(slug);

  if (manage.isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-[232px_minmax(0,1fr)] lg:gap-8" role="status" aria-label="Loading organizer tools">
        <div className="flex gap-2 overflow-hidden lg:flex-col">
          <Skeleton className="hidden h-36 w-full rounded-[var(--radius-lg)] lg:block" />
          {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-9 w-28 shrink-0 rounded-full lg:w-full lg:rounded-[var(--radius-md)]" />)}
        </div>
        <div className="space-y-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-24 w-full rounded-[var(--radius-lg)]" />
          <Skeleton className="h-56 w-full rounded-[var(--radius-lg)]" />
        </div>
      </div>
    );
  }

  if (manage.isError) {
    const e = manage.error;
    if (e instanceof ApiError && e.status === 401) return <PermissionDenied signIn />;
    if (e instanceof ApiError && e.status === 403) {
      return (
        <div className="space-y-4">
          <PermissionDenied message="Only organizers of this competition can open the organizer tools. Ask an organizer to add you as staff." />
          {detail.data?.viewer.roles.includes("judge") ? (
            <div className="flex justify-center">
              <LinkButton href={`/judge/${slug}`} variant="secondary" icon={<Gavel className="h-4 w-4" />}>Open your judging queue</LinkButton>
            </div>
          ) : null}
        </div>
      );
    }
    if (e instanceof ApiError && e.status === 404) return <NotFoundState what="competition" />;
    return <ErrorState error={e} onRetry={() => manage.refetch()} />;
  }

  const scoringMode = String(manage.data.raw.scoring_mode ?? "automatic");
  const m = manage.data;
  const status = detail.data?.status ?? m.status;
  const isDemo = Boolean(detail.data?.is_demo);
  return (
    <div className="grid gap-5 lg:grid-cols-[232px_minmax(0,1fr)] lg:gap-8">
      <aside className="min-w-0 space-y-3 lg:sticky lg:top-[7.75rem] lg:max-h-[calc(100dvh-8.75rem)] lg:self-start lg:overflow-y-auto lg:pb-4 lg:[scrollbar-width:thin]">
        <div className="lg:hidden">
          <MobileIdentity status={status} lifecycle={m.lifecycle} scoringMode={scoringMode} isDemo={isDemo} />
        </div>
        <div className="hidden lg:block">
          <IdentityCard title={m.title} status={status} lifecycle={m.lifecycle} scoringMode={scoringMode} isDemo={isDemo} configVersion={detail.data?.config_version} />
        </div>
        <ManageNav slug={slug} scoringMode={scoringMode} />
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
