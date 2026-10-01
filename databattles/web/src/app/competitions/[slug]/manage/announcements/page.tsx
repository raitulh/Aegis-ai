"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Megaphone, Pin, PinOff, Send, Trash2, X } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ManageHeading, isTerminal, useManage } from "@/components/organizer/shared";
import { UserLink } from "@/components/domain/cards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Switch } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError, api, post } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation, useUnsavedChangesWarning } from "@/lib/hooks";
import type { Message, Schemas } from "@/lib/types";

type Announcement = Schemas["AnnouncementOut"];

const annKey = (slug: string) => ["competitions", slug, "announcements"] as const;

function Composer({ slug, disabled }: { slug: string; disabled: boolean }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [pinned, setPinned] = useState(false);
  const [notify, setNotify] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  useUnsavedChangesWarning(Boolean(title.trim() || body.trim()));
  const create = useApiMutation(
    () => post<Message>(`/competitions/${slug}/announcements`, { title: title.trim(), body_md: body, pinned, notify }),
    {
      success: (m) => m.message,
      invalidate: [annKey(slug), ["competitions", slug]],
      onSuccess: () => {
        setTitle("");
        setBody("");
        setPinned(false);
        setNotify(true);
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  const tooShort = title.trim().length < 3 || !body.trim();
  return (
    <Card>
      <CardHeader title="New announcement" description="Posted on the competition page. Participants get an in-app notification and, if enabled in their settings, an email." />
      <CardBody className="space-y-4">
        <Field label="Title" required error={error?.fields.title} hint="3–160 characters.">
          {(p) => <Input {...p} value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} disabled={disabled} />}
        </Field>
        <Field label="Message" required error={error?.fields.body_md}>
          {(p) => <MarkdownEditor id={p.id} aria-invalid={p["aria-invalid"]} aria-describedby={p["aria-describedby"]} value={body} onChange={setBody} rows={6} maxLength={20000} placeholder="Share an update, clarification or reminder…" />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Switch checked={pinned} onChange={setPinned} label="Pin to the top" description="Pinned announcements stay above newer ones." disabled={disabled} />
          <Switch checked={notify} onChange={setNotify} label="Notify participants" description="In-app notification plus email for those who opted in." disabled={disabled} />
        </div>
        <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
      </CardBody>
      <CardFooter>
        <Button icon={<Send className="h-4 w-4" />} loading={create.isPending} disabled={tooShort || disabled} onClick={() => create.mutate(undefined)}>
          Publish announcement
        </Button>
      </CardFooter>
    </Card>
  );
}

function AnnouncementItem({ a, slug }: { a: Announcement; slug: string }) {
  const invalidate = [annKey(slug), ["competitions", slug]] as const;
  const review = useApiMutation(
    (approve: boolean) => post<Message>(`/competitions/${slug}/announcements/${a.id}/review`, undefined, { approve }),
    { success: (m) => m.message, invalidate },
  );
  const update = useApiMutation(
    (q: { pinned?: boolean; delete?: boolean }) => api<Message>(`/competitions/${slug}/announcements/${a.id}`, { method: "PATCH", query: q }),
    { invalidate, onSuccess: (_, v) => toast.success(v.delete ? "Announcement deleted." : v.pinned ? "Announcement pinned." : "Announcement unpinned.") },
  );
  const pending = a.status === "pending_review";
  return (
    <li>
      <Card className={pending ? "border-warning/50" : undefined}>
        <CardBody>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold text-fg">{a.title}</h3>
                {a.pinned ? <Badge tone="accent" icon={<Pin className="h-3 w-3" aria-hidden />}>Pinned</Badge> : null}
                {pending ? <Badge tone="warning">Awaiting review</Badge> : a.status === "rejected" ? <Badge tone="danger">Rejected</Badge> : null}
                {a.sponsor ? <Badge tone="outline">Sponsor · {a.sponsor.name}</Badge> : null}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-subtle">
                {a.author ? <UserLink user={a.author} size={18} className="text-xs" /> : null}
                <span title={formatDateTime(a.created_at)}>{relativeTime(a.created_at)}</span>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              {pending ? (
                <>
                  <ConfirmDialog
                    trigger={<Button size="sm" icon={<Check className="h-4 w-4" />} loading={review.isPending && review.variables === true}>Approve</Button>}
                    title="Approve this sponsor announcement?"
                    description="It will be published on the competition page and participants will be notified."
                    confirmLabel="Approve and publish"
                    tone="primary"
                    onConfirm={() => review.mutateAsync(true).catch(() => undefined)}
                  />
                  <ConfirmDialog
                    trigger={<Button size="sm" variant="secondary" icon={<X className="h-4 w-4" />} loading={review.isPending && review.variables === false}>Reject</Button>}
                    title="Reject this sponsor announcement?"
                    description="It won't be published. The sponsor can submit a revised version."
                    confirmLabel="Reject"
                    onConfirm={() => review.mutateAsync(false).catch(() => undefined)}
                  />
                </>
              ) : a.status === "published" ? (
                <Button
                  size="sm"
                  variant="ghost"
                  icon={a.pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                  loading={update.isPending && update.variables?.pinned !== undefined}
                  onClick={() => update.mutate({ pinned: !a.pinned })}
                >
                  {a.pinned ? "Unpin" : "Pin"}
                </Button>
              ) : null}
              <ConfirmDialog
                trigger={<Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} aria-label={`Delete announcement ${a.title}`}>Delete</Button>}
                title="Delete this announcement?"
                description="It disappears from the competition page. Notifications already sent are not recalled."
                confirmLabel="Delete"
                onConfirm={() => update.mutateAsync({ delete: true }).catch(() => undefined)}
              />
            </div>
          </div>
          <Prose html={a.body_html} className="mt-3 text-sm" />
        </CardBody>
      </Card>
    </li>
  );
}

export default function ManageAnnouncementsPage() {
  const { slug } = useParams<{ slug: string }>();
  const manage = useManage(slug);
  const list = useQuery({
    queryKey: annKey(slug),
    queryFn: () => api<Announcement[]>(`/competitions/${slug}/announcements`),
  });
  const archived = manage.data?.lifecycle === "archived";
  const items = list.data ?? [];
  const pending = items.filter((a) => a.status === "pending_review");
  const rest = items.filter((a) => a.status !== "pending_review");

  return (
    <div className="space-y-6">
      <ManageHeading title="Announcements" description="Keep participants informed. Sponsor posts wait for your review before they go live." />
      {!archived ? <Composer slug={slug} disabled={archived} /> : null}
      {list.isPending ? (
        <SkeletonRows rows={4} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState icon={<Megaphone className="h-5 w-5" />} title="No announcements yet" description="Post a welcome message, rule clarifications or deadline reminders." />
      ) : (
        <>
          {pending.length ? (
            <section aria-labelledby="pending-heading">
              <h3 id="pending-heading" className="mb-3 text-sm font-semibold text-fg">Awaiting your review ({pending.length})</h3>
              <ul className="space-y-3">
                {pending.map((a) => <AnnouncementItem key={a.id} a={a} slug={slug} />)}
              </ul>
            </section>
          ) : null}
          {rest.length ? (
            <section aria-labelledby="posted-heading">
              <h3 id="posted-heading" className="mb-3 text-sm font-semibold text-fg">Posted ({rest.length})</h3>
              <ul className="space-y-3">
                {rest.map((a) => <AnnouncementItem key={a.id} a={a} slug={slug} />)}
              </ul>
            </section>
          ) : null}
        </>
      )}
      {manage.data && isTerminal(manage.data.lifecycle) && !archived ? (
        <p className="text-xs text-subtle">Results are final — announcements are still a good place to thank participants and share winners.</p>
      ) : null}
    </div>
  );
}
