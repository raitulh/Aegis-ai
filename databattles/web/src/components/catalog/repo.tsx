"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowUpRight, CircleDot, ExternalLink, FolderGit2, GitFork, MessageSquare, Pin, Plus, RefreshCw, Scale, Sparkles, Star, Users } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/form";
import { InlineNotice, Skeleton } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import { describeError } from "./errors";
import type { IssueRow, RepoCard } from "./types";

import { osKeys } from "./os-keys";

export { osKeys };

const SYNC: Record<string, { tone: "success" | "warning" | "danger" | "neutral" | "info"; label: string }> = {
  ok: { tone: "success", label: "Synced" },
  pending: { tone: "info", label: "Sync pending" },
  failed: { tone: "danger", label: "Sync failed" },
  rate_limited: { tone: "warning", label: "Rate limited" },
  gone: { tone: "neutral", label: "Unavailable" },
};

export function SyncStatusBadge({ status, isDemo }: { status: string; isDemo?: boolean }) {
  if (isDemo) return <Badge tone="neutral" title="Demo repositories are never synced with GitHub">Not synced (demo)</Badge>;
  const s = SYNC[status] ?? { tone: "neutral" as const, label: status };
  return (
    <Badge tone={s.tone}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {s.label}
    </Badge>
  );
}

/** Repository name that only links to GitHub when there is a real URL (seeded demo repos have none). */
export function RepoName({ fullName, htmlUrl, className }: { fullName: string; htmlUrl: string | null | undefined; className?: string }) {
  if (!htmlUrl) return <span className={cn("font-mono", className)}>{fullName}</span>;
  return (
    <a href={htmlUrl} target="_blank" rel="noopener noreferrer" className={cn("inline-flex items-center gap-1 font-mono hover:text-accent-strong hover:underline", className)}>
      {fullName}
      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
      <span className="sr-only">(opens GitHub in a new tab)</span>
    </a>
  );
}

export function SyncButton({ repo, size = "sm" }: { repo: Pick<RepoCard, "id" | "full_name" | "is_demo">; size?: "sm" | "md" }) {
  const sync = useApiMutation(
    () =>
      post<{ message: string }>(`/opensource/repos/${repo.id}/sync`).catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) {
          throw new ApiError(403, e.code, "Only the person who registered this repository, maintainers of a linked project, or moderators can request a sync.", e.details, e.requestId);
        }
        throw e;
      }),
    { success: (d) => d.message || "Sync queued.", invalidate: [osKeys.all] },
  );
  if (repo.is_demo) return null;
  return (
    <Button
      variant="secondary"
      size={size}
      loading={sync.isPending}
      onClick={() => sync.mutate(undefined)}
      className="max-sm:h-9"
      icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />}
      aria-label={`Sync ${repo.full_name} now`}
    >
      Sync now
    </Button>
  );
}

/** One stat in a <dl>; the label is visible to screen readers and as a tooltip. */
export function RepoStat({ icon, label, value }: { icon?: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-1" title={label}>
      <dt className="inline-flex items-center">
        {icon}
        <span className="sr-only">{label}</span>
      </dt>
      <dd>{value}</dd>
    </div>
  );
}

/**
 * Repository title: the owner is quiet, the repository name carries the weight. Links to GitHub only when
 * there is a real URL (seeded demo repos have none) — the same rule as `RepoName`.
 */
function RepoTitle({ fullName, htmlUrl, className }: { fullName: string; htmlUrl: string | null | undefined; className?: string }) {
  const slash = fullName.indexOf("/");
  const owner = slash > 0 ? fullName.slice(0, slash + 1) : "";
  const name = slash > 0 ? fullName.slice(slash + 1) : fullName;
  const text = (
    <span className="break-all">
      {owner ? <span className="font-normal text-subtle">{owner}</span> : null}
      <span className="text-fg transition-colors group-hover/name:text-accent-strong">{name}</span>
    </span>
  );
  if (!htmlUrl) return <span className={cn("font-mono", className)}>{text}</span>;
  return (
    <a
      href={htmlUrl}
      target="_blank"
      rel="noopener noreferrer"
      className={cn("group/name inline-flex items-baseline gap-1 rounded-sm font-mono hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]", className)}
    >
      {text}
      <ExternalLink className="h-3 w-3 shrink-0 self-center text-subtle" aria-hidden />
      <span className="sr-only">(opens GitHub in a new tab)</span>
    </a>
  );
}

