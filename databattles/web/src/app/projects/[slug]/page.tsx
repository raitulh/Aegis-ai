"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BadgeCheck, Building2, CheckCircle2, Database, EyeOff, GitFork, Link2, Lock, MessageSquarePlus, MessagesSquare, Pencil, Pin, ShieldAlert, ShieldCheck, Star, StarOff, Trophy } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { settle } from "@/components/catalog/errors";
import { MediaGallery, MemberRow, ProjectLinks, RepositoryCard } from "@/components/catalog/project-bits";
import { RegisterRepoDialog } from "@/components/catalog/repo";
import { ReportDialog } from "@/components/catalog/report-dialog";
import type { ProjectDetail, ThreadList } from "@/components/catalog/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Prose } from "@/components/ui/markdown";
import { Container } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { formatDate, formatDateTime, relativeTime } from "@/lib/format";
import { hasRole, useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";

function DetailSkeleton() {
  return (
    <Container className="py-8">
      <Skeleton className="h-56 w-full rounded-[var(--radius-lg)]" />
      <Skeleton className="mt-6 h-8 w-1/2" />
      <Skeleton className="mt-3 h-4 w-2/3" />
      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2"><SkeletonRows rows={6} /></div>
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
    <Card>
      <CardHeader
        title={`Discussion${p.discussion_count ? ` · ${p.discussion_count}` : ""}`}
        description="Questions, feedback and ideas about this project."
        action={<LinkButton href={`/discussions/new?project=${encodeURIComponent(p.slug)}`} size="sm" variant="secondary" icon={<MessageSquarePlus className="h-4 w-4" aria-hidden />}>Start a discussion</LinkButton>}
      />
      <CardBody>
        {threads.isPending ? (
          <SkeletonRows rows={3} />
        ) : threads.isError ? (
          <p className="text-sm text-subtle">Discussions couldn&apos;t be loaded. <button type="button" className="text-accent-strong hover:underline" onClick={() => threads.refetch()}>Try again</button></p>
        ) : threads.data.items.length === 0 ? (
          <p className="text-sm text-subtle">No discussions yet — start the first one.</p>
        ) : (
          <>
            <ul className="divide-y divide-border">
              {threads.data.items.map((t) => (
                <li key={t.id} className="flex items-start justify-between gap-3 py-2.5">
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
                    <span className="inline-flex items-center gap-1"><MessagesSquare className="h-3.5 w-3.5" aria-hidden />{t.reply_count}<span className="sr-only"> replies</span></span>
                  </div>
                </li>
              ))}
            </ul>
            {threads.data.total > threads.data.items.length ? (
              <Link href={`/discussions?project=${encodeURIComponent(p.slug)}`} className="mt-3 inline-block text-sm text-accent-strong hover:underline">
                View all {threads.data.total} discussions
              </Link>
            ) : null}
          </>
        )}
      </CardBody>
    </Card>
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

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <Link href="/projects" className="hover:text-fg">Projects</Link>
        <span aria-hidden> / </span>
        <span className="text-muted">{p.title}</span>
      </nav>

      <header className="flex flex-col gap-4 py-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
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
          <h1 className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{p.title}</h1>
          {p.summary ? <p className="mt-2 max-w-3xl text-base text-muted">{p.summary}</p> : null}
          <p className="mt-3 text-xs text-subtle">
            Created {formatDate(p.created_at)} · updated <span title={formatDateTime(p.updated_at)}>{relativeTime(p.updated_at)}</span>
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {p.viewer.can_edit ? <LinkButton href={`/projects/${p.slug}/edit`} variant="secondary" icon={<Pencil className="h-4 w-4" aria-hidden />}>Edit</LinkButton> : null}
          <LinkButton href={`/discussions/new?project=${encodeURIComponent(p.slug)}`} variant="ghost" icon={<MessageSquarePlus className="h-4 w-4" aria-hidden />}>Discuss</LinkButton>
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
      </header>

      {p.taken_down ? (
        <div className="mb-6">
          <InlineNotice tone="danger" title="This project has been taken down by a moderator">
            {p.takedown_reason ? <>Reason: {p.takedown_reason}</> : "It is hidden from the public."}
          </InlineNotice>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <MediaGallery media={p.media} coverStyle={p.cover_style} title={p.title} />
          <Card>
            <CardHeader title="About" />
            <CardBody>
              {p.description_html ? <Prose html={p.description_html} /> : <p className="text-sm text-subtle">No description yet.</p>}
            </CardBody>
          </Card>
          <Discussions p={p} />
        </div>

        <aside className="space-y-6" aria-label="Project details">
          <ProjectLinks p={p} />

          {p.technologies.length || p.tags.length ? (
            <Card>
              <CardHeader title="Stack & tags" />
              <CardBody className="space-y-3">
                {p.technologies.length ? (
                  <div>
                    <h3 className="sr-only">Technologies</h3>
                    <ul className="flex flex-wrap gap-1.5">
                      {p.technologies.map((t) => (
                        <li key={t}>
                          <Link href={`/projects?tech=${encodeURIComponent(t)}`} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted hover:text-fg">{t}</Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {p.tags.length ? (
                  <div>
                    <h3 className="sr-only">Tags</h3>
                    <ul className="flex flex-wrap gap-1.5">
                      {p.tags.map((t) => (
                        <li key={t}>
                          <Link href={`/projects?tag=${encodeURIComponent(t)}`} className="text-xs text-accent-strong hover:underline">#{t}</Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader title={`Team · ${p.members.length}`} />
            <CardBody className="py-2">
              {p.members.length ? (
                <ul className="divide-y divide-border">
                  {p.members.map((m) => <MemberRow key={m.user.id} m={m} />)}
                </ul>
              ) : (
                <p className="py-2 text-sm text-subtle">No members listed.</p>
              )}
            </CardBody>
          </Card>

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
                <LinkButton href="/settings/integrations" size="sm" variant="secondary">Link your GitHub account</LinkButton>
              ) : null
            }
          />

          {p.competition || p.datasets.length || p.org ? (
            <Card>
              <CardHeader title="Connections" />
              <CardBody className="space-y-3 text-sm">
                {p.org ? (
                  <Link href={`/orgs/${p.org.slug}`} className="flex items-center gap-2 text-fg hover:text-accent-strong">
                    <Building2 className="h-4 w-4 text-subtle" aria-hidden /> {p.org.name}
                  </Link>
                ) : null}
                {p.competition ? (
                  <Link href={`/competitions/${p.competition.slug}`} className="flex items-center gap-2 text-fg hover:text-accent-strong">
                    <Trophy className="h-4 w-4 text-subtle" aria-hidden /> {p.competition.title}
                  </Link>
                ) : null}
                {p.datasets.map((d) => (
                  <Link key={d.slug} href={`/datasets/${d.slug}`} className="flex items-center gap-2 text-fg hover:text-accent-strong">
                    <Database className="h-4 w-4 text-subtle" aria-hidden /> {d.title}
                  </Link>
                ))}
              </CardBody>
            </Card>
          ) : null}
        </aside>
      </div>
    </Container>
  );
}
