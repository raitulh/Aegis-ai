"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ExternalLink, GitCommitVertical, Plus, Rocket, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { FileKindBadge, ScanBadge, Sha, VersionStatusBadge, toDictionary } from "@/components/catalog/dataset-bits";
import { settle } from "@/components/catalog/errors";
import { DatasetMetaForm, datasetPayload, type DatasetFormValues } from "@/components/catalog/dataset-form";
import { DataDictionaryEditor, DatasetUploader, useDirty } from "@/components/catalog/dataset-manage";
import type { DataDictionaryEntry, DatasetVersion } from "@/components/catalog/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, PermissionDenied, SkeletonRows } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { formatBytes, formatDate, formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { DatasetDetail } from "@/lib/types";

function toForm(d: DatasetDetail): DatasetFormValues {
  return {
    title: d.title,
    subtitle: d.subtitle ?? "",
    description_md: d.description_md ?? "",
    license: d.license,
    citation: d.citation ?? "",
    attribution: d.attribution ?? "",
    source_url: d.source_url ?? "",
    tags: d.tags,
    visibility: (d.visibility as DatasetFormValues["visibility"]) ?? "public",
    requires_terms: d.requires_terms,
    terms_md: "",
    owner_org_id: d.owner_org?.id ?? "",
  };
}

/* ------------------------------------------------------------------------------------------------ metadata */

function MetadataTab({ d }: { d: DatasetDetail }) {
  const qc = useQueryClient();
  const [error, setError] = useState<ApiError | null>(null);
  const [formKey, setFormKey] = useState(0);
  const licenses = useQuery({ queryKey: ["datasets", "licenses"], queryFn: () => get<Record<string, string>>("/datasets/licenses"), staleTime: 10 * 60_000 });
  const initial = useMemo(() => toForm(d), [d]);
  const save = useApiMutation((v: DatasetFormValues) => patch<DatasetDetail>(`/datasets/${d.slug}`, datasetPayload(v, "edit")), {
    success: "Dataset details saved",
    invalidate: [["datasets", "list"]],
    onSuccess: (updated) => {
      qc.setQueryData(qk.dataset(d.slug), updated);
      void qc.invalidateQueries({ queryKey: ["datasets", d.slug] });
      setFormKey((k) => k + 1);
    },
    onError: (e) => setError(e),
  });
  return (
    <DatasetMetaForm
      key={formKey}
      mode="edit"
      initial={initial}
      licenses={licenses.data}
      hasOrgOwner={Boolean(d.owner_org)}
      existingTermsHtml={d.terms_html}
      submitting={save.isPending}
      error={error}
      submitLabel="Save details"
      onSubmit={(v) => {
        setError(null);
        save.mutate(v);
      }}
    />
  );
}

/* ------------------------------------------------------------------------------------------------ versions */

function NewVersionDialog({ slug, disabled, onCreated }: { slug: string; disabled: boolean; onCreated: (v: DatasetVersion) => void }) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const create = useApiMutation(() => post<DatasetVersion>(`/datasets/${slug}/versions`, { release_notes_md: notes || null }), {
    success: (v) => `Draft version ${v.version} created`,
    invalidate: [["datasets", slug]],
    onSuccess: (v) => {
      setOpen(false);
      setNotes("");
      onCreated(v);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="Create a new draft version"
      description="Upload files to the draft and publish it when ready. Earlier versions stay available and unchanged."
      trigger={
        <Button size="sm" disabled={disabled} icon={<Plus className="h-4 w-4" aria-hidden />} title={disabled ? "Publish the existing draft first" : undefined}>
          New version
        </Button>
      }
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button loading={create.isPending} onClick={() => create.mutate(undefined)}>Create draft</Button>
        </>
      }
    >
      <Field label="Release notes (optional)" hint="What changed compared to the previous version. You can edit this until you publish.">
        {(p) => <MarkdownEditor {...p} value={notes} onChange={setNotes} rows={5} maxLength={20_000} />}
      </Field>
    </Dialog>
  );
}

