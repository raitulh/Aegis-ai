"use client";

import {
  Activity,
  ArrowUpRight,
  Award,
  BookOpen,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  Circle,
  Database,
  Eye,
  GitMerge,
  GraduationCap,
  Mail,
  Medal,
  Sparkles,
  Trophy,
  UploadCloud,
  UserRoundCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { createContext, useContext, type ReactNode } from "react";

import { ActivityHeatmap } from "@/components/charts/charts";
import { BadgeIcon } from "@/components/profile/badge-icon";
import type { DashboardData, DashboardWidget, PublicProfile } from "@/components/profile/types";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { RankBadge } from "@/components/ui/extras";
import { Countdown, Cover, ProgressBar, ProgressRing } from "@/components/ui/misc";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatScore, relativeTime, titleCase } from "@/lib/format";

/**
 * Widget catalogue. On wide screens (lg+) `zone` decides which column a widget lives in (the main column or the
 * sticky side rail) and the user's saved order applies within each column; below lg every widget is laid out in
 * one flow in exactly the saved order. `wide` widgets always span the full width of their column.
 */
export const WIDGET_META: Record<DashboardWidget, { label: string; description: string; wide?: boolean; zone: "main" | "side"; icon: LucideIcon }> = {
  competitions: { label: "Active competitions", description: "Your rank, best public score and time left.", wide: true, zone: "main", icon: Trophy },
  deadlines: { label: "Upcoming deadlines", description: "Next 30 days, shown in your local time.", zone: "side", icon: CalendarClock },
  submissions: { label: "Recent submissions", description: "Latest scoring results and errors.", zone: "main", icon: UploadCloud },
  invitations: { label: "Team invitations", description: "Pending invitations to join a team.", zone: "side", icon: Mail },
  contributions: { label: "Open-source updates", description: "Unread merged-PR and GitHub sync notices.", zone: "main", icon: GitMerge },
  credentials: { label: "Badges & certificates", description: "Your most recent credentials.", zone: "main", icon: Award },
  recommended: { label: "Recommended competitions", description: "Open competitions you have not joined.", zone: "main", icon: Sparkles },
  recent: { label: "Recently viewed", description: "Competitions and datasets you opened lately.", zone: "main", icon: Eye },
  learning: { label: "Continue learning", description: "Courses you are enrolled in.", zone: "main", icon: BookOpen },
  activity: { label: "Activity", description: "Submissions, discussion posts, lessons and merged PRs.", wide: true, zone: "main", icon: Activity },
  completion: { label: "Profile completion", description: "A complete profile helps teammates find you.", zone: "side", icon: UserRoundCheck },
};

export const COMPLETION_ITEMS: { key: string; label: string; href: string }[] = [
  { key: "avatar", label: "Add a profile photo", href: "/settings/profile" },
  { key: "headline", label: "Write a headline", href: "/settings/profile" },
  { key: "bio", label: "Add a short bio", href: "/settings/profile" },
  { key: "skills", label: "List your skills", href: "/settings/profile" },
  { key: "university", label: "Choose your university", href: "/settings/profile" },
  { key: "github", label: "Connect GitHub", href: "/settings/integrations" },
  { key: "verified_university", label: "Verify your university membership", href: "/orgs" },
];

/** Extra data some widgets enrich themselves with (the viewer's own public profile, already cached by /u/[handle]). */
export interface WidgetContext {
  profile?: PublicProfile;
  profileLoading: boolean;
}

/** Widgets rendered in the narrow side rail use compact empty states. */
export const CompactContext = createContext(false);

