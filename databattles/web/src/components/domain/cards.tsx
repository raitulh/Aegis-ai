"use client";

import { BadgeCheck, BookOpen, Clock, Code2, Database, Download, ExternalLink, FileStack, FolderGit2, GitFork, Layers, Star, Trophy, Users } from "lucide-react";
import Link from "next/link";

import { useTilt } from "@/components/motion/tilt";
import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Cover, ProgressBar } from "@/components/ui/misc";
import { cn } from "@/lib/cn";
import { compactNumber, formatBytes, formatDate, relativeTime, titleCase } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import type { CompetitionCard as CompetitionCardT, CourseCard as CourseCardT, DatasetCard as DatasetCardT, OrgCard as OrgCardT, ProjectCard as ProjectCardT, UserMini } from "@/lib/types";

/**
 * Interactive card shell: lifts, tilts toward the cursor and catches a soft spotlight (desktop only;
 * `--tilt` is unset on touch and under reduced motion, so the card simply lifts).
 */
const cardCls =
  "group spotlight relative flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card " +
  "[transform:var(--tilt)] transition-[transform,box-shadow,border-color] duration-500 ease-out-expo will-change-transform " +
  "hover:border-border-strong hover:shadow-elevated focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";

const glassChip = "border-0 bg-black/45 text-white ring-white/15 backdrop-blur-md";

export function UserLink({ user, size = 24, className }: { user: UserMini | null | undefined; size?: number; className?: string }) {
  if (!user) return <span className="text-sm text-subtle">Deleted user</span>;
  return (
    <Link href={`/u/${user.handle}`} className={cn("inline-flex min-w-0 items-center gap-2 text-sm text-fg transition-colors hover:text-accent-strong", className)}>
      <Avatar name={user.display_name} src={user.avatar_url} size={size} />
      <span className="truncate">{user.display_name}</span>
    </Link>
  );
}

/** Where the competition is in its own timeline — computed from its real start/end dates. */
function Timeline({ c }: { c: CompetitionCardT }) {
  const now = useNow(60_000).getTime();
  const start = c.starts_at ? new Date(c.starts_at).getTime() : null;
  const end = c.ends_at ? new Date(c.ends_at).getTime() : null;
  if (c.status !== "active" || !start || !end || end <= start) return null;
  const pct = Math.max(0, Math.min(100, ((now - start) / (end - start)) * 100));
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex justify-between font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">
        <span>Timeline</span>
        <span className="tabular">{Math.round(pct)}% elapsed</span>
      </div>
      <ProgressBar value={pct} label={`${c.title}: ${Math.round(pct)}% of the competition window has elapsed`} />
    </div>
  );
}

