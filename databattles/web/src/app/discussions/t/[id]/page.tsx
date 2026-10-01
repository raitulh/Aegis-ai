"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  BellOff,
  ChevronRight,
  CircleCheck,
  Ellipsis,
  Eye,
  EyeOff,
  Flag,
  History,
  Lock,
  LockOpen,
  MessageSquare,
  Pencil,
  Pin,
  PinOff,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CommentItem } from "@/components/discussions/comment-item";
import { ReasonDialog, ReportDialog, RevisionsDialog } from "@/components/discussions/dialogs";
import { ThreadFlags } from "@/components/discussions/thread-list";
import type { CommentOut, ThreadDetail } from "@/components/discussions/types";
import { friendlyError, useAction } from "@/components/discussions/use-action";
import { UserLink } from "@/components/domain/cards";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Container } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { ErrorState, InlineNotice, NotFoundState, SignInPrompt, Skeleton } from "@/components/ui/states";
import { ApiError, get, patch, post } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { hasRole, useDraft, useMe, useUnsavedChangesWarning } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { useUrlState } from "@/lib/url-state";

const PAGE_SIZE = 30;
const DEFAULTS = { page: "1" };

type ModAction = "lock" | "unlock" | "pin" | "unpin" | "hide" | "unhide";
type ThreadDialog = { kind: "report" } | { kind: "delete" } | { kind: "history" } | { kind: "moderate"; action: ModAction } | null;

const MOD_COPY: Record<ModAction, { title: string; description: string; confirm: string; tone: "danger" | "primary" }> = {
  lock: { title: "Lock this discussion?", description: "No one except moderators will be able to reply or edit replies.", confirm: "Lock", tone: "primary" },
  unlock: { title: "Unlock this discussion?", description: "Participants will be able to reply again.", confirm: "Unlock", tone: "primary" },
  pin: { title: "Pin this discussion?", description: "It will stay at the top of the list.", confirm: "Pin", tone: "primary" },
  unpin: { title: "Unpin this discussion?", description: "It will be sorted normally again.", confirm: "Unpin", tone: "primary" },
  hide: { title: "Hide this discussion?", description: "Only the author and moderators will be able to see it.", confirm: "Hide discussion", tone: "danger" },
  unhide: { title: "Restore this discussion?", description: "It will be visible to everyone who can see this forum.", confirm: "Restore", tone: "primary" },
};

function ThreadSkeleton() {
  return (
    <Container size="lg" className="py-8">
      <div role="status" aria-label="Loading discussion">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="mt-6 h-8 w-3/4" />
        <Skeleton className="mt-3 h-4 w-1/3" />
        <div className="mt-8 space-y-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-4 w-full" />)}
        </div>
        <div className="mt-10 space-y-4">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-28 w-full rounded-[var(--radius-lg)]" />)}
        </div>
      </div>
    </Container>
  );
}

