import { BadgeCheck, Clock, Database, GitFork, Star, Trophy, Users } from "lucide-react";
import Link from "next/link";

import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Cover, ProgressBar } from "@/components/ui/misc";
import { cn } from "@/lib/cn";
import { compactNumber, formatBytes, formatDate, relativeTime, titleCase } from "@/lib/format";
import type { CompetitionCard as CompetitionCardT, CourseCard as CourseCardT, DatasetCard as DatasetCardT, OrgCard as OrgCardT, ProjectCard as ProjectCardT, UserMini } from "@/lib/types";

const cardCls = "group flex flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-card";

export function UserLink({ user, size = 24, className }: { user: UserMini | null | undefined; size?: number; className?: string }) {
  if (!user) return <span className="text-sm text-subtle">Deleted user</span>;
  return (
    <Link href={`/u/${user.handle}`} className={cn("inline-flex min-w-0 items-center gap-2 text-sm text-fg hover:text-accent-strong", className)}>
      <Avatar name={user.display_name} src={user.avatar_url} size={size} />
      <span className="truncate">{user.display_name}</span>
    </Link>
  );
}

export function CompetitionCard({ c }: { c: CompetitionCardT }) {
  const deadline = c.status === "upcoming" ? c.starts_at : c.ends_at;
  return (
    <Link href={`/competitions/${c.slug}`} className={cardCls}>
      <Cover style={c.cover_style} className="h-28">
        <div className="absolute inset-x-3 top-3 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={c.status} className="bg-black/40 text-white backdrop-blur" />
          {c.is_demo ? <DemoBadge /> : null}
        </div>
        {c.has_prize ? (
          <span className="absolute bottom-3 right-3 inline-flex items-center gap-1 rounded-full bg-black/45 px-2 py-0.5 text-xs text-white backdrop-blur">
            <Trophy className="h-3 w-3" /> {c.prize_summary ?? "Prizes"}
          </span>
        ) : null}
      </Cover>
      <div className="flex flex-1 flex-col p-4">
        <div className="flex items-center gap-2 text-xs text-subtle">
          <span>{titleCase(c.event_type)}</span>·<span>{titleCase(c.task_type)}</span>
          {c.metric ? <>·<span className="font-mono">{c.metric}</span></> : null}
        </div>
        <h3 className="mt-1.5 line-clamp-2 font-semibold text-fg group-hover:text-accent-strong">{c.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{c.summary}</p>
        <div className="mt-auto flex items-center justify-between gap-2 pt-4 text-xs text-muted">
          <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" /> {compactNumber(c.participant_count)}</span>
          {deadline ? (
            <span className="inline-flex items-center gap-1" title={formatDate(deadline, { dateStyle: "full", timeStyle: "short" })}>
              <Clock className="h-3.5 w-3.5" /> {c.status === "upcoming" ? "Starts" : c.status === "active" ? "Ends" : "Ended"} {relativeTime(deadline)}
            </span>
          ) : null}
          {c.host ? <span className="truncate">{c.host.name}</span> : null}
        </div>
      </div>
    </Link>
  );
}

export function DatasetCard({ d }: { d: DatasetCardT }) {
  return (
    <Link href={`/datasets/${d.slug}`} className={cn(cardCls, "p-4")}>
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-strong"><Database className="h-4 w-4" /></span>
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-fg group-hover:text-accent-strong">{d.title}</h3>
          <p className="line-clamp-2 text-sm text-muted">{d.subtitle}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {d.is_demo ? <DemoBadge /> : null}
        <Badge tone="outline">{d.license_name}</Badge>
        {d.tags.slice(0, 3).map((t) => <Badge key={t}>{t}</Badge>)}
      </div>
      <div className="mt-auto flex items-center justify-between pt-4 text-xs text-subtle">
        <span>{d.file_count} files · {formatBytes(d.total_bytes)}</span>
        <span>v{d.latest_version ?? "—"} · {relativeTime(d.updated_at)}</span>
      </div>
    </Link>
  );
}

export function ProjectCard({ p }: { p: ProjectCardT }) {
  return (
    <Link href={`/projects/${p.slug}`} className={cardCls}>
      {p.cover_image_url ? (
         
        <img src={p.cover_image_url} alt="" className="h-32 w-full object-cover" />
      ) : (
        <Cover style={p.cover_style} className="h-32" />
      )}
      <div className="flex flex-1 flex-col p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {p.is_featured ? <Badge tone="accent" icon={<Star className="h-3 w-3" />}>Featured</Badge> : null}
          {p.is_open_source ? <Badge tone="info" icon={<GitFork className="h-3 w-3" />}>Open source</Badge> : null}
          {p.maintainer_verified ? <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" />} title="Owner's linked GitHub account owns the repository">Verified maintainer</Badge> : null}
          {p.is_demo ? <DemoBadge /> : null}
        </div>
        <h3 className="mt-2 font-semibold text-fg group-hover:text-accent-strong">{p.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{p.summary}</p>
        <div className="mt-3 flex flex-wrap gap-1">
          {p.technologies.slice(0, 4).map((t) => <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{t}</span>)}
        </div>
        {p.owner ? <div className="mt-auto pt-4"><span className="text-xs text-subtle">by {p.owner.display_name}</span></div> : null}
      </div>
    </Link>
  );
}

export function CourseCard({ c }: { c: CourseCardT }) {
  return (
    <Link href={`/learn/${c.slug}`} className={cardCls}>
      <Cover style={c.cover_style} className="h-24">
        <div className="absolute left-3 top-3 flex gap-1.5">
          <Badge className="bg-black/40 text-white backdrop-blur">{titleCase(c.difficulty)}</Badge>
          {c.is_demo ? <DemoBadge /> : null}
        </div>
      </Cover>
      <div className="flex flex-1 flex-col p-4">
        <span className="text-xs text-subtle">{titleCase(c.category)} · {Math.round(c.estimated_minutes / 60 * 10) / 10}h{c.lesson_count ? ` · ${c.lesson_count} lessons` : ""}</span>
        <h3 className="mt-1 font-semibold text-fg group-hover:text-accent-strong">{c.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{c.summary}</p>
        <div className="mt-auto pt-4">
          {c.progress.enrolled ? (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs text-muted"><span>{c.progress.completed ? "Completed" : "In progress"}</span><span>{c.progress.percent}%</span></div>
              <ProgressBar value={c.progress.percent} label={`${c.title} progress`} />
            </div>
          ) : (
            <div className="flex gap-1.5 text-xs text-subtle">
              {c.issues_certificate ? <Badge tone="outline">Certificate</Badge> : null}
              {c.has_badge ? <Badge tone="outline">Badge</Badge> : null}
              <span className="ml-auto">{compactNumber(c.enrollment_count)} learners</span>
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}

export function OrgCard({ o }: { o: OrgCardT }) {
  return (
    <Link href={`/orgs/${o.slug}`} className={cn(cardCls, "p-4")}>
      <div className="flex items-center gap-3">
        {o.logo_url ? (
           
          <img src={o.logo_url} alt="" className="h-11 w-11 rounded-lg object-cover" />
        ) : (
          <span className="flex h-11 w-11 items-center justify-center rounded-lg text-lg font-bold text-white" style={{ background: o.accent_color ?? "var(--accent)" }}>
            {o.name[0]}
          </span>
        )}
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-fg group-hover:text-accent-strong">{o.name}</h3>
          <p className="text-xs text-subtle">{titleCase(o.type)}{o.city ? ` · ${o.city}` : ""}</p>
        </div>
      </div>
      {o.tagline ? <p className="mt-3 line-clamp-2 text-sm text-muted">{o.tagline}</p> : null}
      <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-4">
        {o.verification_status === "verified" ? <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" />}>Verified</Badge> : <StatusBadge status={o.verification_status} />}
        {o.is_demo ? <DemoBadge /> : null}
        <span className="ml-auto text-xs text-subtle">{compactNumber(o.member_count)} members</span>
      </div>
    </Link>
  );
}
