"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BadgeCheck,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock,
  Code2,
  Database,
  EyeOff,
  FolderGit2,
  GitFork,
  Globe,
  Images,
  Link2,
  Lock,
  MessageSquarePlus,
  MessagesSquare,
  Pencil,
  Pin,
  ShieldAlert,
  ShieldCheck,
  Star,
  StarOff,
  Trophy,
  User,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { DetailBlock, FactGrid } from "@/components/catalog/dataset-bits";
import { settle } from "@/components/catalog/errors";
import { MediaGallery, MemberRow, ProjectLinks, RepositoryCard } from "@/components/catalog/project-bits";
import { RegisterRepoDialog } from "@/components/catalog/repo";
import { ReportDialog } from "@/components/catalog/report-dialog";
import type { ProjectDetail, ThreadList } from "@/components/catalog/types";
import { Avatar, AvatarStack } from "@/components/ui/avatar";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { MetaItem } from "@/components/ui/extras";
import { Prose } from "@/components/ui/markdown";
import { Cover } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { formatDate, formatDateTime, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

/** Two-letter monogram from the first two words that start with a letter or digit ("Crop Yield Baselines" → "CY"). */
function monogram(title: string) {
  const words = title.trim().split(/\s+/).filter((w) => /^[\p{L}\p{N}]/u.test(w));
  const letters = words.length > 1 ? words.slice(0, 2).map((w) => Array.from(w)[0]) : Array.from(words[0] ?? "").slice(0, 2);
  return letters.join("").toUpperCase() || "P";
}

function DetailSkeleton() {
  return (
    <Container className="pb-16">
      <Skeleton className="mt-6 h-4 w-48" />
      <div className="mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface" role="status" aria-label="Loading">
        <Skeleton className="h-44 w-full rounded-none sm:h-60" />
        <div className="p-5 sm:p-8">
          <Skeleton className="h-5 w-40 rounded-full" />
          <Skeleton className="mt-5 h-9 w-2/3" />
          <Skeleton className="mt-3 h-4 w-1/2" />
        </div>
      </div>
      <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <SkeletonRows rows={6} />
        <SkeletonRows rows={4} />
      </div>
    </Container>
  );
}

function Discussions({ p }: { p: ProjectDetail }) {
  const threads = useQuery({
    queryKey: qk.threads({ project: p.slug, page_size: 5 }),
    queryFn: () => get<ThreadList>("/discussions/threads", { project: p.slug, page_size: 5, sort: "activity" }),
  });
  return (
    <DetailBlock
      id="discussion"
      eyebrow="Community"
      title={`Discussion${p.discussion_count ? ` · ${p.discussion_count}` : ""}`}
      description="Questions, feedback and ideas about this project."
      action={<LinkButton href={`/discussions/new?project=${encodeURIComponent(p.slug)}`} size="sm" variant="secondary" className="max-sm:h-9" icon={<MessageSquarePlus className="h-4 w-4" aria-hidden />}>Start a discussion</LinkButton>}
    >
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
        {threads.isPending ? (
          <div className="p-4"><SkeletonRows rows={3} /></div>
        ) : threads.isError ? (
          <p className="px-5 py-4 text-sm text-subtle">Discussions couldn&apos;t be loaded. <button type="button" className="text-accent-strong hover:underline" onClick={() => threads.refetch()}>Try again</button></p>
        ) : threads.data.items.length === 0 ? (
          <div className="flex items-center gap-3 px-5 py-5 text-sm text-subtle">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-dashed border-border-strong text-accent-strong" aria-hidden>
              <MessagesSquare className="h-4 w-4" />
            </span>
            No discussions yet — start the first one.
          </div>
        ) : (
          <>
            <ul className="divide-y divide-border">
              {threads.data.items.map((t) => (
                <li key={t.id} className="flex items-start justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-2/50">
                  <div className="min-w-0">
                    <Link href={`/discussions/t/${t.id}`} className="flex items-center gap-1.5 text-sm font-medium text-fg hover:text-accent-strong">
                      {t.pinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-accent-strong" aria-label="Pinned" /> : null}
                      {t.locked ? <Lock className="h-3.5 w-3.5 shrink-0 text-subtle" aria-label="Locked" /> : null}
                      <span className="truncate">{t.title}</span>
                    </Link>
                    <p className="mt-0.5 text-xs text-subtle">
                      {t.author ? `${t.author.display_name} · ` : ""}
                      <span title={formatDateTime(t.last_activity_at)}>active {relativeTime(t.last_activity_at)}</span>
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-xs text-subtle">
                    {t.has_accepted_answer ? <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-label="Answered" /> : null}
                    <span className="tabular inline-flex items-center gap-1"><MessagesSquare className="h-3.5 w-3.5" aria-hidden />{t.reply_count}<span className="sr-only"> replies</span></span>
                  </div>
                </li>
              ))}
            </ul>
            {threads.data.total > threads.data.items.length ? (
              <div className="border-t border-border px-5 py-3">
                <Link href={`/discussions?project=${encodeURIComponent(p.slug)}`} className="inline-flex items-center gap-1 text-sm text-accent-strong hover:underline">
                  View all {threads.data.total} discussions <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
              </div>
            ) : null}
          </>
        )}
      </div>
    </DetailBlock>
  );
}

export default function ProjectPage() {
  const { slug } = useParams<{ slug: string }>();
  const qc = useQueryClient();
  const me = useMe();
  const q = useQuery({ queryKey: qk.project(slug), queryFn: () => get<ProjectDetail>(`/projects/${slug}`) });

  const feature = useApiMutation((featured: boolean) => post<{ message: string }>(`/projects/${slug}/feature`, undefined, { featured }), {
    success: (d) => d.message,
    invalidate: [["projects"]],
  });
  const moderate = useApiMutation(
    ({ reason, restore }: { reason: string; restore: boolean }) => post<{ message: string }>(`/projects/${slug}/takedown`, { reason }, { restore: restore || undefined }),
    { success: (d) => d.message, invalidate: [["projects"]] },
  );

  if (q.isPending) return <DetailSkeleton />;
  if (q.isError) {
    return (
      <Container className="py-10">
        {q.error instanceof ApiError && q.error.status === 404 ? <NotFoundState what="project" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      </Container>
    );
  }

  const p = q.data;
  const viewer = me.data;
  const isMod = hasRole(viewer, "moderator") || p.viewer.can_moderate;
  const isMember = Boolean(p.viewer.role);
  const others = p.members.filter((m) => m.user.id !== p.owner?.id).length;
  const hasLinks = Boolean(p.demo_url || p.repo_url);

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 items-center gap-1.5">
          <li><Link href="/projects" className="transition-colors hover:text-fg">Projects</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="min-w-0 truncate text-muted" aria-current="page">{p.title}</li>
        </ol>
      </nav>

      {/* ------------------------------------------------------------------ case-study hero */}
      <header className="relative isolate mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
        <div className="relative h-40 border-b border-border sm:h-52 lg:h-56">
          {p.cover_image_url ? (
            <img src={p.cover_image_url} alt="" className="absolute inset-0 h-full w-full object-cover" />
          ) : (
            <Cover style={p.cover_style} className="absolute inset-0" />
          )}
          <div aria-hidden className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-surface/80 via-surface/25 to-transparent" />
        </div>

        <div className="relative px-5 pb-6 sm:px-8 sm:pb-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-5">
            <span
              aria-hidden
              className="-mt-9 flex h-[4.5rem] w-[4.5rem] shrink-0 items-center justify-center rounded-2xl border border-border-strong bg-surface-2 shadow-elevated sm:-mt-11 sm:h-[5.5rem] sm:w-[5.5rem]"
            >
              <span className="text-gradient text-[1.75rem] font-semibold tracking-[-0.04em] sm:text-[2.125rem]">{monogram(p.title)}</span>
            </span>
            <div className="flex min-w-0 flex-wrap items-center gap-1.5 empty:hidden sm:pt-3">
              {p.is_featured ? <Badge tone="accent" icon={<Star className="h-3 w-3" aria-hidden />}>Featured</Badge> : null}
              {p.is_open_source ? <Badge tone="info" icon={<GitFork className="h-3 w-3" aria-hidden />}>Open source</Badge> : null}
              {p.maintainer_verified ? <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" aria-hidden />} title="The owner's linked GitHub account owns the repository">Verified maintainer</Badge> : null}
              {p.is_demo ? <DemoBadge /> : null}
              {p.status === "draft" ? <Badge tone="warning">Draft</Badge> : null}
              {p.status === "archived" ? <Badge tone="neutral" icon={<Archive className="h-3 w-3" aria-hidden />}>Archived</Badge> : null}
              {p.visibility === "unlisted" ? <Badge tone="neutral" icon={<Link2 className="h-3 w-3" aria-hidden />}>Unlisted</Badge> : null}
              {p.visibility === "private" ? <Badge tone="neutral" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Private</Badge> : null}
              {p.taken_down ? <Badge tone="danger" icon={<ShieldAlert className="h-3 w-3" aria-hidden />}>Taken down</Badge> : null}
            </div>
          </div>

          <div className="mt-4 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between lg:gap-10">
            <div className="min-w-0 max-w-3xl">
              <h1 className="text-title text-fg">{p.title}</h1>
              {p.summary ? <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted sm:text-base">{p.summary}</p> : null}
              <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
                {p.members.length > 1 ? <AvatarStack people={p.members.map((m) => m.user)} size={26} /> : null}
                {p.owner ? (
                  <span className="inline-flex min-w-0 items-center gap-2">
                    {p.members.length > 1 ? null : <Avatar name={p.owner.display_name} src={p.owner.avatar_url} size={26} />}
                    <span className="text-subtle">by</span>
                    <Link href={`/u/${p.owner.handle}`} className="truncate font-medium text-fg transition-colors hover:text-accent-strong">{p.owner.display_name}</Link>
                    {others > 0 ? <span className="text-subtle">and {others} other{others === 1 ? "" : "s"}</span> : null}
                  </span>
                ) : null}
                <span className="text-xs text-subtle">
                  Created {formatDate(p.created_at)} · updated <span title={formatDateTime(p.updated_at)}>{relativeTime(p.updated_at)}</span>
                </span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 lg:max-w-md lg:shrink-0 lg:justify-end">
              {p.demo_url ? (
                <LinkButton href={p.demo_url} external icon={<Globe className="h-4 w-4" aria-hidden />}>
                  Live demo<span className="sr-only"> (opens in a new tab)</span>
                </LinkButton>
              ) : null}
              {p.repo_url ? (
                <LinkButton href={p.repo_url} external variant="secondary" icon={<Code2 className="h-4 w-4" aria-hidden />}>
                  Source code<span className="sr-only"> (opens in a new tab)</span>
                </LinkButton>
              ) : null}
              {p.viewer.can_edit ? <LinkButton href={`/projects/${p.slug}/edit`} variant="secondary" icon={<Pencil className="h-4 w-4" aria-hidden />}>Edit</LinkButton> : null}
              <LinkButton href={`/discussions/new?project=${encodeURIComponent(p.slug)}`} variant={hasLinks ? "ghost" : "secondary"} icon={<MessageSquarePlus className="h-4 w-4" aria-hidden />}>Discuss</LinkButton>
              {viewer && !isMember ? <ReportDialog targetType="project" targetId={p.id} targetLabel="project" /> : null}
              {isMod ? (
                <>
                  <Button
                    variant="outline"
                    loading={feature.isPending}
                    onClick={() => feature.mutate(!p.is_featured)}
                    icon={p.is_featured ? <StarOff className="h-4 w-4" aria-hidden /> : <Star className="h-4 w-4" aria-hidden />}
                  >
                    {p.is_featured ? "Unfeature" : "Feature"}
                  </Button>
                  {p.taken_down ? (
                    <ConfirmDialog
                      trigger={<Button variant="outline" icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>Restore</Button>}
                      title="Restore this project?"
                      description="It becomes visible again according to its visibility and status."
                      confirmLabel="Restore"
                      tone="primary"
                      requireReason
                      onConfirm={(reason) => settle(moderate.mutateAsync({ reason, restore: true }))}
                    />
                  ) : (
                    <ConfirmDialog
                      trigger={<Button variant="danger" icon={<ShieldAlert className="h-4 w-4" aria-hidden />}>Take down</Button>}
                      title="Take down this project?"
                      description="It will be hidden from everyone except its members and moderators, and removed from search. Members see the reason."
                      confirmLabel="Take down"
                      requireReason
                      onConfirm={(reason) => settle(moderate.mutateAsync({ reason, restore: false }))}
                    />
                  )}
                </>
              ) : null}
            </div>
          </div>
        </div>

        <FactGrid className="border-t border-border">
          <MetaItem icon={<User />} label="Owner">
            {p.owner ? (
              <Link href={`/u/${p.owner.handle}`} className="transition-colors hover:text-accent-strong">{p.owner.display_name}</Link>
            ) : (
              "—"
            )}
          </MetaItem>
          <MetaItem icon={<Users />} label="Team">
            <span className="tabular">{p.members.length}</span> {p.members.length === 1 ? "member" : "members"}
          </MetaItem>
          <MetaItem icon={<Trophy />} label="Built for">
            {p.competition ? (
              <Link href={`/competitions/${p.competition.slug}`} className="transition-colors hover:text-accent-strong" title={p.competition.title}>{p.competition.title}</Link>
            ) : (
              <span className="text-subtle">—</span>
            )}
          </MetaItem>
          <MetaItem icon={<FolderGit2 />} label="Status">
            {titleCase(p.status)} <span className="text-xs font-normal text-subtle">· {titleCase(p.visibility)}</span>
          </MetaItem>
          <MetaItem icon={<CalendarDays />} label="Created">
            <span title={formatDateTime(p.created_at)}>{formatDate(p.created_at)}</span>
          </MetaItem>
          <MetaItem icon={<Clock />} label="Updated">
            <span title={formatDateTime(p.updated_at)}>{relativeTime(p.updated_at)}</span>
          </MetaItem>
        </FactGrid>
      </header>

      {p.taken_down ? (
        <div className="mt-6">
          <InlineNotice tone="danger" title="This project has been taken down by a moderator">
            {p.takedown_reason ? <>Reason: {p.takedown_reason}</> : "It is hidden from the public."}
          </InlineNotice>
        </div>
      ) : null}

      <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-12">
          <DetailBlock id="about" eyebrow="Case study" title="About the project">
            {p.description_html ? <Prose html={p.description_html} className="max-w-[70ch]" /> : <p className="text-sm text-subtle">No description yet.</p>}
          </DetailBlock>

          {p.media.length ? (
            <DetailBlock
              id="gallery"
              eyebrow="Media"
              title="Gallery"
              description={<span className="inline-flex items-center gap-1.5"><Images className="h-3.5 w-3.5" aria-hidden /><span className="tabular">{p.media.length}</span> {p.media.length === 1 ? "image" : "images"}</span>}
            >
              <MediaGallery media={p.media} coverStyle={p.cover_style} title={p.title} />
            </DetailBlock>
          ) : null}

          <DetailBlock id="team" eyebrow="People" title={`Team · ${p.members.length}`} description="The people who built and maintain this project.">
            {p.members.length ? (
              <ul className="grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card sm:grid-cols-2 [&>li]:bg-surface [&>li]:px-4 sm:[&>li:last-child:nth-child(odd)]:col-span-2">
                {p.members.map((m) => <MemberRow key={m.user.id} m={m} />)}
              </ul>
            ) : (
              <p className="py-2 text-sm text-subtle">No members listed.</p>
            )}
          </DetailBlock>

          <Discussions p={p} />
        </div>

        <aside className="min-w-0 space-y-4" aria-label="Project details">
          <ProjectLinks p={p} />

          {p.technologies.length || p.tags.length ? (
            <Card>
              <CardHeader icon={<Code2 />} title="Stack & tags" />
              <CardBody className="space-y-4">
                {p.technologies.length ? (
                  <div>
                    <h3 className="mb-2 text-eyebrow text-subtle">Built with</h3>
                    <ul className="flex flex-wrap gap-1.5">
                      {p.technologies.map((t) => (
                        <li key={t}>
                          <Link
                            href={`/projects?tech=${encodeURIComponent(t)}`}
                            className="inline-flex h-7 items-center rounded-md border border-border bg-bg-elevated px-2 font-mono text-xs text-muted transition-colors hover:border-border-strong hover:text-fg max-sm:h-9 max-sm:px-3"
                          >
                            {t}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {p.tags.length ? (
                  <div>
                    <h3 className="mb-2 text-eyebrow text-subtle">Topics</h3>
                    <ul className="flex flex-wrap gap-x-3 gap-y-1.5">
                      {p.tags.map((t) => (
                        <li key={t}>
                          <Link href={`/projects?tag=${encodeURIComponent(t)}`} className="inline-flex items-center text-xs text-accent-strong hover:underline max-sm:min-h-9">#{t}</Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          <RepositoryCard
            p={p}
            footer={
              p.repo_url && !p.repository && p.viewer.can_edit ? (
                <RegisterRepoDialog
                  triggerLabel="Register repository"
                  triggerVariant="secondary"
                  defaultUrl={p.repo_url}
                  onRegistered={() => void qc.invalidateQueries({ queryKey: qk.project(slug) })}
                />
              ) : p.viewer.is_owner && p.repository && !p.maintainer_verified ? (
                <LinkButton href="/settings/integrations" size="sm" className="max-sm:h-9" variant="secondary">Link your GitHub account</LinkButton>
              ) : null
            }
          />

          {p.competition || p.datasets.length || p.org ? (
            <Card>
              <CardHeader icon={<Link2 />} title="Connections" />
              <ul className="divide-y divide-border text-sm">
                {p.org ? (
                  <li>
                    <Link href={`/orgs/${p.org.slug}`} className="group flex items-center gap-3 px-5 py-3 text-fg transition-colors hover:bg-surface-2/60 hover:text-accent-strong">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-subtle" aria-hidden><Building2 className="h-3.5 w-3.5" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-eyebrow text-subtle">Organization</span>
                        <span className="block truncate font-medium">{p.org.name}</span>
                      </span>
                    </Link>
                  </li>
                ) : null}
                {p.competition ? (
                  <li>
                    <Link href={`/competitions/${p.competition.slug}`} className="group flex items-center gap-3 px-5 py-3 text-fg transition-colors hover:bg-surface-2/60 hover:text-accent-strong">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-soft text-accent-strong" aria-hidden><Trophy className="h-3.5 w-3.5" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-eyebrow text-subtle">Competition</span>
                        <span className="block truncate font-medium">{p.competition.title}</span>
                      </span>
                    </Link>
                  </li>
                ) : null}
                {p.datasets.map((d) => (
                  <li key={d.slug}>
                    <Link href={`/datasets/${d.slug}`} className="group flex items-center gap-3 px-5 py-3 text-fg transition-colors hover:bg-surface-2/60 hover:text-accent-strong">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-cyan-soft text-cyan" aria-hidden><Database className="h-3.5 w-3.5" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-eyebrow text-subtle">Dataset</span>
                        <span className="block truncate font-medium">{d.title}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </aside>
      </div>
    </Container>
  );
}
