"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  Building2,
  CalendarClock,
  ChevronRight,
  Database,
  Download,
  ExternalLink,
  Eye,
  FileCheck2,
  FileStack,
  GitCommitVertical,
  HardDrive,
  Lock,
  Quote,
  Scale,
  ScrollText,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Trophy,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { DataDictionaryTable, DetailBlock, FactGrid, FilePreviewPanel, FileRow, SectionNav, VersionStatusBadge, toDictionary } from "@/components/catalog/dataset-bits";
import { describeError, settle } from "@/components/catalog/errors";
import { ReportDialog } from "@/components/catalog/report-dialog";
import type { DatasetFile } from "@/components/catalog/types";
import { UserLink } from "@/components/domain/cards";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { MetaItem } from "@/components/ui/extras";
import { Select } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { CopyButton } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, SignInPrompt, Skeleton, SkeletonHero, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { compactNumber, formatBytes, formatDate, formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { hasRole, useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { DatasetDetail } from "@/lib/types";

function DetailSkeleton() {
  return (
    <Container className="pb-16">
      <Skeleton className="mt-6 h-4 w-48" />
      <SkeletonHero className="mt-5" />
      <Skeleton className="mt-6 h-11 w-full rounded-[var(--radius-md)]" />
      <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-4"><SkeletonRows rows={4} /></div>
        <SkeletonRows rows={3} />
      </div>
    </Container>
  );
}

const glow = { background: "radial-gradient(closest-side, color-mix(in oklab, var(--cyan) 16%, transparent), transparent)" };

function DatasetDetailView() {
  const { slug } = useParams<{ slug: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const me = useMe();
  const versionParam = Number(params.get("version")) || undefined;

  const q = useQuery({
    queryKey: qk.dataset(slug, versionParam),
    queryFn: () => get<DatasetDetail>(`/datasets/${slug}`, { version: versionParam }),
  });

  const [previewId, setPreviewId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  const accept = useApiMutation(() => post<{ message: string }>(`/datasets/${slug}/accept-terms`), {
    success: "Terms accepted — downloads are unlocked.",
    invalidate: [["datasets", slug]],
  });
  const moderate = useApiMutation(
    ({ reason, restore }: { reason: string; restore: boolean }) => post<{ message: string }>(`/datasets/${slug}/takedown`, { reason }, { restore: restore || undefined }),
    { success: (d) => d.message, invalidate: [["datasets"]] },
  );

  if (q.isPending) return <DetailSkeleton />;
  if (q.isError) {
    return (
      <Container className="py-10">
        {q.error instanceof ApiError && q.error.status === 404 ? <NotFoundState what="dataset" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      </Container>
    );
  }

  const d = q.data;
  const v = d.current_version;
  const viewer = me.data;
  const isMod = hasRole(viewer, "moderator");
  const takenDown = d.status === "taken_down";
  const gated = d.requires_terms && !d.terms_accepted && !d.can_manage;
  const dictionary = toDictionary(v);

  const setVersion = (n: string) => {
    const sp = new URLSearchParams(params.toString());
    if (!n || Number(n) === d.latest_version) sp.delete("version");
    else sp.set("version", n);
    setPreviewId(null);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  async function download(file: DatasetFile) {
    setDownloading(file.id);
    try {
      const res = await post<{ url: string; expires_in_seconds: number }>(`/datasets/${slug}/files/${file.id}/download`);
      window.location.assign(res.url);
      void qc.invalidateQueries({ queryKey: ["datasets", slug] });
    } catch (e) {
      const { title, message } = describeError(e, "Download failed");
      toast.error(title, { description: message });
    } finally {
      setDownloading(null);
    }
  }

  const sections = [
    { id: "files", label: "Files", count: d.files.length },
    { id: "about", label: "About" },
    { id: "dictionary", label: "Data dictionary", count: dictionary.length },
    ...(v?.release_notes_html ? [{ id: "release-notes", label: "Release notes" }] : []),
    ...(d.versions.length ? [{ id: "versions", label: "Versions", count: d.versions.length }] : []),
  ];

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <ol className="flex min-w-0 items-center gap-1.5">
          <li><Link href="/datasets" className="transition-colors hover:text-fg">Datasets</Link></li>
          <li aria-hidden><ChevronRight className="h-3.5 w-3.5" /></li>
          <li className="min-w-0 truncate text-muted" aria-current="page">{d.title}</li>
        </ol>
      </nav>

      {/* ------------------------------------------------------------------ identity hero */}
      <header className="relative isolate mt-5 overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen shadow-card animate-rise">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-40 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_60%)]" />
        <div aria-hidden className="pointer-events-none absolute -right-20 -top-28 -z-10 h-80 w-[34rem] max-w-full" style={glow} />
        <div aria-hidden className="pointer-events-none absolute -left-24 -top-24 -z-10 h-64 w-96 max-w-full" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />

        <div className="p-5 sm:p-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex min-w-0 items-start gap-5">
              <span
                aria-hidden
                className="hidden h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-border bg-cyan-soft text-cyan shadow-[inset_0_1px_0_var(--hairline-highlight),0_12px_32px_-14px_var(--cyan)] sm:flex"
              >
                <Database className="h-6 w-6" />
              </span>
              <div className="min-w-0">
                <p className="text-eyebrow text-cyan">Dataset{v ? ` · version ${v.version}` : ""}</p>
                <h1 className="mt-2 text-title text-fg">{d.title}</h1>
                {d.subtitle ? <p className="mt-2.5 max-w-3xl text-[15px] leading-relaxed text-muted">{d.subtitle}</p> : null}
                <div className="mt-4 flex flex-wrap items-center gap-1.5">
                  {d.is_demo ? <DemoBadge /> : null}
                  <Badge tone="outline" icon={<Scale className="h-3 w-3" aria-hidden />} title={`License: ${d.license}`}>{d.license_name}</Badge>
                  {d.visibility === "org" ? <Badge tone="info" icon={<Building2 className="h-3 w-3" aria-hidden />}>Organization only</Badge> : null}
                  {d.visibility === "private" ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Private</Badge> : null}
                  {d.requires_terms ? <Badge tone="warning" icon={<FileCheck2 className="h-3 w-3" aria-hidden />}>Terms required</Badge> : null}
                  {takenDown ? <Badge tone="danger" icon={<ShieldAlert className="h-3 w-3" aria-hidden />}>Taken down</Badge> : null}
                </div>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2 lg:max-w-sm lg:justify-end">
              {d.files.length ? (
                <LinkButton href="#files" variant={d.can_manage ? "secondary" : "primary"} icon={<ArrowDown className="h-4 w-4" aria-hidden />}>
                  Browse files
                </LinkButton>
              ) : null}
              {d.can_manage ? (
                <LinkButton href={`/datasets/${d.slug}/manage`} icon={<Settings className="h-4 w-4" aria-hidden />}>Manage</LinkButton>
              ) : null}
              {viewer && !d.can_manage ? <ReportDialog targetType="dataset" targetId={d.id} targetLabel="dataset" /> : null}
              {isMod ? (
                takenDown ? (
                  <ConfirmDialog
                    trigger={<Button variant="outline" icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>Restore</Button>}
                    title="Restore this dataset?"
                    description="It becomes visible again according to its visibility setting."
                    confirmLabel="Restore"
                    tone="primary"
                    requireReason
                    onConfirm={(reason) => settle(moderate.mutateAsync({ reason, restore: true }))}
                  />
                ) : (
                  <ConfirmDialog
                    trigger={<Button variant="danger" icon={<ShieldAlert className="h-4 w-4" aria-hidden />}>Take down</Button>}
                    title="Take down this dataset?"
                    description="It will be hidden from everyone except its managers and moderators. The reason is shown to the owner and recorded in the audit log."
                    confirmLabel="Take down"
                    requireReason
                    onConfirm={(reason) => settle(moderate.mutateAsync({ reason, restore: false }))}
                  />
                )
              ) : null}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-border pt-5 text-sm text-muted">
            {d.owner_org ? (
              <Link href={`/orgs/${d.owner_org.slug}`} className="inline-flex min-w-0 items-center gap-2 text-fg transition-colors hover:text-accent-strong">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 text-subtle" aria-hidden>
                  <Building2 className="h-3.5 w-3.5" />
                </span>
                <span className="truncate">{d.owner_org.name}</span>
              </Link>
            ) : (
              <UserLink user={d.owner} />
            )}
            <span className="inline-flex items-center gap-1.5 text-subtle" title={formatDateTime(d.updated_at)}>
              <CalendarClock className="h-4 w-4" aria-hidden /> Updated {relativeTime(d.updated_at)}
            </span>
            {d.tags.length ? (
              <ul className="flex flex-wrap gap-1.5" aria-label="Tags">
                {d.tags.map((t) => (
                  <li key={t}>
                    <Link
                      href={`/datasets?tag=${encodeURIComponent(t)}`}
                      className="inline-flex h-6 items-center rounded-md border border-border bg-bg-elevated px-2 font-mono text-[11px] text-muted transition-colors hover:border-border-strong hover:text-fg max-sm:h-9 max-sm:px-3"
                    >
                      #{t}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>

        <FactGrid className="border-t border-border">
          <MetaItem icon={<GitCommitVertical />} label="Version">
            {v ? `v${v.version}` : "—"}
            <span className="ml-1.5 text-xs font-normal text-subtle">
              {d.latest_version ? (v?.version === d.latest_version ? "latest" : `latest v${d.latest_version}`) : "unpublished"}
            </span>
          </MetaItem>
          <MetaItem icon={<FileStack />} label="Files">
            <span className="tabular">{formatNumber(v?.file_count ?? d.file_count)}</span>
          </MetaItem>
          <MetaItem icon={<HardDrive />} label="Total size">
            <span className="tabular">{formatBytes(v?.total_bytes ?? d.total_bytes)}</span>
          </MetaItem>
          <MetaItem icon={<Download />} label="Downloads">
            <span className="tabular" title="All-time downloads">{compactNumber(d.download_count)}</span>
            <span className="tabular ml-1.5 text-xs font-normal text-subtle" title="Downloads in the last 30 days">{formatNumber(d.downloads_30d)} in 30d</span>
          </MetaItem>
          <MetaItem icon={<CalendarClock />} label="Updated">
            <span title={formatDateTime(d.updated_at)}>{relativeTime(d.updated_at)}</span>
          </MetaItem>
          <MetaItem icon={<Scale />} label="License">
            <span className="font-mono text-[13px]" title={d.license_name}>{d.license.toUpperCase()}</span>
          </MetaItem>
        </FactGrid>
        {d.is_demo ? (
          <p className="border-t border-border bg-bg-elevated/50 px-5 py-2.5 text-xs text-subtle sm:px-8">Download counts for demo datasets are synthetic.</p>
        ) : null}
      </header>

      {takenDown ? (
        <div className="mt-6">
          <InlineNotice tone="danger" title="This dataset has been taken down by a moderator">
            {d.takedown_reason ? <>Reason: {d.takedown_reason}</> : "It is hidden from the public."}
          </InlineNotice>
        </div>
      ) : null}

      <SectionNav className="mt-6" label="Dataset sections" items={sections} />

      <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-12">
          {v?.status === "draft" || v?.status === "archived" || gated ? (
            <div className="space-y-4">
              {v?.status === "draft" ? (
                <InlineNotice tone="warning" title="You're viewing an unpublished draft">
                  Only dataset managers can see draft versions. Publish it from the manage page when it's ready.
                </InlineNotice>
              ) : null}
              {v?.status === "archived" ? (
                <InlineNotice tone="info" title="Archived version">
                  This version is archived but stays downloadable so competitions and papers that used it remain reproducible.
                </InlineNotice>
              ) : null}

              {/* Terms gate */}
              {gated ? (
                <Card className="border-warning/40">
                  <CardHeader
                    icon={<FileCheck2 />}
                    title="Accept the dataset terms to download"
                    description="The owner requires everyone to accept these terms first. If the terms change you'll be asked again."
                  />
                  <CardBody className="space-y-4">
                    <div className="max-h-72 overflow-y-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated px-4 py-3">
                      {d.terms_html ? <Prose html={d.terms_html} /> : <p className="text-sm text-subtle">No terms text provided.</p>}
                    </div>
                    {viewer ? (
                      <Button loading={accept.isPending} onClick={() => accept.mutate(undefined)} icon={<FileCheck2 className="h-4 w-4" aria-hidden />}>
                        I accept these terms
                      </Button>
                    ) : (
                      <SignInPrompt text="You need an account to accept the terms and download files." />
                    )}
                  </CardBody>
                </Card>
              ) : null}
            </div>
          ) : null}

          {/* ------------------------------------------------------------------ files */}
          <DetailBlock
            id="files"
            eyebrow={v ? `Version ${v.version}` : "Files"}
            title="Files"
            description={
              v ? (
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <VersionStatusBadge status={v.status} />
                  <span className="tabular">{v.file_count} files · {formatBytes(v.total_bytes)}</span>
                  {v.published_at ? <span className="text-subtle" title={formatDateTime(v.published_at)}>· Published {relativeTime(v.published_at)}</span> : null}
                  {d.requires_terms && d.terms_accepted ? <Badge tone="success" icon={<FileCheck2 className="h-3 w-3" aria-hidden />}>Terms accepted</Badge> : null}
                </span>
              ) : d.requires_terms && d.terms_accepted ? (
                <Badge tone="success" icon={<FileCheck2 className="h-3 w-3" aria-hidden />}>Terms accepted</Badge>
              ) : undefined
            }
            action={
              d.versions.length ? (
                <div className="flex items-center gap-2">
                  <label htmlFor="version-select" className="text-eyebrow text-subtle">Version</label>
                  <Select id="version-select" className="h-9 w-auto min-w-0 max-w-full sm:min-w-56" value={String(v?.version ?? "")} onChange={(e) => setVersion(e.target.value)}>
                    {d.versions.map((ver) => (
                      <option key={ver.id} value={ver.version}>
                        v{ver.version} · {ver.status}{ver.published_at ? ` · ${formatDate(ver.published_at)}` : ""}{ver.version === d.latest_version ? " (latest)" : ""}
                      </option>
                    ))}
                  </Select>
                </div>
              ) : undefined
            }
          >
            {d.files.length === 0 ? (
              <EmptyState
                icon={<FileStack />}
                title="No files in this version"
                description={d.can_manage ? "Upload files to the draft version from the manage page." : "The owner hasn't uploaded files yet."}
                action={d.can_manage ? <LinkButton href={`/datasets/${d.slug}/manage`} variant="secondary">Manage files</LinkButton> : undefined}
              />
            ) : (
              <>
                <ul aria-label={v ? `Files in version ${v.version}` : "Files"} className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                  {d.files.map((f) => (
                    <FileRow
                      key={f.id}
                      file={f}
                      selected={previewId === f.id}
                      actions={
                        <>
                          {f.has_preview ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-pressed={previewId === f.id}
                              onClick={() => setPreviewId(previewId === f.id ? null : f.id)}
                              icon={<Eye className="h-3.5 w-3.5" aria-hidden />}
                              aria-label={`Preview ${f.filename}`}
                              className={cn("max-sm:h-9", previewId === f.id && "bg-surface-2 text-fg")}
                            >
                              Preview
                            </Button>
                          ) : null}
                          <Button
                            size="sm"
                            variant="secondary"
                            className="max-sm:h-9"
                            disabled={gated || f.scan_status === "infected"}
                            loading={downloading === f.id}
                            onClick={() => download(f)}
                            icon={<Download className="h-3.5 w-3.5" aria-hidden />}
                            aria-label={`Download ${f.filename}`}
                            title={gated ? "Accept the terms first" : undefined}
                          >
                            Download
                          </Button>
                        </>
                      }
                    />
                  ))}
                </ul>
                <p className="mt-3 flex items-start gap-1.5 text-xs text-subtle">
                  <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
                  Downloads use short-lived signed links. Verify integrity with the SHA-256 checksum.
                </p>
              </>
            )}

            {previewId ? (
              <div className="mt-5">
                <FilePreviewPanel slug={d.slug} fileId={previewId} onClose={() => setPreviewId(null)} />
              </div>
            ) : null}
          </DetailBlock>

          {/* ------------------------------------------------------------------ about */}
          <DetailBlock id="about" eyebrow="Overview" title="About this dataset">
            {d.description_html ? <Prose html={d.description_html} className="max-w-[72ch]" /> : <p className="text-sm text-subtle">No description provided.</p>}
          </DetailBlock>

          <DetailBlock
            id="dictionary"
            eyebrow="Schema"
            title="Data dictionary"
            description={v ? `Columns documented for version ${v.version}` : undefined}
          >
            <DataDictionaryTable entries={dictionary} />
          </DetailBlock>

          {v?.release_notes_html ? (
            <DetailBlock id="release-notes" eyebrow="Changelog" title={`Release notes · v${v.version}`}>
              <div className="border-l-2 border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] pl-5">
                <Prose html={v.release_notes_html} className="max-w-[72ch]" />
              </div>
            </DetailBlock>
          ) : null}

          {d.versions.length ? (
            <DetailBlock id="versions" eyebrow="History" title="Versions" description="Published versions are immutable, so results that used them stay reproducible.">
              <ol className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                {d.versions.map((ver, i) => {
                  const viewing = ver.version === v?.version;
                  return (
                    <li key={ver.id} className={cn("relative flex items-start gap-4 px-4 py-3.5 sm:px-5", i > 0 && "border-t border-border", viewing && "bg-accent-soft/40")}>
                      <span aria-hidden className="relative mt-1 flex h-3 w-3 shrink-0 items-center justify-center">
                        <span className={cn("h-2.5 w-2.5 rounded-full ring-4 ring-surface", viewing ? "bg-brand" : "bg-border-strong")} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-sm font-semibold text-fg">v{ver.version}</span>
                          <VersionStatusBadge status={ver.status} />
                          {ver.version === d.latest_version ? <Badge tone="accent">Latest</Badge> : null}
                        </div>
                        <p className="tabular mt-1 text-xs text-subtle">
                          {ver.file_count} files · {formatBytes(ver.total_bytes)} ·{" "}
                          {ver.published_at ? (
                            <span title={formatDateTime(ver.published_at)}>published {formatDate(ver.published_at)}</span>
                          ) : (
                            <span title={formatDateTime(ver.created_at)}>created {formatDate(ver.created_at)}</span>
                          )}
                        </p>
                      </div>
                      {viewing ? (
                        <span className="shrink-0 self-center text-xs font-medium text-accent-strong" aria-current="true">Viewing</span>
                      ) : (
                        <Button size="sm" variant="ghost" className="shrink-0 self-center max-sm:h-9" onClick={() => setVersion(String(ver.version))} aria-label={`View version ${ver.version}`}>
                          View
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ol>
            </DetailBlock>
          ) : null}
        </div>

        {/* ------------------------------------------------------------------ aside */}
        <aside className="min-w-0 space-y-4" aria-label="Dataset details">
          <Card>
            <CardHeader icon={<Scale />} title="License" />
            <CardBody className="space-y-1 text-sm">
              <p className="font-medium text-fg">{d.license_name}</p>
              <p className="font-mono text-xs text-subtle">{d.license}</p>
              {d.requires_terms ? (
                <p className="flex items-center gap-1.5 pt-2 text-xs text-muted">
                  <FileCheck2 className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
                  {d.terms_accepted ? "You've accepted the download terms." : "Downloading requires accepting the owner's terms."}
                </p>
              ) : null}
            </CardBody>
          </Card>

          {d.citation ? (
            <Card>
              <CardHeader icon={<Quote />} title="Cite this dataset" action={<CopyButton value={d.citation} label="Copy citation" />} />
              <CardBody>
                <pre className="whitespace-pre-wrap break-words rounded-[var(--radius-md)] border border-border bg-bg-elevated p-3 font-mono text-xs leading-relaxed text-muted">{d.citation}</pre>
              </CardBody>
            </Card>
          ) : null}

          {d.attribution || d.source_url ? (
            <Card>
              <CardHeader icon={<ScrollText />} title="Provenance" />
              <CardBody className="space-y-3 text-sm">
                {d.attribution ? (
                  <div>
                    <p className="text-eyebrow text-subtle">Attribution</p>
                    <p className="mt-1 whitespace-pre-line text-muted">{d.attribution}</p>
                  </div>
                ) : null}
                {d.source_url ? (
                  <div>
                    <p className="text-eyebrow text-subtle">Original source</p>
                    <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 break-all text-accent-strong hover:underline">
                      {d.source_url}
                      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                      <span className="sr-only">(opens in a new tab)</span>
                    </a>
                  </div>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader icon={<Trophy />} title="Used by competitions" />
            {d.used_by.length ? (
              <ul className="divide-y divide-border">
                {d.used_by.map((c) => (
                  <li key={c.slug}>
                    <Link href={`/competitions/${c.slug}`} className="group flex items-center gap-3 px-5 py-3 text-sm text-fg transition-colors hover:bg-surface-2/60 hover:text-accent-strong">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-soft text-accent-strong" aria-hidden>
                        <Trophy className="h-3.5 w-3.5" />
                      </span>
                      <span className="min-w-0 flex-1 truncate font-medium">{c.title}</span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-subtle transition-transform group-hover:translate-x-0.5" aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <CardBody>
                <p className="text-sm text-subtle">Not used by any competition you can see.</p>
              </CardBody>
            )}
          </Card>
        </aside>
      </div>
    </Container>
  );
}

export default function DatasetPage() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <DatasetDetailView />
    </Suspense>
  );
}
