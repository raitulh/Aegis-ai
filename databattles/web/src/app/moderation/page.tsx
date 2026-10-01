"use client";

import { useQuery } from "@tanstack/react-query";
import { EyeOff, Flag, Gavel, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";

import { UserStatusBadge } from "@/components/admin/admin-ui";
import type { QueueItem, ReportTargetType } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, PermissionDenied, SkeletonRows, Spinner } from "@/components/ui/states";
import { get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import { hasRole, useApiMutation, useRequireAuth } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { useUrlState } from "@/lib/url-state";

type Action = "dismiss" | "warn" | "hide" | "restore" | "delete" | "takedown" | "suspend_user";

const PAGE_SIZE = 20;
const TARGET_TYPES: ReportTargetType[] = ["thread", "comment", "project", "dataset", "competition", "user"];
const REASON_TONE: Record<string, "danger" | "warning" | "info" | "neutral"> = {
  abuse: "danger",
  spam: "warning",
  privacy: "danger",
  copyright: "info",
  misleading: "warning",
  other: "neutral",
};

const ACTIONS: Record<Action, { label: string; description: string; danger?: boolean }> = {
  dismiss: { label: "Dismiss", description: "No violation. Closes the reports without changing the content." },
  warn: { label: "Warn owner", description: "Keeps the content up and notifies the owner that a moderator issued a warning." },
  hide: { label: "Hide", description: "Hides the content from other users. It can be restored later." },
  takedown: { label: "Take down", description: "Removes the project/dataset from public view (competitions are frozen). Reversible with Restore." },
  delete: { label: "Delete", description: "Removes the post or reply. Cannot be restored from here.", danger: true },
  restore: { label: "Restore", description: "Makes previously hidden or taken-down content visible again." },
  suspend_user: { label: "Suspend owner", description: "Suspends the owner’s account and signs them out everywhere.", danger: true },
};

function actionsFor(item: QueueItem): Action[] {
  const hidden = Boolean(item.preview.hidden);
  const out: Action[] = [];
  if (item.target_type === "user") {
    out.push("dismiss", "warn");
    if (item.owner) out.push("suspend_user");
    return out;
  }
  out.push("dismiss", "warn");
  if (item.target_type === "thread" || item.target_type === "comment") {
    if (!hidden) out.push("hide");
    out.push("delete");
  } else if (!hidden) {
    out.push("takedown");
  }
  if (hidden) out.push("restore");
  if (item.owner && item.owner_status === "active") out.push("suspend_user");
  return out;
}

function ResolveDialog({ item, resolved }: { item: QueueItem; resolved: boolean }) {
  const options: Action[] = resolved ? (item.preview.hidden && item.target_type !== "user" ? ["restore"] : []) : actionsFor(item);
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<Action>(options[0] ?? "dismiss");
  const [note, setNote] = useState("");
  const resolve = useApiMutation(
    (body: { target_type: string; target_id: string; action: Action; note: string }) => post<{ message: string }>("/moderation/resolve", body),
    {
      success: (r) => r.message,
      invalidate: [["moderation"], ["admin", "health"]],
      onSuccess: () => {
        setOpen(false);
        setNote("");
      },
    },
  );
  if (!options.length) return null;
  const danger = ACTIONS[action]?.danger;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setAction(options[0]);
          setNote("");
        }
      }}
      trigger={<Button size="sm" variant={resolved ? "secondary" : "primary"} icon={<Gavel className="h-4 w-4" />}>{resolved ? "Restore" : "Resolve"}</Button>}
      title={`Resolve: ${item.preview.title}`}
      description="All open reports on this item are closed with your decision. The owner is notified of actions (not of dismissals); reporters are never revealed."
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            loading={resolve.isPending}
            disabled={note.trim().length < 3}
            onClick={() => resolve.mutate({ target_type: item.target_type, target_id: item.target_id, action, note: note.trim() })}
          >
            {ACTIONS[action].label}
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="text-sm font-medium text-fg">Action</legend>
        <div className="mt-2 space-y-2">
          {options.map((a) => (
            <label
              key={a}
              className={cn(
                "flex cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3 focus-within:ring-2 focus-within:ring-[var(--ring)]",
                action === a ? (ACTIONS[a].danger ? "border-danger bg-danger-soft" : "border-accent bg-accent-soft") : "border-border hover:bg-surface-2",
              )}
            >
              <input type="radio" name={`action-${item.target_id}`} value={a} checked={action === a} onChange={() => setAction(a)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
              <span>
                <span className={cn("block text-sm font-medium", ACTIONS[a].danger ? "text-danger" : "text-fg")}>{ACTIONS[a].label}</span>
                <span className="block text-xs text-muted">{ACTIONS[a].description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <Field label="Moderator note (required, recorded in the audit log)" required className="mt-4" hint="For takedowns, the first 500 characters are shown to the owner as the reason.">
        {(p) => <Textarea {...p} value={note} onChange={(e) => setNote(e.target.value)} minLength={3} maxLength={1000} rows={3} />}
      </Field>
    </Dialog>
  );
}

function QueueCard({ item, status }: { item: QueueItem; status: string }) {
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="outline">{titleCase(item.target_type)}</Badge>
              <Badge tone={item.report_count >= 3 ? "danger" : "warning"} icon={<Flag className="h-3 w-3" aria-hidden />}>
                {item.report_count} report{item.report_count === 1 ? "" : "s"}
              </Badge>
              {item.reasons.map((r) => <Badge key={r} tone={REASON_TONE[r] ?? "neutral"}>{titleCase(r)}</Badge>)}
              {item.preview.hidden ? <Badge tone="neutral" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden</Badge> : null}
            </div>
            <h3 className="mt-2 font-medium text-fg">
              {item.preview.url ? (
                <Link href={item.preview.url} className="hover:text-accent-strong" target="_blank" rel="noopener noreferrer">
                  {item.preview.title}<span className="sr-only"> (opens in a new tab)</span>
                </Link>
              ) : (
                item.preview.title
              )}
            </h3>
            {item.preview.excerpt ? (
              <p className="mt-1 line-clamp-4 whitespace-pre-wrap break-words rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-muted">{item.preview.excerpt}</p>
            ) : null}
          </div>
          <div className="shrink-0">
            <ResolveDialog item={item} resolved={status !== "open"} />
          </div>
        </div>
        {item.details.length ? (
          <div>
            <p className="text-xs font-medium text-subtle">Reporter comments</p>
            <ul className="mt-1 space-y-1">
              {item.details.map((d, i) => <li key={i} className="border-l-2 border-border pl-2 text-xs text-muted">{d}</li>)}
            </ul>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-3 text-xs text-subtle">
          <span className="inline-flex items-center gap-2">
            Owner: <UserLink user={item.owner} size={18} className="text-xs" />
            {item.owner_status && item.owner_status !== "active" ? <UserStatusBadge status={item.owner_status} /> : null}
          </span>
          <span title={formatDateTime(item.first_reported_at)}>First reported {relativeTime(item.first_reported_at)}</span>
        </div>
      </CardBody>
    </Card>
  );
}

function Queue() {
  const [state, setState] = useUrlState({ status: "open", target_type: "", page: "1" });
  const page = Math.max(1, Number(state.page) || 1);
  const status = state.status || "open";
  const filters = { status, target_type: state.target_type || undefined, page, page_size: PAGE_SIZE };
  const queue = useQuery({ queryKey: ["moderation", "queue", filters], queryFn: () => get<Page<QueueItem>>("/moderation/queue", filters), refetchInterval: 60_000 });

  return (
    <>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-1 rounded-[var(--radius-md)] border border-border bg-surface p-1" role="group" aria-label="Report status">
          {(["open", "actioned", "dismissed"] as const).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={status === s}
              onClick={() => setState({ status: s })}
              className={cn("rounded-md px-3 py-1.5 text-sm font-medium", status === s ? "bg-surface-3 text-fg" : "text-muted hover:text-fg")}
            >
              {titleCase(s)}
            </button>
          ))}
        </div>
        <Select aria-label="Content type" className="sm:w-48" value={state.target_type ?? ""} onChange={(e) => setState({ target_type: e.target.value })}>
          <option value="">All content types</option>
          {TARGET_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}s</option>)}
        </Select>
      </div>
      {queue.isPending ? (
        <SkeletonRows rows={5} />
      ) : queue.isError ? (
        <ErrorState error={queue.error} onRetry={() => queue.refetch()} />
      ) : queue.data.items.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-5 w-5" />}
          title={status === "open" ? "The queue is clear" : `No ${status} reports`}
          description={status === "open" ? "New reports appear here, grouped by the reported item." : undefined}
        />
      ) : (
        <>
          <p className="mb-3 text-sm text-muted" aria-live="polite">{formatNumber(queue.data.total)} reported item{queue.data.total === 1 ? "" : "s"}</p>
          <div className="space-y-4">
            {queue.data.items.map((item) => <QueueCard key={`${item.target_type}:${item.target_id}`} item={item} status={status} />)}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={queue.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </>
  );
}

export default function ModerationPage() {
  const me = useRequireAuth();
  if (me.isPending || !me.data) return <Spinner />;
  if (!hasRole(me.data, "moderator")) {
    return (
      <Container className="py-10">
        <PermissionDenied message="The moderation queue is available to moderators and platform administrators." />
      </Container>
    );
  }
  return (
    <Container size="lg">
      <PageHeader
        eyebrow="Trust & safety"
        title="Moderation queue"
        description="Reports are grouped by item, most-reported first. Every decision needs a note and is recorded in the audit log."
        actions={
          <>
            <LinkButton href="/guidelines" variant="ghost">Guidelines</LinkButton>
            <LinkButton href="/admin/users" variant="secondary">Users</LinkButton>
          </>
        }
      />
      <Suspense fallback={<SkeletonRows rows={5} />}>
        <Queue />
      </Suspense>
    </Container>
  );
}