function ReplyForm({
  threadId,
  replyTo,
  onCancelReplyTo,
  onPosted,
}: {
  threadId: string;
  replyTo: CommentOut | null;
  onCancelReplyTo: () => void;
  onPosted: (id: string) => void | Promise<void>;
}) {
  const [body, setBody, clearDraft] = useDraft(`reply:${threadId}`, "");
  const [error, setError] = useState<ApiError | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  useUnsavedChangesWarning(body.trim().length > 0);
  const send = useAction(
    (v: { body_md: string; reply_to_id: string | null }) => post<{ id: string }>(`/discussions/threads/${threadId}/comments`, v),
    {
      success: "Reply posted",
      toastErrors: false,
      onSuccess: (d) => {
        setBody("");
        clearDraft();
        setError(null);
        onCancelReplyTo();
        void onPosted(d.id);
      },
      onError: (e) => setError(e),
    },
  );
  return (
    <form
      id="reply"
      className="scroll-mt-20 space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (body.trim().length < 2) {
          setLocalError("Write a reply first.");
          return;
        }
        setLocalError(null);
        send.mutate({ body_md: body, reply_to_id: replyTo?.id ?? null });
      }}
      aria-labelledby="reply-heading"
    >
      <h2 id="reply-heading" className="text-lg font-semibold text-fg">Add a reply</h2>
      {replyTo ? (
        <div className="flex items-center justify-between gap-2 rounded-[var(--radius-md)] border border-border bg-surface-2 px-3 py-2 text-sm">
          <span className="min-w-0 truncate text-muted">
            Replying to <a href={`#c-${replyTo.id}`} className="font-medium text-fg hover:text-accent-strong">{replyTo.author?.display_name ?? "a reply"}</a>
          </span>
          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Cancel replying to this comment" onClick={onCancelReplyTo}>
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      ) : null}
      <Field
        label="Your reply"
        error={localError ?? error?.fields.body_md}
        hint="Markdown supported. Mention someone with @handle to notify them. Your draft is saved on this device."
      >
        {(p) => <MarkdownEditor {...p} value={body} onChange={setBody} rows={6} maxLength={20_000} placeholder="Share an answer, a hint or a follow-up question…" />}
      </Field>
      {error && !Object.keys(error.fields).length ? <FormError message={friendlyError(error)} /> : null}
      <div className="flex justify-end">
        <Button type="submit" loading={send.isPending} icon={<MessageSquare className="h-4 w-4" aria-hidden />}>
          Post reply
        </Button>
      </div>
    </form>
  );
}

function ThreadEditForm({ thread, onDone }: { thread: ThreadDetail; onDone: () => void }) {
  const [title, setTitle] = useState(thread.title);
  const [body, setBody] = useState(thread.body_md ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const dirty = title !== thread.title || body !== (thread.body_md ?? "");
  useUnsavedChangesWarning(dirty);
  const save = useAction((v: { title: string; body_md: string }) => patch<{ message: string }>(`/discussions/threads/${thread.id}`, v), {
    success: (d) => d.message,
    invalidate: [["discussions"]],
    onSuccess: () => onDone(),
    onError: (e) => setErrors(e.fields),
  });
  return (
    <form
      noValidate
      className="mt-6 space-y-4 rounded-[var(--radius-lg)] border border-border bg-surface p-4"
      onSubmit={(e) => {
        e.preventDefault();
        const local: Record<string, string> = {};
        if (title.trim().length < 5) local.title = "Use at least 5 characters.";
        if (body.trim().length < 10) local.body_md = "Add a little more detail (10+ characters).";
        setErrors(local);
        if (Object.keys(local).length) return;
        save.mutate({ title: title.trim(), body_md: body });
      }}
      aria-label="Edit discussion"
    >
      <Field label="Title" required error={errors.title}>
        {(p) => <Input {...p} value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} />}
      </Field>
      <Field label="Details" required error={errors.body_md} hint={thread.edit_note}>
        {(p) => <MarkdownEditor {...p} value={body} onChange={setBody} rows={10} maxLength={40_000} />}
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button type="submit" loading={save.isPending} disabled={!dirty}>Save changes</Button>
      </div>
    </form>
  );
}

