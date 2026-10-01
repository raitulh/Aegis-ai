"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Database, Eye, EyeOff, FileSpreadsheet, FlaskConical, Gauge, Link2, Lock, Rows3, Search, ShieldAlert, Upload } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ManageHeading, competitionKeys, isTerminal, useCompetitionDetail, useManage } from "@/components/organizer/shared";
import { FactGrid, InlineEmpty, Panel, SubHeading, Tile, TileGrid } from "@/components/organizer/ui";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { ApiError, api, errorMessage, get, upload } from "@/lib/api";
import { formatBytes, formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useApiMutation, useConfig } from "@/lib/hooks";
import type { CompetitionDetail, DatasetDetail, Schemas } from "@/lib/types";

type EvaluationAsset = Schemas["EvaluationAssetOut"];

function GroundTruthCard({ slug, disabledReason, hasAsset, maxMb }: { slug: string; disabledReason: string | null; hasAsset: boolean; maxMb: number }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EvaluationAsset | null>(null);
  const [busy, setBusy] = useState(false);
  const [pickerKey, setPickerKey] = useState(0);

  async function send() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setProgress(0);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await upload<EvaluationAsset>(`/competitions/${slug}/ground-truth`, form, { onProgress: setProgress });
      setResult(res);
      setFile(null);
      setPickerKey((k) => k + 1);
      toast.success(`Ground truth validated: ${formatNumber(res.row_count)} rows.`);
      await Promise.all(competitionKeys(slug).map((key) => qc.invalidateQueries({ queryKey: key })));
    } catch (e) {
      setError(e instanceof ApiError && e.code === "scoring_locked" ? "Ground truth is locked because submissions have already been scored." : errorMessage(e, "Upload failed."));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const uploadButton = <Button icon={<Upload className="h-4 w-4" />} loading={busy} disabled={!file || !!disabledReason}>{hasAsset ? "Replace ground truth" : "Upload and validate"}</Button>;

  return (
    <div className="space-y-3">
      {disabledReason ? <InlineNotice tone="info">{disabledReason}</InlineNotice> : null}
      <FileDrop
        key={pickerKey}
        accept=".csv"
        maxBytes={maxMb * 1024 * 1024}
        disabled={!!disabledReason || busy}
        onFile={(f) => {
          setFile(f);
          setError(null);
          setResult(null);
        }}
        hint={`CSV only, up to ${maxMb} MB. The file is validated against your metric configuration before it is accepted.`}
        progress={progress}
      />
      {file ? <p className="text-xs text-muted">Selected: <span className="font-mono">{file.name}</span> ({formatBytes(file.size)})</p> : null}
      <FormError message={error} />
      {result ? (
        <InlineNotice tone="success" title="Accepted">
          {formatNumber(result.row_count)} rows — {formatNumber(result.public_count)} public, {formatNumber(result.private_count)} private. Fingerprint{" "}
          <span className="font-mono">{result.sha256_prefix}</span>.
        </InlineNotice>
      ) : null}
      <div className="flex justify-end">
        {hasAsset ? (
          <ConfirmDialog
            trigger={uploadButton}
            title="Replace the ground truth?"
            description="The new file becomes the active solution immediately. The previous file is kept for audit but no longer used. A new configuration version is recorded if the competition is published."
            confirmLabel="Replace"
            onConfirm={() => send()}
          />
        ) : (
          <Button icon={<Upload className="h-4 w-4" />} loading={busy} disabled={!file || !!disabledReason} onClick={send}>
            Upload and validate
          </Button>
        )}
      </div>
    </div>
  );
}

