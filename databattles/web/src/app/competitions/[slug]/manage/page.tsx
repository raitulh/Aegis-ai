"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CopyPlus, ExternalLink, KeyRound, Lock, RefreshCw, Send, Snowflake } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  CheckRow,
  ManageHeading,
  competitionKeys,
  isTerminal,
  manageKey,
  publishChecksKey,
  useCompetitionDetail,
  useManage,
  type PublishCheck,
} from "@/components/organizer/shared";
import { UserLink } from "@/components/domain/cards";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { CopyButton, KeyValue } from "@/components/ui/misc";
import { EmptyState, ErrorState, InlineNotice, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get, post } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useMe } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { CompetitionDetail } from "@/lib/types";

function When({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-subtle">Not set</span>;
  return (
    <span>
      {formatDateTime(value)} <span className="text-xs text-subtle">({relativeTime(value)})</span>
    </span>
  );
}

export default function ManageOverviewPage() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const manage = useManage(slug);
  const detail = useCompetitionDetail(slug);
  const checks = useQuery({
    queryKey: publishChecksKey(slug),
    queryFn: () => get<PublishCheck[]>(`/competitions/${slug}/publish-checks`),
  });
  const [origin, setOrigin] = useState("");
  useEffect(() => setOrigin(window.location.origin), []);
  const [publishError, setPublishError] = useState<string | null>(null);

  const invalidateAll = [...competitionKeys(slug), ["competitions", "organizing"]] as const;

  const publish = useApiMutation(() => post<CompetitionDetail>(`/competitions/${slug}/publish`), {
    success: "Competition published.",
    invalidate: invalidateAll,
    onSuccess: () => setPublishError(null),
    onError: (e) => {
      if (e.code === "publish_requirements_unmet") setPublishError(e.message);
      qc.invalidateQueries({ queryKey: publishChecksKey(slug) });
    },
  });
  const archive = useApiMutation((reason: string) => post<CompetitionDetail>(`/competitions/${slug}/archive`, { reason }), {
    success: "Competition archived.",
    invalidate: invalidateAll,
  });
  const clone = useApiMutation(() => post<CompetitionDetail>(`/competitions/${slug}/clone`), {
    success: (c) => `Created “${c.title}” as a draft.`,
    invalidate: [["competitions", "organizing"]],
    onSuccess: (c) => router.push(`/competitions/${c.slug}/manage/settings`),
  });
  const rotate = useApiMutation(() => post<{ invite_code: string }>(`/competitions/${slug}/invite-code`), {
    success: "New invite code generated. The previous code no longer works.",
    invalidate: [manageKey(slug)],
  });
  const freeze = useApiMutation(
    ({ frozen, reason }: { frozen: boolean; reason: string }) => post(`/competitions/${slug}/${frozen ? "freeze" : "unfreeze"}`, { reason }),
    { success: "Competition updated.", invalidate: [qk.competition(slug), manageKey(slug)] },
  );

  if (manage.isPending || detail.isPending) return <SkeletonRows rows={6} />;
  if (manage.isError) return <ErrorState error={manage.error} onRetry={() => manage.refetch()} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  const m = manage.data;
  const c = detail.data;
  const lifecycle = m.lifecycle;
  const checkList = checks.data ?? m.publish_checks;
  const required = checkList.filter((x) => x.required);
  const requiredDone = required.filter((x) => x.ok).length;
  const canPublish = lifecycle === "draft" && requiredDone === required.length;
  const inviteVisible = c.visibility === "invite_only" || c.visibility === "private";
  const inviteLink = m.invite_code && origin ? `${origin}/competitions/${slug}?code=${encodeURIComponent(m.invite_code)}` : "";
  const moderator = hasRole(me.data, "moderator");

  return (
    <div className="space-y-6">
      <ManageHeading
        title="Overview"
        description="Status, publication checklist and lifecycle controls."
        actions={
          <>
            <LinkButton href={`/competitions/${slug}`} variant="ghost" size="sm" icon={<ExternalLink className="h-4 w-4" />}>
              Public page
            </LinkButton>
            <ConfirmDialog
              trigger={<Button variant="secondary" size="sm" icon={<CopyPlus className="h-4 w-4" />} loading={clone.isPending}>Clone</Button>}
              title="Clone this competition?"
              description="Creates a new draft with the same settings, content, rubric and scoring configuration. Participants, submissions, schedule and results are not copied, and you'll need to upload ground truth again."
              confirmLabel="Create copy"
              tone="primary"
              onConfirm={() => clone.mutateAsync(undefined).catch(() => undefined)}
            />
            {lifecycle !== "archived" ? (
              <ConfirmDialog
                trigger={<Button variant="outline" size="sm" icon={<Archive className="h-4 w-4" />}>Archive</Button>}
                title="Archive this competition?"
                description="Archived competitions are read-only and leave discovery lists. Results and certificates stay valid. This cannot be undone."
                confirmLabel="Archive"
                requireReason
                onConfirm={(reason) => archive.mutateAsync(reason).catch(() => undefined)}
              />
            ) : null}
          </>
        }
      />

      {c.frozen ? (
        <InlineNotice tone="danger" title="Frozen by platform moderators">
          Registration and submissions are paused. {c.frozen_reason ? <>Reason: {c.frozen_reason}</> : null}
        </InlineNotice>
      ) : null}
      {lifecycle === "archived" ? <InlineNotice tone="info" title="Archived">Hidden from discovery. Results and certificates stay valid; only presentation details can still be edited.</InlineNotice> : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Status" value={<StatusBadge status={c.status} className="text-sm" />} hint={`Lifecycle: ${titleCase(lifecycle)}`} />
        <Stat label="Participants" value={formatNumber(c.participant_count)} hint={`${formatNumber(c.team_count)} teams`} />
        <Stat label="Config version" value={`v${c.config_version}`} hint={m.evaluation_locked_at ? "Scoring locked" : "Scoring editable"} />
        <Stat label="Announcements" value={formatNumber(c.announcement_count)} hint="Published" />
      </div>

      <Card>
        <CardHeader title="At a glance" />
        <CardBody>
          <KeyValue
            items={[
              { label: "Starts", value: <When value={c.starts_at} /> },
              { label: "Ends", value: <When value={c.ends_at} /> },
              { label: "Registration closes", value: <When value={c.registration_closes_at ?? c.ends_at} /> },
              { label: "Event time zone", value: c.timezone },
              { label: "Visibility", value: titleCase(c.visibility) },
              { label: "Scoring", value: c.evaluation ? `${c.evaluation.metric_label} (${c.evaluation.direction})` : titleCase(c.scoring_mode) },
              { label: "Published", value: c.published_at ? <When value={c.published_at} /> : "Not yet" },
              { label: "Results finalized", value: c.finalized_at ? <When value={c.finalized_at} /> : "Not yet" },
            ]}
          />
          {m.evaluation_locked_at ? (
            <p className="mt-4 flex items-center gap-2 text-xs text-muted">
              <Lock className="h-3.5 w-3.5" aria-hidden /> Scoring rules locked {relativeTime(m.evaluation_locked_at)} because submissions have been scored.
            </p>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title={lifecycle === "draft" ? "Publish checklist" : "Publication"}
          description={
            lifecycle === "draft"
              ? `${requiredDone} of ${required.length} required items complete. Participants can't see the competition until you publish.`
              : `Published ${c.published_at ? relativeTime(c.published_at) : ""}. Checks are shown for reference.`
          }
          action={
            lifecycle === "draft" ? (
              <ConfirmDialog
                trigger={<Button size="sm" icon={<Send className="h-4 w-4" />} disabled={!canPublish} loading={publish.isPending}>Publish</Button>}
                title="Publish this competition?"
                description={`It becomes visible according to its visibility (${titleCase(c.visibility)}). Later changes to rules, schedule and scoring are versioned and shown in the change history.`}
                confirmLabel="Publish now"
                tone="primary"
                onConfirm={() => publish.mutateAsync(undefined).catch(() => undefined)}
              />
            ) : null
          }
        />
        <CardBody>
          {checks.isError ? <p className="mb-2 text-xs text-danger">Couldn't refresh checks — showing the last known state.</p> : null}
          <ul className="divide-y divide-border">
            {checkList.map((x) => (
              <CheckRow key={x.key} ok={x.ok} required={x.required} label={x.label} detail={x.message} />
            ))}
          </ul>
          {publishError ? <div className="mt-3"><InlineNotice tone="danger">{publishError}</InlineNotice></div> : null}
          {lifecycle === "draft" && !canPublish ? (
            <p className="mt-3 text-xs text-muted">Complete the items marked “!” in Settings{c.scoring_mode === "automatic" ? " and Evaluation" : c.scoring_mode === "judged" ? " and Judging" : ""}, then publish.</p>
          ) : null}
        </CardBody>
      </Card>

      {inviteVisible ? (
        <Card>
          <CardHeader
            title="Invite access"
            description={c.visibility === "private" ? "Private: only invited people can find and join." : "Invite only: anyone with the link can view; joining needs the code."}
            action={
              <ConfirmDialog
                trigger={<Button size="sm" variant="secondary" icon={<RefreshCw className="h-4 w-4" />} loading={rotate.isPending} disabled={isTerminal(lifecycle)}>{m.invite_code ? "Rotate code" : "Generate code"}</Button>}
                title={m.invite_code ? "Rotate the invite code?" : "Generate an invite code?"}
                description={m.invite_code ? "The current code and links stop working immediately. People who already joined are not affected." : "Creates a code you can share with invited participants."}
                confirmLabel={m.invite_code ? "Rotate" : "Generate"}
                tone={m.invite_code ? "danger" : "primary"}
                onConfirm={() => rotate.mutateAsync(undefined).catch(() => undefined)}
              />
            }
          />
          <CardBody className="space-y-3">
            {m.invite_code ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <KeyRound className="h-4 w-4 text-subtle" aria-hidden />
                  <span className="text-sm text-muted">Code</span>
                  <code className="rounded-md bg-surface-2 px-2 py-1 font-mono text-sm text-fg">{m.invite_code}</code>
                  <CopyButton value={m.invite_code} label="Copy code" />
                </div>
                {inviteLink ? (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <code className="min-w-0 flex-1 truncate rounded-md bg-surface-2 px-2 py-1 font-mono text-xs text-muted" title={inviteLink}>{inviteLink}</code>
                    <CopyButton value={inviteLink} label="Copy invite link" />
                  </div>
                ) : null}
                <p className="text-xs text-subtle">Participants enter the code when they join. Share it only with people you want to admit, and rotate it if it leaks.</p>
              </>
            ) : (
              <p className="text-sm text-muted">{lifecycle === "draft" ? "A code is generated automatically when you publish." : "No invite code yet."}</p>
            )}
          </CardBody>
        </Card>
      ) : null}

      {moderator ? (
        <Card>
          <CardHeader title="Platform safety controls" description="Moderators can freeze a competition to pause registration and submissions." />
          <CardBody>
            {c.frozen ? (
              <ConfirmDialog
                trigger={<Button variant="secondary" size="sm" icon={<Snowflake className="h-4 w-4" />}>Unfreeze</Button>}
                title="Unfreeze this competition?"
                description="Registration and submissions resume according to the schedule."
                confirmLabel="Unfreeze"
                tone="primary"
                requireReason
                onConfirm={(reason) => freeze.mutateAsync({ frozen: false, reason }).catch(() => undefined)}
              />
            ) : (
              <ConfirmDialog
                trigger={<Button variant="danger" size="sm" icon={<Snowflake className="h-4 w-4" />}>Freeze</Button>}
                title="Freeze this competition?"
                description="Pauses registration and submissions immediately. Participants see the reason."
                confirmLabel="Freeze"
                requireReason
                onConfirm={(reason) => freeze.mutateAsync({ frozen: true, reason }).catch(() => undefined)}
              />
            )}
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader title="Change history" description="Versioned changes to rules, schedule and scoring after publication." />
        {m.config_versions.length === 0 ? (
          <CardBody>
            <EmptyState title="No versions yet" description="A version is recorded when you publish and whenever rules, dates or scoring change afterwards." className="border-0 py-6" />
          </CardBody>
        ) : (
          <div className="p-3">
            <Table>
              <THead>
                <tr>
                  <TH>Version</TH>
                  <TH>Changed</TH>
                  <TH>Reason</TH>
                  <TH>By</TH>
                  <TH>When</TH>
                </tr>
              </THead>
              <TBody>
                {m.config_versions.map((v, i) => (
                  <TR key={`${v.version}-${i}`}>
                    <TD className="font-mono text-xs">v{v.version}</TD>
                    <TD>
                      <div className="flex max-w-xs flex-wrap gap-1">
                        {v.changed_fields.map((f) => <Badge key={f} tone="outline">{titleCase(f)}</Badge>)}
                      </div>
                    </TD>
                    <TD className="max-w-xs text-muted">{v.reason ?? "—"}</TD>
                    <TD><UserLink user={v.changed_by} size={20} /></TD>
                    <TD className="whitespace-nowrap text-muted" title={formatDateTime(v.created_at)}>{relativeTime(v.created_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
