"use client";

import { BadgeCheck, BookOpen, CircleDot, Code2, ExternalLink, FileText, GitFork, Globe, Info, PlayCircle, Scale, Star } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Avatar } from "@/components/ui/avatar";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Cover } from "@/components/ui/misc";
import { cn } from "@/lib/cn";
import { compactNumber, formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { RepoName, RepoStat, SyncStatusBadge } from "./repo";
import type { ProjectDetail, ProjectMedia, ProjectMember } from "./types";

/** Main image + thumbnail strip. Falls back to generated cover art when there are no images. */
export function MediaGallery({ media, coverStyle, title }: { media: ProjectMedia[]; coverStyle: string; title: string }) {
  const [index, setIndex] = useState(0);
  if (!media.length) return <Cover style={coverStyle} className="h-48 rounded-[var(--radius-lg)] sm:h-64" />;
  const current = media[Math.min(index, media.length - 1)];
  return (
    <figure className="space-y-3" aria-label={`${title} gallery`}>
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface-2">
        { }
        <img
          src={current.url}
          alt={current.alt_text || `${title} screenshot ${index + 1}`}
          width={current.width}
          height={current.height}
          className="mx-auto max-h-[28rem] w-full object-contain"
        />
      </div>
      {current.alt_text ? <figcaption className="text-xs text-subtle">{current.alt_text}</figcaption> : null}
      {media.length > 1 ? (
        <div className="flex gap-2 overflow-x-auto pb-1" role="group" aria-label="Choose image">
          {media.map((m, i) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setIndex(i)}
              aria-label={`Show image ${i + 1}${m.alt_text ? `: ${m.alt_text}` : ""}`}
              aria-pressed={i === index}
              className={cn(
                "h-16 w-24 shrink-0 overflow-hidden rounded-[var(--radius-md)] border-2 transition-colors",
                i === index ? "border-accent" : "border-transparent opacity-70 hover:opacity-100",
              )}
            >
              { }
              <img src={m.url} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      ) : null}
    </figure>
  );
}

const LINKS: { key: "repo_url" | "demo_url" | "paper_url" | "video_url" | "docs_url"; label: string; icon: ReactNode }[] = [
  { key: "repo_url", label: "Source code", icon: <Code2 className="h-4 w-4" aria-hidden /> },
  { key: "demo_url", label: "Live demo", icon: <Globe className="h-4 w-4" aria-hidden /> },
  { key: "paper_url", label: "Paper", icon: <FileText className="h-4 w-4" aria-hidden /> },
  { key: "video_url", label: "Video", icon: <PlayCircle className="h-4 w-4" aria-hidden /> },
  { key: "docs_url", label: "Documentation", icon: <BookOpen className="h-4 w-4" aria-hidden /> },
];

export function ProjectLinks({ p }: { p: ProjectDetail }) {
  const links = LINKS.filter((l) => p[l.key]);
  if (!links.length) return null;
  return (
    <Card>
      <CardHeader title="Links" />
      <CardBody>
        <ul className="space-y-2">
          {links.map((l) => (
            <li key={l.key}>
              <a
                href={p[l.key] as string}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center gap-2 text-sm text-fg hover:text-accent-strong"
              >
                <span className="text-subtle group-hover:text-accent-strong">{l.icon}</span>
                <span className="font-medium">{l.label}</span>
                <span className="min-w-0 truncate text-xs text-subtle">{hostOf(p[l.key] as string)}</span>
                <ExternalLink className="ml-auto h-3.5 w-3.5 shrink-0 text-subtle" aria-hidden />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const ROLE_TONE: Record<string, "accent" | "info" | "neutral"> = { owner: "accent", maintainer: "info", contributor: "neutral" };

export function MemberRow({ m, children }: { m: ProjectMember; children?: ReactNode }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <Link href={`/u/${m.user.handle}`} className="flex min-w-0 flex-1 items-center gap-3 hover:text-accent-strong">
        <Avatar name={m.user.display_name} src={m.user.avatar_url} size={32} />
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-fg">{m.user.display_name}</span>
          <span className="block truncate text-xs text-subtle">@{m.user.handle}</span>
        </span>
      </Link>
      <Badge tone={ROLE_TONE[m.role] ?? "neutral"}>{titleCase(m.role)}</Badge>
      {children}
    </li>
  );
}

/** GitHub repository stats + an honest explanation of the "verified maintainer" claim. */
export function RepositoryCard({ p, footer }: { p: ProjectDetail; footer?: ReactNode }) {
  const r = p.repository;
  return (
    <Card>
      <CardHeader title="Repository" action={r?.is_demo ? <DemoBadge /> : undefined} />
      <CardBody className="space-y-4 text-sm">
        {r ? (
          <>
            <p className="break-all text-fg"><RepoName fullName={r.full_name} htmlUrl={r.html_url} /></p>
            <dl className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted">
              <RepoStat icon={<Star className="h-3.5 w-3.5" aria-hidden />} label="Stars" value={compactNumber(r.stars)} />
              <RepoStat icon={<GitFork className="h-3.5 w-3.5" aria-hidden />} label="Forks" value={compactNumber(r.forks)} />
              <RepoStat icon={<CircleDot className="h-3.5 w-3.5" aria-hidden />} label="Open issues" value={compactNumber(r.open_issues)} />
              {r.language ? <RepoStat label="Language" value={<span className="font-medium text-fg">{r.language}</span>} /> : null}
              {r.license ? <RepoStat icon={<Scale className="h-3.5 w-3.5" aria-hidden />} label="License" value={r.license} /> : null}
            </dl>
            <div className="flex flex-wrap items-center gap-2 text-xs text-subtle">
              <SyncStatusBadge status={r.sync_status} isDemo={r.is_demo} />
              {r.last_synced_at ? <span title={formatDateTime(r.last_synced_at)}>Last synced {relativeTime(r.last_synced_at)}</span> : <span>Not synced yet</span>}
            </div>
            {r.is_demo ? <p className="text-xs text-subtle">Stats for demo repositories are synthetic and not fetched from GitHub.</p> : null}
          </>
        ) : p.repo_url ? (
          <p className="text-muted">
            The linked repository isn&apos;t registered in the open source hub yet, so live stats and maintainer verification aren&apos;t available.
          </p>
        ) : (
          <p className="text-muted">No repository linked.</p>
        )}
        <div className={cn("rounded-[var(--radius-md)] border px-3 py-2.5 text-xs", p.maintainer_verified ? "border-success/30 bg-success-soft" : "border-border bg-surface-2")}>
          <p className={cn("flex items-center gap-1.5 font-semibold", p.maintainer_verified ? "text-success" : "text-muted")}>
            {p.maintainer_verified ? <BadgeCheck className="h-3.5 w-3.5" aria-hidden /> : <Info className="h-3.5 w-3.5" aria-hidden />}
            {p.maintainer_verified ? "Verified maintainer" : "Maintainer not verified"}
          </p>
          <p className="mt-1 text-muted">
            {p.maintainer_verified
              ? `The project owner's linked GitHub account owns ${r?.full_name ?? "the registered repository"}. This is re-checked every time the project is saved.`
              : "A project is verified when it links a registered GitHub repository and the owner's GitHub account (linked in settings) owns that repository."}
          </p>
        </div>
        {footer}
      </CardBody>
    </Card>
  );
}
