"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownRight,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  ChevronDown,
  ExternalLink,
  FileCode2,
  FolderGit2,
  HelpCircle,
  Mail,
  MapPin,
  Megaphone,
  NotebookPen,
  Pin,
  Scale,
  ScrollText,
  Trophy,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Block } from "@/components/competition/block";
import { ck, useCompetition } from "@/components/competition/context";
import { DateTime } from "@/components/competition/datetime";
import { FORMAT_LABELS, LEADERBOARD_VISIBILITY_LABELS, VISIBILITY_LABELS, directionLabel } from "@/components/competition/labels";
import type { Announcement, Criterion, Rubric } from "@/components/competition/types";
import { UserLink } from "@/components/domain/cards";
import { Reveal } from "@/components/motion/reveal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Prose } from "@/components/ui/markdown";
import { EmptyState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation, useNow } from "@/lib/hooks";
import type { CompetitionDetail } from "@/lib/types";

/** Gap-px tile grid used for evaluation facts and awards. Odd last tiles stretch to keep the grid whole. */
function Tiles({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-2 [&>*]:bg-surface sm:[&>*:last-child:nth-child(odd)]:col-span-2", className)}>
      {children}
    </dl>
  );
}

function Tile({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0 px-4 py-3.5", className)}>
      <dt className="text-eyebrow text-subtle">{label}</dt>
      <dd className="mt-1.5 min-w-0 break-words text-sm text-fg">{children}</dd>
    </div>
  );
}

// ----------------------------------------------------------------------------- announcements

/** Shared by the Announcements block and the "On this page" nav (one cached query, same key). */
function useAnnouncements(comp: CompetitionDetail) {
  const slug = comp.slug;
  const isManager = comp.viewer.roles.includes("organizer");
  return useQuery({
    queryKey: ck.announcements(slug),
    queryFn: () => get<Announcement[]>(`/competitions/${encodeURIComponent(slug)}/announcements`),
    enabled: comp.announcement_count > 0 || isManager,
  });
}

