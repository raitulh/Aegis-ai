"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { Award, BadgeCheck, ExternalLink, FileCheck2, History, Lock, ShieldX, Trophy } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CheckRow, DownloadLink, ManageHeading, useCompetitionDetail, useManage } from "@/components/organizer/shared";
import { Badge, DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Textarea } from "@/components/ui/form";
import { EmptyState, ErrorState, InlineNotice, QueryState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, post } from "@/lib/api";
import { formatDateTime, formatNumber, formatScore, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useMe, useNow } from "@/lib/hooks";
import type { CertificateOut, CompetitionDetail, LeaderboardRow, Page, Schemas, SubmissionOut } from "@/lib/types";

type Snapshot = Schemas["SnapshotOut"];
type Eligibility = Schemas["EligibilityRow"];

const previewKey = (slug: string) => ["competitions", slug, "leaderboard", "private-preview"] as const;
const historyKey = (slug: string) => ["competitions", slug, "results-history"] as const;
const certPreviewKey = (slug: string) => ["competitions", slug, "certificates", "preview"] as const;
const certListKey = (slug: string) => ["competitions", slug, "certificates", "issued"] as const;

function RankingTable({ rows, judged, direction }: { rows: LeaderboardRow[]; judged: boolean; direction?: string }) {
  return (
    <>
    <Table>
      <caption className="sr-only">Ranking preview</caption>
      <THead>
        <tr>
          <TH className="w-16">Rank</TH>
          <TH>Team</TH>
          <TH className="text-right">{judged ? "Judged score (0–100)" : "Final (private) score"}</TH>
          {judged ? <TH className="text-right">Judges</TH> : <TH className="text-right">Public score</TH>}
          <TH>Result</TH>
        </tr>
      </THead>
      <TBody>
        {rows.map((r) => (
          <TR key={r.team_id}>
            <TD className="font-semibold tabular-nums">{r.rank ?? "—"}</TD>
            <TD>
              <p className="font-medium text-fg">{r.team_name ?? "Team"}</p>
              <p className="text-xs text-subtle">{r.members.map((m) => `${m.display_name} (@${m.handle})`).join(", ")}</p>
            </TD>
            <TD className="text-right font-mono text-xs tabular-nums">{formatScore(r.score, judged ? 2 : 5)}</TD>
            {judged ? (
              <TD className="text-right tabular-nums">{r.judge_count ?? "—"}</TD>
            ) : (
              <TD className="text-right font-mono text-xs tabular-nums">{formatScore(r.public_score)}</TD>
            )}
            <TD>{r.label ? <Badge tone={r.label === "Winner" ? "accent" : r.label === "Participant" ? "neutral" : "info"}>{r.label}</Badge> : null}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
    {direction ? <p className="mt-2 text-xs text-subtle">{direction === "maximize" ? "Higher scores rank first." : "Lower scores rank first."}</p> : null}
    </>
  );
}

function FinalizeCard({ slug, c }: { slug: string; c: CompetitionDetail }) {
  const me = useMe();
  const now = useNow(30_000);
  const isAdmin = hasRole(me.data, "platform_admin");
  const automatic = c.scoring_mode === "automatic";
  const pendingQueries = useQueries({
    queries: (["queued", "validating", "scoring"] as const).map((status) => ({
      queryKey: ["competitions", slug, "submissions", "all", { status, page: 1, count: true }],
      queryFn: () => get<Page<SubmissionOut>>(`/competitions/${slug}/submissions/all`, { status, page: 1, page_size: 1 }),
      enabled: automatic && c.status !== "completed" && c.status !== "draft",
      refetchInterval: 15_000,
    })),
  });
  const pending = pendingQueries.reduce((n, q) => n + (q.data?.total ?? 0), 0);
  const ended = !c.ends_at || new Date(c.ends_at).getTime() <= now.getTime();
  const finalize = useApiMutation(() => post<CompetitionDetail>(`/competitions/${slug}/finalize`), {
    success: "Results finalized and published to participants.",
    invalidate: [["competitions", slug], ["competitions", "organizing"]],
  });

  if (c.status === "draft") {
    return <InlineNotice tone="info" title="Not published yet">Publish the competition and let it run before finalizing results.</InlineNotice>;
  }
  if (c.finalized_at) {
    return (
      <InlineNotice
        tone="success"
        title={`Results finalized ${relativeTime(c.finalized_at)}`}
        action={<LinkButton href={`/competitions/${slug}/leaderboard`} size="sm" variant="secondary">View public results</LinkButton>}
      >
        Final ranking snapshot published on {formatDateTime(c.finalized_at)}. Use a correction below if something must change.
      </InlineNotice>
    );
  }
  const canFinalize = c.status !== "archived" && (ended || isAdmin) && pending === 0;
  return (
    <Card>
      <CardHeader
        title="Finalize results"
        description="Freezes a versioned snapshot of the final ranking and publishes it."
        action={
          <ConfirmDialog
            trigger={<Button icon={<Lock className="h-4 w-4" />} disabled={!canFinalize} loading={finalize.isPending}>Finalize results</Button>}
            title="Finalize results? This can't be undone."
            description={
              automatic
                ? "The final ranking is computed from each team's selected submissions on the private split and frozen as snapshot v1. Participants are notified, results and badges are published, and scoring configuration becomes read-only. Later changes are only possible through a documented correction that creates a new snapshot version."
                : "The ranking is computed from submitted judge scores (conflicts excluded) and frozen as snapshot v1. Judging is locked, participants are notified and results are published. Later changes require a documented correction."
            }
            confirmLabel="Finalize and publish"
            onConfirm={() => finalize.mutateAsync(undefined).catch(() => undefined)}
          />
        }
      />
      <CardBody>
        <ul className="divide-y divide-border">
          <CheckRow
            ok={ended}
            required={!isAdmin}
            label="The competition has ended"
            detail={c.ends_at ? (ended ? `Ended ${relativeTime(c.ends_at, now)}.` : `Ends ${relativeTime(c.ends_at, now)} (${formatDateTime(c.ends_at)}).${isAdmin ? " Platform admins may finalize early." : ""}`) : "No end time set."}
          />
          {automatic ? (
            <CheckRow
              ok={pending === 0}
              label="All submissions finished scoring"
              detail={pendingQueries.some((q) => q.isPending) ? "Checking…" : pending ? `${formatNumber(pending)} still queued or scoring — refreshes automatically.` : "Nothing is waiting in the queue."}
            />
          ) : c.scoring_mode === "judged" ? (
            <li className="py-2.5 text-sm text-muted">
              Only submitted judge scores count — check progress on the <Link href={`/competitions/${slug}/manage/judging`} className="text-accent-strong hover:underline">Judging</Link> page first.
            </li>
          ) : null}
        </ul>
      </CardBody>
    </Card>
  );
}

function Corrections({ slug }: { slug: string }) {
  const [reason, setReason] = useState("");
  const correct = useApiMutation((r: string) => post<Snapshot>(`/competitions/${slug}/results/corrections`, { reason: r }), {
    success: (s) => `Correction published as snapshot v${s.version}.`,
    invalidate: [["competitions", slug]],
    onSuccess: () => setReason(""),
  });
  const valid = reason.trim().length >= 5;
  return (
    <Card>
      <CardHeader title="Publish a correction" description="Recomputes the ranking from current data (for example after invalidating a submission) and publishes it as a new snapshot. Participants are notified with your reason." />
      <CardBody className="space-y-3">
        <Field label="Reason" required hint="At least 5 characters. Shown to participants and recorded in the audit log.">
          {(p) => <Textarea {...p} rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Invalidated a submission that used leaked test labels." />}
        </Field>
        <div className="flex justify-end">
          <ConfirmDialog
            trigger={<Button variant="secondary" icon={<History className="h-4 w-4" />} disabled={!valid} loading={correct.isPending}>Publish correction</Button>}
            title="Publish a results correction?"
            description="A new snapshot version replaces the current results. Previous versions stay visible in the history."
            confirmLabel="Publish correction"
            onConfirm={() => correct.mutateAsync(reason.trim()).catch(() => undefined)}
          />
        </div>
      </CardBody>
    </Card>
  );
}

function Certificates({ slug, finalized }: { slug: string; finalized: boolean }) {
  const preview = useQuery({ queryKey: certPreviewKey(slug), queryFn: () => get<Eligibility[]>(`/competitions/${slug}/certificates/preview`) });
  const issued = useQuery({ queryKey: certListKey(slug), queryFn: () => get<CertificateOut[]>(`/competitions/${slug}/certificates`) });
  const issue = useApiMutation(() => post<{ issued: number }>(`/competitions/${slug}/certificates/issue`), {
    success: (r) => (r.issued ? `Issued ${formatNumber(r.issued)} certificates.` : "Everyone eligible already has a certificate."),
    invalidate: [["competitions", slug, "certificates"]],
  });
  const revoke = useApiMutation(({ id, reason }: { id: string; reason: string }) => post<CertificateOut>(`/certificates/${encodeURIComponent(id)}/revoke`, { reason }), {
    success: "Certificate revoked. Verification now shows it as revoked.",
    invalidate: [["competitions", slug, "certificates"]],
  });
  const newCount = (preview.data ?? []).filter((r) => !r.already_issued).length;

  return (
    <Card>
      <CardHeader
        title="Certificates"
        description="Verifiable certificates for ranked teams, award winners and participants with a valid entry, based on your certificate rules."
        action={
          <ConfirmDialog
            trigger={<Button icon={<BadgeCheck className="h-4 w-4" />} disabled={!finalized || newCount === 0} loading={issue.isPending}>Issue certificates</Button>}
            title={`Issue ${formatNumber(newCount)} certificate${newCount === 1 ? "" : "s"}?`}
            description="Recipients are notified and certificates become publicly verifiable. People who already have a certificate are skipped, so this is safe to run again after corrections or new awards."
            confirmLabel="Issue"
            tone="primary"
            onConfirm={() => issue.mutateAsync(undefined).catch(() => undefined)}
          />
        }
      />
      <CardBody className="space-y-6">
        {!finalized ? <InlineNotice tone="info">Certificates can be issued once results are finalized. The preview below shows who would qualify right now.</InlineNotice> : null}
        <section aria-labelledby="cert-preview">
          <h3 id="cert-preview" className="mb-2 text-sm font-semibold text-fg">Eligibility preview</h3>
          <QueryState
            query={preview}
            loading={<SkeletonRows rows={3} />}
            isEmpty={(d) => d.length === 0}
            empty={<EmptyState icon={<Award className="h-5 w-5" />} title="No one is eligible yet" description="Participants qualify once they have a valid entry, a final rank within your award threshold or an award category win." className="py-8" />}
          >
            {(rows) => (
              <Table>
                <THead>
                  <tr>
                    <TH>Recipient</TH>
                    <TH>Certificate</TH>
                    <TH>Result</TH>
                    <TH>Status</TH>
                  </tr>
                </THead>
                <TBody>
                  {rows.map((r, i) => (
                    <TR key={`${r.user_id}-${r.kind}-${i}`}>
                      <TD>
                        <Link href={`/u/${r.handle}`} className="font-medium text-fg hover:text-accent-strong">{r.display_name}</Link>
                        <span className="block text-xs text-subtle">@{r.handle}{r.team ? ` · ${r.team}` : ""}</span>
                      </TD>
                      <TD>{titleCase(r.kind.replace(/^competition_/, ""))}</TD>
                      <TD>{r.label}{r.rank ? <span className="text-xs text-subtle"> · rank {r.rank}</span> : null}</TD>
                      <TD>{r.already_issued ? <Badge tone="success">Issued</Badge> : <Badge tone="warning">Not issued</Badge>}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </QueryState>
        </section>
        <section aria-labelledby="cert-issued">
          <h3 id="cert-issued" className="mb-2 text-sm font-semibold text-fg">Issued certificates</h3>
          <QueryState
            query={issued}
            loading={<SkeletonRows rows={3} />}
            isEmpty={(d) => d.length === 0}
            empty={<p className="text-sm text-subtle">None issued yet.</p>}
          >
            {(certs) => (
              <Table>
                <THead>
                  <tr>
                    <TH>Recipient</TH>
                    <TH>Result</TH>
                    <TH>Issued</TH>
                    <TH>Status</TH>
                    <TH><span className="sr-only">Actions</span></TH>
                  </tr>
                </THead>
                <TBody>
                  {certs.map((cert) => (
                    <TR key={cert.public_id}>
                      <TD>
                        <p className="font-medium text-fg">{cert.recipient_name}</p>
                        <p className="font-mono text-[11px] text-subtle">{cert.public_id}</p>
                        {cert.is_demo ? <DemoBadge className="mt-1" /> : null}
                      </TD>
                      <TD>
                        {cert.result_label}
                        <span className="block text-xs text-subtle">{titleCase(cert.kind.replace(/^competition_/, ""))}</span>
                      </TD>
                      <TD className="whitespace-nowrap" title={formatDateTime(cert.issued_at)}>{relativeTime(cert.issued_at)}</TD>
                      <TD>
                        <StatusBadge status={cert.status} />
                        {cert.revoked_reason ? <p className="mt-1 max-w-48 text-xs text-muted">{cert.revoked_reason}</p> : null}
                      </TD>
                      <TD className="text-right">
                        <div className="flex justify-end gap-1">
                          <a href={cert.verification_url} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 items-center gap-1 rounded-[var(--radius-md)] px-2 text-sm text-muted hover:bg-surface-2 hover:text-fg">
                            <ExternalLink className="h-4 w-4" aria-hidden /> Verify<span className="sr-only"> certificate for {cert.recipient_name}</span>
                          </a>
                          {cert.status === "valid" ? (
                            <ConfirmDialog
                              trigger={<Button size="sm" variant="ghost" icon={<ShieldX className="h-4 w-4" />} aria-label={`Revoke certificate for ${cert.recipient_name}`}>Revoke</Button>}
                              title={`Revoke ${cert.recipient_name}'s certificate?`}
                              description="Verification will show it as revoked with your reason. This cannot be undone; issue again only if the person becomes eligible under a new record."
                              confirmLabel="Revoke"
                              requireReason
                              reasonLabel="Reason (shown on the verification page and recorded in the audit log)"
                              onConfirm={(reason) => revoke.mutateAsync({ id: cert.public_id, reason }).catch(() => undefined)}
                            />
                          ) : null}
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </QueryState>
        </section>
      </CardBody>
    </Card>
  );
}

export default function ManageResultsPage() {
  const { slug } = useParams<{ slug: string }>();
  const detail = useCompetitionDetail(slug);
  const manage = useManage(slug);
  const preview = useQuery({
    queryKey: previewKey(slug),
    queryFn: () => get<LeaderboardRow[]>(`/competitions/${slug}/leaderboard/private-preview`),
    enabled: detail.data?.scoring_mode !== "none",
  });
  const history = useQuery({ queryKey: historyKey(slug), queryFn: () => get<Snapshot[]>(`/competitions/${slug}/results/history`) });

  if (detail.isPending || manage.isPending) return <SkeletonRows rows={6} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const c = detail.data;
  const finalized = Boolean(c.finalized_at) || manage.data?.lifecycle === "finalized" || manage.data?.lifecycle === "archived";
  const judged = c.scoring_mode === "judged";

  return (
    <div className="space-y-6">
      <ManageHeading
        title="Results & certificates"
        description="Preview the final ranking, finalize it, correct it if needed, and issue certificates."
        actions={<DownloadLink path={`/competitions/${slug}/results.csv`}>Export results CSV</DownloadLink>}
      />

      <FinalizeCard slug={slug} c={c} />

      {c.scoring_mode !== "none" ? (
        <Card>
          <CardHeader
            title={finalized ? "Recomputed ranking (current data)" : "Private leaderboard preview"}
            description={
              finalized
                ? "What a correction would publish now. The official results are the current snapshot below."
                : judged
                  ? "Average weighted judge scores (submitted scores only, conflicts excluded). Visible to organizers only."
                  : "Ranked by the private split using each team's selected (or best public) submissions. Visible to organizers only."
            }
          />
          <CardBody>
            <QueryState
              query={preview}
              loading={<SkeletonRows rows={5} />}
              isEmpty={(d) => d.length === 0}
              empty={<EmptyState icon={<Trophy className="h-5 w-5" />} title="Nothing to rank yet" description={judged ? "Rankings appear once judges submit scores." : "Rankings appear once teams have scored submissions."} className="py-8" />}
            >
              {(rows) => <RankingTable rows={rows} judged={judged} direction={c.evaluation?.direction} />}
            </QueryState>
          </CardBody>
        </Card>
      ) : null}

      {finalized ? <Corrections slug={slug} /> : null}

      <Card>
        <CardHeader title="Snapshot history" description="Every published version of the results. The current one is what participants see." />
        <CardBody>
          <QueryState
            query={history}
            loading={<SkeletonRows rows={2} />}
            isEmpty={(d) => d.length === 0}
            empty={<p className="text-sm text-subtle">No snapshots yet — the first is created when you finalize.</p>}
          >
            {(snaps) => (
              <ol className="divide-y divide-border rounded-[var(--radius-md)] border border-border">
                {snaps.map((s) => (
                  <li key={s.version} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <FileCheck2 className="h-4 w-4 text-subtle" aria-hidden />
                        <span className="font-medium text-fg">v{s.version}</span>
                        <Badge tone={s.kind === "final" ? "accent" : "warning"}>{titleCase(s.kind)}</Badge>
                        {s.is_current ? <Badge tone="success">Current</Badge> : null}
                        <span className="text-xs text-subtle">{formatNumber(s.team_count)} teams · {titleCase(s.source)} · rules v{s.rules_version}{s.evaluator_version ? ` · evaluator ${s.evaluator_version}` : ""}</span>
                      </div>
                      {s.reason ? <p className="mt-1 text-sm text-muted">{s.reason}</p> : null}
                    </div>
                    <span className="shrink-0 text-xs text-subtle" title={formatDateTime(s.created_at)}>{relativeTime(s.created_at)}</span>
                  </li>
                ))}
              </ol>
            )}
          </QueryState>
        </CardBody>
      </Card>

      <Certificates slug={slug} finalized={finalized} />
    </div>
  );
}
