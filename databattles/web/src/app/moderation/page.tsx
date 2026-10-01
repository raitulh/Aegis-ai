"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Database,
  EyeOff,
  Flag,
  FolderGit2,
  Gavel,
  MessageSquare,
  MessagesSquare,
  Quote,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trophy,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";

import { UserStatusBadge } from "@/components/admin/admin-ui";
import type { QueueItem, ReportTargetType } from "@/components/admin/types";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState, PermissionDenied, Skeleton, Spinner } from "@/components/ui/states";
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

/** Presentational: glyph per reported content type (the type name is always shown beside it). */
const TYPE_ICON: Record<ReportTargetType, LucideIcon> = {
  thread: MessagesSquare,
  comment: MessageSquare,
  project: FolderGit2,
  dataset: Database,
  competition: Trophy,
  user: UserRound,
};

/** Presentational: how the current status tab is labelled on each row. */
const STATUS_BADGE: Record<string, { tone: "warning" | "accent" | "neutral"; label: string }> = {
  open: { tone: "warning", label: "Open" },
  actioned: { tone: "accent", label: "Actioned" },
  dismissed: { tone: "neutral", label: "Dismissed" },
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
      trigger={<Button size="sm" className="h-9 w-full sm:h-8 sm:w-auto lg:w-full" variant={resolved ? "secondary" : "primary"} icon={<Gavel className="h-4 w-4" />}>{resolved ? "Restore" : "Resolve"}</Button>}
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
                "flex cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3 transition-colors focus-within:ring-2 focus-within:ring-[var(--ring)]",
                action === a ? (ACTIONS[a].danger ? "border-danger bg-danger-soft" : "border-accent bg-accent-soft") : "border-border hover:bg-surface-2",
              )}
            >
              <input type="radio" name={`action-${item.target_id}`} value={a} checked={action === a} onChange={() => setAction(a)} className="mt-1 h-4 w-4 accent-[var(--accent)]" />
              <span>
                <span className={cn("flex items-center gap-1.5 text-sm font-medium", ACTIONS[a].danger ? "text-danger" : "text-fg")}>
                  {ACTIONS[a].label}
                  {ACTIONS[a].danger ? <ShieldAlert className="h-3.5 w-3.5" aria-hidden /> : null}
                </span>
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

