"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Award,
  BookOpen,
  CalendarClock,
  CheckCircle2,
  Circle,
  Database,
  Eye,
  GitMerge,
  Mail,
  Settings2,
  Sparkles,
  Trophy,
  UploadCloud,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ActivityHeatmap } from "@/components/charts/charts";
import { BadgeIcon } from "@/components/profile/badge-icon";
import { MiniSwitch } from "@/components/profile/mini-switch";
import { DASHBOARD_WIDGETS, type DashboardData, type DashboardWidget } from "@/components/profile/types";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Countdown, Cover, ProgressBar, ProgressRing } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton, Spinner } from "@/components/ui/states";
import { errorMessage, get, post, put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatScore, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Me } from "@/lib/types";

const WIDGET_META: Record<DashboardWidget, { label: string; description: string; wide?: boolean }> = {
  competitions: { label: "Active competitions", description: "Your rank, best public score and time left.", wide: true },
  deadlines: { label: "Upcoming deadlines", description: "Next 30 days, shown in your local time." },
  submissions: { label: "Recent submissions", description: "Latest scoring results and errors." },
  invitations: { label: "Team invitations", description: "Pending invitations to join a team." },
  contributions: { label: "Open-source updates", description: "Unread merged-PR and GitHub sync notices." },
  credentials: { label: "Badges & certificates", description: "Your most recent credentials." },
  recommended: { label: "Recommended competitions", description: "Open competitions you have not joined.", wide: true },
  recent: { label: "Recently viewed", description: "Competitions and datasets you opened lately." },
  learning: { label: "Continue learning", description: "Courses you are enrolled in." },
  activity: { label: "Activity", description: "Submissions, discussion posts, lessons and merged PRs.", wide: true },
  completion: { label: "Profile completion", description: "A complete profile helps teammates find you." },
};

const COMPLETION_ITEMS: { key: string; label: string; href: string }[] = [
  { key: "avatar", label: "Add a profile photo", href: "/settings/profile" },
  { key: "headline", label: "Write a headline", href: "/settings/profile" },
  { key: "bio", label: "Add a short bio", href: "/settings/profile" },
  { key: "skills", label: "List your skills", href: "/settings/profile" },
  { key: "university", label: "Choose your university", href: "/settings/profile" },
  { key: "github", label: "Connect GitHub", href: "/settings/integrations" },
  { key: "verified_university", label: "Verify your university membership", href: "/orgs" },
];

const isWidget = (k: string): k is DashboardWidget => (DASHBOARD_WIDGETS as readonly string[]).includes(k);

function resolveLayout(prefs: DashboardData["prefs"] | undefined) {
  const saved = (prefs?.order ?? []).filter(isWidget);
  const order = Array.from(new Set<DashboardWidget>([...saved, ...DASHBOARD_WIDGETS]));
  const hidden = (prefs?.hidden ?? []).filter(isWidget);
  return { order, hidden };
}

// ----------------------------------------------------------------------------- widgets

function Widget({ id, action, children }: { id: DashboardWidget; action?: ReactNode; children: ReactNode }) {
  const meta = WIDGET_META[id];
  return (
    <Card className={cn("flex flex-col", meta.wide && "lg:col-span-2")}>
      <CardHeader title={meta.label} description={meta.description} action={action} />
      <CardBody className="flex-1">{children}</CardBody>
    </Card>
  );
}

