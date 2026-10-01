"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Ban, ClipboardList, EyeOff, RefreshCw, Star } from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ManageHeading, useCompetitionDetail } from "@/components/organizer/shared";
import { Toolbar } from "@/components/organizer/ui";
import { UserLink } from "@/components/domain/cards";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, NoResults, QueryState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, post } from "@/lib/api";
import { formatBytes, formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";
import type { Page, SubmissionOut } from "@/lib/types";

const STATUSES = ["", "queued", "validating", "scoring", "scored", "failed", "rejected", "canceled"] as const;
const PENDING = new Set(["queued", "validating", "scoring"]);

function Submissions() {
  const { slug } = useParams<{ slug: string }>();
  const initialStatus = useSearchParams().get("status") ?? "";
  const [status, setStatus] = useState<string>((STATUSES as readonly string[]).includes(initialStatus) ? initialStatus : "");
  const [page, setPage] = useState(1);
  const detail = useCompetitionDetail(slug);
  const key = ["competitions", slug, "submissions", "all", { status, page }] as const;
  const query = useQuery({
    queryKey: key,
    queryFn: () => get<Page<SubmissionOut>>(`/competitions/${slug}/submissions/all`, { status: status || undefined, page, page_size: 25 }),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some((s) => PENDING.has(s.status)) ? 10_000 : false),
  });
  const invalidate = useApiMutation(
    ({ id, reason }: { id: string; reason: string }) => post<SubmissionOut>(`/competitions/${slug}/submissions/${id}/invalidate`, { reason }),
    { success: "Submission invalidated. The team has been notified.", invalidate: [["competitions", slug, "submissions"], ["competitions", slug, "leaderboard"]] },
  );
  const direction = detail.data?.evaluation?.direction;
  const metricLabel = detail.data?.evaluation?.metric_label ?? "Score";

  return (
    <div>
      <ManageHeading
        eyebrow="Run"
        icon={<ClipboardList />}
        title="Submissions"
        description="Every submission from every team, including private-split scores."
        actions={
          <Button variant="secondary" size="sm" icon={<RefreshCw className="h-4 w-4" />} onClick={() => query.refetch()} loading={query.isFetching && !query.isPending}>
            Refresh
          </Button>
        }
      />
      <Toolbar>
        <label className="flex items-center gap-2 text-xs text-muted">
          <span className="font-mono uppercase tracking-[0.12em] text-subtle">Status</span>
          <Select className="h-9 w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? titleCase(s) : "All statuses"}</option>)}
          </Select>
        </label>
        <p className="flex items-start gap-1.5 text-xs text-subtle sm:items-center sm:text-right">
          <EyeOff className="mt-px h-3.5 w-3.5 shrink-0 sm:mt-0" aria-hidden />
          <span>
            Private scores are visible to organizers only until results are final.
            {direction ? <> · {metricLabel}: {direction === "maximize" ? "higher is better" : "lower is better"}</> : null}
          </span>
        </p>
      </Toolbar>
      <QueryState
        query={query}
        loading={<SkeletonRows rows={8} />}
        isEmpty={(d) => d.total === 0}
        empty={
          status ? (
            <NoResults onReset={() => { setStatus(""); setPage(1); }} />
          ) : (
            <EmptyState icon={<ClipboardList />} title="No submissions yet" description="Submissions appear here as soon as teams upload them, with validation errors and scores." />
          )
        }
      >
        {(d) => (
          <>
            <p className="mb-2 px-1 text-xs text-subtle" aria-live="polite">
              <span className="tabular font-medium text-muted">{formatNumber(d.total)}</span> {d.total === 1 ? "submission" : "submissions"}
              {status ? <> · {titleCase(status)}</> : null}
              {d.items.some((s) => PENDING.has(s.status)) ? <> · refreshing every 10 s while scoring</> : null}
            </p>
            <Table>
              <caption className="sr-only">Submissions</caption>
              <THead>
                <tr>
                  <TH>Submitted</TH>
                  <TH>Team</TH>
                  <TH>File</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Public</TH>
                  <TH className="text-right">Private</TH>
                  <TH className="relative"><span className="sr-only">Actions</span></TH>
                </tr>
              </THead>
              <TBody>
                {d.items.map((s) => (
                  <TR key={s.id} className={s.invalidated ? "opacity-70" : undefined}>
                    <TD className="whitespace-nowrap align-top">
                      <span className="tabular text-fg" title={formatDateTime(s.submitted_at)}>{relativeTime(s.submitted_at)}</span>
                      <span className="block font-mono text-[11px] text-subtle">{s.sha256_prefix}</span>
                    </TD>
                    <TD className="align-top">
                      <p className="font-medium text-fg">{s.team_name ?? "—"}</p>
                      {s.submitter ? <UserLink user={s.submitter} size={18} className="text-xs text-muted" /> : null}
                    </TD>
                    <TD className="max-w-56 align-top">
                      <p className="truncate font-mono text-xs text-fg" title={s.filename}>{s.filename}</p>
                      <p className="tabular text-xs text-subtle">
                        {formatBytes(s.size_bytes)}
                        {s.row_count !== null ? ` · ${formatNumber(s.row_count)} rows` : ""}
                        {s.config_version ? ` · rules v${s.config_version}` : ""}
                      </p>
                      {s.description ? <p className="mt-0.5 line-clamp-2 text-xs text-muted">{s.description}</p> : null}
                    </TD>
                    <TD className="align-top">
                      <div className="flex flex-wrap items-center gap-1">
                        <StatusBadge status={s.status} />
                        {s.is_final_selected ? <Badge tone="accent" icon={<Star className="h-3 w-3" aria-hidden />}>Final</Badge> : null}
                        {s.invalidated ? <Badge tone="danger" title={s.invalidation_reason ?? undefined}>Invalidated</Badge> : null}
                      </div>
                      {s.error_message ? (
                        <p className="mt-1 max-w-64 text-xs text-danger">
                          {s.error_code ? <span className="font-mono">{s.error_code}: </span> : null}
                          {s.error_message}
                        </p>
                      ) : null}
                      {s.invalidated && s.invalidation_reason ? <p className="mt-1 max-w-64 text-xs text-muted">Reason: {s.invalidation_reason}</p> : null}
                    </TD>
                    <TD className="tabular text-right align-top font-mono text-xs text-fg">
                      {formatScore(s.public_score)}
                      {Object.entries(s.secondary_scores ?? {}).map(([k, v]) => (
                        <span key={k} className="block text-[10px] text-subtle">{k}: {formatScore((v as { public?: number | null }).public ?? null, 4)}</span>
                      ))}
                    </TD>
                    <TD className="tabular text-right align-top font-mono text-xs text-fg">
                      {formatScore(s.private_score)}
                      {Object.entries(s.secondary_scores ?? {}).map(([k, v]) => (
                        <span key={k} className="block text-[10px] text-subtle">{k}: {formatScore((v as { private?: number | null }).private ?? null, 4)}</span>
                      ))}
                    </TD>
                    <TD className="text-right align-top">
                      {!s.invalidated ? (
                        <ConfirmDialog
                          trigger={<Button size="sm" variant="ghost" icon={<Ban className="h-4 w-4" />} aria-label={`Invalidate submission ${s.filename} from ${s.team_name ?? "team"}`}>Invalidate</Button>}
                          title="Invalidate this submission?"
                          description={`It will be excluded from the leaderboard and final results, and the team (${s.team_name ?? "unknown"}) will be notified with your reason. This can't be undone.`}
                          confirmLabel="Invalidate"
                          requireReason
                          reasonLabel="Reason (sent to the team and recorded in the audit log)"
                          onConfirm={(reason) => invalidate.mutateAsync({ id: s.id, reason }).catch(() => undefined)}
                        />
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <Pagination page={d.page} pageSize={d.page_size} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </div>
  );
}

export default function ManageSubmissionsPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <Submissions />
    </Suspense>
  );
}
