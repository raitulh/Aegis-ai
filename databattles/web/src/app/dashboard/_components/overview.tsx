"use client";

import {
  Activity,
  ArrowRight,
  Award,
  BookOpen,
  Compass,
  Mail,
  Medal,
  Star,
  Target,
  Trophy,
  UserRoundCheck,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useId, useSyncExternalStore, type ReactNode } from "react";

import { CountUp } from "@/components/motion/count-up";
import type { DashboardData, PublicProfile } from "@/components/profile/types";
import { cn } from "@/lib/cn";
import { formatNumber, relativeTime } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { COMPLETION_ITEMS } from "./widgets";

// ----------------------------------------------------------------------------- greeting

const noopSubscribe = () => () => {};

/**
 * Time-of-day greeting. The server snapshot is `null`, so the server (and the hydration pass) render the neutral
 * "Welcome back"; the client's local hour is only read after mount — no hydration mismatch.
 */
export function useGreeting(): string {
  const hour = useSyncExternalStore(noopSubscribe, () => new Date().getHours(), () => null);
  if (hour === null) return "Welcome back";
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

// ----------------------------------------------------------------------------- metrics

interface Tile {
  key: string;
  label: string;
  icon: LucideIcon;
  value: number | null;
  format?: (n: number) => string;
  /** Count up on first view. Off for ranks: a rank animating up from #0 would show places that cannot exist. */
  animate?: boolean;
  hint: ReactNode;
  tone: string;
}

/**
 * The dashboard payload caps its in-progress course list (backend `users/service.py`, `.limit(4)`), so its length is
 * only ever shown as a lower bound — never as a total.
 */
const LEARNING_LIST_CAP = 4;
/** The profile's joined-competitions list is capped server-side; at the cap the count is a lower bound. */
const JOINED_LIST_CAP = 30;

const count = (n: number, one: string, many: string) => `${formatNumber(n)} ${n === 1 ? one : many}`;

/**
 * Command strip of real totals only: uncapped counts from the dashboard payload (active competitions, the 182-day
 * activity sum), or the viewer's own public-profile stats (top-10 finishes, badges, certificates, completed courses)
 * once that query resolves — a dash shows until then. Capped preview lists are never presented as totals.
 */
export function MetricsStrip({ d, profile }: { d: DashboardData; profile?: PublicProfile }) {
  const ranked = d.active_competitions.filter((c) => c.rank !== null).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const best = ranked[0];
  const activityTotal = Object.values(d.activity).reduce((a, b) => a + b, 0);
  const stats = profile?.stats;
  const inProgress = d.learning.length;
  const inProgressLabel = !inProgress
    ? "none in progress"
    : inProgress >= LEARNING_LIST_CAP
      ? `${formatNumber(LEARNING_LIST_CAP)}+ in progress`
      : `${formatNumber(inProgress)} in progress`;

  // The dashboard list holds every joined published competition (upcoming, active or ended): count the active ones.
  const activeCount = d.active_competitions.filter((c) => c.status === "active").length;
  const joined = stats
    ? stats.competitions >= JOINED_LIST_CAP
      ? `${formatNumber(JOINED_LIST_CAP)}+ joined`
      : `${formatNumber(stats.competitions)} joined`
    : null;
  const tiles: Tile[] = [
    {
      key: "competitions",
      label: "Competitions",
      icon: Trophy,
      value: activeCount,
      hint: joined ? `active now · ${joined}` : "active now",
      tone: "text-accent-strong",
    },
    {
      key: "rank",
      label: "Best rank",
      icon: Medal,
      value: best?.rank ?? null,
      format: (n) => `#${formatNumber(n)}`,
      animate: false,
      hint: best ? (
        <>
          of {best.ranked_teams ?? "—"} · <span className="text-muted">{best.title}</span>
        </>
      ) : (
        "No ranked entries yet"
      ),
      tone: "text-warning",
    },
    {
      key: "top10",
      label: "Top 10",
      icon: Star,
      value: stats ? stats.top10_finishes : null,
      hint: "finishes in final results",
      tone: "text-cyan",
    },
    {
      key: "achievements",
      label: "Achievements",
      icon: Award,
      value: stats ? stats.badges + stats.certificates : null,
      hint: stats ? `${count(stats.badges, "badge", "badges")} · ${count(stats.certificates, "certificate", "certificates")}` : "Badges & certificates",
      tone: "text-success",
    },
    {
      key: "learning",
      label: "Courses done",
      icon: BookOpen,
      value: stats ? stats.courses_completed : null,
      hint: `completed · ${inProgressLabel}`,
      tone: "text-info",
    },
    {
      key: "activity",
      label: "Activity",
      icon: Activity,
      value: activityTotal,
      hint: "events in the last 182 days",
      tone: "text-accent-strong",
    },
  ];

  return (
    <section aria-label="Your numbers" className="relative animate-rise">
      <div aria-hidden className="pointer-events-none absolute inset-x-8 top-0 z-10 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border shadow-card sm:grid-cols-3 xl:grid-cols-6">
        {tiles.map((t) => (
          <div key={t.key} className="group relative flex min-w-0 flex-col bg-surface px-3.5 py-4 transition-colors duration-300 hover:bg-surface-2/70 sm:px-5">
            <dt className="flex items-center gap-1.5 text-eyebrow tracking-[0.08em] text-subtle sm:gap-2 sm:tracking-[0.14em]">
              <t.icon className={cn("h-3.5 w-3.5 shrink-0", t.tone)} aria-hidden />
              <span className="truncate">{t.label}</span>
            </dt>
            <dd className="mt-3 text-[1.75rem] font-semibold leading-none tracking-[-0.035em] text-fg">
              {t.animate === false ? (
                <span className="tabular">{t.value === null ? "—" : (t.format ?? formatNumber)(t.value)}</span>
              ) : (
                <CountUp value={t.value} format={t.format} />
              )}
            </dd>
            <dd className="mt-2 line-clamp-2 text-xs leading-snug text-subtle">{t.hint}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// ----------------------------------------------------------------------------- up next

interface NextAction {
  key: string;
  icon: LucideIcon;
  title: string;
  detail: string;
  href: string;
}

/** Suggestions derived only from the dashboard payload — each points at a real place to act. */
function nextActions(d: DashboardData, now: number): NextAction[] {
  const out: NextAction[] = [];
  if (d.invitations.length) {
    const n = d.invitations.length;
    out.push({
      key: "invites",
      icon: Mail,
      title: `Respond to ${n} team invitation${n === 1 ? "" : "s"}`,
      detail: `${d.invitations[0].team_name} · ${d.invitations[0].competition.title}`,
      href: "/invites",
    });
  }
  // Only competitions you can still submit to: active and not yet past their end.
  const closing = d.active_competitions
    .filter((c) => c.status === "active" && c.ends_at && new Date(c.ends_at).getTime() > now)
    .sort((a, b) => String(a.ends_at).localeCompare(String(b.ends_at)))[0];
  if (closing) {
    out.push({
      key: "compete",
      icon: Target,
      // A missing best score does not prove there is no submission (judged events never get one, and the recent list
      // is capped), so only say "again" when a submission is known and stay neutral otherwise.
      title:
        closing.best_score !== null || d.recent_submissions.some((s) => s.competition.slug === closing.slug)
          ? `Submit again to ${closing.title}`
          : `Open submissions for ${closing.title}`,
      detail: [closing.rank ? `Rank #${closing.rank} of ${closing.ranked_teams ?? "—"}` : null, `closes ${relativeTime(closing.ends_at)}`].filter(Boolean).join(" · "),
      href: `/competitions/${closing.slug}/submissions`,
    });
  } else if (d.recommended[0]) {
    out.push({
      key: "explore",
      icon: Compass,
      title: `Explore ${d.recommended[0].title}`,
      detail: d.recommendation_basis === "declared_interests" ? "Matches your declared interests" : "Upcoming public competition",
      href: `/competitions/${d.recommended[0].slug}`,
    });
  } else {
    out.push({ key: "browse", icon: Compass, title: "Find a competition to join", detail: "Browse open competitions", href: "/competitions" });
  }
  const course = [...d.learning].filter((c) => c.progress_pct < 100).sort((a, b) => b.progress_pct - a.progress_pct)[0];
  if (course) {
    out.push({ key: "learn", icon: BookOpen, title: `Resume ${course.title}`, detail: `${Math.round(course.progress_pct)}% complete`, href: course.resume_url });
  }
  const missing = COMPLETION_ITEMS.find((i) => !d.profile_completion.checks[i.key]);
  if (missing) {
    out.push({ key: "profile", icon: UserRoundCheck, title: missing.label, detail: `Profile ${Math.round(d.profile_completion.percent)}% complete`, href: missing.href });
  }
  if (!course && out.length < 4) {
    out.push({ key: "course", icon: BookOpen, title: "Start a short course", detail: "Short practical courses", href: "/learn" });
  }
  return out.slice(0, 4);
}

export function UpNext({ d, className }: { d: DashboardData; className?: string }) {
  const now = useNow(60_000).getTime();
  const items = nextActions(d, now);
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className={cn("relative", className)}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 id={titleId} className="text-eyebrow text-accent-strong">Up next</h2>
        <span className="text-[11px] text-subtle">Suggestions</span>
      </div>
      <ol className="space-y-1">
        {items.map((a, i) => (
          <li key={a.key}>
            <Link
              href={a.href}
              className="group flex items-center gap-3 rounded-[var(--radius-md)] px-2 py-2 -mx-2 transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-[var(--ring)]"
            >
              <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] border border-border bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)]">
                <a.icon className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular absolute -right-1.5 -top-1.5 rounded-full border border-border bg-bg-elevated px-1 font-mono text-[9px] leading-[14px] text-subtle" aria-hidden>
                  {i + 1}
                </span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{a.title}</span>
                <span className="block truncate text-xs text-muted">{a.detail}</span>
              </span>
              <ArrowRight className="h-3.5 w-3.5 shrink-0 text-subtle transition-transform duration-300 group-hover:translate-x-0.5 group-hover:text-accent-strong" aria-hidden />
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