function MiniEmpty({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  return <EmptyState icon={icon} title={title} description={description} action={action} className="border-0 px-2 py-6" />;
}

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
    <ul className="-my-3 divide-y divide-border">
      {d.active_competitions.map((c) => (
        <li key={c.slug} className="flex flex-col gap-3 py-3 md:flex-row md:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <Cover style={c.cover_style} className="h-11 w-11 shrink-0 rounded-lg" />
            <div className="min-w-0">
              <Link href={`/competitions/${c.slug}`} className="block truncate font-medium text-fg hover:text-accent-strong">
                {c.title}
              </Link>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <StatusBadge status={c.status} />
                <span className="inline-flex items-center gap-1">
                  <Users className="h-3 w-3" aria-hidden />
                  {c.team_name ? (c.is_solo ? "Solo entry" : c.team_name) : "No team yet"}
                </span>
              </div>
            </div>
          </div>
          <dl className="grid grid-cols-3 gap-4 text-sm md:w-[380px] md:shrink-0">
            <div>
              <dt className="text-xs text-subtle">Rank</dt>
              <dd className="mt-0.5 tabular-nums text-fg">
                {c.rank ? (
                  <>
                    #{c.rank}
                    <span className="text-muted"> of {c.ranked_teams ?? "—"}</span>
                  </>
                ) : (
                  <span className="text-muted" title={c.best_score === null ? "Make a scored submission to get a rank" : "Ranking is not automatic for this event"}>
                    Unranked
                  </span>
                )}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="truncate text-xs text-subtle">
                Best{c.metric ? <span className="font-mono"> · {c.metric}</span> : null}
              </dt>
              <dd className="mt-0.5 font-mono tabular-nums text-fg">{formatScore(c.best_score)}</dd>
            </div>
            <div>
              <dt className="text-xs text-subtle">Time left</dt>
              <dd className="mt-0.5 text-fg" title={c.ends_at ? `Ends ${formatDateTime(c.ends_at)}` : undefined}>
                {c.ends_at ? <Countdown to={c.ends_at} /> : <span className="text-muted">No end date</span>}
              </dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

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
    <ol className="space-y-3">
      {d.deadlines.map((x, i) => (
        <li key={`${x.url}-${x.at}-${i}`} className="flex items-start gap-3">
          <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
          <div className="min-w-0">
            <Link href={x.url} className="text-sm font-medium text-fg hover:text-accent-strong">{x.title}</Link>
            <p className="mt-0.5 text-xs text-muted">
              <time dateTime={x.at}>{formatDateTime(x.at)}</time> · <span className="font-medium text-fg">{relativeTime(x.at)}</span>
              {x.kind !== "deadline" ? <Badge tone="outline" className="ml-2">{titleCase(x.kind)}</Badge> : null}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

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
    <ul className="-my-2 divide-y divide-border">
      {d.recent_submissions.map((s) => (
        <li key={s.id} className="py-2.5">
          <div className="flex items-center justify-between gap-3">
            <Link href={`/competitions/${s.competition.slug}/submissions`} className="min-w-0 truncate text-sm font-medium text-fg hover:text-accent-strong">
              {s.competition.title}
            </Link>
            <StatusBadge status={s.status} />
          </div>
          <div className="mt-1 flex items-center justify-between gap-3 text-xs text-muted">
            <span title={formatDateTime(s.submitted_at)}>{relativeTime(s.submitted_at)}</span>
            {s.public_score !== null ? <span className="font-mono tabular-nums text-fg">Public {formatScore(s.public_score)}</span> : null}
          </div>
          {s.error_message ? <p className="mt-1 line-clamp-2 text-xs text-danger">{s.error_message}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function InvitationsWidget({ d }: { d: DashboardData }) {
  if (!d.invitations.length) {
    return <MiniEmpty icon={<Mail className="h-5 w-5" />} title="No pending invitations" description="When someone invites you to their team, it shows up here." />;
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-2.5">
        {d.invitations.map((inv) => (
          <li key={inv.id} className="rounded-[var(--radius-md)] border border-border bg-surface-2 px-3 py-2.5 text-sm">
            <p className="font-medium text-fg">{inv.team_name}</p>
            <p className="text-xs text-muted">
              {inv.competition.title}
              {inv.expires_at ? <> · expires {relativeTime(inv.expires_at)}</> : null}
            </p>
          </li>
        ))}
      </ul>
      <LinkButton href="/invites" size="sm" className="w-full">Review invitations</LinkButton>
    </div>
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
            <GitMerge className="mt-0.5 h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
            {n.link?.startsWith("/") ? (
              <Link href={n.link} className="min-w-0 hover:text-accent-strong">{inner}</Link>
            ) : n.link ? (
              <a href={n.link} target="_blank" rel="noopener noreferrer" className="min-w-0 hover:text-accent-strong">{inner}</a>
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
        <ul className="space-y-2.5" aria-label="Recent certificates">
          {d.recent_certificates.map((c) => (
            <li key={c.public_id}>
              <Link href={`/verify/${c.public_id}`} className="flex items-center gap-3 hover:text-accent-strong">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-strong">
                  <Award className="h-4 w-4" aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{c.result_label} · {c.event_title}</span>
                  <span className="text-xs text-muted">Certificate · {relativeTime(c.issued_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <LinkButton href="/settings/achievements" variant="ghost" size="sm">Manage achievements</LinkButton>
    </div>
  );
}

function RecommendedWidget({ d }: { d: DashboardData }) {
  const byInterests = d.recommendation_basis === "declared_interests";
  return (
    <div>
      <p className="mb-4 inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-muted">
        <Sparkles className="h-3.5 w-3.5" aria-hidden />
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
        <ul className="grid gap-3 sm:grid-cols-2">
          {d.recommended.map((c) => (
            <li key={c.slug}>
              <Link href={`/competitions/${c.slug}`} className="group flex h-full gap-3 rounded-[var(--radius-md)] border border-border p-3 transition-colors hover:border-border-strong hover:bg-surface-2">
                <Cover style={c.cover_style} className="h-14 w-14 shrink-0 rounded-md" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-fg group-hover:text-accent-strong">{c.title}</p>
                  <p className="line-clamp-2 text-xs text-muted">{c.summary}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-subtle">
                    <span>{titleCase(c.task_type)}</span>
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
    <ul className="space-y-2.5">
      {d.recently_viewed.map((v, i) => (
        <li key={`${v.url}-${i}`} className="flex items-center gap-3">
          {v.type === "dataset" ? <Database className="h-4 w-4 shrink-0 text-subtle" aria-label="Dataset" /> : <Trophy className="h-4 w-4 shrink-0 text-subtle" aria-label="Competition" />}
          <Link href={v.url} className="min-w-0 flex-1 truncate text-sm text-fg hover:text-accent-strong">{v.title}</Link>
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
        <li key={c.slug}>
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <Link href={`/learn/${c.slug}`} className="min-w-0 truncate text-sm font-medium text-fg hover:text-accent-strong">{c.title}</Link>
            <LinkButton href={c.resume_url} size="sm" variant="secondary">Resume</LinkButton>
          </div>
          <div className="flex items-center gap-3">
            <ProgressBar value={c.progress_pct} label={`${c.title} progress`} />
            <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted">{Math.round(c.progress_pct)}%</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function ActivityWidget({ d }: { d: DashboardData }) {
  return (
    <div>
      <ActivityHeatmap counts={d.activity} days={182} label="Your activity" />
      <p className="mt-3 text-xs text-subtle">
        Counts submissions to public competitions, discussion threads and replies, completed lessons and merged pull requests. Days are UTC.
      </p>
    </div>
  );
}

function CompletionWidget({ d }: { d: DashboardData }) {
  const { percent, checks } = d.profile_completion;
  return (
    <div>
      <div className="mb-4 flex items-center gap-4">
        <ProgressRing value={percent} size={56} stroke={5} />
        <p className="text-sm text-muted">
          {percent >= 100 ? "Your profile is complete — nice work." : "Complete these steps so organizers and teammates can recognize your work."}
        </p>
      </div>
      <ul className="space-y-2">
        {COMPLETION_ITEMS.map((item) => {
          const done = Boolean(checks[item.key]);
          return (
            <li key={item.key} className="flex items-center gap-2.5 text-sm">
              {done ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden /> : <Circle className="h-4 w-4 shrink-0 text-subtle" aria-hidden />}
              {done ? (
                <span className="text-muted line-through decoration-subtle">
                  {item.label}
                  <span className="sr-only"> (done)</span>
                </span>
              ) : (
                <Link href={item.href} className="text-fg hover:text-accent-strong hover:underline">{item.label}</Link>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const RENDER: Record<DashboardWidget, (d: DashboardData) => ReactNode> = {
  competitions: (d) => <CompetitionsWidget d={d} />,
  deadlines: (d) => <DeadlinesWidget d={d} />,
  submissions: (d) => <SubmissionsWidget d={d} />,
  invitations: (d) => <InvitationsWidget d={d} />,
  contributions: (d) => <ContributionsWidget d={d} />,
  credentials: (d) => <CredentialsWidget d={d} />,
  recommended: (d) => <RecommendedWidget d={d} />,
  recent: (d) => <RecentWidget d={d} />,
  learning: (d) => <LearningWidget d={d} />,
  activity: (d) => <ActivityWidget d={d} />,
  completion: (d) => <CompletionWidget d={d} />,
};

function widgetAction(id: DashboardWidget, d: DashboardData): ReactNode {
  if (id === "competitions" && d.active_competitions.length) return <LinkButton href="/competitions" variant="ghost" size="sm">Browse</LinkButton>;
  if (id === "invitations" && d.invitations.length) return <Badge tone="accent">{d.invitations.length} pending</Badge>;
  if (id === "learning" && d.learning.length) return <LinkButton href="/learn" variant="ghost" size="sm">All courses</LinkButton>;
  return null;
}

// ----------------------------------------------------------------------------- customize

function CustomizeDialog({ prefs }: { prefs: DashboardData["prefs"] }) {
  const [open, setOpen] = useState(false);
  const [order, setOrder] = useState<DashboardWidget[]>([]);
  const [hidden, setHidden] = useState<Set<DashboardWidget>>(new Set());
  const save = useApiMutation((body: { hidden: string[]; order: string[] }) => put("/me/dashboard-prefs", body), {
    success: "Dashboard layout saved",
    invalidate: [qk.dashboard],
    onSuccess: () => setOpen(false),
  });

  const onOpenChange = (o: boolean) => {
    if (o) {
      const l = resolveLayout(prefs);
      setOrder(l.order);
      setHidden(new Set(l.hidden));
    }
    setOpen(o);
  };

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      trigger={<Button variant="secondary" icon={<Settings2 className="h-4 w-4" />}>Customize</Button>}
      title="Customize dashboard"
      description="Show, hide and reorder widgets. Your layout is saved to your account."
      footer={
        <>
          <Button
            variant="ghost"
            className="mr-auto"
            onClick={() => {
              setOrder([...DASHBOARD_WIDGETS]);
              setHidden(new Set());
            }}
          >
            Reset to default
          </Button>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button loading={save.isPending} onClick={() => save.mutate({ hidden: [...hidden], order })}>Save layout</Button>
        </>
      }
    >
      <ol className="divide-y divide-border rounded-[var(--radius-md)] border border-border" aria-label="Dashboard widgets">
        {order.map((id, i) => {
          const meta = WIDGET_META[id];
          const visible = !hidden.has(id);
          return (
            <li key={id} className="flex items-center gap-3 px-3 py-2.5">
              <MiniSwitch
                checked={visible}
                label={`Show ${meta.label}`}
                onChange={(v) => {
                  const next = new Set(hidden);
                  if (v) next.delete(id);
                  else next.add(id);
                  setHidden(next);
                }}
              />
              <div className="min-w-0 flex-1">
                <p className={cn("truncate text-sm font-medium", visible ? "text-fg" : "text-subtle")}>{meta.label}</p>
                <p className="truncate text-xs text-subtle">{meta.description}</p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move ${meta.label} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move ${meta.label} down`} disabled={i === order.length - 1} onClick={() => move(i, 1)}>
                  <ArrowDown className="h-4 w-4" />
                </Button>
              </div>
            </li>
          );
        })}
      </ol>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------- page

function Nudges({ me }: { me: Me }) {
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-3">
      {!me.onboarding_completed ? (
        <InlineNotice
          tone="info"
          title="Finish setting up your profile"
          action={<LinkButton href="/onboarding?next=/dashboard" size="sm">Continue setup</LinkButton>}
        >
          Tell us your university and interests so we can suggest relevant competitions — it takes about a minute.
        </InlineNotice>
      ) : null}
      {!me.email_verified ? (
        <InlineNotice
          tone="warning"
          title="Verify your email address"
          action={
            <Button
              size="sm"
              variant="secondary"
              loading={busy}
              disabled={sent}
              onClick={async () => {
                setBusy(true);
                try {
                  await post("/auth/resend-verification", { email: me.email });
                  setSent(true);
                  toast.success("Verification link sent");
                } catch (e) {
                  toast.error(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {sent ? "Link sent" : "Resend link"}
            </Button>
          }
        >
          We sent a link to <span className="font-medium">{me.email}</span>. Some actions, like submitting to competitions, need a verified email.
        </InlineNotice>
      ) : null}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-2" role="status" aria-label="Loading dashboard">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className={cn("rounded-[var(--radius-lg)] border border-border bg-surface p-5", i === 0 && "lg:col-span-2")}>
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-2 h-3 w-64" />
          <Skeleton className="mt-6 h-16 w-full" />
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const me = useRequireAuth();
  const dash = useQuery({
    queryKey: qk.dashboard,
    queryFn: () => get<DashboardData>("/me/dashboard"),
    enabled: Boolean(me.data),
  });
  const layout = useMemo(() => resolveLayout(dash.data?.prefs), [dash.data?.prefs]);

  if (me.isPending || !me.data) return <Spinner label="Loading your dashboard" />;
  const d = dash.data;
  const visible = layout.order.filter((id) => !layout.hidden.includes(id));

  return (
    <Container>
      <PageHeader
        eyebrow="Dashboard"
        title={d ? `Welcome back, ${d.greeting_name}` : `Welcome back, ${me.data.display_name.split(" ")[0]}`}
        description="Your competitions, deadlines, learning and credentials in one place."
        actions={
          <>
            <LinkButton href={`/u/${me.data.handle}`} variant="ghost">View profile</LinkButton>
            {d ? <CustomizeDialog prefs={d.prefs} /> : null}
          </>
        }
      />
      <Nudges me={me.data} />
      <div className="mt-6 pb-12">
        {dash.isPending ? (
          <DashboardSkeleton />
        ) : dash.isError ? (
          <ErrorState error={dash.error} onRetry={() => dash.refetch()} />
        ) : !visible.length ? (
          <EmptyState
            icon={<Settings2 className="h-5 w-5" />}
            title="All widgets are hidden"
            description="Use Customize to bring back the widgets you want to see."
            action={d ? <CustomizeDialog prefs={d.prefs} /> : undefined}
          />
        ) : d ? (
          <div className="grid grid-flow-dense gap-6 lg:grid-cols-2">
            {visible.map((id) => (
              <Widget key={id} id={id} action={widgetAction(id, d)}>
                {RENDER[id](d)}
              </Widget>
            ))}
          </div>
        ) : null}
      </div>
    </Container>
  );
}
