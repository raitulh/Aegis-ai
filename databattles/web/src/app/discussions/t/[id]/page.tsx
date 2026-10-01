"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  BellOff,
  CalendarDays,
  ChevronRight,
  CircleCheck,
  Ellipsis,
  Eye,
  EyeOff,
  Flag,
  Hash,
  History,
  Lock,
  LockOpen,
  MessageSquare,
  MessagesSquare,
  Pencil,
  Pin,
  PinOff,
  Trash2,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CommentItem } from "@/components/discussions/comment-item";
import { ReasonDialog, ReportDialog, RevisionsDialog } from "@/components/discussions/dialogs";
import { CategoryIcon, ThreadFlags } from "@/components/discussions/thread-list";
import type { CommentOut, ThreadDetail } from "@/components/discussions/types";
import { friendlyError, useAction } from "@/components/discussions/use-action";
import { UserLink } from "@/components/domain/cards";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { MetaItem } from "@/components/ui/extras";
import { Field, FormError, Input } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Container } from "@/components/ui/page";
import { Pagination } from "@/components/ui/pagination";
import { ErrorState, InlineNotice, NotFoundState, SignInPrompt, Skeleton } from "@/components/ui/states";
import { ApiError, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, relativeTime } from "@/lib/format";
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
    <Container className="py-8">
      <div role="status" aria-label="Loading discussion" className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_17rem] xl:gap-14">
        <div>
          <Skeleton className="h-4 w-48" />
          <Skeleton className="mt-8 h-5 w-24 rounded-full" />
          <Skeleton className="mt-4 h-9 w-3/4" />
          <div className="mt-4 flex items-center gap-2">
            <Skeleton className="h-7 w-7 rounded-full" />
            <Skeleton className="h-3.5 w-40" />
          </div>
          <div className="mt-6 space-y-3 rounded-[var(--radius-xl)] border border-border bg-surface p-6">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className={cn("h-3.5", i === 4 ? "w-2/3" : "w-full")} />)}
          </div>
          <div className="mt-12 space-y-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-4">
                <Skeleton className="mx-auto mt-3 h-8 w-8 rounded-full" />
                <Skeleton className="h-28 w-full rounded-[var(--radius-lg)]" />
              </div>
            ))}
          </div>
        </div>
        <Skeleton className="hidden h-72 w-full rounded-[var(--radius-xl)] lg:block" />
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
      <h2 id="reply-heading" className="text-base font-semibold tracking-[-0.01em] text-fg">Add a reply</h2>
      {replyTo ? (
        <div className="flex items-center justify-between gap-2 rounded-[var(--radius-md)] border border-[color-mix(in_oklab,var(--accent)_30%,transparent)] bg-accent-soft px-3 py-1.5 text-sm animate-slide-down">
          <span className="min-w-0 truncate text-muted">
            Replying to <a href={`#c-${replyTo.id}`} className="font-medium text-fg hover:text-accent-strong">{replyTo.author?.display_name ?? "a reply"}</a>
          </span>
          <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Cancel replying to this comment" onClick={onCancelReplyTo}>
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
      className="mt-6 space-y-4 rounded-[var(--radius-xl)] border border-[color-mix(in_oklab,var(--accent)_30%,var(--border))] bg-surface surface-sheen p-4 shadow-card sm:p-6"
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
      <Container size="lg" className="py-16">
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

  // Presentational extras derived from data already on the page.
  const participants = (() => {
    const seen = new Map<string, { id: string; display_name: string; avatar_url?: string | null }>();
    if (t.author) seen.set(t.author.id, t.author);
    for (const c of comments.items) if (c.author && !c.deleted) seen.set(c.author.id, c.author);
    return [...seen.values()];
  })();
  const multiPage = comments.total > comments.items.length;
  const opId = t.author?.id ?? null;
  const scope = t.competition
    ? { label: t.competition.title, href: `/competitions/${t.competition.slug}`, kind: "Competition" }
    : t.project
      ? { label: t.project.title, href: `/projects/${t.project.slug}`, kind: "Project" }
      : t.category
        ? { label: t.category.name, href: `/discussions/c/${t.category.slug}`, kind: "Category" }
        : null;

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 pt-6 text-sm text-subtle">
        {t.competition ? (
          <>
            <Link href="/competitions" className="hover:text-fg">Competitions</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/competitions/${t.competition.slug}`} className="max-w-[16rem] truncate hover:text-fg">{t.competition.title}</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <span className="text-muted">Discussion</span>
          </>
        ) : t.project ? (
          <>
            <Link href="/projects" className="hover:text-fg">Projects</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/projects/${t.project.slug}`} className="max-w-[16rem] truncate hover:text-fg">{t.project.title}</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <span className="text-muted">Discussion</span>
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

      <div className="mt-6 grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_17rem] xl:gap-14">
        <div className="min-w-0">
          <div className="mb-6 space-y-3 empty:hidden">
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

          <article aria-labelledby="thread-title">
            <header className="relative isolate animate-rise">
              <div
                aria-hidden
                className="pointer-events-none absolute -left-24 -top-12 -z-10 h-48 w-[26rem] max-w-full"
                style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
              />
              <ThreadFlags t={{ ...t, has_accepted_answer: Boolean(t.accepted_comment) }} className="mb-3" />
              <h1 id="thread-title" className="text-title text-fg [overflow-wrap:anywhere]">{t.title}</h1>
              <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
                {t.deleted ? <span className="text-subtle">Deleted</span> : <UserLink user={t.author} size={28} className="font-medium" />}
                <span className="text-subtle" aria-hidden>·</span>
                <time dateTime={t.created_at} title={formatDateTime(t.created_at)} className="text-subtle">{relativeTime(t.created_at)}</time>
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
                      className="max-sm:h-9"
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
                    <Button variant="ghost" size="sm" className="max-sm:h-9" icon={<Pencil className="h-4 w-4" aria-hidden />} onClick={() => setEditing(true)}>
                      Edit
                    </Button>
                  ) : null}
                  {showMenu ? (
                    <Menu modal={false}>
                      <MenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-9 w-9" aria-label="More discussion actions">
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
              <div className="relative mt-6 overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-5 shadow-card animate-rise [animation-delay:60ms] sm:p-7">
                <span aria-hidden className="absolute inset-y-6 left-0 w-0.5 rounded-full bg-brand opacity-70" />
                <Prose html={t.body_html} />
              </div>
            ) : null}
          </article>

          {t.accepted_comment && !t.accepted_comment.deleted ? (
            <section
              aria-labelledby="accepted-heading"
              className="mt-8 overflow-hidden rounded-[var(--radius-xl)] border border-[color-mix(in_oklab,var(--success)_40%,transparent)] bg-[linear-gradient(180deg,var(--success-soft),transparent_70%)] p-1.5 shadow-card"
            >
              <div className="flex items-center justify-between gap-2 px-3 pb-1 pt-2">
                <h2 id="accepted-heading" className="inline-flex items-center gap-1.5 text-sm font-semibold text-success">
                  <CircleCheck className="h-4 w-4" aria-hidden /> Accepted answer
                </h2>
                <a href={`#c-${t.accepted_comment.id}`} className="inline-flex min-h-8 items-center text-xs text-muted hover:text-accent-strong">Jump to reply</a>
              </div>
              <div className="rounded-[var(--radius-lg)] bg-surface/70">
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
                  opId={opId}
                  variant="featured"
                />
              </div>
            </section>
          ) : null}

          <section aria-labelledby="replies-heading" className="mt-12" aria-busy={thread.isFetching || undefined}>
            <div className="mb-5 flex items-end justify-between gap-3 border-b border-border pb-3">
              <h2 id="replies-heading" className="scroll-mt-24 text-lg font-semibold tracking-[-0.02em] text-fg">
                <span className="tabular">{comments.total}</span> {comments.total === 1 ? "reply" : "replies"}
              </h2>
              {canReply ? (
                <a href="#reply" className="inline-flex min-h-8 items-center gap-1 text-sm text-accent-strong hover:underline">
                  <MessageSquare className="h-3.5 w-3.5" aria-hidden /> Write a reply
                </a>
              ) : null}
            </div>
            {comments.items.length === 0 ? (
              <div className="rounded-[var(--radius-lg)] border border-dashed border-border-strong bg-surface/40 px-4 py-10 text-center">
                <MessagesSquare className="mx-auto h-5 w-5 text-subtle" aria-hidden />
                <p className="mt-2 text-sm text-muted">
                  {t.deleted ? "No replies." : canReply ? "No replies yet — be the first to help." : "No replies yet."}
                </p>
              </div>
            ) : (
              <ol className="relative space-y-5 before:pointer-events-none before:absolute before:bottom-6 before:left-5 before:top-6 before:w-px before:bg-[linear-gradient(to_bottom,var(--border-strong),var(--border)_85%,transparent)]">
                {comments.items.map((c) => {
                  const parentOnPage = c.reply_to_id ? byId.has(c.reply_to_id) : false;
                  return (
                    <li key={c.id} className={cn("relative", c.reply_to_id && "pl-6 sm:pl-10")}>
                      {c.reply_to_id ? (
                        <span
                          aria-hidden
                          className={cn(
                            "absolute left-5 top-0 h-7 w-4 rounded-bl-xl border-b border-l sm:w-8",
                            parentOnPage ? "border-border-strong" : "border-dashed border-border-strong",
                          )}
                        />
                      ) : null}
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
                        opId={opId}
                      />
                    </li>
                  );
                })}
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

          <div className="mt-10">
            {t.deleted ? null : canReply ? (
              <div className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 sm:gap-x-4">
                <div className="flex items-start justify-center pt-4" aria-hidden>
                  {me.data ? (
                    <span className="flex rounded-full bg-bg p-[3px]">
                      <Avatar name={me.data.display_name} src={me.data.avatar_url} size={34} />
                    </span>
                  ) : null}
                </div>
                <div className="min-w-0 rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-4 shadow-card sm:p-5">
                  <ReplyForm threadId={t.id} replyTo={replyTo} onCancelReplyTo={() => setReplyTo(null)} onPosted={onPosted} />
                </div>
              </div>
            ) : !v.signed_in ? (
              <div className="flex flex-col gap-3 rounded-[var(--radius-xl)] border border-border bg-surface/60 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                <SignInPrompt text="Sign in to join the discussion." />
              </div>
            ) : t.locked ? (
              <p className="inline-flex items-center gap-2 rounded-[var(--radius-lg)] border border-border bg-surface/60 px-4 py-3 text-sm text-muted"><Lock className="h-4 w-4" aria-hidden /> This discussion is locked. New replies are disabled.</p>
            ) : (
              <p className="rounded-[var(--radius-lg)] border border-border bg-surface/60 px-4 py-3 text-sm text-muted">You can&apos;t reply to this discussion right now.</p>
            )}
          </div>
        </div>

        <aside aria-label="About this discussion" className="hidden min-w-0 lg:block">
          <div className="sticky top-24 space-y-5">
            <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card">
              <dl className="grid grid-cols-1 divide-y divide-border">
                {scope ? (
                  <MetaItem label={scope.kind} icon={t.category && !t.competition && !t.project ? <CategoryIcon slug={t.category.slug} /> : <Hash />} className="px-5 py-3.5">
                    <Link href={scope.href} className="hover:text-accent-strong">{scope.label}</Link>
                  </MetaItem>
                ) : null}
                <MetaItem label="Started" icon={<CalendarDays />} className="px-5 py-3.5">
                  <time dateTime={t.created_at} title={formatDateTime(t.created_at)}>{formatDate(t.created_at)}</time>
                </MetaItem>
                <MetaItem label="Last activity" icon={<Activity />} className="px-5 py-3.5">
                  <time dateTime={t.last_activity_at} title={formatDateTime(t.last_activity_at)}>{relativeTime(t.last_activity_at)}</time>
                </MetaItem>
                <MetaItem label="Replies" icon={<MessageSquare />} className="px-5 py-3.5">
                  <span className="tabular">{comments.total}</span>
                  {t.accepted_comment ? <span className="ml-2 text-xs font-normal text-success">· answered</span> : null}
                </MetaItem>
                {participants.length ? (
                  <div className="px-5 py-3.5">
                    <dt className="flex items-center gap-1.5 text-eyebrow text-subtle">
                      <Users className="h-3.5 w-3.5" aria-hidden /> {multiPage ? "Participants (this page)" : "Participants"}
                    </dt>
                    <dd className="mt-2 flex flex-wrap items-center gap-1.5">
                      <ul className="flex flex-wrap items-center gap-1.5">
                        {participants.slice(0, 6).map((p) => (
                          <li key={p.id} title={p.display_name} className="flex">
                            <Avatar name={p.display_name} src={p.avatar_url} size={28} />
                            <span className="sr-only">{p.display_name}</span>
                          </li>
                        ))}
                      </ul>
                      {participants.length > 6 ? (
                        <span className="tabular inline-flex h-7 min-w-7 items-center justify-center rounded-full bg-surface-3 px-1.5 text-[11px] font-medium text-muted">
                          +{participants.length - 6}
                          <span className="sr-only"> more</span>
                        </span>
                      ) : null}
                      <span className="tabular ml-0.5 text-xs text-subtle">
                        {participants.length}
                        <span className="sr-only"> {participants.length === 1 ? "participant" : "participants"}</span>
                      </span>
                    </dd>
                  </div>
                ) : null}
              </dl>
            </div>
          </div>
        </aside>
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