function VersionEditor({ slug, version, files }: { slug: string; version: DatasetVersion; files: DatasetDetail["files"] }) {
  const qc = useQueryClient();
  const initialNotes = version.release_notes_md ?? "";
  const initialDict = useMemo(() => toDictionary(version), [version]);
  const [notes, setNotes] = useState(initialNotes);
  const [dict, setDict] = useState<DataDictionaryEntry[]>(initialDict);
  const dirty = useDirty({ notes, dict }, { notes: initialNotes, dict: initialDict });
  const save = useApiMutation(
    () =>
      patch<DatasetVersion>(`/datasets/${slug}/versions/${version.version}`, {
        release_notes_md: notes,
        data_dictionary: dict.filter((r) => r.column.trim()).map((r) => ({ column: r.column.trim(), type: r.type.trim(), description: r.description.trim() })),
      }),
    {
      success: "Version notes and data dictionary saved",
      onSuccess: () => void qc.invalidateQueries({ queryKey: ["datasets", slug] }),
    },
  );
  return (
    <Card>
      <CardHeader title="Release notes & data dictionary" description="Editable until the version is published." />
      <CardBody className="space-y-6">
        <Field label="Release notes">
          {(p) => <MarkdownEditor {...p} value={notes} onChange={setNotes} rows={5} maxLength={20_000} placeholder="What's in this version?" />}
        </Field>
        <div>
          <h3 className="mb-2 text-sm font-medium text-fg">Data dictionary</h3>
          <DataDictionaryEditor slug={slug} value={dict} onChange={setDict} files={files} />
        </div>
      </CardBody>
      <CardFooter>
        {dirty ? <span className="mr-auto text-xs text-subtle" aria-live="polite">Unsaved changes</span> : null}
        <Button loading={save.isPending} disabled={!dirty} onClick={() => save.mutate(undefined)}>Save version details</Button>
      </CardFooter>
    </Card>
  );
}

