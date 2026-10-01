"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  ChevronDown,
  ExternalLink,
  FileCode2,
  FolderGit2,
  Mail,
  MapPin,
  Megaphone,
  NotebookPen,
  Pin,
  Scale,
  Trophy,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { ck, useCompetition } from "@/components/competition/context";
import { DateTime } from "@/components/competition/datetime";
import { FORMAT_LABELS, LEADERBOARD_VISIBILITY_LABELS, VISIBILITY_LABELS, directionLabel } from "@/components/competition/labels";
import type { Announcement, Criterion, Rubric } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Prose } from "@/components/ui/markdown";
import { KeyValue } from "@/components/ui/misc";
import { EmptyState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import type { CompetitionDetail } from "@/lib/types";

function SectionCard({ id, title, icon, action, children }: { id?: string; title: ReactNode; icon?: ReactNode; action?: ReactNode; children: ReactNode }) {
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-24 rounded-[var(--radius-lg)] border border-border bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-fg">
          {icon}
          {title}
        </h2>
        {action}
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

// ----------------------------------------------------------------------------- announcements

function Announcements({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const isManager = comp.viewer.roles.includes("organizer");
  const query = useQuery({
    queryKey: ck.announcements(slug),
    queryFn: () => get<Announcement[]>(`/competitions/${encodeURIComponent(slug)}/announcements`),
    enabled: comp.announcement_count > 0 || isManager,
  });
  const markRead = useApiMutation(() => post(`/competitions/${encodeURIComponent(slug)}/announcements/read`), {
    success: "Announcements marked as read",
    invalidate: [ck.announcements(slug)],
  });

  const items = query.data ?? [];
  useEffect(() => {
    if (items.length && typeof window !== "undefined" && window.location.hash === "#announcements") {
      document.getElementById("announcements")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [items.length]);

  if (comp.announcement_count === 0 && !isManager) return null;
  if (query.isPending) {
    return (
      <SectionCard id="announcements" title="Announcements" icon={<Megaphone className="h-4 w-4 text-accent-strong" aria-hidden />}>
        <SkeletonRows rows={2} />
      </SectionCard>
    );
  }
  if (query.isError || items.length === 0) return null;

  const unread = comp.viewer.is_authenticated ? items.filter((a) => !a.is_read && a.status === "published").length : 0;
  return (
    <SectionCard
      id="announcements"
      title={<>Announcements {unread ? <Badge tone="accent">{unread} new</Badge> : null}</>}
      icon={<Megaphone className="h-4 w-4 text-accent-strong" aria-hidden />}
      action={
        unread ? (
          <Button size="sm" variant="ghost" loading={markRead.isPending} onClick={() => markRead.mutate(undefined)}>
            Mark all as read
          </Button>
        ) : null
      }
    >
      <ol className="divide-y divide-border">
        {items.map((a) => (
          <li key={a.id} className="py-4 first:pt-0 last:pb-0">
            <article aria-labelledby={`ann-${a.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <h3 id={`ann-${a.id}`} className="font-semibold text-fg">{a.title}</h3>
                {a.pinned ? <Badge tone="info" icon={<Pin className="h-3 w-3" aria-hidden />}>Pinned</Badge> : null}
                {comp.viewer.is_authenticated && !a.is_read && a.status === "published" ? <Badge tone="accent">New</Badge> : null}
                {a.status !== "published" ? <Badge tone="warning">{titleCase(a.status)}</Badge> : null}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-subtle">
                {a.sponsor ? (
                  <span>
                    From sponsor <Link href={`/orgs/${a.sponsor.slug}`} className="text-muted hover:text-fg">{a.sponsor.name}</Link>
                  </span>
                ) : a.author ? (
                  <UserLink user={a.author} size={18} className="text-xs" />
                ) : null}
                <span aria-hidden>·</span>
                <time dateTime={a.created_at} title={new Date(a.created_at).toLocaleString()}>{relativeTime(a.created_at)}</time>
              </div>
              <Prose html={a.body_html} className="mt-2 text-sm" />
            </article>
          </li>
        ))}
      </ol>
    </SectionCard>
  );
}

// ----------------------------------------------------------------------------- evaluation

function RubricCriteria({ slug }: { slug: string }) {
  const query = useQuery({
    queryKey: ck.rubric(slug),
    queryFn: () => get<Rubric>(`/competitions/${encodeURIComponent(slug)}/rubric`),
  });
  if (query.isPending) return <SkeletonRows rows={2} />;
  if (query.isError) return null;
  const criteria = (query.data.criteria as unknown as Criterion[]) ?? [];
  if (!criteria.length) return <p className="text-sm text-muted">The judging rubric will be published before judging starts.</p>;
  const totalWeight = criteria.reduce((s, c) => s + (Number(c.weight) || 0), 0) || 1;
  return (
    <div>
      <h3 className="text-sm font-semibold text-fg">Judging rubric{query.data.version ? <span className="ml-1 font-normal text-subtle">v{query.data.version}</span> : null}</h3>
      <ul className="mt-2 divide-y divide-border rounded-[var(--radius-md)] border border-border">
        {criteria.map((c) => (
          <li key={c.key} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="font-medium text-fg">{c.label}</p>
              {c.description ? <p className="text-sm text-muted">{c.description}</p> : null}
            </div>
            <div className="shrink-0 text-xs text-subtle sm:text-right">
              <div>Score {formatNumber(c.min)}–{formatNumber(c.max)}</div>
              <div>Weight {Math.round(((Number(c.weight) || 0) / totalWeight) * 100)}%</div>
            </div>
          </li>
        ))}
      </ul>
      {query.data.blind_judging ? <p className="mt-2 text-xs text-subtle">Judging is blind: judges don&apos;t see team member names.</p> : null}
    </div>
  );
}

function Evaluation({ comp }: { comp: CompetitionDetail }) {
  const ev = comp.evaluation;
  if (comp.scoring_mode === "none") return null;
  const hasContent = ev || comp.evaluation_html || comp.scoring_notes_md || comp.scoring_mode === "judged";
  if (!hasContent) return null;
  return (
    <SectionCard id="evaluation" title="Evaluation" icon={<Scale className="h-4 w-4 text-accent-strong" aria-hidden />}>
      <div className="space-y-5">
        {ev ? (
          <KeyValue
            items={[
              { label: "Metric", value: <span>{ev.metric_label} <span className="font-mono text-xs text-subtle">({ev.metric})</span></span> },
              { label: "Direction", value: directionLabel(ev.direction) },
              { label: "Submission format", value: ev.submission_format },
              { label: "Evaluator version", value: <span className="font-mono text-xs">{ev.evaluator} · {ev.evaluator_version}</span> },
              { label: "ID column", value: <code className="font-mono text-xs">{ev.id_column}</code> },
              { label: "Target column", value: <code className="font-mono text-xs">{ev.target_column}</code> },
              ...(ev.secondary_metrics.length
                ? [{ label: "Also reported", value: <span className="font-mono text-xs">{ev.secondary_metrics.join(", ")}</span> }]
                : []),
              { label: "Schema checks", value: ev.strict_schema ? "Strict — extra or missing rows are rejected" : "Lenient" },
            ]}
          />
        ) : null}
        {comp.scoring_mode === "judged" ? <RubricCriteria slug={comp.slug} /> : null}
        {comp.evaluation_html ? <Prose html={comp.evaluation_html} /> : null}
        {comp.scoring_notes_md ? (
          <div>
            <h3 className="text-sm font-semibold text-fg">Scoring notes</h3>
            <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{comp.scoring_notes_md}</p>
          </div>
        ) : null}
        {comp.scoring_mode === "automatic" ? (
          <p className="text-xs text-subtle">
            The live leaderboard uses a public subset of the test data; final ranks use the hidden private subset.{" "}
            <Link href={`/competitions/${comp.slug}/leaderboard`} className="text-accent-strong hover:underline">How ranking works</Link>
          </p>
        ) : null}
      </div>
    </SectionCard>
  );
}

// ----------------------------------------------------------------------------- rules (collapsible)

function Rules({ comp }: { comp: CompetitionDetail }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined" && window.location.hash === "#rules") {
      setOpen(true);
      requestAnimationFrame(() => document.getElementById("rules")?.scrollIntoView({ block: "start" }));
    }
  }, []);
  return (
    <section id="rules" aria-labelledby="rules-heading" className="scroll-mt-24 rounded-[var(--radius-lg)] border border-border bg-surface">
      <h2 id="rules-heading">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="rules-body"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-between gap-3 rounded-[var(--radius-lg)] px-5 py-3.5 text-left text-base font-semibold text-fg hover:bg-surface-2/60"
        >
          <span className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-accent-strong" aria-hidden /> Rules
            {comp.viewer.accepted_rules_at ? <Badge tone="success">Accepted</Badge> : null}
          </span>
          <ChevronDown className={cn("h-4 w-4 text-muted transition-transform", open && "rotate-180")} aria-hidden />
        </button>
      </h2>
      <div id="rules-body" hidden={!open} className="border-t border-border px-5 py-4">
        {comp.rules_html ? <Prose html={comp.rules_html} /> : <p className="text-sm text-muted">No additional rules were published. The platform code of conduct applies.</p>}
        {comp.viewer.accepted_rules_at ? (
          <p className="mt-4 text-xs text-subtle">You accepted these rules {relativeTime(comp.viewer.accepted_rules_at)} (rules version {comp.config_version}).</p>
        ) : null}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------- sidebar

function SideCard({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody className="text-sm">{children}</CardBody>
    </Card>
  );
}

function assetIcon(kind: string) {
  const cls = "h-4 w-4 shrink-0 text-muted";
  if (kind === "github") return <FolderGit2 className={cls} aria-hidden />;
  if (kind === "colab" || kind === "kaggle" || kind === "notebook") return <NotebookPen className={cls} aria-hidden />;
  if (kind === "docs") return <BookOpen className={cls} aria-hidden />;
  return <FileCode2 className={cls} aria-hidden />;
}

function Details({ comp }: { comp: CompetitionDetail }) {
  const rows: { label: string; value: ReactNode }[] = [
    { label: "Format", value: FORMAT_LABELS[comp.format] ?? comp.format },
    { label: "Visibility", value: VISIBILITY_LABELS[comp.visibility] ?? comp.visibility },
    {
      label: "Team size",
      value: comp.team_max_size === 1 ? "Individual only" : `${comp.team_min_size}–${comp.team_max_size} members`,
    },
  ];
  if (comp.scoring_mode === "automatic") {
    rows.push(
      { label: "Daily submissions", value: formatNumber(comp.daily_submission_limit) },
      ...(comp.total_submission_limit ? [{ label: "Total submissions", value: formatNumber(comp.total_submission_limit) }] : []),
      { label: "Max file size", value: `${comp.max_submission_mb} MB` },
      { label: "Final selections", value: formatNumber(comp.final_selection_limit) },
    );
  }
  if (comp.scoring_mode !== "none") rows.push({ label: "Leaderboard", value: LEADERBOARD_VISIBILITY_LABELS[comp.leaderboard_visibility] ?? comp.leaderboard_visibility });
  if (comp.registration_opens_at) rows.push({ label: "Registration opens", value: <DateTime value={comp.registration_opens_at} eventTimeZone={comp.timezone} /> });
  if (comp.registration_closes_at) rows.push({ label: "Registration closes", value: <DateTime value={comp.registration_closes_at} eventTimeZone={comp.timezone} relative /> });
  if (comp.team_lock_at) rows.push({ label: "Team changes lock", value: <DateTime value={comp.team_lock_at} eventTimeZone={comp.timezone} relative /> });
  rows.push({ label: "Event time zone", value: comp.timezone });
  return (
    <SideCard title="Key details">
      <dl className="space-y-3">
        {rows.map((r) => (
          <div key={r.label} className="flex items-start justify-between gap-4">
            <dt className="text-subtle">{r.label}</dt>
            <dd className="text-right text-fg">{r.value}</dd>
          </div>
        ))}
      </dl>
    </SideCard>
  );
}

function Schedule({ comp }: { comp: CompetitionDetail }) {
  if (!comp.schedule.length) return null;
  const now = Date.now();
  return (
    <SideCard title="Schedule">
      <ol className="relative space-y-4 border-l border-border pl-4">
        {comp.schedule.map((s) => {
          const past = new Date(s.ends_at ?? s.starts_at).getTime() < now;
          return (
            <li key={s.id} className={cn("relative", past && "opacity-70")}>
              <span className={cn("absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-surface", past ? "bg-surface-3" : "bg-accent")} aria-hidden />
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-fg">{s.title}</span>
                <Badge tone="outline">{titleCase(s.kind)}</Badge>
              </div>
              <DateTime value={s.starts_at} eventTimeZone={comp.timezone} className="mt-0.5 text-xs text-muted" />
              {s.location ? <p className="mt-0.5 flex items-center gap-1 text-xs text-muted"><MapPin className="h-3 w-3" aria-hidden /> {s.location}</p> : null}
              {s.description ? <p className="mt-1 text-xs text-muted">{s.description}</p> : null}
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-accent-strong hover:underline">
                  Join link <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ) : null}
            </li>
          );
        })}
      </ol>
    </SideCard>
  );
}

function Sidebar({ comp }: { comp: CompetitionDetail }) {
  const inPerson = comp.format === "offline" || comp.format === "hybrid";
  return (
    <aside className="space-y-4" aria-label="Competition details">
      <Details comp={comp} />
      <Schedule comp={comp} />

      {comp.parent ? (
        <SideCard title="Qualification round">
          <p className="text-muted">This event follows the qualification round:</p>
          <Link href={`/competitions/${comp.parent.slug}`} className="mt-1 block font-medium text-accent-strong hover:underline">{comp.parent.title}</Link>
        </SideCard>
      ) : null}

      {inPerson && (comp.venue_name || comp.venue_address || comp.venue_notes) ? (
        <SideCard title="Venue">
          {comp.venue_name ? <p className="font-medium text-fg">{comp.venue_name}</p> : null}
          {comp.venue_address ? (
            <p className="mt-1 flex items-start gap-1.5 text-muted"><MapPin className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {comp.venue_address}</p>
          ) : null}
          {comp.venue_notes ? <p className="mt-2 whitespace-pre-wrap text-muted">{comp.venue_notes}</p> : null}
        </SideCard>
      ) : null}

      {comp.starter_assets.length ? (
        <SideCard title="Starter resources">
          <ul className="space-y-2">
            {comp.starter_assets.map((a, i) => (
              <li key={`${a.url}-${i}`}>
                <a href={a.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 text-fg hover:text-accent-strong">
                  {assetIcon(a.kind)}
                  <span className="min-w-0 flex-1 truncate">{a.label}</span>
                  <ExternalLink className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              </li>
            ))}
          </ul>
          {comp.starter_asset_version > 1 ? <p className="mt-3 text-xs text-subtle">Resources updated (revision {comp.starter_asset_version}).</p> : null}
        </SideCard>
      ) : null}

      {comp.awards.length ? (
        <SideCard title="Awards">
          <ul className="space-y-3">
            {comp.awards.map((a) => (
              <li key={a.id} className="flex items-start gap-2">
                <Trophy className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                <div className="min-w-0">
                  <p className="font-medium text-fg">{a.name}</p>
                  {a.description ? <p className="text-xs text-muted">{a.description}</p> : null}
                  {a.winner_team_name ? <p className="mt-0.5 text-xs text-success">Awarded to {a.winner_team_name}</p> : null}
                </div>
              </li>
            ))}
          </ul>
        </SideCard>
      ) : null}

      {comp.sponsors.length ? (
        <SideCard title="Sponsors">
          <ul className="space-y-3">
            {comp.sponsors.map((s) => (
              <li key={s.org.id}>
                <div className="flex items-center justify-between gap-2">
                  <Link href={`/orgs/${s.org.slug}`} className="truncate font-medium text-fg hover:text-accent-strong">{s.org.name}</Link>
                  <Badge tone="outline">{titleCase(s.tier)}</Badge>
                </div>
                {s.blurb ? <p className="mt-0.5 text-xs text-muted">{s.blurb}</p> : null}
              </li>
            ))}
          </ul>
        </SideCard>
      ) : null}

      {comp.tags.length ? (
        <SideCard title="Tags">
          <div className="flex flex-wrap gap-1.5">
            {comp.tags.map((t) => (
              <Link key={t} href={`/competitions?tag=${encodeURIComponent(t)}`} className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted hover:text-fg">
                #{t}
              </Link>
            ))}
          </div>
        </SideCard>
      ) : null}

      {comp.organizer_contact_email ? (
        <SideCard title="Questions?">
          <a href={`mailto:${comp.organizer_contact_email}`} className="inline-flex items-center gap-2 text-accent-strong hover:underline">
            <Mail className="h-4 w-4" aria-hidden /> {comp.organizer_contact_email}
          </a>
          <p className="mt-2 text-xs text-muted">
            For questions others may share, use the <Link href={`/competitions/${comp.slug}/discussion`} className="text-accent-strong hover:underline">discussion forum</Link>.
          </p>
        </SideCard>
      ) : null}
    </aside>
  );
}

// ----------------------------------------------------------------------------- page

export default function CompetitionOverviewPage() {
  const comp = useCompetition();
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-6">
        <Announcements comp={comp} />

        <SectionCard id="overview" title="Overview">
          {comp.description_html ? (
            <Prose html={comp.description_html} />
          ) : (
            <EmptyState title="No description yet" description="The organizers haven't written a problem statement yet." className="border-0 py-6" />
          )}
        </SectionCard>

        <Evaluation comp={comp} />

        {comp.has_prize || comp.prize_html ? (
          <SectionCard id="prizes" title="Prizes" icon={<Trophy className="h-4 w-4 text-warning" aria-hidden />}>
            {comp.prize_summary ? <p className="mb-3 text-base font-semibold text-fg">{comp.prize_summary}</p> : null}
            {comp.prize_html ? <Prose html={comp.prize_html} /> : <p className="text-sm text-muted">Prize details will be announced by the organizers.</p>}
          </SectionCard>
        ) : null}

        <Rules comp={comp} />

        {comp.faq.length ? (
          <SectionCard id="faq" title="Frequently asked questions">
            <div className="divide-y divide-border">
              {comp.faq.map((f, i) => (
                <details key={i} className="group py-3 first:pt-0 last:pb-0">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium text-fg">
                    {f.q}
                    <ChevronDown className="h-4 w-4 shrink-0 text-muted transition-transform group-open:rotate-180" aria-hidden />
                  </summary>
                  <p className="mt-2 whitespace-pre-wrap text-sm text-muted">{f.a}</p>
                </details>
              ))}
            </div>
          </SectionCard>
        ) : null}

        {comp.status === "upcoming" && comp.viewer.is_participant ? (
          <InlineNotice tone="info" title="You're registered">
            Submissions open when the competition starts. Meanwhile, explore the data and form your team.
          </InlineNotice>
        ) : null}
      </div>
      <Sidebar comp={comp} />
    </div>
  );
}