function ThreadView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const [state, set] = useUrlState(DEFAULTS);
  const page = Math.max(1, Number(state.page) || 1);
  const thread = useQuery({
    queryKey: qk.thread(id, page),
    queryFn: () => get<ThreadDetail>(`/discussions/threads/${id}`, { page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const invalidate = useMemo(() => [["discussions", "thread", id], ["discussions", "threads"]] as const, [id]);

  const [dialog, setDialog] = useState<ThreadDialog>(null);
  const [editing, setEditing] = useState(false);
  const [replyTo, setReplyTo] = useState<CommentOut | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  // `waitPage`: don't search until that page has loaded (set when we navigate to it ourselves).
  const [seek, setSeek] = useState<{ id: string; restarted: boolean; hops: number; waitPage?: number } | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Deep links (#c-<commentId>, e.g. from notifications): find the comment across pages, scroll to it and highlight it.
  useEffect(() => {
    const read = () => {
      const h = decodeURIComponent(window.location.hash.slice(1));
      if (h.startsWith("c-")) setSeek({ id: h, restarted: false, hops: 0 });
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  useEffect(() => {
    if (!seek || !thread.data || thread.isPlaceholderData || thread.isFetching) return;
    if (seek.waitPage !== undefined && thread.data.comments.page !== seek.waitPage) return;
    const el = document.getElementById(seek.id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      el.focus({ preventScroll: true });
      setHighlight(seek.id);
      if (highlightTimer.current) clearTimeout(highlightTimer.current);
      highlightTimer.current = setTimeout(() => setHighlight(null), 3000);
      setSeek(null);
      return;
    }
    const comments = thread.data.comments;
    if (!seek.restarted && comments.page !== 1) {
      setSeek({ ...seek, restarted: true, waitPage: 1 });
      set({ page: "1" });
    } else if (comments.has_next && seek.hops < 50) {
      setSeek({ ...seek, restarted: true, hops: seek.hops + 1, waitPage: comments.page + 1 });
      set({ page: String(comments.page + 1) });
    } else {
      setSeek(null);
    }
  }, [seek, thread.data, thread.isPlaceholderData, thread.isFetching, set]);

  useEffect(() => () => {
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
  }, []);

  const mute = useAction((muted: boolean) => post<{ message: string }>(`/discussions/threads/${id}/mute`, undefined, { muted }), {
    success: (d) => d.message,
    invalidate: [["discussions", "thread", id]],
  });
  const moderate = useAction(
    (v: { action: ModAction; reason: string }) => post<{ message: string }>(`/discussions/threads/${id}/moderate`, { action: v.action, reason: v.reason || null }),
    { success: (d) => d.message, invalidate },
  );
  const remove = useAction((reason: string) => post<{ message: string }>(`/discussions/threads/${id}/delete`, { reason: reason || null }), {
    success: (d) => d.message,
    invalidate: [["discussions"]],
  });

  if (thread.isPending) return <ThreadSkeleton />;
  if (thread.isError) {
    return (
      <Container size="lg" className="py-12">
        {thread.error instanceof ApiError && (thread.error.status === 404 || thread.error.status === 422) ? (
          <NotFoundState what="discussion" />
        ) : (
          <ErrorState error={thread.error} onRetry={() => thread.refetch()} />
        )}
      </Container>
    );
  }

  const t = thread.data;
  const v = t.viewer;
  const meId = me.data?.id ?? null;
  const isAuthor = Boolean(meId && t.author && t.author.id === meId);
  const canDelete = !t.deleted && (v.can_edit || v.can_moderate);
  const canHistory = Boolean(t.edited_at) && (isAuthor || v.can_moderate);
  const canReport = v.signed_in && !isAuthor && !t.deleted;
  // Hiding a thread is reserved for platform moderators (and organizers, in competition forums).
  const canHide = v.can_moderate && (hasRole(me.data, "moderator") || Boolean(t.competition));
  // The server lets moderators post in locked threads even though `can_reply` is false.
  const canReply = !t.deleted && v.signed_in && (v.can_reply || (v.can_moderate && t.locked));
  const comments = t.comments;
  const byId = new Map(comments.items.map((c) => [c.id, c]));
  const backHref = t.competition ? `/competitions/${t.competition.slug}` : t.project ? `/projects/${t.project.slug}` : t.category ? `/discussions/c/${t.category.slug}` : "/discussions";

  const startReply = (c: CommentOut) => {
    setReplyTo(c);
    requestAnimationFrame(() => {
      document.getElementById("reply")?.scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelector<HTMLTextAreaElement>("#reply textarea")?.focus({ preventScroll: true });
    });
  };
  const onPosted = async (newId: string) => {
    const lastPage = Math.max(1, Math.ceil((comments.total + 1) / comments.page_size));
    void qc.invalidateQueries({ queryKey: ["discussions", "threads"] });
    if (lastPage !== page) {
      setSeek({ id: `c-${newId}`, restarted: true, hops: 0, waitPage: lastPage });
      set({ page: String(lastPage) });
      void qc.invalidateQueries({ queryKey: ["discussions", "thread", id] });
    } else {
      await qc.invalidateQueries({ queryKey: ["discussions", "thread", id] });
      setSeek({ id: `c-${newId}`, restarted: true, hops: 0 });
    }
  };

  const modItems: { action: ModAction; label: string; icon: ReactNode; show: boolean }[] = [
    { action: t.locked ? "unlock" : "lock", label: t.locked ? "Unlock discussion" : "Lock discussion", icon: t.locked ? <LockOpen className="h-4 w-4" aria-hidden /> : <Lock className="h-4 w-4" aria-hidden />, show: true },
    { action: t.pinned ? "unpin" : "pin", label: t.pinned ? "Unpin" : "Pin to top", icon: t.pinned ? <PinOff className="h-4 w-4" aria-hidden /> : <Pin className="h-4 w-4" aria-hidden />, show: true },
    { action: t.hidden ? "unhide" : "hide", label: t.hidden ? "Restore discussion" : "Hide discussion", icon: t.hidden ? <Eye className="h-4 w-4" aria-hidden /> : <EyeOff className="h-4 w-4" aria-hidden />, show: canHide },
  ];
  const modDialog = dialog?.kind === "moderate" ? dialog.action : null;
  const showMenu = canHistory || canReport || canDelete || v.can_moderate;

  return (
    <Container size="lg" className="pb-16">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 pt-6 text-sm text-muted">
        {t.competition ? (
          <>
            <Link href="/competitions" className="hover:text-fg">Competitions</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/competitions/${t.competition.slug}`} className="max-w-[16rem] truncate hover:text-fg">{t.competition.title}</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <span>Discussion</span>
          </>
        ) : t.project ? (
          <>
            <Link href="/projects" className="hover:text-fg">Projects</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/projects/${t.project.slug}`} className="max-w-[16rem] truncate hover:text-fg">{t.project.title}</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <span>Discussion</span>
          </>
        ) : (
          <>
            <Link href="/discussions" className="hover:text-fg">Discussions</Link>
            {t.category ? (
              <>
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                <Link href={`/discussions/c/${t.category.slug}`} className="hover:text-fg">{t.category.name}</Link>
              </>
            ) : null}
          </>
        )}
      </nav>

      <div className="mt-4 space-y-3">
        {t.deleted ? (
          <InlineNotice tone="danger" title="This discussion was deleted">Its content is no longer available.</InlineNotice>
        ) : null}
        {t.hidden && !t.deleted ? (
          <InlineNotice tone="warning" title="Hidden by moderators">Only the author and moderators can see this discussion.</InlineNotice>
        ) : null}
        {t.locked && !t.deleted ? (
          <InlineNotice tone="info" title="Locked">
            This discussion is locked — new replies {v.can_moderate ? "are limited to moderators" : "are disabled"}.
          </InlineNotice>
        ) : null}
      </div>

      <article className="mt-6" aria-labelledby="thread-title">
        <header>
          <ThreadFlags t={{ ...t, has_accepted_answer: Boolean(t.accepted_comment) }} className="mb-2" />
          <h1 id="thread-title" className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{t.title}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
            {t.deleted ? <span className="text-subtle">Deleted</span> : <UserLink user={t.author} size={28} className="font-medium" />}
            <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>{relativeTime(t.created_at)}</time>
            {t.edited_at ? (
              <span className="inline-flex items-center gap-1 text-subtle">
                <span title={`Edited ${formatDateTime(t.edited_at)}`}>edited {relativeTime(t.edited_at)}</span>
                {canHistory ? (
                  <button type="button" className="text-accent-strong hover:underline" onClick={() => setDialog({ kind: "history" })}>
                    view history
                  </button>
                ) : null}
              </span>
            ) : null}
            <div className="ml-auto flex flex-wrap items-center gap-1">
              {v.signed_in && !t.deleted ? (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={mute.isPending}
                  onClick={() => mute.mutate(!v.muted)}
                  aria-pressed={v.muted}
                  icon={v.muted ? <BellOff className="h-4 w-4" aria-hidden /> : <Bell className="h-4 w-4" aria-hidden />}
                  title={v.muted ? "You won't get reply notifications for this discussion" : "Stop reply notifications for this discussion"}
                >
                  {v.muted ? "Muted" : "Mute"}
                </Button>
              ) : null}
              {v.can_edit && !editing ? (
                <Button variant="ghost" size="sm" icon={<Pencil className="h-4 w-4" aria-hidden />} onClick={() => setEditing(true)}>
                  Edit
                </Button>
              ) : null}
              {showMenu ? (
                <Menu modal={false}>
                  <MenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="More discussion actions">
                      <Ellipsis className="h-4 w-4" aria-hidden />
                    </Button>
                  </MenuTrigger>
                  <MenuContent>
                    {canHistory ? (
                      <MenuItem onSelect={() => setDialog({ kind: "history" })}>
                        <History className="h-4 w-4" aria-hidden /> View edit history
                      </MenuItem>
                    ) : null}
                    {canReport ? (
                      <MenuItem onSelect={() => setDialog({ kind: "report" })}>
                        <Flag className="h-4 w-4" aria-hidden /> Report
                      </MenuItem>
                    ) : null}
                    {canDelete ? (
                      <MenuItem destructive onSelect={() => setDialog({ kind: "delete" })}>
                        <Trash2 className="h-4 w-4" aria-hidden /> Delete discussion
                      </MenuItem>
                    ) : null}
                    {v.can_moderate && !t.deleted ? (
                      <>
                        <MenuSeparator />
                        <MenuLabel>Moderation</MenuLabel>
                        {modItems.filter((m) => m.show).map((m) => (
                          <MenuItem
                            key={m.action}
                            destructive={m.action === "hide"}
                            onSelect={() => {
                              if (m.action === "pin" || m.action === "unpin") moderate.mutate({ action: m.action, reason: "" });
                              else setDialog({ kind: "moderate", action: m.action });
                            }}
                          >
                            {m.icon} {m.label}
                          </MenuItem>
                        ))}
                      </>
                    ) : null}
                  </MenuContent>
                </Menu>
              ) : null}
            </div>
          </div>
        </header>

        {editing ? (
          <ThreadEditForm thread={t} onDone={() => setEditing(false)} />
        ) : t.body_html ? (
          <Prose html={t.body_html} className="mt-6" />
        ) : null}
      </article>

      {t.accepted_comment && !t.accepted_comment.deleted ? (
        <section aria-labelledby="accepted-heading" className="mt-8 rounded-[var(--radius-lg)] border border-success/40 bg-success-soft/40 p-1">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <h2 id="accepted-heading" className="inline-flex items-center gap-1.5 text-sm font-semibold text-success">
              <CircleCheck className="h-4 w-4" aria-hidden /> Accepted answer
            </h2>
            <a href={`#c-${t.accepted_comment.id}`} className="text-xs text-muted hover:text-accent-strong">Jump to reply</a>
          </div>
          <CommentItem
            c={t.accepted_comment}
            threadId={t.id}
            viewer={v}
            meId={meId}
            canReply={false}
            threadLocked={t.locked}
            replyToLabel={t.accepted_comment.reply_to_id ? byId.get(t.accepted_comment.reply_to_id)?.author?.display_name : null}
            onReply={startReply}
            invalidate={invalidate}
          />
        </section>
      ) : null}

      <section aria-labelledby="replies-heading" className="mt-10" aria-busy={thread.isFetching || undefined}>
        <h2 id="replies-heading" className="mb-4 text-lg font-semibold text-fg">
          {comments.total} {comments.total === 1 ? "reply" : "replies"}
        </h2>
        {comments.items.length === 0 ? (
          <p className="rounded-[var(--radius-lg)] border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
            {t.deleted ? "No replies." : canReply ? "No replies yet — be the first to help." : "No replies yet."}
          </p>
        ) : (
          <ol className="space-y-4">
            {comments.items.map((c) => (
              <li key={c.id}>
                <CommentItem
                  c={c}
                  anchorId={`c-${c.id}`}
                  threadId={t.id}
                  viewer={v}
                  meId={meId}
                  canReply={canReply}
                  threadLocked={t.locked}
                  replyToLabel={c.reply_to_id ? (byId.get(c.reply_to_id)?.author?.display_name ?? null) : null}
                  highlighted={highlight === `c-${c.id}`}
                  onReply={startReply}
                  invalidate={invalidate}
                />
              </li>
            ))}
          </ol>
        )}
        <Pagination
          page={comments.page}
          pageSize={comments.page_size}
          total={comments.total}
          onPage={(p) => {
            set({ page: String(p) });
            document.getElementById("replies-heading")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        />
      </section>

      <div className="mt-10 border-t border-border pt-8">
        {t.deleted ? null : canReply ? (
          <ReplyForm threadId={t.id} replyTo={replyTo} onCancelReplyTo={() => setReplyTo(null)} onPosted={onPosted} />
        ) : !v.signed_in ? (
          <SignInPrompt text="Sign in to join the discussion." />
        ) : t.locked ? (
          <p className="inline-flex items-center gap-2 text-sm text-muted"><Lock className="h-4 w-4" aria-hidden /> This discussion is locked. New replies are disabled.</p>
        ) : (
          <p className="text-sm text-muted">You can&apos;t reply to this discussion right now.</p>
        )}
      </div>

      <ReportDialog open={dialog?.kind === "report"} onOpenChange={(o) => setDialog(o ? { kind: "report" } : null)} targetType="thread" targetId={t.id} />
      {canHistory ? (
        <RevisionsDialog open={dialog?.kind === "history"} onOpenChange={(o) => setDialog(o ? { kind: "history" } : null)} targetType="thread" targetId={t.id} />
      ) : null}
      <ReasonDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(o) => setDialog(o ? { kind: "delete" } : null)}
        title="Delete this discussion?"
        description={isAuthor ? "The discussion and its content will be removed for everyone." : "You're deleting someone else's discussion as a moderator."}
        confirmLabel="Delete discussion"
        reason={isAuthor ? "none" : "required"}
        onConfirm={async (reason) => {
          await remove.mutateAsync(reason);
          router.push(backHref);
        }}
      />
      <ReasonDialog
        open={modDialog !== null}
        onOpenChange={(o) => {
          if (!o) setDialog(null);
        }}
        title={modDialog ? MOD_COPY[modDialog].title : ""}
        description={modDialog ? MOD_COPY[modDialog].description : undefined}
        confirmLabel={modDialog ? MOD_COPY[modDialog].confirm : "Confirm"}
        tone={modDialog ? MOD_COPY[modDialog].tone : "primary"}
        reason={modDialog === "hide" ? "required" : "optional"}
        onConfirm={(reason) => (modDialog ? moderate.mutateAsync({ action: modDialog, reason }) : Promise.resolve())}
      />
    </Container>
  );
}

export default function ThreadPage() {
  return (
    <Suspense>
      <ThreadView />
    </Suspense>
  );
}