function VersionWorkspace({ d, versionNumber }: { d: DatasetDetail; versionNumber: number }) {
  const sameAsBase = d.current_version?.version === versionNumber;
  const q = useQuery({
    queryKey: qk.dataset(d.slug, versionNumber),
    queryFn: () => get<DatasetDetail>(`/datasets/${d.slug}`, { version: versionNumber }),
    enabled: !sameAsBase,
  });
  const data = sameAsBase ? d : q.data;
  const v = data?.current_version?.version === versionNumber ? data.current_version : null;

  const publish = useApiMutation(() => post<DatasetVersion>(`/datasets/${d.slug}/versions/${versionNumber}/publish`), {
    success: `Version ${versionNumber} published`,
    invalidate: [["datasets"]],
  });
  const archive = useApiMutation(() => post<DatasetVersion>(`/datasets/${d.slug}/versions/${versionNumber}/archive`), {
    success: `Version ${versionNumber} archived`,
    invalidate: [["datasets"]],
  });
  const remove = useApiMutation((fileId: string) => del<{ message: string }>(`/datasets/${d.slug}/versions/${versionNumber}/files/${fileId}`), {
    success: (r) => r.message,
    invalidate: [["datasets", d.slug]],
  });

  if (!sameAsBase && q.isPending) return <SkeletonRows rows={5} />;
  if (!sameAsBase && q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!data || !v) return <EmptyState title="Version not found" description="It may have been removed. Choose another version." />;

  const files = data.files;
  const isDraft = v.status === "draft";

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title={<span className="inline-flex items-center gap-2">Version {v.version} <VersionStatusBadge status={v.status} /></span>}
          description={
            isDraft
              ? "Drafts are only visible to managers. Publishing makes the files immutable."
              : v.status === "published"
                ? `Published ${formatDateTime(v.published_at)} · immutable`
                : `Archived ${formatDateTime(v.archived_at)} · still downloadable for reproducibility`
          }
          action={
            <div className="flex flex-wrap gap-2">
              <LinkButton href={`/datasets/${d.slug}?version=${v.version}`} size="sm" variant="ghost" icon={<ExternalLink className="h-3.5 w-3.5" aria-hidden />}>
                View
              </LinkButton>
              {isDraft ? (
                <ConfirmDialog
                  trigger={<Button size="sm" disabled={files.length === 0} icon={<Rocket className="h-3.5 w-3.5" aria-hidden />} title={files.length === 0 ? "Upload at least one file first" : undefined}>Publish</Button>}
                  title={`Publish version ${v.version}?`}
                  description="Published versions are immutable: files, release notes and the data dictionary can't be changed afterwards. It becomes the latest version shown to everyone who can see the dataset."
                  confirmLabel="Publish"
                  tone="primary"
                  onConfirm={() => settle(publish.mutateAsync(undefined))}
                />
              ) : null}
              {v.status === "published" ? (
                <ConfirmDialog
                  trigger={<Button size="sm" variant="outline" icon={<Archive className="h-3.5 w-3.5" aria-hidden />}>Archive</Button>}
                  title={`Archive version ${v.version}?`}
                  description="Archived versions stay downloadable so competitions that reference them keep working. If this is the latest version, the newest other published version becomes the latest."
                  confirmLabel="Archive"
                  onConfirm={() => settle(archive.mutateAsync(undefined))}
                />
              ) : null}
            </div>
          }
        />
        <CardBody className="space-y-5">
          {isDraft ? <DatasetUploader slug={d.slug} version={v.version} /> : null}
          {files.length === 0 ? (
            <EmptyState title="No files yet" description={isDraft ? "Drop a file above to add it to this draft." : "This version has no files."} />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>File</TH>
                  <TH className="text-right">Size</TH>
                  <TH className="text-right">Rows</TH>
                  <TH>SHA-256</TH>
                  <TH>Scan</TH>
                  <TH>Added</TH>
                  {isDraft ? <TH><span className="sr-only">Actions</span></TH> : null}
                </tr>
              </THead>
              <TBody>
                {files.map((f) => (
                  <TR key={f.id}>
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
                    <TD className="whitespace-nowrap text-xs text-subtle" title={formatDateTime(f.created_at)}>{relativeTime(f.created_at)}</TD>
                    {isDraft ? (
                      <TD className="text-right">
                        <ConfirmDialog
                          trigger={<Button size="icon" variant="ghost" aria-label={`Delete ${f.filename}`}><Trash2 className="h-4 w-4" /></Button>}
                          title={`Delete ${f.filename}?`}
                          description="The file is removed from this draft and from storage."
                          confirmLabel="Delete file"
                          onConfirm={() => settle(remove.mutateAsync(f.id))}
                        />
                      </TD>
                    ) : null}
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <p className="text-xs text-subtle">
            {v.file_count} files · {formatBytes(v.total_bytes)} in this version. Storage counts toward the owner&apos;s quota.
          </p>
        </CardBody>
      </Card>

      {isDraft ? (
        <VersionEditor key={`${v.id}:${v.release_notes_md}:${JSON.stringify(v.data_dictionary)}`} slug={d.slug} version={v} files={files} />
      ) : (
        <Card>
          <CardHeader title="Release notes & data dictionary" description="Read-only — published versions can't change. Create a new version to make edits." />
          <CardBody className="space-y-5">
            {v.release_notes_html ? <Prose html={v.release_notes_html} /> : <p className="text-sm text-subtle">No release notes.</p>}
            <div className="overflow-x-auto">
              {toDictionary(v).length ? (
                <ul className="divide-y divide-border rounded-[var(--radius-md)] border border-border text-sm">
                  {toDictionary(v).map((e, i) => (
                    <li key={i} className="flex flex-wrap gap-x-3 px-3 py-2">
                      <span className="font-mono text-xs text-fg">{e.column}</span>
                      {e.type ? <span className="font-mono text-xs text-subtle">{e.type}</span> : null}
                      <span className="text-muted">{e.description}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-subtle">No data dictionary.</p>
              )}
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function VersionsTab({ d }: { d: DatasetDetail }) {
  const draft = d.versions.find((v) => v.status === "draft");
  const [selected, setSelected] = useState<number | null>(null);
  const current = selected ?? draft?.version ?? d.current_version?.version ?? d.versions[0]?.version ?? null;

  useEffect(() => {
    // Selected version disappeared (should not happen, but keep the UI consistent).
    if (selected !== null && !d.versions.some((v) => v.version === selected)) setSelected(null);
  }, [d.versions, selected]);

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
      <Card className="h-fit">
        <CardHeader title="Versions" action={<NewVersionDialog slug={d.slug} disabled={Boolean(draft)} onCreated={(v) => setSelected(v.version)} />} />
        <ul className="divide-y divide-border" aria-label="Dataset versions">
          {d.versions.map((v) => {
            const active = v.version === current;
            return (
              <li key={v.id}>
                <button
                  type="button"
                  onClick={() => setSelected(v.version)}
                  aria-current={active ? "true" : undefined}
                  className={`flex w-full items-start gap-3 px-5 py-3 text-left transition-colors hover:bg-surface-2 ${active ? "bg-accent-soft" : ""}`}
                >
                  <GitCommitVertical className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="font-medium text-fg">v{v.version}</span>
                      <VersionStatusBadge status={v.status} />
                      {v.version === d.latest_version && v.status !== "draft" ? <Badge tone="accent">Latest</Badge> : null}
                    </span>
                    <span className="mt-0.5 block text-xs text-subtle">
                      {v.file_count} files · {formatBytes(v.total_bytes)} · {v.published_at ? `published ${formatDate(v.published_at)}` : `created ${formatDate(v.created_at)}`}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {draft ? <p className="border-t border-border px-5 py-3 text-xs text-subtle">Only one draft at a time — publish v{draft.version} before starting another.</p> : null}
      </Card>
      <div className="min-w-0">
        {current !== null ? <VersionWorkspace key={current} d={d} versionNumber={current} /> : <EmptyState title="No versions" description="Create a version to upload files." />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ page */

export default function ManageDatasetPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const q = useQuery({
    queryKey: qk.dataset(slug),
    queryFn: () => get<DatasetDetail>(`/datasets/${slug}`),
    enabled: Boolean(me.data),
  });
  const [tab, setTab] = useState("files");

  if (me.isPending || !me.data || q.isPending) {
    return <Container className="py-8"><SkeletonRows rows={8} /></Container>;
  }
  if (q.isError) {
    return (
      <Container className="py-10">
        {q.error instanceof ApiError && q.error.status === 404 ? <NotFoundState what="dataset" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      </Container>
    );
  }
  const d = q.data;
  if (!d.can_manage) {
    return (
      <Container className="py-10">
        <PermissionDenied message="Only the dataset owner (or managers of the owning organization) can manage this dataset." />
      </Container>
    );
  }
  const hasPublished = d.versions.some((v) => v.status !== "draft");

  return (
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <Link href="/datasets" className="hover:text-fg">Datasets</Link>
        <span aria-hidden> / </span>
        <Link href={`/datasets/${d.slug}`} className="hover:text-fg">{d.title}</Link>
        <span aria-hidden> / </span>
        <span className="text-muted">Manage</span>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow={d.owner_org ? `Managed by ${d.owner_org.name}` : "Your dataset"}
        title={<span className="inline-flex flex-wrap items-center gap-3">{d.title}{d.is_demo ? <DemoBadge /> : null}</span>}
        description={hasPublished ? `Latest published version: v${d.latest_version}.` : "Not published yet — upload files to the draft and publish it to make the dataset available."}
        actions={<LinkButton href={`/datasets/${d.slug}`} variant="secondary" icon={<ExternalLink className="h-4 w-4" aria-hidden />}>View public page</LinkButton>}
      />
      {d.status === "taken_down" ? (
        <div className="mb-6">
          <InlineNotice tone="danger" title="Taken down by a moderator">{d.takedown_reason ?? "This dataset is hidden from the public."}</InlineNotice>
        </div>
      ) : null}
      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "files", label: "Versions & files", count: d.versions.length },
          { value: "details", label: "Details" },
        ]}
      >
        <TabPanel value="files"><VersionsTab d={d} /></TabPanel>
        <TabPanel value="details"><MetadataTab d={d} /></TabPanel>
      </Tabs>
    </Container>
  );
}
