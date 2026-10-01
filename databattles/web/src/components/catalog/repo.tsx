"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Archive, CircleDot, ExternalLink, GitFork, MessageSquare, Pin, Plus, RefreshCw, Scale, Sparkles, Star, Users } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/form";
import { InlineNotice } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import { describeError } from "./errors";
import type { IssueRow, RepoCard } from "./types";

export const osKeys = {
  all: ["opensource"] as const,
  overview: ["opensource", "overview"] as const,
  repos: (f: object) => ["opensource", "repos", f] as const,
  issues: (f: object) => ["opensource", "issues", f] as const,
  account: ["opensource", "github-account"] as const,
};

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

export function RepoCardView({ repo, canSync }: { repo: RepoCard; canSync?: boolean }) {
  return (
    <article className="flex flex-col rounded-[var(--radius-lg)] border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="min-w-0 break-all text-sm font-semibold text-fg">
          <RepoName fullName={repo.full_name} htmlUrl={repo.html_url} />
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {repo.is_demo ? <DemoBadge /> : null}
          {repo.is_archived ? <Badge tone="neutral" icon={<Archive className="h-3 w-3" aria-hidden />}>Archived</Badge> : null}
        </div>
      </div>
      {repo.description ? <p className="mt-2 line-clamp-2 text-sm text-muted">{repo.description}</p> : <p className="mt-2 text-sm text-subtle">No description.</p>}
      {repo.topics.length ? (
        <div className="mt-3 flex flex-wrap gap-1">
          {repo.topics.slice(0, 6).map((t) => (
            <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{t}</span>
          ))}
        </div>
      ) : null}
      <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <RepoStat icon={<Star className="h-3.5 w-3.5" aria-hidden />} label="Stars" value={compactNumber(repo.stars)} />
        <RepoStat icon={<GitFork className="h-3.5 w-3.5" aria-hidden />} label="Forks" value={compactNumber(repo.forks)} />
        <RepoStat icon={<CircleDot className="h-3.5 w-3.5" aria-hidden />} label="Open issues" value={compactNumber(repo.open_issues)} />
        {repo.contributors_count !== null ? (
          <RepoStat icon={<Users className="h-3.5 w-3.5" aria-hidden />} label="Contributors" value={compactNumber(repo.contributors_count)} />
        ) : null}
        {repo.language ? <RepoStat label="Language" value={<span className="font-medium text-fg">{repo.language}</span>} /> : null}
        {repo.license ? <RepoStat icon={<Scale className="h-3.5 w-3.5" aria-hidden />} label="License" value={repo.license} /> : null}
      </dl>
      <div className="mt-auto pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-xs text-subtle">
        <div className="flex flex-wrap items-center gap-2">
          <SyncStatusBadge status={repo.sync_status} isDemo={repo.is_demo} />
          {repo.last_synced_at ? <span title={formatDateTime(repo.last_synced_at)}>Last synced {relativeTime(repo.last_synced_at)}</span> : <span>Never synced</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {repo.project ? (
            <Link href={`/projects/${repo.project.slug}`} className="text-accent-strong hover:underline">Project: {repo.project.title}</Link>
          ) : null}
          <Link href={`/open-source/issues?repo_id=${repo.id}`} className="text-accent-strong hover:underline">Issues</Link>
          {canSync ? <SyncButton repo={repo} /> : null}
        </div>
      </div>
      </div>
    </article>
  );
}

export function IssueItem({ issue, canPromote }: { issue: IssueRow; canPromote?: boolean }) {
  const promote = useApiMutation(
    (promoted: boolean) => post<{ message: string }>(`/opensource/issues/${issue.id}/promote`, undefined, { promoted }),
    { success: (d) => d.message, invalidate: [osKeys.all] },
  );
  return (
    <li className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-subtle">
          <Link href={`/open-source/issues?repo_id=${issue.repo.id}`} className="font-mono hover:text-accent-strong hover:underline">{issue.repo.full_name}</Link>
          <span aria-hidden>·</span>
          <span>#{issue.number}</span>
          {issue.repo.language ? <><span aria-hidden>·</span><span>{issue.repo.language}</span></> : null}
        </div>
        <h3 className="mt-1 text-sm font-semibold text-fg">
          {issue.html_url ? (
            <a href={issue.html_url} target="_blank" rel="noopener noreferrer" className="hover:text-accent-strong hover:underline">
              {issue.title}
              <ExternalLink className="ml-1 inline h-3 w-3 align-baseline" aria-hidden />
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
      <div className="flex shrink-0 flex-row items-center gap-3 text-xs text-subtle sm:flex-col sm:items-end sm:gap-2">
        <span className="inline-flex items-center gap-1" title="Comments">
          <MessageSquare className="h-3.5 w-3.5" aria-hidden /> {issue.comments}
          <span className="sr-only">comments</span>
        </span>
        {issue.updated_at ? <span title={formatDateTime(issue.updated_at)}>Updated {relativeTime(issue.updated_at)}</span> : null}
        {canPromote ? (
          <Button
            size="sm"
            variant={issue.is_promoted ? "ghost" : "secondary"}
            loading={promote.isPending}
            onClick={() => promote.mutate(!issue.is_promoted)}
            icon={<Pin className="h-3.5 w-3.5" aria-hidden />}
          >
            {issue.is_promoted ? "Unpromote" : "Promote"}
          </Button>
        ) : null}
      </div>
    </li>
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
