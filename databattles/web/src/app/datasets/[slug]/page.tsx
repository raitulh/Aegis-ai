"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, CalendarClock, Download, ExternalLink, Eye, FileCheck2, Lock, Scale, Settings, ShieldAlert, ShieldCheck, Trophy } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { DataDictionaryTable, FileKindBadge, FilePreviewPanel, ScanBadge, Sha, VersionStatusBadge, toDictionary } from "@/components/catalog/dataset-bits";
import { describeError, settle } from "@/components/catalog/errors";
import { ReportDialog } from "@/components/catalog/report-dialog";
import type { DatasetFile } from "@/components/catalog/types";
import { UserLink } from "@/components/domain/cards";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { CopyButton } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, SignInPrompt, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
import { compactNumber, formatBytes, formatDate, formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { hasRole, useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { DatasetDetail } from "@/lib/types";

function DetailSkeleton() {
  return (
    <Container className="py-8">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="mt-4 h-8 w-2/3" />
      <Skeleton className="mt-3 h-4 w-1/2" />
      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2"><SkeletonRows rows={6} /></div>
        <SkeletonRows rows={4} />
      </div>
    </Container>
  );
}

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

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <Link href="/datasets" className="hover:text-fg">Datasets</Link>
        <span aria-hidden> / </span>
        <span className="text-muted">{d.title}</span>
      </nav>

      <header className="flex flex-col gap-4 py-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {d.is_demo ? <DemoBadge /> : null}
            <Badge tone="outline" icon={<Scale className="h-3 w-3" aria-hidden />} title={`License: ${d.license}`}>{d.license_name}</Badge>
            {d.visibility === "org" ? <Badge tone="info" icon={<Building2 className="h-3 w-3" aria-hidden />}>Organization only</Badge> : null}
            {d.visibility === "private" ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Private</Badge> : null}
            {d.requires_terms ? <Badge tone="warning" icon={<FileCheck2 className="h-3 w-3" aria-hidden />}>Terms required</Badge> : null}
            {takenDown ? <Badge tone="danger" icon={<ShieldAlert className="h-3 w-3" aria-hidden />}>Taken down</Badge> : null}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{d.title}</h1>
          {d.subtitle ? <p className="mt-2 max-w-3xl text-base text-muted">{d.subtitle}</p> : null}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted">
            {d.owner_org ? (
              <Link href={`/orgs/${d.owner_org.slug}`} className="inline-flex items-center gap-1.5 hover:text-accent-strong">
                <Building2 className="h-4 w-4" aria-hidden /> {d.owner_org.name}
              </Link>
            ) : (
              <UserLink user={d.owner} />
            )}
            <span className="inline-flex items-center gap-1" title={formatDateTime(d.updated_at)}>
              <CalendarClock className="h-4 w-4" aria-hidden /> Updated {relativeTime(d.updated_at)}
            </span>
            {d.tags.length ? (
              <span className="flex flex-wrap gap-1">
                {d.tags.map((t) => (
                  <Link key={t} href={`/datasets?tag=${encodeURIComponent(t)}`} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted hover:text-fg">#{t}</Link>
                ))}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {d.can_manage ? (
            <LinkButton href={`/datasets/${d.slug}/manage`} variant="secondary" icon={<Settings className="h-4 w-4" aria-hidden />}>Manage</LinkButton>
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
      </header>

      {takenDown ? (
        <div className="mb-6">
          <InlineNotice tone="danger" title="This dataset has been taken down by a moderator">
            {d.takedown_reason ? <>Reason: {d.takedown_reason}</> : "It is hidden from the public."}
          </InlineNotice>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          {/* Version selector */}
          {d.versions.length ? (
            <Card>
              <CardBody className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3">
                  <label htmlFor="version-select" className="text-sm font-medium text-fg">Version</label>
                  <Select id="version-select" className="w-auto min-w-48" value={String(v?.version ?? "")} onChange={(e) => setVersion(e.target.value)}>
                    {d.versions.map((ver) => (
                      <option key={ver.id} value={ver.version}>
                        v{ver.version} · {ver.status}{ver.published_at ? ` · ${formatDate(ver.published_at)}` : ""}{ver.version === d.latest_version ? " (latest)" : ""}
                      </option>
                    ))}
                  </Select>
                </div>
                {v ? (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <VersionStatusBadge status={v.status} />
                    <span>{v.file_count} files · {formatBytes(v.total_bytes)}</span>
                    {v.published_at ? <span title={formatDateTime(v.published_at)}>Published {relativeTime(v.published_at)}</span> : null}
                  </div>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

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
              <CardHeader title="Accept the dataset terms to download" description="The owner requires everyone to accept these terms first. If the terms change you'll be asked again." />
              <CardBody className="space-y-4">
                <div className="max-h-72 overflow-y-auto rounded-[var(--radius-md)] border border-border bg-surface-2 px-4 py-3">
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

          {/* Files */}
          <Card>
            <CardHeader
              title="Files"
              description={v ? `Version ${v.version}` : undefined}
              action={d.requires_terms && d.terms_accepted ? <Badge tone="success" icon={<FileCheck2 className="h-3 w-3" aria-hidden />}>Terms accepted</Badge> : undefined}
            />
            <CardBody className="p-0">
              {d.files.length === 0 ? (
                <div className="p-5">
                  <EmptyState
                    title="No files in this version"
                    description={d.can_manage ? "Upload files to the draft version from the manage page." : "The owner hasn't uploaded files yet."}
                    action={d.can_manage ? <LinkButton href={`/datasets/${d.slug}/manage`} variant="secondary">Manage files</LinkButton> : undefined}
                  />
                </div>
              ) : (
                <div className="p-3">
                  <Table>
                    <THead>
                      <tr>
                        <TH>File</TH>
                        <TH className="text-right">Size</TH>
                        <TH className="text-right">Rows</TH>
                        <TH>SHA-256</TH>
                        <TH>Scan</TH>
                        <TH><span className="sr-only">Actions</span></TH>
                      </tr>
                    </THead>
                    <TBody>
                      {d.files.map((f) => (
                        <TR key={f.id} className={previewId === f.id ? "bg-accent-soft" : undefined}>
                          <TD>
                            <div className="flex flex-col gap-1">
                              <span className="break-all font-mono text-xs text-fg">{f.filename}</span>
                              <FileKindBadge kind={f.kind} />
                            </div>
                          </TD>
                          <TD className="whitespace-nowrap text-right tabular-nums text-muted">{formatBytes(f.size_bytes)}</TD>
                          <TD className="whitespace-nowrap text-right tabular-nums text-muted">{f.row_count !== null ? formatNumber(f.row_count) : "—"}</TD>
                          <TD><Sha value={f.sha256} /></TD>
                          <TD><ScanBadge status={f.scan_status} /></TD>
                          <TD>
                            <div className="flex justify-end gap-1.5">
                              {f.has_preview ? (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  aria-pressed={previewId === f.id}
                                  onClick={() => setPreviewId(previewId === f.id ? null : f.id)}
                                  icon={<Eye className="h-3.5 w-3.5" aria-hidden />}
                                  aria-label={`Preview ${f.filename}`}
                                >
                                  Preview
                                </Button>
                              ) : null}
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={gated || f.scan_status === "infected"}
                                loading={downloading === f.id}
                                onClick={() => download(f)}
                                icon={<Download className="h-3.5 w-3.5" aria-hidden />}
                                aria-label={`Download ${f.filename}`}
                                title={gated ? "Accept the terms first" : undefined}
                              >
                                Download
                              </Button>
                            </div>
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                  <p className="mt-2 px-1 text-xs text-subtle">Downloads use short-lived signed links. Verify integrity with the SHA-256 checksum.</p>
                </div>
              )}
            </CardBody>
          </Card>

          {previewId ? <FilePreviewPanel slug={d.slug} fileId={previewId} onClose={() => setPreviewId(null)} /> : null}

          <Card>
            <CardHeader title="About this dataset" />
            <CardBody>
              {d.description_html ? <Prose html={d.description_html} /> : <p className="text-sm text-subtle">No description provided.</p>}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Data dictionary" description={v ? `Columns documented for version ${v.version}` : undefined} />
            <CardBody>
              <DataDictionaryTable entries={dictionary} />
            </CardBody>
          </Card>

          {v?.release_notes_html ? (
            <Card>
              <CardHeader title={`Release notes · v${v.version}`} />
              <CardBody><Prose html={v.release_notes_html} /></CardBody>
            </Card>
          ) : null}
        </div>

        <aside className="space-y-6" aria-label="Dataset details">
          <Card>
            <CardBody>
              <dl className="grid grid-cols-2 gap-4 text-sm">
                <div><dt className="text-xs text-subtle">Downloads (30 days)</dt><dd className="mt-0.5 text-lg font-semibold tabular-nums">{formatNumber(d.downloads_30d)}</dd></div>
                <div><dt className="text-xs text-subtle">All-time downloads</dt><dd className="mt-0.5 text-lg font-semibold tabular-nums">{compactNumber(d.download_count)}</dd></div>
                <div><dt className="text-xs text-subtle">Latest version</dt><dd className="mt-0.5 font-medium">{d.latest_version ? `v${d.latest_version}` : "Unpublished"}</dd></div>
                <div><dt className="text-xs text-subtle">Size</dt><dd className="mt-0.5 font-medium">{formatBytes(v?.total_bytes ?? d.total_bytes)}</dd></div>
              </dl>
              {d.is_demo ? <p className="mt-3 text-xs text-subtle">Download counts for demo datasets are synthetic.</p> : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="License" />
            <CardBody className="space-y-1 text-sm">
              <p className="font-medium text-fg">{d.license_name}</p>
              <p className="font-mono text-xs text-subtle">{d.license}</p>
            </CardBody>
          </Card>

          {d.citation ? (
            <Card>
              <CardHeader title="Cite this dataset" action={<CopyButton value={d.citation} label="Copy citation" />} />
              <CardBody>
                <pre className="whitespace-pre-wrap break-words rounded-[var(--radius-md)] bg-surface-2 p-3 font-mono text-xs text-muted">{d.citation}</pre>
              </CardBody>
            </Card>
          ) : null}

          {d.attribution || d.source_url ? (
            <Card>
              <CardHeader title="Provenance" />
              <CardBody className="space-y-3 text-sm">
                {d.attribution ? (
                  <div>
                    <p className="text-xs text-subtle">Attribution</p>
                    <p className="mt-0.5 whitespace-pre-line text-muted">{d.attribution}</p>
                  </div>
                ) : null}
                {d.source_url ? (
                  <div>
                    <p className="text-xs text-subtle">Original source</p>
                    <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-flex items-center gap-1 break-all text-accent-strong hover:underline">
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
            <CardHeader title="Used by competitions" />
            <CardBody>
              {d.used_by.length ? (
                <ul className="space-y-2">
                  {d.used_by.map((c) => (
                    <li key={c.slug}>
                      <Link href={`/competitions/${c.slug}`} className="inline-flex items-center gap-2 text-sm text-fg hover:text-accent-strong">
                        <Trophy className="h-4 w-4 text-subtle" aria-hidden /> {c.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-subtle">Not used by any competition you can see.</p>
              )}
            </CardBody>
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