function TrainingDataCard({ slug, comp, locked }: { slug: string; comp: CompetitionDetail; locked: boolean }) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<DatasetDetail | null>(null);
  const [findError, setFindError] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);
  const [versionId, setVersionId] = useState("");

  const attach = useApiMutation(
    (dataset_version_id: string | null) => api<CompetitionDetail>(`/competitions/${slug}`, { method: "PATCH", body: { dataset_version_id, change_reason: dataset_version_id ? "Attached training dataset" : "Detached training dataset" } }),
    {
      success: (c) => (c.dataset ? `Attached ${c.dataset.title} v${c.dataset.version}.` : "Dataset detached."),
      invalidate: competitionKeys(slug),
      onSuccess: () => {
        setFound(null);
        setQuery("");
        setVersionId("");
      },
    },
  );

  async function find(e: React.FormEvent) {
    e.preventDefault();
    const s = query.trim().replace(/^.*\/datasets\//, "").replace(/\/.*$/, "");
    if (!s) return;
    setFinding(true);
    setFindError(null);
    setFound(null);
    try {
      const d = await get<DatasetDetail>(`/datasets/${encodeURIComponent(s)}`);
      setFound(d);
      const published = d.versions.filter((v) => v.status === "published").sort((a, b) => b.version - a.version);
      setVersionId(published[0]?.id ?? "");
      if (!published.length) setFindError("This dataset has no published version yet.");
    } catch (err) {
      setFindError(err instanceof ApiError && err.status === 404 ? "No dataset with that slug (or you can't see it)." : errorMessage(err));
    } finally {
      setFinding(false);
    }
  }

  const published = found?.versions.filter((v) => v.status === "published").sort((a, b) => b.version - a.version) ?? [];

  return (
    <Panel icon={<Database />} title="Training data" description="Attach a published dataset version participants can download. Recommended before publishing.">
      <div className="space-y-4">
        {comp.dataset ? (
          <div className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated/50 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-strong"><Database className="h-4 w-4" aria-hidden /></span>
              <div className="min-w-0">
                <Link href={`/datasets/${comp.dataset.slug}`} className="font-medium text-fg hover:text-accent-strong">{comp.dataset.title}</Link>
                <p className="tabular text-xs text-subtle">Version {comp.dataset.version} · {comp.dataset.file_count} files · {formatBytes(comp.dataset.total_bytes)} · {comp.dataset.license}</p>
              </div>
            </div>
            {!locked ? (
              <ConfirmDialog
                trigger={<Button size="sm" variant="ghost">Detach</Button>}
                title="Detach this dataset?"
                description="Participants will no longer see the dataset on the competition's Data tab."
                confirmLabel="Detach"
                onConfirm={() => attach.mutateAsync(null).catch(() => undefined)}
              />
            ) : null}
          </div>
        ) : (
          <InlineEmpty icon={<Database />} title="No dataset attached" description="Find a published dataset by its slug to attach it." className="py-6" />
        )}
        {!locked ? (
          <>
            <form onSubmit={find} className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <Field label="Dataset slug or URL" className="flex-1" hint="Find a dataset you can access, e.g. “housing-prices”.">
                {(p) => <Input {...p} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="dataset-slug" />}
              </Field>
              <Button type="submit" variant="secondary" icon={<Search className="h-4 w-4" />} loading={finding} className="sm:mb-5">Find</Button>
            </form>
            <FormError message={findError} />
            {found && published.length ? (
              <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-border bg-bg-elevated/50 p-3 sm:flex-row sm:items-end">
                <Field label={`Version of “${found.title}”`} className="flex-1">
                  {(p) => (
                    <Select {...p} value={versionId} onChange={(e) => setVersionId(e.target.value)}>
                      {published.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version} · {v.file_count} files · {formatBytes(v.total_bytes)}{v.published_at ? ` · ${formatDateTime(v.published_at)}` : ""}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Button icon={<Link2 className="h-4 w-4" />} loading={attach.isPending} disabled={!versionId} onClick={() => attach.mutate(versionId)}>
                  Attach
                </Button>
              </div>
            ) : null}
          </>
        ) : (
          <p className="flex items-center gap-2 text-xs text-muted"><Lock className="h-3.5 w-3.5" aria-hidden /> The dataset can't change after results are final.</p>
        )}
      </div>
    </Panel>
  );
}

export default function ManageEvaluationPage() {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const detail = useCompetitionDetail(slug);
  const config = useConfig();

  if (manage.isPending || detail.isPending) return <SkeletonRows rows={6} />;
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  const m = manage.data;
  const c = detail.data;
  const scoringMode = String(m.raw.scoring_mode ?? "");
  if (scoringMode !== "automatic") {
    return (
      <div>
        <ManageHeading eyebrow="Configure" icon={<FlaskConical />} title="Evaluation" />
        <EmptyState
          icon={<FileSpreadsheet />}
          title="This competition isn't scored automatically"
          description="Ground truth and metrics apply only to automatic scoring. Change the scoring mode in Settings if you want CSV predictions scored against a hidden solution."
          action={<LinkButton href={`/competitions/${slug}/manage/settings`} variant="secondary">Open settings</LinkButton>}
        />
      </div>
    );
  }

  const ev = m.evaluation as Record<string, unknown>;
  const metricKey = typeof ev.metric === "string" ? ev.metric : null;
  const metric = config.data?.metrics.find((x) => x.key === metricKey);
  const idCol = typeof ev.id_column === "string" ? ev.id_column : "id";
  const targetCol = typeof ev.target_column === "string" ? ev.target_column : "target";
  const positive = typeof ev.positive_label === "string" ? ev.positive_label : "1";
  const secondary = Array.isArray(ev.secondary_metrics) ? (ev.secondary_metrics as string[]) : [];
  const evaluator = config.data?.evaluators.find((x) => x.key === (ev.evaluator ?? "csv_prediction"));
  const asset = m.evaluation_asset;
  const finalized = isTerminal(m.lifecycle);
  const locked = Boolean(m.evaluation_locked_at);
  const maxMb = config.data?.limits.dataset_file_mb ?? 100;

  const disabledReason = finalized
    ? "Results are final; the ground truth can no longer change."
    : locked
      ? "Ground truth is locked because submissions have already been scored."
      : !metricKey
        ? "Choose a metric and the id/target columns in Settings → Scoring first — the file is validated against them."
        : null;

  const sampleTarget = metric?.kind === "numeric" ? ["3.42", "0.17", "12.9", "7.05"] : metric?.kind === "probability" ? [positive, "0", positive, "0"] : ["cat", "dog", "dog", "cat"];

  return (
    <div className="space-y-6">
      <ManageHeading
        eyebrow="Configure"
        icon={<FlaskConical />}
        title="Evaluation"
        description="How submissions are scored, and the hidden solution file they are scored against."
        actions={!locked && !finalized ? <LinkButton href={`/competitions/${slug}/manage/settings`} variant="secondary" size="sm">Edit metric settings</LinkButton> : undefined}
      />

      {locked ? (
        <InlineNotice tone="info" title="Scoring configuration locked">
          Submissions were first scored {relativeTime(m.evaluation_locked_at)}. The metric, columns and ground truth are frozen so every submission is scored identically.
        </InlineNotice>
      ) : null}

      <Panel icon={<Gauge />} title="Metric configuration" action={locked ? <Badge tone="outline" icon={<Lock className="h-3 w-3" aria-hidden />}>Locked</Badge> : undefined}>
        {metricKey ? (
          <FactGrid
            items={[
              { label: "Evaluator", value: evaluator ? `${evaluator.label} (v${evaluator.version})` : String(ev.evaluator ?? "csv_prediction") },
              {
                label: "Primary metric",
                value: (
                  <span className="inline-flex flex-wrap items-center gap-2">
                    <span className="font-medium">{metric?.label ?? metricKey}</span>
                    {metric ? <Badge tone="outline">{metric.direction === "maximize" ? "Higher is better ↑" : "Lower is better ↓"}</Badge> : null}
                  </span>
                ),
              },
              { label: "Metric definition", value: <span className="text-muted">{metric?.description ?? "—"}</span> },
              { label: "Secondary metrics", value: secondary.length ? secondary.map((k) => config.data?.metrics.find((x) => x.key === k)?.label ?? k).join(", ") : "None" },
              { label: "Id column", value: <code className="font-mono">{idCol}</code> },
              { label: "Target column", value: <code className="font-mono">{targetCol}</code> },
              { label: "Strict schema", value: ev.strict_schema === false ? "Off — extra columns are ignored" : "On — extra columns are rejected" },
              ...(metric?.kind === "probability" ? [{ label: "Positive label", value: <code className="font-mono">{positive}</code> }] : []),
              ...(ev.expected_row_count ? [{ label: "Expected rows", value: <span className="tabular">{formatNumber(Number(ev.expected_row_count))}</span> }] : []),
            ]}
          />
        ) : (
          <InlineNotice tone="warning" title="No metric configured" action={<LinkButton href={`/competitions/${slug}/manage/settings`} size="sm" variant="secondary">Configure</LinkButton>}>
            Choose the metric and columns in Settings → Scoring. This is required before publishing.
          </InlineNotice>
        )}
      </Panel>

      <Panel icon={<EyeOff />} title="Hidden ground truth" description="The solution file used to score every submission.">
        <div className="space-y-5">
          <div className="flex items-start gap-3 rounded-[var(--radius-md)] border border-warning/30 bg-warning-soft p-3 text-sm">
            <EyeOff className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
            <p className="text-fg/90">
              This file is stored in private storage and is <strong>never shown or downloadable</strong> by participants — only the resulting scores are.
              Private-split scores stay hidden until results are finalized.
            </p>
          </div>
          {asset ? (
            <TileGrid cols={4}>
              <Tile label="Rows" value={formatNumber(asset.row_count)} icon={<Rows3 />} />
              <Tile label="Public split" value={formatNumber(asset.public_count)} icon={<Eye />} accent="cyan" />
              <Tile label="Private split" value={formatNumber(asset.private_count)} icon={<EyeOff />} accent="warning" />
              <Tile
                label="Uploaded"
                icon={<Upload />}
                accent="muted"
                value={<span className="text-base font-medium tracking-normal" title={formatDateTime(asset.created_at)}>{relativeTime(asset.created_at)}</span>}
                hint={<span className="font-mono text-[11px]">sha256 {asset.sha256_prefix}…</span>}
              />
            </TileGrid>
          ) : (
            <InlineNotice tone="warning" title="No ground truth uploaded">Upload the solution file — it is required before publishing.</InlineNotice>
          )}
          {asset && asset.private_count === 0 ? (
            <InlineNotice tone="warning" title="No private rows">
              Every row is public, so the final ranking will equal the public leaderboard and can be overfit. Add a <code className="font-mono">Usage</code> column marking some rows as <code className="font-mono">Private</code>.
            </InlineNotice>
          ) : null}
          <GroundTruthCard slug={slug} disabledReason={disabledReason} hasAsset={Boolean(asset)} maxMb={maxMb} />
        </div>
      </Panel>

      <Panel icon={<FileSpreadsheet />} title="Required CSV format" description="Ground truth and participant submissions share the same id and target columns.">
        <div className="space-y-4 text-sm">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="min-w-0">
              <SubHeading className="mb-1.5">Ground truth (hidden)</SubHeading>
              <pre className="overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-3 font-mono text-xs leading-relaxed text-fg">
{`${idCol},${targetCol},Usage
1001,${sampleTarget[0]},Public
1002,${sampleTarget[1]},Private
1003,${sampleTarget[2]},Public
1004,${sampleTarget[3]},Private`}
              </pre>
            </div>
            <div className="min-w-0">
              <SubHeading className="mb-1.5">What participants upload</SubHeading>
              <pre className="overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-3 font-mono text-xs leading-relaxed text-fg">
{`${idCol},${targetCol}
1001,${metric?.kind === "probability" ? "0.83" : sampleTarget[0]}
1002,${metric?.kind === "probability" ? "0.12" : sampleTarget[1]}
…`}
              </pre>
              <p className="mt-1.5 text-xs text-muted">
                One row per test id{ev.strict_schema === false ? "; extra columns are ignored" : ", no extra columns"}.{evaluator ? ` ${evaluator.submission_format}` : ""}
              </p>
            </div>
          </div>
          <ul className="list-disc space-y-1.5 pl-5 text-muted marker:text-subtle">
            <li><code className="font-mono text-fg">{idCol}</code> — unique row id. Duplicate ids are rejected.</li>
            <li>
              <code className="font-mono text-fg">{targetCol}</code> —{" "}
              {metric?.kind === "numeric"
                ? "a finite number for every row."
                : metric?.kind === "probability"
                  ? <>the true label; rows equal to <code className="font-mono text-fg">{positive}</code> count as the positive class. Participants submit probabilities between 0 and 1.</>
                  : "the true label for every row (compared exactly with predictions)."}
            </li>
            <li>
              <code className="font-mono text-fg">Usage</code> (optional, case-insensitive) — <code className="font-mono">Public</code> rows drive the live leaderboard;{" "}
              <code className="font-mono">Private</code> rows are used only for final results. Without this column every row is public.
            </li>
            <li>UTF-8 CSV with a header row; every row must have the same number of columns.</li>
          </ul>
          <p className="flex items-center gap-2 text-xs text-subtle"><ShieldAlert className="h-3.5 w-3.5" aria-hidden /> Never include the private ground truth in the public dataset.</p>
        </div>
      </Panel>

      <TrainingDataCard slug={slug} comp={c} locked={finalized} />
    </div>
  );
}