function QueueCard({ item, status, index }: { item: QueueItem; status: string; index: number }) {
  const severe = item.report_count >= 3;
  const TypeIcon = TYPE_ICON[item.target_type] ?? Flag;
  const st = STATUS_BADGE[status] ?? { tone: "neutral" as const, label: titleCase(status) };
  return (
    <li className="relative animate-rise" style={{ animationDelay: `${Math.min(index, 6) * 40}ms` }}>
      {/* Severity rail — the report count beside it carries the same information as text. */}
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-0.5", severe ? "bg-danger" : "bg-warning/70")} />
      <article className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_15.5rem]">
        <div className="min-w-0 px-4 py-4 sm:px-5">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="outline" icon={<TypeIcon className="h-3 w-3" aria-hidden />}>{titleCase(item.target_type)}</Badge>
            <Badge tone={severe ? "danger" : "warning"} icon={<Flag className="h-3 w-3" aria-hidden />}>
              {item.report_count} report{item.report_count === 1 ? "" : "s"}
            </Badge>
            {item.reasons.map((r) => <Badge key={r} tone={REASON_TONE[r] ?? "neutral"}>{titleCase(r)}</Badge>)}
            {item.preview.hidden ? <Badge tone="neutral" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden</Badge> : null}
          </div>
          <h3 className="mt-2.5 break-words text-[15px] font-semibold tracking-[-0.01em] text-fg">
            {item.preview.url ? (
              <Link href={item.preview.url} className="rounded-sm hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]" target="_blank" rel="noopener noreferrer">
                {item.preview.title}<span className="sr-only"> (opens in a new tab)</span>
              </Link>
            ) : (
              item.preview.title
            )}
          </h3>
          {item.preview.excerpt ? (
            <figure className="mt-2.5">
              <figcaption className="mb-1.5 text-eyebrow text-subtle">Reported content</figcaption>
              <blockquote className="line-clamp-4 whitespace-pre-wrap break-words rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3.5 py-2.5 text-sm leading-relaxed text-fg/90">
                {item.preview.excerpt}
              </blockquote>
            </figure>
          ) : null}
          {item.details.length ? (
            <div className="mt-3.5">
              <p className="text-eyebrow text-subtle">Reporter comments</p>
              <ul className="mt-1.5 space-y-1.5">
                {item.details.map((d, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs leading-relaxed text-muted">
                    <Quote className="mt-0.5 h-3 w-3 shrink-0 text-subtle" aria-hidden />
                    <span className="min-w-0 break-words">{d}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="flex min-w-0 flex-col gap-4 border-t border-border bg-bg-elevated/40 px-4 py-4 sm:px-5 lg:border-l lg:border-t-0">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm lg:grid-cols-1">
            <div className="min-w-0">
              <dt className="text-eyebrow text-subtle">Status</dt>
              <dd className="mt-1">
                <Badge tone={st.tone}>
                  <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                  {st.label}
                </Badge>
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-eyebrow text-subtle">First reported</dt>
              <dd className="mt-1 text-fg" title={formatDateTime(item.first_reported_at)}>
                {relativeTime(item.first_reported_at)}
                <span className="tabular block text-xs text-subtle">{formatDateTime(item.first_reported_at)}</span>
              </dd>
            </div>
            <div className="col-span-2 min-w-0 lg:col-span-1">
              <dt className="text-eyebrow text-subtle">Owner</dt>
              <dd className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
                <UserLink user={item.owner} size={20} className="max-w-full" />
                {item.owner_status && item.owner_status !== "active" ? <UserStatusBadge status={item.owner_status} /> : null}
              </dd>
            </div>
          </dl>
          <div className="mt-auto">
            <ResolveDialog item={item} resolved={status !== "open"} />
          </div>
        </div>
      </article>
    </li>
  );
}

function QueueSkeleton() {
  return (
    <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface" role="status" aria-label="Loading">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_15.5rem]" style={{ opacity: 1 - i * 0.18 }}>
          <div className="px-5 py-4">
            <div className="flex gap-1.5">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-20 rounded-full" />
            </div>
            <Skeleton className="mt-3 h-4 w-2/3" />
            <Skeleton className="mt-3 h-14 w-full rounded-[var(--radius-md)]" />
          </div>
          <div className="hidden space-y-3 border-l border-border px-5 py-4 lg:block">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="mt-4 h-8 w-full rounded-[var(--radius-md)]" />
          </div>
        </div>
      ))}
    </div>
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
      {/* Filter bar */}
      <div className="mb-4 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border p-2 surface-glass sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-0.5 [scrollbar-width:none]" role="group" aria-label="Report status">
          {(["open", "actioned", "dismissed"] as const).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={status === s}
              onClick={() => setState({ status: s })}
              className={cn(
                "inline-flex h-9 shrink-0 items-center rounded-[var(--radius-sm)] px-3.5 text-sm font-medium transition-[background-color,color,box-shadow] duration-200 sm:h-8",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]",
                status === s ? "bg-surface-3 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),0_1px_2px_rgb(0_0_0/0.2)]" : "text-muted hover:text-fg",
              )}
            >
              {titleCase(s)}
            </button>
          ))}
        </div>
        <Select aria-label="Content type" className="h-9 sm:w-52" value={state.target_type ?? ""} onChange={(e) => setState({ target_type: e.target.value })}>
          <option value="">All content types</option>
          {TARGET_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}s</option>)}
        </Select>
      </div>

      {queue.isPending ? (
        <QueueSkeleton />
      ) : queue.isError ? (
        <ErrorState error={queue.error} onRetry={() => queue.refetch()} />
      ) : queue.data.items.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck />}
          title={status === "open" ? "The queue is clear" : `No ${status} reports`}
          description={status === "open" ? "New reports appear here, grouped by the reported item." : "Reports closed with this decision appear here."}
          action={status !== "open" ? <Button variant="secondary" onClick={() => setState({ status: "open" })}>View open reports</Button> : undefined}
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1 text-xs text-subtle">
            <p className="text-sm text-muted" aria-live="polite">
              <span className="tabular font-medium text-fg">{formatNumber(queue.data.total)}</span> reported item{queue.data.total === 1 ? "" : "s"}
            </p>
            <p className="inline-flex items-center gap-1.5">
              <RefreshCw className={cn("h-3 w-3", queue.isFetching && "animate-spin")} aria-hidden />
              Auto-refreshes every minute while open{queue.dataUpdatedAt ? ` · updated ${relativeTime(new Date(queue.dataUpdatedAt))}` : ""}
            </p>
          </div>
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
            {queue.data.items.map((item, i) => <QueueCard key={`${item.target_type}:${item.target_id}`} item={item} status={status} index={i} />)}
          </ul>
          <Pagination page={page} pageSize={PAGE_SIZE} total={queue.data.total} onPage={(p) => setState({ page: String(p) })} />
        </>
      )}
    </>
  );
}