export function CompetitionCard({ c, featured = false }: { c: CompetitionCardT; featured?: boolean }) {
  const ref = useTilt<HTMLAnchorElement>({ max: featured ? 3 : 5 });
  const deadline = c.status === "upcoming" ? c.starts_at : c.ends_at;
  return (
    <Link ref={ref} href={`/competitions/${c.slug}`} className={cardCls}>
      <Cover style={c.cover_style} interactive className={featured ? "h-44 shrink-0 sm:h-56 lg:h-auto lg:min-h-56 lg:flex-1" : "h-32 shrink-0"}>
        <div className="absolute inset-x-3 top-3 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={c.status} className={glassChip} />
          {c.is_demo ? <DemoBadge /> : null}
          <Badge className={cn(glassChip, "ml-auto")}>{titleCase(c.difficulty)}</Badge>
        </div>
        <div className="absolute inset-x-3 bottom-3 flex items-end justify-between gap-2">
          <span className="truncate rounded-md bg-black/40 px-2 py-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-white/85 backdrop-blur-md">
            {titleCase(c.event_type)} · {titleCase(c.task_type)}
          </span>
          {c.has_prize ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-black/45 px-2 py-0.5 text-xs text-white ring-1 ring-inset ring-amber-300/30 backdrop-blur-md">
              <Trophy className="h-3 w-3 text-amber-300" aria-hidden /> {c.prize_summary ?? "Prizes"}
            </span>
          ) : null}
        </div>
      </Cover>
      <div className="relative z-10 flex flex-1 flex-col p-4 sm:p-5">
        {c.metric ? (
          <span className="mb-2 inline-flex w-fit items-center gap-1 rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">
            <span className="text-subtle">metric</span> {c.metric}
          </span>
        ) : null}
        <h3 className={cn("line-clamp-2 font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong", featured ? "text-xl sm:text-2xl" : "text-[15.5px]")}>
          {c.title}
        </h3>
        <p className={cn("mt-1.5 text-sm leading-relaxed text-muted", featured ? "line-clamp-3" : "line-clamp-2")}>{c.summary}</p>
        <Timeline c={c} />
        <div aria-hidden className={cn("min-h-4", !featured && "grow")} />
        <div className="flex items-center justify-between gap-3 border-t border-border pt-3.5 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5" title={`${c.participant_count} participants`}>
            <Users className="h-3.5 w-3.5 text-subtle" aria-hidden /> <span className="tabular">{compactNumber(c.participant_count)}</span>
            <span className="sr-only">participants</span>
          </span>
          {deadline ? (
            <span className="inline-flex items-center gap-1.5" title={formatDate(deadline, { dateStyle: "full", timeStyle: "short" })}>
              <Clock className="h-3.5 w-3.5 text-subtle" aria-hidden />
              {c.status === "upcoming" ? "Starts" : c.status === "active" ? "Ends" : "Ended"} {relativeTime(deadline)}
            </span>
          ) : null}
          {c.host ? (
            <span className="inline-flex min-w-0 items-center gap-1 truncate">
              <span className="truncate">{c.host.name}</span>
              {c.host.verification_status === "verified" ? <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-success" aria-label="Verified organization" /> : null}
            </span>
          ) : null}
        </div>
      </div>
    </Link>
  );
}

export function DatasetCard({ d }: { d: DatasetCardT }) {
  const ref = useTilt<HTMLAnchorElement>({ max: 4 });
  const owner = d.owner_org?.name ?? d.owner?.display_name ?? null;
  return (
    <Link ref={ref} href={`/datasets/${d.slug}`} className={cn(cardCls, "p-4 sm:p-5")}>
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-20 dot-grid opacity-50 [mask-image:linear-gradient(to_bottom,black,transparent)]" />
      <div className="relative z-10 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-cyan-soft text-cyan shadow-[inset_0_1px_0_var(--hairline-highlight)]">
          <Database className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <h3 className="truncate text-[15.5px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{d.title}</h3>
          {owner ? <p className="truncate text-xs text-subtle">{owner}</p> : null}
        </div>
      </div>
      {d.subtitle ? <p className="relative z-10 mt-3 line-clamp-2 text-sm leading-relaxed text-muted">{d.subtitle}</p> : null}
      <div className="relative z-10 mt-3 flex flex-wrap gap-1.5">
        {d.is_demo ? <DemoBadge /> : null}
        <Badge tone="outline">{d.license_name}</Badge>
        {d.tags.slice(0, 3).map((t) => (
          <Badge key={t}>{t}</Badge>
        ))}
      </div>
      <div aria-hidden className="min-h-4 grow" />
      <dl className="relative z-10 grid grid-cols-4 gap-2 border-t border-border pt-3.5 text-xs">
        <div>
          <dt className="sr-only">Files</dt>
          <dd className="flex items-center gap-1 text-muted"><FileStack className="h-3.5 w-3.5 text-subtle" aria-hidden /><span className="tabular">{d.file_count}</span></dd>
        </div>
        <div>
          <dt className="sr-only">Size</dt>
          <dd className="tabular text-muted">{formatBytes(d.total_bytes)}</dd>
        </div>
        <div>
          <dt className="sr-only">Downloads</dt>
          <dd className="flex items-center gap-1 text-muted"><Download className="h-3.5 w-3.5 text-subtle" aria-hidden /><span className="tabular">{compactNumber(d.download_count)}</span></dd>
        </div>
        <div className="text-right">
          <dt className="sr-only">Version and last update</dt>
          <dd className="font-mono text-[11px] text-subtle" title={`Updated ${relativeTime(d.updated_at)}`}>v{d.latest_version ?? "—"}</dd>
        </div>
      </dl>
      <p className="relative z-10 mt-2 text-[11px] text-subtle">Updated {relativeTime(d.updated_at)}</p>
    </Link>
  );
}