function MiniEmpty({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  const compact = useContext(CompactContext);
  if (compact) {
    return (
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-border-strong text-subtle [&_svg]:h-3.5 [&_svg]:w-3.5" aria-hidden>
          {icon}
        </span>
        <div className="min-w-0 pt-0.5">
          <p className="text-sm font-medium text-fg">{title}</p>
          {description ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p> : null}
          {action ? <div className="mt-2.5">{action}</div> : null}
        </div>
      </div>
    );
  }
  return <EmptyState icon={icon} title={title} description={description} action={action} className="border-0 bg-transparent px-2 py-6" />;
}

const rowLink = "rounded-sm text-fg transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";

// ----------------------------------------------------------------------------- competitions

function CompetitionsWidget({ d }: { d: DashboardData }) {
  if (!d.active_competitions.length) {
    return (
      <MiniEmpty
        icon={<Trophy className="h-5 w-5" />}
        title="You haven't joined a competition yet"
        description="Pick a challenge that matches your level — many are beginner friendly and allow solo entries."
        action={<LinkButton href="/competitions">Browse competitions</LinkButton>}
      />
    );
  }
  return (
    <ul className="-my-1 divide-y divide-border">
      {d.active_competitions.map((c) => (
        <li key={c.slug} className="grid gap-4 py-4 first:pt-1 last:pb-1 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:gap-x-6 lg:grid-cols-1 xl:grid-cols-[minmax(0,1fr)_auto]">
          <div className="flex min-w-0 flex-1 items-center gap-3.5">
            <Cover style={c.cover_style} className="h-12 w-12 shrink-0 rounded-[var(--radius-md)] ring-1 ring-border" />
            <div className="min-w-0">
              <Link href={`/competitions/${c.slug}`} className={cn("block truncate font-medium", rowLink)}>
                {c.title}
              </Link>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                <StatusBadge status={c.status} />
                <span className="inline-flex items-center gap-1">
                  <Users className="h-3 w-3 text-subtle" aria-hidden />
                  {c.team_name ? (c.is_solo ? "Solo entry" : c.team_name) : "No team yet"}
                </span>
                {c.ends_at ? (
                  <span className="inline-flex items-center gap-1">
                    <CalendarClock className="h-3 w-3 text-subtle" aria-hidden />
                    <span className="sr-only">End date: </span>
                    <time dateTime={c.ends_at} title={formatDateTime(c.ends_at)} className="tabular">{formatDate(c.ends_at)}</time>
                  </span>
                ) : null}
              </div>
            </div>
          </div>
          <dl className="grid grid-cols-3 gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-3 py-2.5 text-sm sm:gap-4 sm:px-3.5 md:w-[372px] md:border-0 md:bg-transparent md:p-0 lg:w-auto lg:border lg:bg-bg-elevated/60 lg:px-3.5 lg:py-2.5 xl:w-[372px] xl:border-0 xl:bg-transparent xl:p-0">
            <div className="min-w-0">
              <dt className="text-eyebrow text-subtle">Rank</dt>
              <dd className="mt-1 flex items-center gap-1.5 text-fg">
                {c.rank ? (
                  <>
                    <RankBadge rank={c.rank} size="sm" />
                    <span className="tabular text-xs text-muted">of {c.ranked_teams ?? "—"}</span>
                  </>
                ) : (
                  <span className="text-muted" title={c.best_score === null ? "Make a scored submission to get a rank" : "Ranking is not automatic for this event"}>
                    Unranked
                  </span>
                )}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-eyebrow text-subtle">Best</dt>
              <dd className="tabular mt-1 truncate font-mono text-fg">{formatScore(c.best_score)}</dd>
              {c.metric ? <dd className="truncate font-mono text-[10.5px] text-subtle">{c.metric}</dd> : null}
            </div>
            <div className="min-w-0">
              <dt className="text-eyebrow text-subtle">Time left</dt>
              <dd className="mt-1 text-[13px] text-fg sm:text-sm" title={c.ends_at ? `Ends ${formatDateTime(c.ends_at)}` : undefined}>
                {c.ends_at ? <Countdown to={c.ends_at} /> : <span className="text-muted">No end date</span>}
              </dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

// ----------------------------------------------------------------------------- side rail widgets

function DeadlinesWidget({ d }: { d: DashboardData }) {
  if (!d.deadlines.length) {
    return (
      <MiniEmpty
        icon={<CalendarClock className="h-5 w-5" />}
        title="No deadlines in the next 30 days"
        description={d.active_competitions.length ? "You're all caught up." : "Deadlines from competitions you join will appear here."}
      />
    );
  }
  return (
    <ol className={cn("relative space-y-4", d.deadlines.length > 1 && "before:absolute before:bottom-2 before:left-5 before:top-2 before:w-px before:bg-border")}>
      {d.deadlines.map((x, i) => (
        <li key={`${x.url}-${x.at}-${i}`} className="relative flex items-start gap-3">
          <time
            dateTime={x.at}
            className="relative flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-[var(--radius-md)] border border-border bg-surface-2 leading-none shadow-[inset_0_1px_0_var(--hairline-highlight)]"
          >
            <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-subtle">{formatDate(x.at, { month: "short" })}</span>
            <span className="tabular mt-0.5 text-sm font-semibold text-fg">{formatDate(x.at, { day: "numeric" })}</span>
          </time>
          <div className="min-w-0 pt-0.5">
            <Link href={x.url} className={cn("text-sm font-medium", rowLink)}>{x.title}</Link>
            <p className="mt-0.5 text-xs text-muted">
              <span>{formatDateTime(x.at)}</span> · <span className="font-medium text-fg">{relativeTime(x.at)}</span>
              {x.kind !== "deadline" ? <Badge tone="outline" className="ml-2">{titleCase(x.kind)}</Badge> : null}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function InvitationsWidget({ d }: { d: DashboardData }) {
  if (!d.invitations.length) {
    return <MiniEmpty icon={<Mail className="h-5 w-5" />} title="No pending invitations" description="When someone invites you to their team, it shows up here." />;
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {d.invitations.map((inv) => (
          <li key={inv.id} className="flex items-start gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-3 py-2.5 text-sm">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong">
              <Users className="h-3.5 w-3.5" aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block truncate font-medium text-fg">{inv.team_name}</span>
              <span className="block text-xs text-muted">
                {inv.competition.title}
                {inv.expires_at ? <> · expires {relativeTime(inv.expires_at)}</> : null}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <LinkButton href="/invites" size="sm" className="w-full max-sm:h-9">Review invitations</LinkButton>
    </div>
  );
}

function CompletionWidget({ d }: { d: DashboardData }) {
  const { percent, checks } = d.profile_completion;
  const todo = COMPLETION_ITEMS.filter((item) => !checks[item.key]);
  const done = COMPLETION_ITEMS.filter((item) => checks[item.key]);
  return (
    <div>
      <div className="mb-4 flex items-center gap-4">
        <ProgressRing value={percent} size={56} stroke={5} />
        <p className="text-sm leading-relaxed text-muted">
          {percent >= 100 ? "Your profile is complete — nice work." : "Complete these steps so organizers and teammates can recognize your work."}
        </p>
      </div>
      {todo.length ? (
        <ul className="space-y-2">
          {todo.map((item) => (
            <li key={item.key} className="flex items-center gap-2.5 text-sm">
              <Circle className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
              <Link href={item.href} className={cn("hover:underline", rowLink)}>{item.label}</Link>
            </li>
          ))}
        </ul>
      ) : null}
      {done.length ? (
        <details className={cn("group/done", todo.length && "mt-3")} open={!todo.length}>
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-xs text-subtle transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] [&::-webkit-details-marker]:hidden">
            <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-hidden />
            <span className="tabular">{done.length}</span> of {COMPLETION_ITEMS.length} done
            <span className="text-subtle transition-transform group-open/done:rotate-90" aria-hidden>›</span>
          </summary>
          <ul className="mt-2.5 space-y-2">
            {done.map((item) => (
              <li key={item.key} className="flex items-center gap-2.5 text-sm">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden />
                <span className="text-muted line-through decoration-subtle">
                  {item.label}
                  <span className="sr-only"> (done)</span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------------------- main column widgets

function SubmissionsWidget({ d }: { d: DashboardData }) {
  if (!d.recent_submissions.length) {
    return (
      <MiniEmpty
        icon={<UploadCloud className="h-5 w-5" />}
        title="No submissions yet"
        description="Download a competition's data, train a model and upload your predictions to see a score."
        action={d.active_competitions[0] ? <LinkButton variant="secondary" href={`/competitions/${d.active_competitions[0].slug}`}>Open {d.active_competitions[0].title}</LinkButton> : undefined}
      />
    );
  }
  return (
    <ul className="-my-1 divide-y divide-border">
      {d.recent_submissions.map((s) => (
        <li key={s.id} className="flex items-start gap-3 py-3 first:pt-1 last:pb-1">
          <div className="min-w-0 flex-1">
            <Link href={`/competitions/${s.competition.slug}/submissions`} className={cn("block truncate text-sm font-medium", rowLink)}>
              {s.competition.title}
            </Link>
            <p className="mt-0.5 text-xs text-subtle">
              <span title={formatDateTime(s.submitted_at)}>{relativeTime(s.submitted_at)}</span>
            </p>
            {s.error_message ? <p className="mt-1 line-clamp-2 text-xs text-danger">{s.error_message}</p> : null}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <StatusBadge status={s.status} />
            {s.public_score !== null ? (
              <span className="tabular font-mono text-xs text-fg">
                <span className="text-subtle">Public</span> {formatScore(s.public_score)}
              </span>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

function ContributionsWidget({ d }: { d: DashboardData }) {
  if (!d.contribution_notifications.length) {
    return d.profile_completion.checks.github ? (
      <MiniEmpty icon={<GitMerge className="h-5 w-5" />} title="No new contribution updates" action={<LinkButton href="/open-source" variant="secondary">Find an issue to work on</LinkButton>} />
    ) : (
      <MiniEmpty
        icon={<GitMerge className="h-5 w-5" />}
        title="Connect GitHub to track contributions"
        description="Merged pull requests to registered repositories count toward badges once your account is linked."
        action={<LinkButton href="/settings/integrations" variant="secondary">Connect GitHub</LinkButton>}
      />
    );
  }
  return (
    <ul className="space-y-3">
      {d.contribution_notifications.map((n) => {
        const inner = (
          <>
            <span className="block text-sm text-fg">{n.title}</span>
            <span className="text-xs text-muted" title={formatDateTime(n.created_at)}>{relativeTime(n.created_at)}</span>
          </>
        );
        return (
          <li key={n.id} className="flex items-start gap-3">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong">
              <GitMerge className="h-3.5 w-3.5" aria-hidden />
            </span>
            {n.link?.startsWith("/") ? (
              <Link href={n.link} className={cn("min-w-0", rowLink)}>{inner}</Link>
            ) : n.link ? (
              <a href={n.link} target="_blank" rel="noopener noreferrer" className={cn("min-w-0", rowLink)}>{inner}</a>
            ) : (
              <div className="min-w-0">{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function CredentialsWidget({ d }: { d: DashboardData }) {
  if (!d.recent_badges.length && !d.recent_certificates.length) {
    return (
      <MiniEmpty
        icon={<Award className="h-5 w-5" />}
        title="No credentials yet"
        description="Finish a course or place in a competition to earn verifiable certificates and badges."
        action={<LinkButton href="/learn" variant="secondary">Take a course</LinkButton>}
      />
    );
  }
  return (
    <div className="space-y-4">
      {d.recent_badges.length ? (
        <ul className="space-y-2.5" aria-label="Recent badges">
          {d.recent_badges.map((b, i) => (
            <li key={`${b.name}-${i}`} className="flex items-center gap-3">
              <BadgeIcon icon={b.icon} color={b.color} size={32} />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg">{b.name}</p>
                <p className="text-xs text-muted">Badge · {relativeTime(b.awarded_at)}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {d.recent_certificates.length ? (
        <ul className="space-y-2.5 border-t border-border pt-4" aria-label="Recent certificates">
          {d.recent_certificates.map((c) => (
            <li key={c.public_id}>
              <Link href={`/verify/${c.public_id}`} className={cn("group flex items-center gap-3", rowLink)}>
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-border">
                  <Award className="h-4 w-4" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{c.result_label} · {c.event_title}</span>
                  <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    Certificate · {relativeTime(c.issued_at)}
                    {c.is_demo ? <DemoBadge /> : null}
                  </span>
                </span>
                <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-subtle transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <LinkButton href="/settings/achievements" variant="ghost" size="sm" className="-ml-2 max-sm:h-9">Manage achievements</LinkButton>
    </div>
  );
}

function RecommendedWidget({ d }: { d: DashboardData }) {
  const byInterests = d.recommendation_basis === "declared_interests";
  return (
    <div>
      <p className="mb-4 inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 px-2.5 py-1.5 text-xs text-muted">
        <Sparkles className="h-3.5 w-3.5 text-accent-strong" aria-hidden />
        {byInterests ? "Based on your declared interests" : "Upcoming public competitions"}
        <span className="text-subtle">·</span>
        <Link href="/settings/profile" className="font-medium text-accent-strong hover:underline">
          {byInterests ? "Edit interests" : "Add interests to tailor these"}
        </Link>
      </p>
      {!d.recommended.length ? (
        <MiniEmpty
          icon={<Trophy className="h-5 w-5" />}
          title={byInterests ? "Nothing matches your interests right now" : "No open competitions right now"}
          description={byInterests ? "Try broadening your interests, or browse everything that's open." : "Check back soon — new events are added regularly."}
          action={<LinkButton href="/competitions" variant="secondary">Browse all competitions</LinkButton>}
        />
      ) : (
        <ul className="space-y-2.5">
          {d.recommended.map((c) => (
            <li key={c.slug}>
              <Link
                href={`/competitions/${c.slug}`}
                className="group lift flex h-full gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/40 p-3 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                <Cover style={c.cover_style} interactive className="h-14 w-14 shrink-0 rounded-[var(--radius-sm)]" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{c.title}</p>
                  <p className="line-clamp-2 text-xs leading-relaxed text-muted">{c.summary}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-subtle">
                    <span className="font-mono uppercase tracking-[0.1em]">{titleCase(c.task_type)}</span>
                    {c.ends_at ? <span title={formatDateTime(c.ends_at)}>· ends {relativeTime(c.ends_at)}</span> : null}
                    {byInterests && c.matched.length ? <Badge tone="accent">Matches: {c.matched.join(", ")}</Badge> : null}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RecentWidget({ d }: { d: DashboardData }) {
  if (!d.recently_viewed.length) {
    return <MiniEmpty icon={<Eye className="h-5 w-5" />} title="Nothing viewed yet" description="Competitions and datasets you open will be listed here for quick access." />;
  }
  return (
    <ul className="-my-1 divide-y divide-border">
      {d.recently_viewed.map((v, i) => (
        <li key={`${v.url}-${i}`} className="flex items-center gap-3 py-2.5 first:pt-1 last:pb-1">
          {v.type === "dataset" ? (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-cyan-soft text-cyan">
              <Database className="h-3.5 w-3.5" aria-label="Dataset" />
            </span>
          ) : (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-strong">
              <Trophy className="h-3.5 w-3.5" aria-label="Competition" />
            </span>
          )}
          <Link href={v.url} className={cn("min-w-0 flex-1 truncate text-sm", rowLink)}>{v.title}</Link>
          <span className="shrink-0 text-xs text-subtle" title={formatDateTime(v.viewed_at)}>{relativeTime(v.viewed_at)}</span>
        </li>
      ))}
    </ul>
  );
}

function LearningWidget({ d }: { d: DashboardData }) {
  if (!d.learning.length) {
    return (
      <MiniEmpty
        icon={<BookOpen className="h-5 w-5" />}
        title="No courses in progress"
        description="Short, hands-on courses prepare you for competitions and award certificates."
        action={<LinkButton href="/learn">Take a course</LinkButton>}
      />
    );
  }
  return (
    <ul className="space-y-4">
      {d.learning.map((c) => (
        <li key={c.slug} className="flex items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md)] border border-border bg-surface-2 text-accent-strong">
            <GraduationCap className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <Link href={`/learn/${c.slug}`} className={cn("min-w-0 truncate text-sm font-medium", rowLink)}>{c.title}</Link>
              <span className="tabular shrink-0 text-xs text-muted">{Math.round(c.progress_pct)}%</span>
            </div>
            <ProgressBar value={c.progress_pct} label={`${c.title} progress`} />
          </div>
          <LinkButton href={c.resume_url} size="sm" variant="secondary" className="shrink-0 max-sm:h-9">Resume</LinkButton>
        </li>
      ))}
    </ul>
  );
}

const MILESTONE_ICON: Record<string, LucideIcon> = {
  competition_joined: Trophy,
  badge: Medal,
  course_completed: GraduationCap,
  certificate: Award,
  contribution: GitMerge,
};

/** Recent milestones from the viewer's own public-profile timeline (real events only). */
function Milestones({ ctx }: { ctx: WidgetContext }) {
  if (ctx.profileLoading) {
    return (
      <div className="space-y-3" aria-hidden>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-6 w-6 rounded-full" />
            <Skeleton className="h-3 flex-1" />
          </div>
        ))}
      </div>
    );
  }
  const items = (ctx.profile?.timeline ?? []).slice(0, 5);
  if (!items.length) return <p className="text-sm text-muted">Milestones like badges, certificates and completed courses will appear here.</p>;
  return (
    <ol className="relative space-y-3.5 before:absolute before:bottom-1 before:left-3 before:top-1 before:w-px before:bg-border">
      {items.map((t, i) => {
        const Icon = MILESTONE_ICON[t.kind] ?? CalendarDays;
        const internal = t.url && t.url.startsWith("/");
        return (
          <li key={`${t.kind}-${i}`} className="relative flex items-start gap-3">
            <span className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2 text-accent-strong">
              <Icon className="h-3 w-3" aria-hidden />
            </span>
            <div className="min-w-0 pt-0.5">
              {internal ? (
                <Link href={t.url as string} className={cn("block truncate text-sm", rowLink)}>{t.title}</Link>
              ) : (
                <span className="block truncate text-sm text-fg">{t.title}</span>
              )}
              {t.at ? <span className="text-xs text-subtle" title={formatDateTime(t.at)}>{relativeTime(t.at)}</span> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ActivityWidget({ d, ctx }: { d: DashboardData; ctx: WidgetContext }) {
  return (
    <div className="grid gap-6 xl:grid-cols-[auto_minmax(0,1fr)] xl:gap-8">
      <div className="min-w-0">
        {/* Phones get a shorter window of the same data so the latest weeks are visible without scrolling. */}
        <div className="sm:hidden">
          <ActivityHeatmap counts={d.activity} days={126} label="Your activity" />
        </div>
        <div className="hidden sm:block">
          <ActivityHeatmap counts={d.activity} days={182} label="Your activity" />
        </div>
        <p className="mt-3 max-w-[22rem] text-xs leading-relaxed text-subtle">
          Counts submissions to public competitions, discussion threads and replies, completed lessons and merged pull requests. Days are UTC.
        </p>
      </div>
      <div className="min-w-0 border-t border-border pt-5 xl:border-l xl:border-t-0 xl:pl-8 xl:pt-0">
        <p className="mb-3 text-eyebrow text-subtle">Recent milestones</p>
        <Milestones ctx={ctx} />
      </div>
    </div>
  );
}

export const RENDER: Record<DashboardWidget, (d: DashboardData, ctx: WidgetContext) => ReactNode> = {
  competitions: (d) => <CompetitionsWidget d={d} />,
  deadlines: (d) => <DeadlinesWidget d={d} />,
  submissions: (d) => <SubmissionsWidget d={d} />,
  invitations: (d) => <InvitationsWidget d={d} />,
  contributions: (d) => <ContributionsWidget d={d} />,
  credentials: (d) => <CredentialsWidget d={d} />,
  recommended: (d) => <RecommendedWidget d={d} />,
  recent: (d) => <RecentWidget d={d} />,
  learning: (d) => <LearningWidget d={d} />,
  activity: (d, ctx) => <ActivityWidget d={d} ctx={ctx} />,
  completion: (d) => <CompletionWidget d={d} />,
};

/**
 * True when the widget would only show its empty state. Mirrors each widget's own empty check, so runs of empty
 * widgets can be collapsed into one compact panel instead of a wall of half-empty cards.
 */
export function isWidgetEmpty(id: DashboardWidget, d: DashboardData): boolean {
  switch (id) {
    case "competitions":
      return !d.active_competitions.length;
    case "deadlines":
      return !d.deadlines.length;
    case "submissions":
      return !d.recent_submissions.length;
    case "invitations":
      return !d.invitations.length;
    case "contributions":
      return !d.contribution_notifications.length;
    case "credentials":
      return !d.recent_badges.length && !d.recent_certificates.length;
    case "recent":
      return !d.recently_viewed.length;
    case "learning":
      return !d.learning.length;
    case "recommended":
      return !d.recommended.length;
    // The heatmap/milestones and the completion checklist always have something to show.
    case "activity":
    case "completion":
      return false;
  }
}

export function widgetAction(id: DashboardWidget, d: DashboardData): ReactNode {
  if (id === "competitions" && d.active_competitions.length) return <LinkButton href="/competitions" variant="ghost" size="sm" className="max-sm:h-9">Browse</LinkButton>;
  if (id === "invitations" && d.invitations.length) return <Badge tone="accent">{d.invitations.length} pending</Badge>;
  if (id === "learning" && d.learning.length) return <LinkButton href="/learn" variant="ghost" size="sm" className="max-sm:h-9">All courses</LinkButton>;
  return null;
}