function Announcements({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const isManager = comp.viewer.roles.includes("organizer");
  const query = useAnnouncements(comp);
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
      <Block id="announcements" eyebrow="From the organizers" title="Announcements" icon={<Megaphone />}>
        <SkeletonRows rows={2} />
      </Block>
    );
  }
  if (query.isError || items.length === 0) return null;

  const unread = comp.viewer.is_authenticated ? items.filter((a) => !a.is_read && a.status === "published").length : 0;
  return (
    <Block
      id="announcements"
      eyebrow="From the organizers"
      title={<>Announcements {unread ? <Badge tone="accent" className="animate-pop">{unread} new</Badge> : null}</>}
      icon={<Megaphone />}
      action={
        unread ? (
          <Button size="sm" variant="ghost" loading={markRead.isPending} onClick={() => markRead.mutate(undefined)}>
            Mark all as read
          </Button>
        ) : null
      }
    >
      <ol className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        {items.map((a) => {
          const fresh = comp.viewer.is_authenticated && !a.is_read && a.status === "published";
          return (
            <li key={a.id} className="relative border-b border-border px-5 py-4 last:border-b-0 sm:px-6">
              {fresh ? <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 rounded-full bg-brand" /> : null}
              <article aria-labelledby={`ann-${a.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 id={`ann-${a.id}`} className="font-semibold tracking-[-0.01em] text-fg">{a.title}</h3>
                  {a.pinned ? <Badge tone="info" icon={<Pin className="h-3 w-3" aria-hidden />}>Pinned</Badge> : null}
                  {fresh ? <Badge tone="accent">New</Badge> : null}
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
          );
        })}
      </ol>
    </Block>
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
      <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
        Judging rubric{query.data.version ? <span className="font-mono text-xs font-normal text-subtle">v{query.data.version}</span> : null}
      </h3>
      <ul className="mt-3 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
        {criteria.map((c) => {
          const pct = Math.round(((Number(c.weight) || 0) / totalWeight) * 100);
          return (
            <li key={c.key} className="flex flex-col gap-2 border-b border-border px-4 py-3.5 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
              <div className="min-w-0">
                <p className="font-medium text-fg">{c.label}</p>
                {c.description ? <p className="mt-0.5 text-sm text-muted">{c.description}</p> : null}
              </div>
              <div className="flex shrink-0 items-center gap-4 text-xs text-subtle sm:w-72">
                <span className="tabular whitespace-nowrap">Score {formatNumber(c.min)}–{formatNumber(c.max)}</span>
                <span className="flex flex-1 items-center gap-2">
                  <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                    <span className="block h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
                  </span>
                  <span className="tabular w-[5.5rem] whitespace-nowrap text-right">Weight {pct}%</span>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      {query.data.blind_judging ? <p className="mt-2 text-xs text-subtle">Judging is blind: judges don&apos;t see team member names.</p> : null}
    </div>
  );
}

function hasEvaluation(comp: CompetitionDetail) {
  if (comp.scoring_mode === "none") return false;
  return Boolean(comp.evaluation || comp.evaluation_html || comp.scoring_notes_md || comp.scoring_mode === "judged");
}

function Evaluation({ comp }: { comp: CompetitionDetail }) {
  const ev = comp.evaluation;
  if (!hasEvaluation(comp)) return null;
  const minimize = ev?.direction === "minimize";
  return (
    <Block id="evaluation" eyebrow="How you're scored" title="Evaluation" icon={<Scale />}>
      <div className="space-y-6">
        {ev ? (
          <div>
            <div className="relative overflow-hidden rounded-t-[var(--radius-lg)] border border-b-0 border-border bg-surface surface-sheen px-5 py-5">
              <div aria-hidden className="pointer-events-none absolute -right-10 -top-16 h-40 w-56 rounded-full" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
              {/* Each dt/dd pair is one direct child group of the <dl>; the flex layout lives on the <dl> itself. */}
              <dl className="relative flex flex-wrap items-end justify-between gap-4">
                <div className="min-w-0">
                  <dt className="text-eyebrow text-subtle">Metric</dt>
                  <dd className="mt-1.5 flex flex-wrap items-baseline gap-2">
                    <span className="text-2xl font-semibold tracking-[-0.025em] text-fg">{ev.metric_label}</span>
                    <code className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-muted">{ev.metric}</code>
                  </dd>
                </div>
                <div>
                  <dt className="sr-only">Direction</dt>
                  <dd>
                    <Badge tone={minimize ? "info" : "success"} icon={minimize ? <ArrowDownRight className="h-3 w-3" aria-hidden /> : <ArrowUpRight className="h-3 w-3" aria-hidden />}>
                      {directionLabel(ev.direction)}
                    </Badge>
                  </dd>
                </div>
                <div className="w-full min-w-0 border-t border-border pt-3.5">
                  <dt className="text-eyebrow text-subtle">Submission format</dt>
                  <dd className="mt-1.5 text-sm text-fg">{ev.submission_format}</dd>
                </div>
              </dl>
            </div>
            <Tiles className="rounded-t-none">
              <Tile label="ID column"><code className="font-mono text-xs">{ev.id_column}</code></Tile>
              <Tile label="Target column"><code className="font-mono text-xs">{ev.target_column}</code></Tile>
              <Tile label="Evaluator version"><span className="font-mono text-xs">{ev.evaluator} · {ev.evaluator_version}</span></Tile>
              <Tile label="Schema checks">{ev.strict_schema ? "Strict — extra or missing rows are rejected" : "Lenient"}</Tile>
              {ev.secondary_metrics.length ? (
                <Tile label="Also reported"><span className="font-mono text-xs">{ev.secondary_metrics.join(", ")}</span></Tile>
              ) : null}
            </Tiles>
          </div>
        ) : null}
        {comp.scoring_mode === "judged" ? <RubricCriteria slug={comp.slug} /> : null}
        {comp.evaluation_html ? <Prose html={comp.evaluation_html} className="[&>:first-child]:mt-0" /> : null}
        {comp.scoring_notes_md ? (
          <div>
            <h3 className="text-sm font-semibold text-fg">Scoring notes</h3>
            <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{comp.scoring_notes_md}</p>
          </div>
        ) : null}
        {comp.scoring_mode === "automatic" ? (
          <p className="flex items-start gap-2 text-xs leading-relaxed text-subtle">
            <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan" />
            <span>
              The live leaderboard uses a public subset of the test data; final ranks use the hidden private subset.{" "}
              <Link href={`/competitions/${comp.slug}/leaderboard`} className="text-accent-strong hover:underline">How ranking works</Link>
            </span>
          </p>
        ) : null}
      </div>
    </Block>
  );
}

// ----------------------------------------------------------------------------- timeline

interface KeyDate {
  key: string;
  label: string;
  at: string;
  relative?: boolean;
}

function keyDates(comp: CompetitionDetail): KeyDate[] {
  const list: (KeyDate | null)[] = [
    comp.registration_opens_at ? { key: "reg-open", label: "Registration opens", at: comp.registration_opens_at } : null,
    comp.starts_at ? { key: "start", label: "Competition starts", at: comp.starts_at } : null,
    comp.team_lock_at ? { key: "team-lock", label: "Team changes lock", at: comp.team_lock_at, relative: true } : null,
    comp.registration_closes_at ? { key: "reg-close", label: "Registration closes", at: comp.registration_closes_at, relative: true } : null,
    comp.ends_at ? { key: "end", label: "Competition ends", at: comp.ends_at } : null,
    comp.finalized_at ? { key: "final", label: "Results finalized", at: comp.finalized_at } : null,
  ];
  return list.filter((d): d is KeyDate => d !== null).sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
}

function hasTimeline(comp: CompetitionDetail) {
  return keyDates(comp).length > 0 || comp.schedule.length > 0;
}

function Timeline({ comp }: { comp: CompetitionDetail }) {
  const now = useNow(60_000).getTime();
  const dates = keyDates(comp);
  if (!hasTimeline(comp)) return null;
  return (
    <Block id="timeline" eyebrow="Key dates" title="Timeline" icon={<CalendarDays />}>
      {dates.length ? (
        <ol className="relative grid gap-0 sm:auto-cols-fr sm:grid-flow-col" aria-label="Key dates">
          {dates.map((d, i) => {
            const t = new Date(d.at).getTime();
            const past = t <= now;
            const next = dates[i + 1] ? new Date(dates[i + 1].at).getTime() : null;
            // Fill of the segment to the next date: how much of that interval has really elapsed.
            const fill = next && next > t ? Math.max(0, Math.min(100, ((now - t) / (next - t)) * 100)) : 0;
            const current = past && next !== null && now < next;
            return (
              <li key={d.key} className="relative flex gap-3 pb-6 last:pb-0 sm:block sm:pb-0 sm:pr-4">
                {/* Track: vertical on mobile, horizontal from sm. */}
                {next !== null ? (
                  <span aria-hidden className="absolute left-[5px] top-4 h-[calc(100%-0.5rem)] w-px overflow-hidden bg-border-strong sm:left-4 sm:top-[5px] sm:h-px sm:w-[calc(100%-1rem)]">
                    <span className="absolute inset-x-0 top-0 bg-brand sm:hidden" style={{ height: `${fill}%` }} />
                    <span className="absolute inset-y-0 left-0 hidden bg-brand sm:block" style={{ width: `${fill}%` }} />
                  </span>
                ) : null}
                <span
                  aria-hidden
                  className={cn(
                    "relative mt-1 h-[11px] w-[11px] shrink-0 rounded-full border-2 sm:mt-0 sm:block",
                    past ? "border-transparent bg-accent" : "border-border-strong bg-bg",
                    current && "animate-pulse-ring text-accent",
                  )}
                />
                <div className="min-w-0 sm:mt-3">
                  <p className={past ? "text-eyebrow text-accent-strong" : "text-eyebrow text-subtle"}>
                    {d.label}
                    <span className="sr-only">{past ? " (passed)" : " (upcoming)"}</span>
                  </p>
                  <DateTime value={d.at} eventTimeZone={comp.timezone} relative={d.relative} stacked className="mt-1.5 text-sm font-medium text-fg" />
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}

      {comp.schedule.length ? (
        <div className={cn(dates.length && "mt-8")}>
          <h3 className="mb-3 text-sm font-semibold text-fg">Schedule</h3>
          <ol className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {comp.schedule.map((s) => {
              const past = new Date(s.ends_at ?? s.starts_at).getTime() < now;
              return (
                <li key={s.id} className={cn("flex flex-col gap-2 border-b border-border px-4 py-3.5 last:border-b-0 sm:flex-row sm:items-start sm:gap-5", past && "opacity-70")}>
                  <DateTime value={s.starts_at} eventTimeZone={comp.timezone} stacked className="shrink-0 text-sm font-medium text-fg sm:w-40" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-fg">{s.title}</span>
                      <Badge tone="outline">{titleCase(s.kind)}</Badge>
                      {past ? <Badge>Past</Badge> : null}
                    </div>
                    {s.location ? <p className="mt-1 flex items-center gap-1 text-xs text-muted"><MapPin className="h-3 w-3" aria-hidden /> {s.location}</p> : null}
                    {s.description ? <p className="mt-1 text-xs text-muted">{s.description}</p> : null}
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-accent-strong hover:underline">
                        Join link <ExternalLink className="h-3 w-3" aria-hidden />
                      </a>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </Block>
  );
}

// ----------------------------------------------------------------------------- prizes

function hasPrizes(comp: CompetitionDetail) {
  return comp.has_prize || Boolean(comp.prize_html) || comp.awards.length > 0;
}

function Prizes({ comp }: { comp: CompetitionDetail }) {
  if (!hasPrizes(comp)) return null;
  const showPrize = comp.has_prize || Boolean(comp.prize_html);
  return (
    <Block id="prizes" eyebrow="What's at stake" title={showPrize ? "Prizes" : "Awards"} icon={<Trophy />}>
      <div className="space-y-5">
        {showPrize ? (
          <div className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen px-5 py-5 shadow-card sm:px-6">
            <div aria-hidden className="pointer-events-none absolute -left-10 -top-16 h-40 w-64 rounded-full" style={{ background: "radial-gradient(closest-side, var(--warning-soft), transparent)" }} />
            {comp.prize_summary ? (
              <p className="relative flex items-center gap-3 text-xl font-semibold tracking-[-0.02em] text-fg">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-warning-soft text-warning" aria-hidden>
                  <Trophy className="h-4 w-4" />
                </span>
                {comp.prize_summary}
              </p>
            ) : null}
            <div className={cn("relative", comp.prize_summary && "mt-4")}>
              {comp.prize_html ? <Prose html={comp.prize_html} className="[&>:first-child]:mt-0" /> : <p className="text-sm text-muted">Prize details will be announced by the organizers.</p>}
            </div>
          </div>
        ) : null}

        {comp.awards.length ? (
          <div>
            {showPrize ? <h3 className="mb-3 text-sm font-semibold text-fg">Awards</h3> : null}
            <ul className="grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-2 [&>*]:bg-surface sm:[&>*:last-child:nth-child(odd)]:col-span-2">
              {comp.awards.map((a) => (
                <li key={a.id} className="flex items-start gap-3 px-4 py-3.5">
                  <Trophy className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                  <div className="min-w-0">
                    <p className="font-medium text-fg">{a.name}</p>
                    {a.description ? <p className="text-xs text-muted">{a.description}</p> : null}
                    {a.winner_team_name ? <p className="mt-1 text-xs font-medium text-success">Awarded to {a.winner_team_name}</p> : null}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Block>
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
    <section id="rules" aria-labelledby="rules-heading" className="scroll-mt-20 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
      <h2 id="rules-heading">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="rules-body"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] sm:px-6"
        >
          <span className="flex min-w-0 items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong" aria-hidden>
              <BookOpen className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-eyebrow text-subtle">Read before you join</span>
              <span className="mt-0.5 flex flex-wrap items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg">
                Rules
                {comp.viewer.accepted_rules_at ? <Badge tone="success">Accepted</Badge> : null}
              </span>
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
            <span className="hidden sm:inline">{open ? "Hide" : "Show"}</span>
            <ChevronDown className={cn("h-4 w-4 transition-transform duration-300 ease-out-expo", open && "rotate-180")} aria-hidden />
          </span>
        </button>
      </h2>
      <div id="rules-body" hidden={!open} className="border-t border-border px-5 py-5 sm:px-6">
        {comp.rules_html ? <Prose html={comp.rules_html} className="[&>:first-child]:mt-0" /> : <p className="text-sm text-muted">No additional rules were published. The platform code of conduct applies.</p>}
        {comp.viewer.accepted_rules_at ? (
          <p className="mt-4 text-xs text-subtle">You accepted these rules {relativeTime(comp.viewer.accepted_rules_at)} (rules version {comp.config_version}).</p>
        ) : null}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------- sidebar

function AsideSection({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="px-5 py-4">
      <h2 className="mb-3 text-eyebrow text-subtle">{title}</h2>
      <div className="text-sm">{children}</div>
    </section>
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
  // Registration and team-lock dates are shown on the Timeline track in the main column.
  rows.push({ label: "Event time zone", value: comp.timezone });
  return (
    <AsideSection title="Key details">
      <dl className="divide-y divide-border">
        {rows.map((r) => (
          <div key={r.label} className="flex items-start justify-between gap-4 py-2 first:pt-0 last:pb-0">
            <dt className="text-muted">{r.label}</dt>
            <dd className="tabular text-right font-medium text-fg">{r.value}</dd>
          </div>
        ))}
      </dl>
    </AsideSection>
  );
}

function OnThisPage({ comp }: { comp: CompetitionDetail }) {
  // Only link to Announcements once the block actually renders (it is skipped on error or an empty list).
  const announcements = useAnnouncements(comp);
  const links = [
    announcements.data?.length ? { href: "#announcements", label: "Announcements" } : null,
    { href: "#overview", label: "About" },
    hasEvaluation(comp) ? { href: "#evaluation", label: "Evaluation" } : null,
    hasTimeline(comp) ? { href: "#timeline", label: "Timeline" } : null,
    hasPrizes(comp) ? { href: "#prizes", label: comp.has_prize || comp.prize_html ? "Prizes" : "Awards" } : null,
    { href: "#rules", label: "Rules" },
    comp.faq.length ? { href: "#faq", label: "FAQ" } : null,
  ].filter((l): l is { href: string; label: string } => l !== null);
  return (
    <nav aria-label="On this page" className="hidden px-5 py-4 lg:block">
      <h2 className="mb-2 text-eyebrow text-subtle">On this page</h2>
      <ul className="space-y-0.5">
        {links.map((l) => (
          <li key={l.href}>
            <a
              href={l.href}
              className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
            >
              <span aria-hidden className="h-px w-3 bg-border-strong transition-[width,background-color] duration-300 group-hover:w-4 group-hover:bg-accent" />
              {l.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Sidebar({ comp }: { comp: CompetitionDetail }) {
  const inPerson = comp.format === "offline" || comp.format === "hybrid";
  return (
    <aside aria-label="Competition details" className="min-w-0">
      <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        <OnThisPage comp={comp} />
        <Details comp={comp} />

        {comp.parent ? (
          <AsideSection title="Qualification round">
            <p className="text-muted">This event follows the qualification round:</p>
            <Link href={`/competitions/${comp.parent.slug}`} className="mt-1 block font-medium text-accent-strong hover:underline">{comp.parent.title}</Link>
          </AsideSection>
        ) : null}

        {inPerson && (comp.venue_name || comp.venue_address || comp.venue_notes) ? (
          <AsideSection title="Venue">
            {comp.venue_name ? <p className="font-medium text-fg">{comp.venue_name}</p> : null}
            {comp.venue_address ? (
              <p className="mt-1 flex items-start gap-1.5 text-muted"><MapPin className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {comp.venue_address}</p>
            ) : null}
            {comp.venue_notes ? <p className="mt-2 whitespace-pre-wrap text-muted">{comp.venue_notes}</p> : null}
          </AsideSection>
        ) : null}

        {comp.starter_assets.length ? (
          <AsideSection title="Starter resources">
            <ul className="-mx-2 space-y-0.5">
              {comp.starter_assets.map((a, i) => (
                <li key={`${a.url}-${i}`}>
                  <a href={a.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-fg transition-colors hover:bg-surface-2 hover:text-accent-strong">
                    {assetIcon(a.kind)}
                    <span className="min-w-0 flex-1 truncate">{a.label}</span>
                    <ExternalLink className="h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
                    <span className="sr-only">(opens in a new tab)</span>
                  </a>
                </li>
              ))}
            </ul>
            {comp.starter_asset_version > 1 ? <p className="mt-3 text-xs text-subtle">Resources updated (revision {comp.starter_asset_version}).</p> : null}
          </AsideSection>
        ) : null}

        {comp.sponsors.length ? (
          <AsideSection title="Sponsors">
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
          </AsideSection>
        ) : null}

        {comp.tags.length ? (
          <AsideSection title="Tags">
            <div className="flex flex-wrap gap-1.5">
              {comp.tags.map((t) => (
                <Link
                  key={t}
                  href={`/competitions?tag=${encodeURIComponent(t)}`}
                  className="rounded-full border border-border bg-surface-2 px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:border-border-strong hover:text-fg"
                >
                  #{t}
                </Link>
              ))}
            </div>
          </AsideSection>
        ) : null}

        {comp.organizer_contact_email ? (
          <AsideSection title="Questions?">
            <a href={`mailto:${comp.organizer_contact_email}`} className="inline-flex items-center gap-2 break-all text-accent-strong hover:underline">
              <Mail className="h-4 w-4 shrink-0" aria-hidden /> {comp.organizer_contact_email}
            </a>
            <p className="mt-2 text-xs text-muted">
              For questions others may share, use the <Link href={`/competitions/${comp.slug}/discussion`} className="text-accent-strong hover:underline">discussion forum</Link>.
            </p>
          </AsideSection>
        ) : null}
      </div>
    </aside>
  );
}

// ----------------------------------------------------------------------------- overview

/**
 * Most problem statements open with their own heading (e.g. "## Overview"). Lift that heading into the block
 * title so two section headings don't stack; the HTML is the same server-sanitized markup Prose renders.
 */
const LEADING_HEADING = /^\s*<h([1-3])(?:\s[^>]*)?>([\s\S]*?)<\/h\1>\s*/i;

function splitLeadingHeading(html: string): { heading: string | null; body: string } {
  const m = LEADING_HEADING.exec(html);
  if (!m) return { heading: null, body: html };
  const body = html.slice(m[0].length);
  const text = m[2].replace(/<[^>]*>/g, "").trim();
  // Keep the original markup when the heading is empty, unusually long, or the only content.
  if (!text || text.length > 80 || !body.trim()) return { heading: null, body: html };
  return { heading: m[2], body };
}

function Overview({ comp }: { comp: CompetitionDetail }) {
  const { heading, body } = comp.description_html ? splitLeadingHeading(comp.description_html) : { heading: null, body: "" };
  return (
    <Block
      id="overview"
      eyebrow={heading ? "About this competition" : "Problem statement"}
      title={heading ? <span dangerouslySetInnerHTML={{ __html: heading }} /> : "About this competition"}
      icon={<ScrollText />}
    >
      {comp.description_html ? (
        <Prose html={body} className="[&>:first-child]:mt-0" />
      ) : (
        <EmptyState title="No description yet" description="The organizers haven't written a problem statement yet." className="py-10" />
      )}
    </Block>
  );
}

// ----------------------------------------------------------------------------- page

export default function CompetitionOverviewPage() {
  const comp = useCompetition();
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-12">
      <div className="min-w-0 space-y-12">
        {comp.status === "upcoming" && comp.viewer.is_participant ? (
          <InlineNotice tone="info" title="You're registered">
            Submissions open when the competition starts. Meanwhile, explore the data and form your team.
          </InlineNotice>
        ) : null}

        <Announcements comp={comp} />

        <Overview comp={comp} />

        {hasEvaluation(comp) ? (
          <Reveal>
            <Evaluation comp={comp} />
          </Reveal>
        ) : null}

        {hasTimeline(comp) ? (
          <Reveal>
            <Timeline comp={comp} />
          </Reveal>
        ) : null}

        {hasPrizes(comp) ? (
          <Reveal>
            <Prizes comp={comp} />
          </Reveal>
        ) : null}

        <Rules comp={comp} />

        {comp.faq.length ? (
          <Block id="faq" eyebrow="Good to know" title="Frequently asked questions" icon={<HelpCircle />}>
            <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
              {comp.faq.map((f, i) => (
                <details key={i} className="group border-b border-border last:border-b-0">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-3.5 font-medium text-fg transition-colors hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] [&::-webkit-details-marker]:hidden">
                    {f.q}
                    <ChevronDown className="h-4 w-4 shrink-0 text-muted transition-transform duration-300 group-open:rotate-180" aria-hidden />
                  </summary>
                  <p className="whitespace-pre-wrap px-5 pb-4 text-sm leading-relaxed text-muted">{f.a}</p>
                </details>
              ))}
            </div>
          </Block>
        ) : null}
      </div>
      <Sidebar comp={comp} />
    </div>
  );
}