export function ProjectCard({ p }: { p: ProjectCardT }) {
  const ref = useTilt<HTMLAnchorElement>({ max: 4 });
  return (
    <Link ref={ref} href={`/projects/${p.slug}`} className={cardCls}>
      {p.cover_image_url ? (
        <div className="relative h-36 overflow-hidden">
          <img src={p.cover_image_url} alt="" className="h-full w-full object-cover transition-transform duration-700 ease-out-expo group-hover:scale-[1.05]" />
          <div aria-hidden className="absolute inset-0 bg-gradient-to-t from-black/35 to-transparent" />
        </div>
      ) : (
        <Cover style={p.cover_style} interactive className="h-36" />
      )}
      <div className="relative z-10 flex flex-1 flex-col p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-1.5">
          {p.is_featured ? <Badge tone="accent" icon={<Star className="h-3 w-3" />}>Featured</Badge> : null}
          {p.is_open_source ? <Badge tone="info" icon={<GitFork className="h-3 w-3" />}>Open source</Badge> : null}
          {p.maintainer_verified ? <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" />} title="Owner's linked GitHub account owns the repository">Verified maintainer</Badge> : null}
          {p.is_demo ? <DemoBadge /> : null}
        </div>
        <h3 className="mt-2.5 text-[15.5px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{p.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted">{p.summary}</p>
        {p.technologies.length ? (
          <div className="mt-3 flex flex-wrap gap-1">
            {p.technologies.slice(0, 4).map((t) => (
              <span key={t} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{t}</span>
            ))}
            {p.technologies.length > 4 ? <span className="px-1 py-0.5 font-mono text-[10.5px] text-subtle">+{p.technologies.length - 4}</span> : null}
          </div>
        ) : null}
        <div aria-hidden className="min-h-4 grow" />
        <div className="flex items-center justify-between gap-2 border-t border-border pt-3.5">
          {p.owner ? (
            <span className="inline-flex min-w-0 items-center gap-2 text-xs text-muted">
              <Avatar name={p.owner.display_name} src={p.owner.avatar_url} size={20} />
              <span className="truncate">{p.owner.display_name}</span>
            </span>
          ) : (
            <span />
          )}
          <span className="flex shrink-0 items-center gap-2 text-subtle">
            {p.repo_url ? <FolderGit2 className="h-3.5 w-3.5" aria-label="Has a repository" /> : null}
            {p.demo_url ? <ExternalLink className="h-3.5 w-3.5" aria-label="Has a live demo" /> : null}
            {!p.repo_url && !p.demo_url ? <Code2 className="h-3.5 w-3.5" aria-hidden /> : null}
          </span>
        </div>
      </div>
    </Link>
  );
}

export function CourseCard({ c }: { c: CourseCardT }) {
  const ref = useTilt<HTMLAnchorElement>({ max: 4 });
  const hours = Math.round((c.estimated_minutes / 60) * 10) / 10;
  return (
    <Link ref={ref} href={`/learn/${c.slug}`} className={cardCls}>
      <Cover style={c.cover_style} interactive className="h-28">
        <div className="absolute inset-x-3 top-3 flex gap-1.5">
          <Badge className={glassChip}>{titleCase(c.difficulty)}</Badge>
          {c.is_demo ? <DemoBadge /> : null}
        </div>
        <span className="absolute bottom-3 left-3 flex h-8 w-8 items-center justify-center rounded-lg bg-black/40 text-white ring-1 ring-inset ring-white/15 backdrop-blur-md">
          <BookOpen className="h-4 w-4" aria-hidden />
        </span>
      </Cover>
      <div className="relative z-10 flex flex-1 flex-col p-4 sm:p-5">
        <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-subtle">
          {titleCase(c.category)} · {hours}h{c.lesson_count ? ` · ${c.lesson_count} lessons` : ""}
        </span>
        <h3 className="mt-1.5 text-[15.5px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{c.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted">{c.summary}</p>
        <div className="mt-auto pt-4">
          {c.progress.enrolled ? (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-muted">
                <span className={c.progress.completed ? "text-success" : undefined}>{c.progress.completed ? "Completed" : "In progress"}</span>
                <span className="tabular">{c.progress.percent}%</span>
              </div>
              <ProgressBar value={c.progress.percent} label={`${c.title} progress`} />
            </div>
          ) : (
            <div className="flex items-center gap-1.5 border-t border-border pt-3.5 text-xs text-subtle">
              {c.issues_certificate ? <Badge tone="outline" icon={<BadgeCheck className="h-3 w-3" />}>Certificate</Badge> : null}
              {c.has_badge ? <Badge tone="outline" icon={<Layers className="h-3 w-3" />}>Badge</Badge> : null}
              <span className="tabular ml-auto">{compactNumber(c.enrollment_count)} learners</span>
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}

export function OrgCard({ o }: { o: OrgCardT }) {
  const ref = useTilt<HTMLAnchorElement>({ max: 4 });
  return (
    <Link ref={ref} href={`/orgs/${o.slug}`} className={cn(cardCls, "p-4 sm:p-5")}>
      <div
        aria-hidden
        className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full opacity-25 blur-2xl transition-opacity duration-500 group-hover:opacity-45"
        style={{ background: o.accent_color ?? "var(--accent)" }}
      />
      <div className="relative z-10 flex items-center gap-3">
        {o.logo_url ? (
          <img src={o.logo_url} alt="" className="h-12 w-12 rounded-xl border border-border object-cover" />
        ) : (
          <span
            className="flex h-12 w-12 items-center justify-center rounded-xl text-lg font-bold text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.25)]"
            style={{ background: `linear-gradient(135deg, ${o.accent_color ?? "var(--accent)"}, color-mix(in oklab, ${o.accent_color ?? "var(--accent)"} 55%, black))` }}
          >
            {o.name[0]}
          </span>
        )}
        <div className="min-w-0">
          <h3 className="truncate text-[15.5px] font-semibold tracking-[-0.015em] text-fg transition-colors group-hover:text-accent-strong">{o.name}</h3>
          <p className="text-xs text-subtle">{titleCase(o.type)}{o.city ? ` · ${o.city}` : ""}</p>
        </div>
      </div>
      {o.tagline ? <p className="relative z-10 mt-3 line-clamp-2 text-sm leading-relaxed text-muted">{o.tagline}</p> : null}
      <div aria-hidden className="min-h-4 grow" />
      <div className="relative z-10 flex flex-wrap items-center gap-1.5 border-t border-border pt-3.5">
        {o.verification_status === "verified" ? <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" />}>Verified</Badge> : <StatusBadge status={o.verification_status} />}
        {o.is_demo ? <DemoBadge /> : null}
        <span className="ml-auto inline-flex items-center gap-1 text-xs text-subtle">
          <Users className="h-3.5 w-3.5" aria-hidden /> <span className="tabular">{compactNumber(o.member_count)}</span> members
        </span>
      </div>
    </Link>
  );
}
