"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  BookMarked,
  BookOpen,
  Cpu,
  Database,
  Download,
  ExternalLink,
  Eye,
  FileCheck2,
  FileCode2,
  FileSpreadsheet,
  FileText,
  FolderGit2,
  NotebookPen,
  Quote,
  ShieldCheck,
  Table2,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Block, PanelLabel } from "@/components/competition/block";
import { useCompetition } from "@/components/competition/context";
import { FILE_KIND_LABELS } from "@/components/competition/labels";
import type { DatasetFile, TablePreview } from "@/components/competition/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { CopyButton } from "@/components/ui/misc";
import { EmptyState, ErrorState, InlineNotice, SignInPrompt, Skeleton, Spinner } from "@/components/ui/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail, DatasetDetail, Schemas } from "@/lib/types";

type PreviewOut = Schemas["PreviewOut"];
type DownloadOut = Schemas["DownloadOut"];

function asTablePreview(p: PreviewOut["preview"]): TablePreview | null {
  if (!p || !Array.isArray((p as { columns?: unknown }).columns)) return null;
  const t = p as unknown as TablePreview;
  return { columns: t.columns.map(String), rows: Array.isArray(t.rows) ? t.rows : [], truncated_columns: Boolean(t.truncated_columns) };
}

function PreviewTable({ preview }: { preview: TablePreview }) {
  return (
    <div className="max-h-[60vh] overflow-auto rounded-[var(--radius-md)] border border-border">
      <table className="w-full border-collapse font-mono text-xs">
        <thead className="sticky top-0 bg-surface-2 text-left text-subtle">
          <tr>
            {preview.columns.map((c, i) => (
              <th key={i} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border bg-surface">
          {preview.rows.map((row, r) => (
            <tr key={r} className="hover:bg-surface-2/60">
              {preview.columns.map((_, c) => (
                <td key={c} className="tabular whitespace-nowrap px-3 py-1.5 text-fg">{row[c] ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PreviewDialog({ datasetSlug, file, open, onOpenChange }: { datasetSlug: string; file: DatasetFile; open: boolean; onOpenChange: (o: boolean) => void }) {
  const query = useQuery({
    queryKey: ["datasets", datasetSlug, "preview", file.id],
    queryFn: () => get<PreviewOut>(`/datasets/${encodeURIComponent(datasetSlug)}/files/${file.id}/preview`),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const preview = query.data ? asTablePreview(query.data.preview) : null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" title={`Preview · ${file.filename}`} description="The first rows of the file, as stored. Values are shown as text.">
      {query.isPending ? (
        <Spinner label="Loading preview" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !preview ? (
        <EmptyState title="No preview available" description="Previews are generated for CSV and TSV files. Download the file to inspect it." className="py-8" />
      ) : (
        <div className="space-y-2">
          <PreviewTable preview={preview} />
          <p className="text-xs text-subtle">
            Showing <span className="tabular">{formatNumber(preview.rows.length)}</span> rows
            {query.data?.row_count !== null && query.data?.row_count !== undefined ? <> of <span className="tabular">{formatNumber(query.data.row_count)}</span></> : ""}
            {preview.truncated_columns ? " · some columns are hidden in the preview" : ""}
          </p>
        </div>
      )}
    </Dialog>
  );
}

function TermsCard({ dataset, termsRef }: { dataset: DatasetDetail; termsRef: React.RefObject<HTMLDivElement | null> }) {
  const me = useMe().data;
  const qc = useQueryClient();
  const [agree, setAgree] = useState(false);
  const accept = useMutation<unknown, ApiError>({
    mutationFn: () => post(`/datasets/${encodeURIComponent(dataset.slug)}/accept-terms`),
    onSuccess: async () => {
      toast.success("Terms accepted — downloads are unlocked.");
      await qc.invalidateQueries({ queryKey: ["datasets", dataset.slug] });
    },
    onError: (e) => toast.error(e.message),
  });
  return (
    <div ref={termsRef} tabIndex={-1} className="scroll-mt-28 outline-none">
      <Card className="border-warning/40">
        <CardHeader icon={<ShieldCheck />} title="Dataset terms of use" description="The data owner requires you to accept these terms before downloading." />
        <CardBody className="space-y-4">
          <div className="max-h-64 overflow-y-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated px-4 py-3" tabIndex={0} aria-label="Terms text">
            {dataset.terms_html ? <Prose html={dataset.terms_html} className="text-sm" /> : <p className="text-sm text-muted">See the dataset page for the full terms.</p>}
          </div>
          {me ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <Checkbox label="I have read and agree to the dataset terms" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              <Button disabled={!agree} loading={accept.isPending} onClick={() => accept.mutate()} icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>
                Accept terms
              </Button>
            </div>
          ) : (
            <SignInPrompt text="Sign in to accept the terms and download the data." />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function fileIcon(kind: string) {
  if (kind === "sample_submission") return <FileCheck2 aria-hidden />;
  if (kind === "notebook") return <NotebookPen aria-hidden />;
  if (kind === "documentation") return <FileText aria-hidden />;
  return <FileSpreadsheet aria-hidden />;
}

function FileList({ dataset, needsTerms, onTermsRequired }: { dataset: DatasetDetail; needsTerms: boolean; onTermsRequired: () => void }) {
  const [previewing, setPreviewing] = useState<DatasetFile | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  async function download(file: DatasetFile) {
    if (needsTerms) return onTermsRequired();
    setDownloading(file.id);
    try {
      const res = await post<DownloadOut>(`/datasets/${encodeURIComponent(dataset.slug)}/files/${file.id}/download`);
      window.location.assign(res.url);
    } catch (e) {
      if (e instanceof ApiError && e.code === "terms_required") {
        toast.error(e.message);
        onTermsRequired();
      } else {
        toast.error(e instanceof ApiError ? e.message : "Download failed. Please try again.");
      }
    } finally {
      setDownloading(null);
    }
  }

  if (!dataset.files.length) {
    return <EmptyState title="No files in this version" description="The dataset owner hasn't uploaded files to this version yet." className="m-4" />;
  }
  return (
    <>
      <ul aria-label={`Files in ${dataset.title} version ${dataset.current_version?.version ?? ""}`} className="divide-y divide-border">
        {dataset.files.map((f) => {
          const blocked = f.scan_status === "infected";
          const scanning = f.scan_status === "pending";
          const sample = f.kind === "sample_submission";
          return (
            <li key={f.id} className="flex flex-col gap-3 px-4 py-4 transition-colors hover:bg-surface-2/40 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <span
                  className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border shadow-[inset_0_1px_0_var(--hairline-highlight)] [&_svg]:h-4 [&_svg]:w-4",
                    sample ? "bg-accent-soft text-accent-strong" : "bg-cyan-soft text-cyan",
                  )}
                >
                  {fileIcon(f.kind)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="break-all font-mono text-[13.5px] font-medium text-fg">{f.filename}</span>
                    <Badge tone={sample ? "accent" : "outline"}>{FILE_KIND_LABELS[f.kind] ?? f.kind}</Badge>
                    {scanning ? <Badge tone="info">Scanning</Badge> : null}
                    {blocked ? <Badge tone="danger">Blocked</Badge> : null}
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-subtle">
                    <span className="tabular">{formatBytes(f.size_bytes)}</span>
                    {f.row_count !== null && f.row_count !== undefined ? (
                      <span><span className="tabular">{formatNumber(f.row_count)}</span> rows</span>
                    ) : null}
                    <span className="inline-flex items-center gap-1.5">
                      <span className="font-mono" title={`SHA-256 ${f.sha256}`}>sha256 {f.sha256.slice(0, 12)}…</span>
                      <CopyButton value={f.sha256} label="Copy hash" className="px-1.5 py-0.5 text-[11px]" />
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 gap-2 pl-[3.25rem] sm:pl-0">
                {f.has_preview ? (
                  <Button size="sm" variant="ghost" className="h-9 sm:h-8" icon={<Eye className="h-4 w-4" aria-hidden />} onClick={() => setPreviewing(f)} aria-label={`Preview ${f.filename}`}>
                    <span className="hidden sm:inline">Preview</span>
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="secondary"
                  className="h-9 sm:h-8"
                  disabled={blocked}
                  loading={downloading === f.id}
                  icon={<Download className="h-4 w-4" aria-hidden />}
                  onClick={() => download(f)}
                  aria-label={`Download ${f.filename}`}
                >
                  <span className="hidden sm:inline">Download</span>
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {previewing ? (
        <PreviewDialog datasetSlug={dataset.slug} file={previewing} open onOpenChange={(o) => !o && setPreviewing(null)} />
      ) : null}
    </>
  );
}

function assetIcon(kind: string) {
  if (kind === "github") return <FolderGit2 aria-hidden />;
  if (kind === "colab" || kind === "kaggle" || kind === "notebook") return <NotebookPen aria-hidden />;
  if (kind === "docs") return <BookOpen aria-hidden />;
  return <FileCode2 aria-hidden />;
}

/** Starter notebooks, repositories and docs the organizers linked — external, opened in a new tab. */
function StarterAssets({ comp }: { comp: CompetitionDetail }) {
  if (!comp.starter_assets.length) return null;
  return (
    <Block
      id="starter"
      eyebrow="Start faster"
      title="Starter assets"
      icon={<NotebookPen />}
      description={comp.starter_asset_version > 1 ? `Resources updated (revision ${comp.starter_asset_version}).` : "Notebooks and repositories shared by the organizers."}
    >
      <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
        {comp.starter_assets.map((a, i) => (
          <li key={`${a.url}-${i}`}>
            <a
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 px-4 py-3.5 transition-colors hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)] sm:px-5"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-2 text-muted transition-colors group-hover:text-accent-strong [&_svg]:h-4 [&_svg]:w-4">
                {assetIcon(a.kind)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg transition-colors group-hover:text-accent-strong">{a.label}</span>
                <span className="block truncate font-mono text-[11px] text-subtle">{a.url.replace(/^https?:\/\//, "")}</span>
              </span>
              <ExternalLink className="h-4 w-4 shrink-0 text-subtle transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          </li>
        ))}
      </ul>
    </Block>
  );
}

function SubmissionFormat({ comp, dataset }: { comp: CompetitionDetail; dataset: DatasetDetail | undefined }) {
  const ev = comp.evaluation;
  const sample = dataset?.files.find((f) => f.kind === "sample_submission");
  if (comp.scoring_mode !== "automatic" || !ev) return null;
  const example = `${ev.id_column},${ev.target_column}\n1,…\n2,…`;
  const facts: { label: string; value: ReactNode }[] = [
    { label: "Required header", value: <code className="font-mono text-xs">{ev.id_column},{ev.target_column}</code> },
    { label: "Max file size", value: <span className="tabular">{comp.max_submission_mb} MB</span> },
    { label: "Metric", value: ev.metric_label },
  ];
  return (
    <section aria-labelledby="format-heading" className="relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card border-gradient">
      <div className="px-5 pb-4 pt-5">
        <PanelLabel className="text-accent-strong"><Upload aria-hidden /> Submission format</PanelLabel>
        <h2 id="format-heading" className="mt-2 text-base font-semibold tracking-[-0.01em] text-fg">Upload a CSV with one prediction per test row.</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">{ev.submission_format}</p>
      </div>
      <dl className="grid grid-cols-1 border-y border-border">
        {facts.map((f) => (
          <div key={f.label} className="flex items-center justify-between gap-3 border-b border-border px-5 py-2.5 text-sm last:border-b-0">
            <dt className="text-muted">{f.label}</dt>
            <dd className="text-right text-fg">{f.value}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-4 px-5 py-4 text-sm">
        <pre className="overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2.5 font-mono text-xs leading-relaxed text-muted" aria-label="Example submission">
          {example}
        </pre>
        <ul className="space-y-1.5 text-xs leading-relaxed text-muted">
          <li className="flex gap-2"><span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan" />Include every ID from the test file exactly once{ev.strict_schema ? " — missing, duplicate or unknown IDs are rejected" : ""}.</li>
          <li className="flex gap-2"><span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan" />Save as UTF-8 CSV with a header row; the column order doesn&apos;t matter.</li>
          <li className="flex gap-2"><span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyan" />Validation errors are reported per row on the Submissions tab and don&apos;t count toward your daily limit.</li>
        </ul>
        {sample ? (
          <p className="text-xs text-muted">
            Start from <span className="font-mono text-fg">{sample.filename}</span> in the file list — it already has the right shape.
          </p>
        ) : null}
        {comp.viewer.is_participant ? (
          <LinkButton href={`/competitions/${comp.slug}/submissions`} variant="secondary" size="sm" icon={<Upload className="h-3.5 w-3.5" aria-hidden />} className="w-full">
            Go to submissions
          </LinkButton>
        ) : null}
      </div>
    </section>
  );
}

function TrainAnywhere() {
  return (
    <section aria-labelledby="train-heading" className="rounded-[var(--radius-lg)] border border-border bg-bg-elevated/60 px-5 py-4">
      <PanelLabel><Cpu aria-hidden /> Train anywhere</PanelLabel>
      <h2 id="train-heading" className="sr-only">Train anywhere</h2>
      <p className="mt-2 text-sm leading-relaxed text-muted">
        DataBattles scores prediction files — it doesn&apos;t run your code, so no GPU is needed here. Train in Google Colab,
        Kaggle Notebooks or on your own laptop, then upload the predictions CSV.
      </p>
    </section>
  );
}

function FileListSkeleton() {
  return (
    <div className="divide-y divide-border" role="status" aria-label="Loading files">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-5 py-4">
          <Skeleton className="h-10 w-10 rounded-xl" />
          <div className="flex-1">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="mt-2 h-3 w-64 max-w-full" />
          </div>
          <Skeleton className="hidden h-8 w-24 rounded-[var(--radius-md)] sm:block" />
        </div>
      ))}
    </div>
  );
}

export default function CompetitionDataPage() {
  const comp = useCompetition();
  const ref = comp.dataset;
  const termsRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.dataset(ref?.slug ?? "", ref?.version),
    queryFn: () => get<DatasetDetail>(`/datasets/${encodeURIComponent(ref!.slug)}`, { version: ref!.version }),
    enabled: Boolean(ref),
  });

  const focusTerms = () => {
    termsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    termsRef.current?.focus({ preventScroll: true });
  };
  // A 403 terms_required on download means our cached acceptance is stale (e.g. the terms changed): refresh it.
  const onTermsRequired = () => {
    if (ref) void qc.invalidateQueries({ queryKey: ["datasets", ref.slug] }).then(() => requestAnimationFrame(focusTerms));
    focusTerms();
  };

  if (!ref) {
    return (
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-10">
        <div className="min-w-0 space-y-10">
          <EmptyState
            icon={<Database />}
            title="No dataset attached"
            description="The organizers haven't attached a dataset to this competition yet. Check the overview and announcements for updates."
            action={<LinkButton href={`/competitions/${comp.slug}`} variant="secondary">Back to overview</LinkButton>}
          />
          <StarterAssets comp={comp} />
        </div>
        <div className="space-y-4">
          <SubmissionFormat comp={comp} dataset={undefined} />
          <TrainAnywhere />
        </div>
      </div>
    );
  }

  const dataset = query.data;
  const needsTerms = Boolean(dataset?.requires_terms && !dataset.terms_accepted && !dataset.can_manage);

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-10">
      <div className="min-w-0 space-y-10">
        <section aria-labelledby="dataset-heading" className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
          <div className="relative flex flex-col gap-4 border-b border-border px-4 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-5">
            <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-24 dot-grid opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent)]" />
            <div className="relative flex min-w-0 items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-cyan-soft text-cyan shadow-[inset_0_1px_0_var(--hairline-highlight)]">
                <Database className="h-5 w-5" aria-hidden />
              </span>
              <div className="min-w-0">
                <PanelLabel>Competition dataset</PanelLabel>
                <h2 id="dataset-heading" className="mt-1 flex flex-wrap items-center gap-2 text-lg font-semibold tracking-[-0.02em] text-fg">
                  <Link href={`/datasets/${ref.slug}`} className="transition-colors hover:text-accent-strong">{ref.title}</Link>
                  <Badge tone="outline" className="font-mono">v{ref.version}</Badge>
                  {dataset?.is_demo ? <DemoBadge /> : null}
                </h2>
                <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-subtle">
                  <span><span className="tabular">{formatNumber(ref.file_count)}</span> files</span>
                  <span aria-hidden>·</span>
                  <span className="tabular">{formatBytes(ref.total_bytes)}</span>
                  <span aria-hidden>·</span>
                  <span>{dataset?.license_name ?? ref.license}</span>
                </p>
              </div>
            </div>
            <LinkButton href={`/datasets/${ref.slug}`} size="sm" variant="ghost" className="relative self-start" icon={<ArrowUpRight className="h-4 w-4" aria-hidden />}>
              Dataset page
            </LinkButton>
          </div>
          <p className="flex items-start gap-2 border-b border-border bg-bg-elevated/50 px-4 py-2.5 text-xs text-muted sm:px-5">
            <BookMarked className="mt-px h-3.5 w-3.5 shrink-0 text-accent-strong" aria-hidden />
            <span>
              This competition uses <strong className="font-medium text-fg">version {ref.version}</strong> of the dataset. Later versions of the dataset don&apos;t affect scoring here.
            </span>
          </p>
          {query.isPending ? (
            <FileListSkeleton />
          ) : query.isError ? (
            <div className="p-4"><ErrorState error={query.error} onRetry={() => query.refetch()} /></div>
          ) : (
            <>
              {query.data.takedown_reason || needsTerms ? (
                <div className="space-y-3 px-4 pt-4 sm:px-5">
                  {query.data.takedown_reason ? (
                    <InlineNotice tone="danger" title="This dataset was taken down">{query.data.takedown_reason}</InlineNotice>
                  ) : null}
                  {needsTerms ? (
                    <InlineNotice tone="warning" title="Accept the dataset terms to download" action={<Button size="sm" variant="secondary" onClick={focusTerms}>Review terms</Button>}>
                      Previews are available now; downloads unlock after you accept.
                    </InlineNotice>
                  ) : null}
                </div>
              ) : null}
              <FileList dataset={query.data} needsTerms={needsTerms} onTermsRequired={onTermsRequired} />
            </>
          )}
        </section>

        {dataset && needsTerms ? <TermsCard dataset={dataset} termsRef={termsRef} /> : null}

        <StarterAssets comp={comp} />

        {dataset?.current_version?.data_dictionary?.length ? (
          <Block id="dictionary" eyebrow="Schema" title="Data dictionary" icon={<Table2 />} description="Column descriptions provided by the dataset owner.">
            <Table>
              <THead>
                <tr>
                  <TH>Column</TH>
                  <TH>Type</TH>
                  <TH>Description</TH>
                </tr>
              </THead>
              <TBody>
                {dataset.current_version.data_dictionary.map((d, i) => (
                  <TR key={i}>
                    <TD className="py-2.5 font-mono text-xs text-fg">{String(d.column ?? "")}</TD>
                    <TD className="py-2.5"><span className="rounded-md border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-muted">{String(d.type ?? "")}</span></TD>
                    <TD className="py-2.5 text-sm text-muted">{String(d.description ?? "")}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Block>
        ) : null}

        {dataset?.current_version?.release_notes_html ? (
          <Block
            id="release-notes"
            eyebrow="Changelog"
            title={`Release notes · v${dataset.current_version.version}`}
            icon={<FileText />}
            description={dataset.current_version.published_at ? `Published ${formatDate(dataset.current_version.published_at)}` : undefined}
          >
            <div className="rounded-[var(--radius-lg)] border border-border bg-surface px-5 py-4">
              <Prose html={dataset.current_version.release_notes_html} className="[&>:first-child]:mt-0" />
            </div>
          </Block>
        ) : null}

        {dataset && (dataset.citation || dataset.attribution) ? (
          <Block id="citation" eyebrow="Give credit" title="Citation & attribution" icon={<Quote />}>
            <div className="space-y-3 text-sm">
              {dataset.attribution ? <p className="text-muted">{dataset.attribution}</p> : null}
              {dataset.citation ? (
                <div className="flex items-start gap-2">
                  <pre className="flex-1 overflow-x-auto whitespace-pre-wrap rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2.5 font-mono text-xs leading-relaxed text-muted">{dataset.citation}</pre>
                  <CopyButton value={dataset.citation} />
                </div>
              ) : null}
            </div>
          </Block>
        ) : null}
      </div>

      <aside className="min-w-0 space-y-4" aria-label="How to submit">
        <SubmissionFormat comp={comp} dataset={dataset} />
        <TrainAnywhere />
      </aside>
    </div>
  );
}
