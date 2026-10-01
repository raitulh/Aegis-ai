"use client";

import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/form";
import { ErrorState, EmptyState, SkeletonRows } from "@/components/ui/states";
import { get, post } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { REPORT_REASONS, type ReportReason, type Revision } from "./types";
import { useAction } from "./use-action";

/**
 * Controlled confirmation with an optional/required reason — for actions opened from menus
 * (the shared ConfirmDialog needs its own trigger element).
 */
export function ReasonDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  tone = "danger",
  reason = "none",
  reasonLabel = "Reason (recorded in the audit log)",
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "primary";
  reason?: "none" | "optional" | "required";
  reasonLabel?: string;
  onConfirm: (reason: string) => Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) setText("");
  };
  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={() => close(false)}>Cancel</Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            loading={busy}
            disabled={reason === "required" && text.trim().length < 3}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm(text.trim());
                close(false);
              } catch {
                /* the mutation already showed feedback; keep the dialog open to retry */
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {reason !== "none" ? (
        <Field label={reason === "required" ? reasonLabel : `${reasonLabel} — optional`} hint={reason === "required" ? "At least 3 characters." : undefined}>
          {(p) => <Textarea {...p} value={text} onChange={(e) => setText(e.target.value)} maxLength={500} rows={3} />}
        </Field>
      ) : null}
    </Dialog>
  );
}

export function ReportDialog({
  open,
  onOpenChange,
  targetType,
  targetId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  targetType: "thread" | "comment";
  targetId: string;
}) {
  const [reason, setReason] = useState<ReportReason | "">("");
  const [details, setDetails] = useState("");
  const report = useAction(
    () => post<{ message: string }>("/reports", { target_type: targetType, target_id: targetId, reason, details: details.trim() || null }),
    {
      success: (d) => d.message,
      onSuccess: () => {
        onOpenChange(false);
        setReason("");
        setDetails("");
      },
    },
  );
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={targetType === "thread" ? "Report this discussion" : "Report this reply"}
      description="Reports are reviewed by moderators. The author won't see who reported it."
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="danger" loading={report.isPending} disabled={!reason} onClick={() => report.mutate()}>
            Send report
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Reason" required error={report.error?.fields?.reason}>
          {(p) => (
            <Select {...p} value={reason} onChange={(e) => setReason(e.target.value as ReportReason)}>
              <option value="" disabled>Choose a reason…</option>
              {REPORT_REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Details" hint="Optional context for moderators (up to 1,000 characters)." error={report.error?.fields?.details}>
          {(p) => <Textarea {...p} value={details} onChange={(e) => setDetails(e.target.value)} maxLength={1000} rows={4} />}
        </Field>
      </div>
    </Dialog>
  );
}

/** Previous versions of a post. Raw Markdown is shown as plain text — never rendered as HTML. */
export function RevisionsDialog({
  open,
  onOpenChange,
  targetType,
  targetId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  targetType: "thread" | "comment";
  targetId: string;
}) {
  const revisions = useQuery({
    queryKey: ["discussions", "revisions", targetType, targetId],
    queryFn: () => get<Revision[]>(`/discussions/revisions/${targetType}/${targetId}`),
    enabled: open,
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Edit history"
      description="Earlier versions of this post, newest first. Only the author and moderators can see them."
      size="lg"
    >
      {revisions.isPending ? (
        <SkeletonRows rows={3} />
      ) : revisions.isError ? (
        <ErrorState error={revisions.error} onRetry={() => revisions.refetch()} />
      ) : revisions.data.length === 0 ? (
        <EmptyState title="No earlier versions" description="This post hasn't been edited." />
      ) : (
        <ol className="space-y-4">
          {revisions.data.map((r, i) => (
            <li key={r.id} className="rounded-[var(--radius-md)] border border-border bg-bg-elevated">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs text-muted">
                <span className="font-medium text-fg">Version {revisions.data.length - i}</span>
                <time dateTime={r.created_at} title={formatDateTime(r.created_at)}>Replaced {relativeTime(r.created_at)}</time>
              </div>
              <div className="px-3 py-2">
                {r.title ? <p className="mb-2 text-sm font-semibold text-fg">{r.title}</p> : null}
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-muted">{r.body_md || "(empty)"}</pre>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