/** Reference of the decisions a moderator can take — restates the ACTIONS table used by the resolve dialog. */
function DecisionGuide() {
  return (
    <aside aria-labelledby="moderation-guide-heading" className="hidden xl:sticky xl:top-[5.5rem] xl:block xl:self-start">
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        <div className="border-b border-border px-5 py-4">
          <p className="text-eyebrow text-subtle">Reference</p>
          <h2 id="moderation-guide-heading" className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-fg">Decisions</h2>
        </div>
        <dl className="divide-y divide-border">
          {(Object.keys(ACTIONS) as Action[]).map((a) => (
            <div key={a} className="px-5 py-2">
              <dt className={cn("flex items-center gap-1.5 text-sm font-medium", ACTIONS[a].danger ? "text-danger" : "text-fg")}>
                {ACTIONS[a].label}
                {ACTIONS[a].danger ? (
                  <>
                    <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
                    <span className="sr-only">(high impact)</span>
                  </>
                ) : null}
              </dt>
              <dd className="mt-0.5 text-xs leading-snug text-muted">{ACTIONS[a].description}</dd>
            </div>
          ))}
        </dl>
        <p className="border-t border-border bg-bg-elevated/40 px-5 py-3.5 text-xs leading-relaxed text-muted">
          Every decision needs a note and is recorded in the audit log. Owners are notified of actions (not of dismissals); reporters are never revealed.
        </p>
      </div>
    </aside>
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
    <Container size="xl">
      <PageHeader
        eyebrow="Trust & safety"
        icon={<ShieldCheck />}
        title="Moderation queue"
        description="Reports are grouped by item, most-reported first. Every decision needs a note and is recorded in the audit log."
        actions={
          <>
            <LinkButton href="/guidelines" variant="ghost" icon={<BookOpen className="h-4 w-4" />}>Guidelines</LinkButton>
            <LinkButton href="/admin/users" variant="secondary" icon={<Users className="h-4 w-4" />}>Users</LinkButton>
          </>
        }
      />
      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,1fr)_288px]">
        <section aria-labelledby="moderation-reports-heading" className="min-w-0 animate-rise [animation-delay:60ms]">
          <h2 id="moderation-reports-heading" className="sr-only">Reports</h2>
          <Suspense fallback={<QueueSkeleton />}>
            <Queue />
          </Suspense>
        </section>
        <DecisionGuide />
      </div>
    </Container>
  );
}