/** Language and licence as a quiet facts line (each fact is labelled for screen readers). */
function RepoFacts({ repo, className }: { repo: Pick<RepoCard, "language" | "license">; className?: string }) {
  if (!repo.language && !repo.license) return null;
  return (
    <p className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted", className)}>
      {repo.language ? (
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-brand" aria-hidden />
          <span className="sr-only">Language: </span>
          {repo.language}
        </span>
      ) : null}
      {repo.license ? (
        <span className="inline-flex items-center gap-1">
          <Scale className="h-3.5 w-3.5 text-subtle" aria-hidden />
          <span className="sr-only">License: </span>
          {repo.license}
        </span>
      ) : null}
    </p>
  );
}

function TopicList({ topics, max = 6, className }: { topics: string[]; max?: number; className?: string }) {
  if (!topics.length) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1", className)} aria-label="Topics">
      {topics.slice(0, max).map((t) => (
        <li key={t} className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{t}</li>
      ))}
      {topics.length > max ? <li className="px-1 py-0.5 font-mono text-[10.5px] text-subtle">+{topics.length - max}</li> : null}
    </ul>
  );
}

/** Sync state, last sync time and the repository's links/actions — shared by the card and the row. */
function RepoFooter({ repo, canSync, className }: { repo: RepoCard; canSync?: boolean; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-xs text-subtle", className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <SyncStatusBadge status={repo.sync_status} isDemo={repo.is_demo} />
        {repo.last_synced_at ? <span title={formatDateTime(repo.last_synced_at)}>Last synced {relativeTime(repo.last_synced_at)}</span> : <span>Never synced</span>}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {repo.project ? (
          <Link href={`/projects/${repo.project.slug}`} className="inline-flex min-h-8 items-center gap-1 text-accent-strong hover:underline">
            Project: {repo.project.title}
          </Link>
        ) : null}
        <Link href={`/open-source/issues?repo_id=${repo.id}`} className="inline-flex min-h-8 items-center gap-0.5 text-accent-strong hover:underline">
          Issues <ArrowUpRight className="h-3 w-3" aria-hidden />
        </Link>
        {canSync ? <SyncButton repo={repo} /> : null}
      </div>
    </div>
  );
}

const REPO_METRICS = [
  { key: "stars", label: "stars", sr: "Stars", icon: Star },
  { key: "forks", label: "forks", sr: "Forks", icon: GitFork },
  { key: "open_issues", label: "open issues", sr: "Open issues", icon: CircleDot },
  { key: "contributors_count", label: "contributors", sr: "Contributors", icon: Users },
] as const;

export function RepoCardView({ repo, canSync }: { repo: RepoCard; canSync?: boolean }) {
  return (
    <article className="group relative flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card transition-[border-color,box-shadow] duration-300 hover:border-border-strong hover:shadow-elevated">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-20 dot-grid opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent)]" />
      <div className="relative flex flex-1 flex-col p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-accent-soft text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)]">
            <FolderGit2 className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-[14.5px] font-semibold leading-snug tracking-[-0.01em]">
              <RepoTitle fullName={repo.full_name} htmlUrl={repo.html_url} />
            </h3>
            <RepoFacts repo={repo} className="mt-1" />
          </div>
        </div>
        {repo.is_demo || repo.is_archived ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {repo.is_demo ? <DemoBadge /> : null}
            {repo.is_archived ? <Badge tone="neutral" icon={<Archive className="h-3 w-3" aria-hidden />}>Archived</Badge> : null}
          </div>
        ) : null}
        {repo.description ? (
          <p className="mt-3 line-clamp-2 text-sm leading-relaxed text-muted">{repo.description}</p>
        ) : (
          <p className="mt-3 text-sm text-subtle">No description.</p>
        )}
        <TopicList topics={repo.topics} className="mt-3" />
        <div aria-hidden className="min-h-4 grow" />
        <dl className="grid grid-cols-4 gap-px overflow-hidden rounded-[var(--radius-md)] border border-border bg-border">
          {REPO_METRICS.map((m) => (
            <div key={m.key} className="min-w-0 bg-bg-elevated px-2.5 py-2">
              <dt className="sr-only">{m.sr}</dt>
              <dd className="tabular flex items-center gap-1 text-sm font-semibold text-fg">
                <m.icon className="h-3 w-3 shrink-0 text-subtle" aria-hidden />
                {compactNumber(repo[m.key])}
              </dd>
              <dd aria-hidden className="mt-0.5 truncate text-[11px] text-subtle">{m.label}</dd>
            </div>
          ))}
        </dl>
        <RepoFooter repo={repo} canSync={canSync} className="mt-3 border-t border-border pt-3" />
      </div>
    </article>
  );
}

