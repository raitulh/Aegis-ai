"use client";

import { CircleCheck, CornerDownRight, Ellipsis, Eye, EyeOff, Flag, History, Pencil, Reply, Trash2 } from "lucide-react";
import { useState } from "react";

import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime } from "@/lib/format";
import { ReasonDialog, ReportDialog, RevisionsDialog } from "./dialogs";
import type { CommentOut, ThreadViewer } from "./types";
import { useAction } from "./use-action";

type DialogKind = "report" | "delete" | "hide" | "unhide" | "history" | null;

export function CommentItem({
  c,
  threadId,
  viewer,
  meId,
  canReply,
  threadLocked,
  replyToLabel,
  highlighted,
  anchorId,
  onReply,
  invalidate,
}: {
  c: CommentOut;
  threadId: string;
  viewer: ThreadViewer;
  meId: string | null;
  canReply: boolean;
  threadLocked: boolean;
  /** Display name of the comment this one replies to (null when not on this page). */
  replyToLabel: string | null | undefined;
  highlighted?: boolean;
  /** DOM id for deep links (`c-{id}`); omitted for the duplicate shown in the "accepted answer" box. */
  anchorId?: string;
  onReply: (c: CommentOut) => void;
  invalidate: readonly (readonly unknown[])[];
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(c.body_md ?? "");
  const [editError, setEditError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const isAuthor = Boolean(meId && c.author && c.author.id === meId);

  const save = useAction((md: string) => patch<{ message: string }>(`/discussions/comments/${c.id}`, { body_md: md }), {
    success: (d) => d.message,
    invalidate,
    onSuccess: () => {
      setEditing(false);
      setEditError(null);
    },
    onError: (e) => setEditError(e.fields.body_md ?? null),
  });
  const remove = useAction((reason: string) => post<{ message: string }>(`/discussions/comments/${c.id}/delete`, { reason: reason || null }), {
    success: (d) => d.message,
    invalidate,
  });
  const hide = useAction(
    (v: { hidden: boolean; reason: string }) => post<{ message: string }>(`/discussions/comments/${c.id}/hide`, { reason: v.reason || null }, { hidden: v.hidden }),
    { success: (d) => d.message, invalidate },
  );
  const accept = useAction(
    (commentId: string | null) => post<{ message: string }>(`/discussions/threads/${threadId}/accept`, { comment_id: commentId }),
    { success: (d) => d.message, invalidate },
  );

  if (c.deleted) {
    return (
      <div id={anchorId} className="scroll-mt-20 rounded-[var(--radius-lg)] border border-dashed border-border px-4 py-3 text-sm italic text-subtle">
        This reply was deleted.
      </div>
    );
  }

  const canHistory = Boolean(c.edited_at) && (isAuthor || viewer.can_moderate);
  const canReport = viewer.signed_in && !isAuthor;
  const canAccept = viewer.can_accept && !c.hidden;
  const showMenu = canHistory || canReport || c.can_delete || viewer.can_moderate;

  return (
    <article
      id={anchorId}
      tabIndex={-1}
      aria-label={`Reply by ${c.author?.display_name ?? "a deleted user"}`}
      className={cn(
        "scroll-mt-20 rounded-[var(--radius-lg)] border bg-surface p-4 outline-none transition-shadow",
        c.is_accepted ? "border-success/50" : "border-border",
        c.hidden && "border-danger/40 bg-danger-soft/30",
        highlighted && "ring-2 ring-[var(--ring)]",
      )}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <UserLink user={c.author} size={24} className="font-medium" />
        <span className="text-xs text-subtle" aria-hidden>·</span>
        <time className="text-xs text-subtle" dateTime={c.created_at} title={formatDateTime(c.created_at)}>
          {relativeTime(c.created_at)}
        </time>
        {c.edited_at ? (
          <span className="text-xs text-subtle" title={`Edited ${formatDateTime(c.edited_at)}`}>
            (edited)
          </span>
        ) : null}
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {c.is_accepted ? <Badge tone="success" icon={<CircleCheck className="h-3 w-3" aria-hidden />}>Accepted answer</Badge> : null}
          {c.hidden ? <Badge tone="danger" icon={<EyeOff className="h-3 w-3" aria-hidden />}>Hidden by moderators</Badge> : null}
        </span>
      </header>

      {c.reply_to_id ? (
        <a href={`#c-${c.reply_to_id}`} className="mt-2 inline-flex items-center gap-1 text-xs text-muted hover:text-accent-strong">
          <CornerDownRight className="h-3 w-3" aria-hidden />
          {replyToLabel ? `In reply to ${replyToLabel}` : "In reply to an earlier reply"}
        </a>
      ) : null}

      {editing ? (
        <form
          className="mt-3 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim().length < 2) {
              setEditError("Write a reply.");
              return;
            }
            save.mutate(body);
          }}
        >
          <Field label="Edit reply" error={editError} hint="Edits are recorded; moderators can view previous versions.">
            {(p) => <MarkdownEditor {...p} value={body} onChange={setBody} rows={6} maxLength={20_000} />}
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setBody(c.body_md ?? ""); setEditError(null); }}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={save.isPending} disabled={body === (c.body_md ?? "")}>
              Save
            </Button>
          </div>
        </form>
      ) : (
        <Prose html={c.body_html} className="mt-3" />
      )}

      {!editing ? (
        <footer className="mt-3 flex flex-wrap items-center gap-1">
          {canReply ? (
            <Button variant="ghost" size="sm" icon={<Reply className="h-4 w-4" aria-hidden />} onClick={() => onReply(c)}>
              Reply
            </Button>
          ) : null}
          {c.can_edit && c.body_md !== null ? (
            <Button variant="ghost" size="sm" icon={<Pencil className="h-4 w-4" aria-hidden />} onClick={() => { setBody(c.body_md ?? ""); setEditing(true); }}>
              Edit
            </Button>
          ) : null}
          {canAccept ? (
            <Button
              variant="ghost"
              size="sm"
              icon={<CircleCheck className={cn("h-4 w-4", c.is_accepted && "text-success")} aria-hidden />}
              loading={accept.isPending}
              onClick={() => accept.mutate(c.is_accepted ? null : c.id)}
            >
              {c.is_accepted ? "Unaccept" : "Accept answer"}
            </Button>
          ) : null}
          {showMenu ? (
            <Menu modal={false}>
              <MenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="More actions for this reply">
                  <Ellipsis className="h-4 w-4" aria-hidden />
                </Button>
              </MenuTrigger>
              <MenuContent align="start">
                {canHistory ? (
                  <MenuItem onSelect={() => setDialog("history")}>
                    <History className="h-4 w-4" aria-hidden /> View edit history
                  </MenuItem>
                ) : null}
                {canReport ? (
                  <MenuItem onSelect={() => setDialog("report")}>
                    <Flag className="h-4 w-4" aria-hidden /> Report
                  </MenuItem>
                ) : null}
                {c.can_delete ? (
                  <MenuItem destructive onSelect={() => setDialog("delete")}>
                    <Trash2 className="h-4 w-4" aria-hidden /> Delete
                  </MenuItem>
                ) : null}
                {viewer.can_moderate ? (
                  <>
                    <MenuSeparator />
                    <MenuLabel>Moderation</MenuLabel>
                    {c.hidden ? (
                      <MenuItem onSelect={() => setDialog("unhide")}>
                        <Eye className="h-4 w-4" aria-hidden /> Restore reply
                      </MenuItem>
                    ) : (
                      <MenuItem destructive onSelect={() => setDialog("hide")}>
                        <EyeOff className="h-4 w-4" aria-hidden /> Hide reply
                      </MenuItem>
                    )}
                  </>
                ) : null}
              </MenuContent>
            </Menu>
          ) : null}
          {threadLocked && c.can_edit === false && isAuthor ? <span className="ml-1 text-xs text-subtle">Locked — editing disabled</span> : null}
        </footer>
      ) : null}

      <ReportDialog open={dialog === "report"} onOpenChange={(o) => setDialog(o ? "report" : null)} targetType="comment" targetId={c.id} />
      {canHistory ? (
        <RevisionsDialog open={dialog === "history"} onOpenChange={(o) => setDialog(o ? "history" : null)} targetType="comment" targetId={c.id} />
      ) : null}
      <ReasonDialog
        open={dialog === "delete"}
        onOpenChange={(o) => setDialog(o ? "delete" : null)}
        title="Delete this reply?"
        description={isAuthor ? "The reply will be replaced with a “deleted” placeholder." : "You're deleting someone else's reply as a moderator."}
        confirmLabel="Delete reply"
        reason={isAuthor ? "none" : "required"}
        onConfirm={(reason) => remove.mutateAsync(reason)}
      />
      <ReasonDialog
        open={dialog === "hide" || dialog === "unhide"}
        onOpenChange={(o) => setDialog(o ? dialog : null)}
        title={dialog === "unhide" ? "Restore this reply?" : "Hide this reply?"}
        description={dialog === "unhide" ? "It will be visible to everyone again." : "Hidden replies are visible only to their author and moderators."}
        confirmLabel={dialog === "unhide" ? "Restore" : "Hide reply"}
        tone={dialog === "unhide" ? "primary" : "danger"}
        reason="required"
        onConfirm={(reason) => hide.mutateAsync({ hidden: dialog !== "unhide", reason })}
      />
    </article>
  );
}
