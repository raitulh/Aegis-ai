"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Database, Download, ExternalLink, Eye, FileSpreadsheet, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { useCompetition } from "@/components/competition/context";
import { FILE_KIND_LABELS } from "@/components/competition/labels";
import type { DatasetFile, TablePreview } from "@/components/competition/types";
import { Badge, DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/form";
import { Prose } from "@/components/ui/markdown";
import { CopyButton, KeyValue } from "@/components/ui/misc";
import { EmptyState, ErrorState, InlineNotice, SignInPrompt, SkeletonRows, Spinner } from "@/components/ui/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
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
            <tr key={r}>
              {preview.columns.map((_, c) => (
                <td key={c} className="whitespace-nowrap px-3 py-1.5 text-fg">{row[c] ?? ""}</td>
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
            Showing {formatNumber(preview.rows.length)} rows
            {query.data?.row_count !== null && query.data?.row_count !== undefined ? ` of ${formatNumber(query.data.row_count)}` : ""}
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
    <div ref={termsRef} tabIndex={-1} className="scroll-mt-24 outline-none">
      <Card className="border-warning/40">
        <CardHeader title="Dataset terms of use" description="The data owner requires you to accept these terms before downloading." />
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

function FilesTable({ dataset, needsTerms, onTermsRequired }: { dataset: DatasetDetail; needsTerms: boolean; onTermsRequired: () => void }) {
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
    return <EmptyState title="No files in this version" description="The dataset owner hasn't uploaded files to this version yet." />;
  }
  return (
    <>
      <Table>
        <caption className="sr-only">Files in {dataset.title} version {dataset.current_version?.version}</caption>
        <THead>
          <tr>
            <TH>File</TH>
            <TH className="hidden sm:table-cell">Type</TH>
            <TH className="text-right">Size</TH>
            <TH className="hidden text-right md:table-cell">Rows</TH>
            <TH className="text-right"><span className="sr-only">Actions</span></TH>
          </tr>
        </THead>
        <TBody>
          {dataset.files.map((f) => {
            const blocked = f.scan_status === "infected";
            const scanning = f.scan_status === "pending";
            return (
              <TR key={f.id}>
                <TD>
                  <div className="flex items-center gap-2">
                    <FileSpreadsheet className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                    <span className="break-all font-mono text-[13px] text-fg">{f.filename}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-subtle">
                    <span className="font-mono" title={`SHA-256 ${f.sha256}`}>sha256 {f.sha256.slice(0, 12)}…</span>
                    <CopyButton value={f.sha256} label="Copy hash" className="px-1.5 py-0.5 text-[11px]" />
                    {scanning ? <Badge tone="info">Scanning</Badge> : null}
                    {blocked ? <Badge tone="danger">Blocked</Badge> : null}
                  </div>
                </TD>
                <TD className="hidden sm:table-cell">
                  <Badge tone={f.kind === "sample_submission" ? "accent" : "outline"}>{FILE_KIND_LABELS[f.kind] ?? f.kind}</Badge>
                </TD>
                <TD className="text-right tabular-nums text-muted">{formatBytes(f.size_bytes)}</TD>
                <TD className="hidden text-right tabular-nums text-muted md:table-cell">{formatNumber(f.row_count)}</TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1.5">
                    {f.has_preview ? (
                      <Button size="sm" variant="ghost" icon={<Eye className="h-4 w-4" aria-hidden />} onClick={() => setPreviewing(f)} aria-label={`Preview ${f.filename}`}>
                        <span className="hidden lg:inline">Preview</span>
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={blocked}
                      loading={downloading === f.id}
                      icon={<Download className="h-4 w-4" aria-hidden />}
                      onClick={() => download(f)}
                      aria-label={`Download ${f.filename}`}
                    >
                      <span className="hidden lg:inline">Download</span>
                    </Button>
                  </div>
                </TD>
              </TR>
            );
          })}
        </TBody>
      </Table>
      {previewing ? (
        <PreviewDialog datasetSlug={dataset.slug} file={previewing} open onOpenChange={(o) => !o && setPreviewing(null)} />
      ) : null}
    </>
  );
}

function SubmissionFormat({ comp, dataset }: { comp: CompetitionDetail; dataset: DatasetDetail | undefined }) {
  const ev = comp.evaluation;
  const sample = dataset?.files.find((f) => f.kind === "sample_submission");
  if (comp.scoring_mode !== "automatic" || !ev) return null;
  const example = `${ev.id_column},${ev.target_column}\n1,…\n2,…`;
  return (
    <Card>
      <CardHeader title="Submission format" description="Upload a CSV with one prediction per test row." />
      <CardBody className="space-y-4 text-sm">
        <KeyValue
          items={[
            { label: "Required header", value: <code className="font-mono text-xs">{ev.id_column},{ev.target_column}</code> },
            { label: "Format", value: ev.submission_format },
            { label: "Max file size", value: `${comp.max_submission_mb} MB` },
            { label: "Metric", value: ev.metric_label },
          ]}
        />
        <pre className="overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2 font-mono text-xs text-muted" aria-label="Example submission">
          {example}
        </pre>
        <ul className="list-disc space-y-1 pl-5 text-muted">
          <li>Include every ID from the test file exactly once{ev.strict_schema ? " — missing, duplicate or unknown IDs are rejected" : ""}.</li>
          <li>Save as UTF-8 CSV with a header row; the column order doesn&apos;t matter.</li>
          <li>Validation errors are reported per row on the Submissions tab and don&apos;t count toward your daily limit.</li>
        </ul>
        {sample ? (
          <p className="text-muted">
            Start from <span className="font-mono text-fg">{sample.filename}</span> in the file list above — it already has the right shape.
          </p>
        ) : null}
        {comp.viewer.is_participant ? (
          <LinkButton href={`/competitions/${comp.slug}/submissions`} variant="secondary" size="sm">Go to submissions</LinkButton>
        ) : null}
      </CardBody>
    </Card>
  );
}

function TrainAnywhere({ comp }: { comp: CompetitionDetail }) {
  return (
    <Card>
      <CardHeader title="Train anywhere" />
      <CardBody className="space-y-3 text-sm text-muted">
        <p className="flex items-start gap-2">
          <Cpu className="mt-0.5 h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
          <span>
            DataBattles scores prediction files — it doesn&apos;t run your code, so no GPU is needed here. Train in Google Colab,
            Kaggle Notebooks or on your own laptop, then upload the predictions CSV.
          </span>
        </p>
        {comp.starter_assets.length ? (
          <ul className="space-y-2">
            {comp.starter_assets.map((a, i) => (
              <li key={`${a.url}-${i}`}>
                <a href={a.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-accent-strong hover:underline">
                  {a.label} <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              </li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
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
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <EmptyState
          icon={<Database className="h-5 w-5" />}
          title="No dataset attached"
          description="The organizers haven't attached a dataset to this competition yet. Check the overview and announcements for updates."
        />
        <div className="space-y-4">
          <SubmissionFormat comp={comp} dataset={undefined} />
          <TrainAnywhere comp={comp} />
        </div>
      </div>
    );
  }

  const dataset = query.data;
  const needsTerms = Boolean(dataset?.requires_terms && !dataset.terms_accepted && !dataset.can_manage);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-6">
        <Card>
          <CardHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                <Database className="h-4 w-4 text-accent-strong" aria-hidden />
                <Link href={`/datasets/${ref.slug}`} className="hover:text-accent-strong">{ref.title}</Link>
                <Badge tone="outline">v{ref.version}</Badge>
                {dataset?.is_demo ? <DemoBadge /> : null}
              </span>
            }
            description={`${formatNumber(ref.file_count)} files · ${formatBytes(ref.total_bytes)} · ${dataset?.license_name ?? ref.license}`}
            action={<LinkButton href={`/datasets/${ref.slug}`} size="sm" variant="ghost">Dataset page</LinkButton>}
          />
          <CardBody className="space-y-4">
            <p className="text-sm text-muted">
              This competition uses <strong className="text-fg">version {ref.version}</strong> of the dataset. Later versions of the dataset don&apos;t affect scoring here.
            </p>
            {query.isPending ? (
              <SkeletonRows rows={3} />
            ) : query.isError ? (
              <ErrorState error={query.error} onRetry={() => query.refetch()} />
            ) : (
              <>
                {query.data.takedown_reason ? (
                  <InlineNotice tone="danger" title="This dataset was taken down">{query.data.takedown_reason}</InlineNotice>
                ) : null}
                {needsTerms ? (
                  <InlineNotice tone="warning" title="Accept the dataset terms to download" action={<Button size="sm" variant="secondary" onClick={focusTerms}>Review terms</Button>}>
                    Previews are available now; downloads unlock after you accept.
                  </InlineNotice>
                ) : null}
                <FilesTable dataset={query.data} needsTerms={needsTerms} onTermsRequired={onTermsRequired} />
              </>
            )}
          </CardBody>
        </Card>

        {dataset && needsTerms ? <TermsCard dataset={dataset} termsRef={termsRef} /> : null}

        {dataset?.current_version?.data_dictionary?.length ? (
          <Card>
            <CardHeader title="Data dictionary" description="Column descriptions provided by the dataset owner." />
            <CardBody>
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
                      <TD className="font-mono text-xs text-fg">{String(d.column ?? "")}</TD>
                      <TD className="font-mono text-xs text-muted">{String(d.type ?? "")}</TD>
                      <TD className="text-sm text-muted">{String(d.description ?? "")}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardBody>
          </Card>
        ) : null}

        {dataset?.current_version?.release_notes_html ? (
          <Card>
            <CardHeader
              title={`Release notes · v${dataset.current_version.version}`}
              description={dataset.current_version.published_at ? `Published ${formatDate(dataset.current_version.published_at)}` : undefined}
            />
            <CardBody><Prose html={dataset.current_version.release_notes_html} /></CardBody>
          </Card>
        ) : null}

        {dataset && (dataset.citation || dataset.attribution) ? (
          <Card>
            <CardHeader title="Citation & attribution" />
            <CardBody className="space-y-3 text-sm">
              {dataset.attribution ? <p className="text-muted">{dataset.attribution}</p> : null}
              {dataset.citation ? (
                <div className="flex items-start gap-2">
                  <pre className="flex-1 overflow-x-auto whitespace-pre-wrap rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2 font-mono text-xs text-muted">{dataset.citation}</pre>
                  <CopyButton value={dataset.citation} />
                </div>
              ) : null}
            </CardBody>
          </Card>
        ) : null}
      </div>

      <div className="space-y-4">
        <SubmissionFormat comp={comp} dataset={dataset} />
        <TrainAnywhere comp={comp} />
      </div>
    </div>
  );
}