/**
 * Dense catalogue row for ranked repository lists (the hub's "popular repositories"). Shows the same facts,
 * links and sync action as `RepoCardView`.
 */
export function RepoRow({ repo, rank, canSync }: { repo: RepoCard; rank: number; canSync?: boolean }) {
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 px-4 py-4 transition-colors hover:bg-surface-2/40 sm:px-5 md:grid-cols-[auto_minmax(0,1fr)_auto]">
      <span className="tabular mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-accent-soft font-mono text-[11px] text-accent-strong">
        <span aria-hidden>{String(rank).padStart(2, "0")}</span>
        <span className="sr-only">Rank {rank}</span>
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h3 className="min-w-0 text-[14.5px] font-semibold leading-snug tracking-[-0.01em]">
            <RepoTitle fullName={repo.full_name} htmlUrl={repo.html_url} />
          </h3>
          {repo.is_demo ? <DemoBadge /> : null}
          {repo.is_archived ? <Badge tone="neutral" icon={<Archive className="h-3 w-3" aria-hidden />}>Archived</Badge> : null}
        </div>
        {repo.description ? (
          <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted">{repo.description}</p>
        ) : (
          <p className="mt-1 text-sm text-subtle">No description.</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <RepoFacts repo={repo} />
          <TopicList topics={repo.topics} max={3} />
        </div>
        <RepoFooter repo={repo} canSync={canSync} className="mt-3" />
      </div>
      <dl className="col-start-2 mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs md:col-start-3 md:row-start-1 md:mt-0 md:grid md:grid-cols-[repeat(3,4.25rem)] md:gap-x-2 md:text-right">
        {REPO_METRICS.slice(0, 3).map((m) => (
          <div key={m.key} className="min-w-0">
            <dt className="sr-only">{m.sr}</dt>
            <dd className="tabular inline-flex items-center gap-1 text-sm font-semibold text-fg md:justify-end">
              <m.icon className="h-3 w-3 shrink-0 text-subtle" aria-hidden />
              {compactNumber(repo[m.key])}
            </dd>
            <dd aria-hidden className="text-[11px] text-subtle">{m.label}</dd>
          </div>
        ))}
      </dl>
    </li>
  );
}

export function IssueItem({ issue, canPromote }: { issue: IssueRow; canPromote?: boolean }) {
  const promote = useApiMutation(
    (promoted: boolean) => post<{ message: string }>(`/opensource/issues/${issue.id}/promote`, undefined, { promoted }),
    { success: (d) => d.message, invalidate: [osKeys.all] },
  );
  return (
    <li className="relative flex gap-3.5 px-4 py-4 transition-colors hover:bg-surface-2/40 sm:px-5">
      {issue.is_promoted ? <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 rounded-full bg-brand" /> : null}
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-success-soft text-success">
        <CircleDot className="h-3.5 w-3.5" aria-hidden />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-subtle">
            <Link href={`/open-source/issues?repo_id=${issue.repo.id}`} className="truncate hover:text-accent-strong hover:underline">{issue.repo.full_name}</Link>
            <span aria-hidden>·</span>
            <span className="tabular">#{issue.number}</span>
            {issue.repo.language ? <><span aria-hidden>·</span><span className="font-sans text-muted">{issue.repo.language}</span></> : null}
          </div>
          <h3 className="mt-1 text-[14.5px] font-medium leading-snug text-fg">
            {issue.html_url ? (
              <a href={issue.html_url} target="_blank" rel="noopener noreferrer" className="hover:text-accent-strong hover:underline">
                {issue.title}
                <ExternalLink className="ml-1 inline h-3 w-3 align-baseline text-subtle" aria-hidden />
                <span className="sr-only">(opens GitHub in a new tab)</span>
              </a>
            ) : (
              issue.title
            )}
          </h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {issue.is_promoted ? <Badge tone="accent" icon={<Pin className="h-3 w-3" aria-hidden />}>Promoted</Badge> : null}
            {issue.is_beginner_friendly ? <Badge tone="success" icon={<Sparkles className="h-3 w-3" aria-hidden />}>Beginner friendly</Badge> : null}
            {issue.repo.is_demo ? <DemoBadge /> : null}
            {issue.labels.map((l) => <Badge key={l} tone="outline">{l}</Badge>)}
          </div>
        </div>
        <div className="flex shrink-0 flex-row flex-wrap items-center gap-x-3 gap-y-2 text-xs text-subtle sm:flex-col sm:items-end sm:gap-2">
          <span className="tabular inline-flex items-center gap-1" title="Comments">
            <MessageSquare className="h-3.5 w-3.5" aria-hidden /> {issue.comments}
            <span className="sr-only">comments</span>
          </span>
          {issue.updated_at ? <span title={formatDateTime(issue.updated_at)}>Updated {relativeTime(issue.updated_at)}</span> : null}
          {canPromote ? (
            <Button
              size="sm"
              className="max-sm:h-9"
              variant={issue.is_promoted ? "ghost" : "secondary"}
              loading={promote.isPending}
              onClick={() => promote.mutate(!issue.is_promoted)}
              icon={<Pin className="h-3.5 w-3.5" aria-hidden />}
            >
              {issue.is_promoted ? "Unpromote" : "Promote"}
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

/** Loading placeholder shaped like `IssueItem` / `RepoRow` rows (glyph, meta line, title, chips). */
export function CatalogRowsSkeleton({ rows = 5, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div className="divide-y divide-border" role="status" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3.5 px-4 py-4 sm:px-5" style={{ opacity: 1 - i * 0.1 }}>
          <Skeleton className="h-7 w-7 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1">
            <Skeleton className="h-2.5 w-40 max-w-full" />
            <Skeleton className="mt-2.5 h-4 w-3/4" />
            <div className="mt-3 flex gap-1.5">
              <Skeleton className="h-5 w-24 rounded-full" />
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
          </div>
          <Skeleton className="hidden h-3 w-20 sm:block" />
        </div>
      ))}
    </div>
  );
}

/** POST /opensource/repos {url} — register a public GitHub repository with the hub. */
export function RegisterRepoDialog({
  triggerLabel = "Register a repository",
  defaultUrl = "",
  triggerVariant = "primary",
  onRegistered,
}: {
  triggerLabel?: string;
  defaultUrl?: string;
  triggerVariant?: "primary" | "secondary" | "outline";
  onRegistered?: (repo: RepoCard) => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(defaultUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const repo = await post<RepoCard>("/opensource/repos", { url: url.trim() });
      toast.success(`${repo.full_name} registered. The first sync has been queued.`);
      await qc.invalidateQueries({ queryKey: osKeys.all });
      onRegistered?.(repo);
      setOpen(false);
      setUrl(defaultUrl);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "error", describeError(err).message, null, null));
    } finally {
      setBusy(false);
    }
  }

  const general = error && !Object.keys(error.fields).length ? describeError(error, "Could not register the repository") : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setError(null);
      }}
      title="Register a repository"
      description="Add a public GitHub repository so its issues appear in the hub and merged pull requests can be attributed to members."
      trigger={<Button variant={triggerVariant} icon={<Plus className="h-4 w-4" aria-hidden />}>{triggerLabel}</Button>}
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="GitHub repository URL" hint="For example https://github.com/owner/repo" error={error?.fields.url} required>
          {(p) => (
            <Input
              {...p}
              type="url"
              inputMode="url"
              autoComplete="off"
              placeholder="https://github.com/owner/repo"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              maxLength={300}
            />
          )}
        </Field>
        {general ? (
          <InlineNotice tone={error?.code === "github_rate_limited" || error?.code === "github_unavailable" ? "warning" : "danger"} title={general.title}>
            {general.message}
          </InlineNotice>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button type="submit" loading={busy} disabled={url.trim().length < 5}>Register</Button>
        </div>
      </form>
    </Dialog>
  );
}
