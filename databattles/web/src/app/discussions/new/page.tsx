"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, ChevronRight, Lightbulb, PenLine } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, useSyncExternalStore } from "react";

import { GuidelinesNote } from "@/components/discussions/guidelines-note";
import { CategoryIcon, useCategories } from "@/components/discussions/thread-list";
import type { ThreadPage } from "@/components/discussions/types";
import { friendlyError } from "@/components/discussions/use-action";
import { Button, LinkButton } from "@/components/ui/button";
import { Checkbox, Field, FormError, Input, Select } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { Container, PageHeader } from "@/components/ui/page";
import { ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { hasRole, useDraft, useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";

const noopSubscribe = () => () => {};
/** False on the server and during hydration, true afterwards — keeps the first client render identical to the server's. */
function useHydrated(): boolean {
  return useSyncExternalStore(noopSubscribe, () => true, () => false);
}

function NewDiscussionSkeleton() {
  return (
    <Container size="lg" className="pb-20">
      <div role="status" aria-label="Loading">
        <Skeleton className="mt-6 h-4 w-48" />
        <Skeleton className="mt-10 h-3 w-24" />
        <Skeleton className="mt-4 h-8 w-72 max-w-full" />
        <Skeleton className="mt-3 h-4 w-96 max-w-full" />
        <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_16rem]">
          <div className="space-y-6 rounded-[var(--radius-xl)] border border-border bg-surface p-5 sm:p-7">
            {["h-10", "h-10", "h-64"].map((h, i) => (
              <div key={i}>
                <Skeleton className="h-3 w-20" />
                <Skeleton className={`mt-2.5 w-full ${h}`} />
              </div>
            ))}
          </div>
          <Skeleton className="hidden h-48 w-full rounded-[var(--radius-lg)] lg:block" />
        </div>
      </div>
    </Container>
  );
}

function NewDiscussionForm() {
  const me = useRequireAuth();
  const router = useRouter();
  const qc = useQueryClient();
  const params = useSearchParams();
  const competition = params.get("competition") || null;
  const project = competition ? null : params.get("project") || null;
  const scoped = Boolean(competition || project);

  const categories = useCategories();
  const context = useQuery({
    queryKey: ["discussions", "context", competition, project],
    queryFn: () => get<ThreadPage>("/discussions/threads", { competition, project, page_size: 1 }),
    enabled: scoped,
  });

  const draftKey = `discussion:new:${competition ? `c:${competition}` : project ? `p:${project}` : "general"}`;
  const [draft, setDraft, clearDraft] = useDraft(draftKey, { title: "", body: "" });
  const [category, setCategory] = useState(params.get("category") ?? "");
  const [announcement, setAnnouncement] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  useUnsavedChangesWarning(!submitted && Boolean(draft.title.trim() || draft.body.trim()));
  const hydrated = useHydrated();

  // The server always renders the skeleton (the account is not known there); `hydrated` makes the client's first
  // render match it even when the account query has already resolved by the time this boundary hydrates.
  if (!hydrated || me.isPending || !me.data) return <NewDiscussionSkeleton />;

  const isStaff = hasRole(me.data, "moderator");
  const ctx = context.data?.context;
  const canAnnounce = competition ? Boolean(ctx?.can_moderate) : !project && isStaff;
  const scopeTitle = ctx?.competition?.title ?? ctx?.project?.title ?? competition ?? project ?? "";
  const backHref = competition
    ? `/competitions/${competition}`
    : project
      ? `/projects/${project}`
      : category
        ? `/discussions/c/${category}`
        : "/discussions";
  const selectedCategory = categories.data?.find((c) => c.slug === category);
  const unverified = me.data.email_verified === false;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (draft.title.trim().length < 5) local.title = "Use at least 5 characters.";
    if (draft.body.trim().length < 10) local.body_md = "Add a little more detail (10+ characters).";
    if (!scoped && !category) local.category = "Choose a category.";
    setLocalErrors(local);
    setError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      const res = await post<{ id: string }>("/discussions/threads", {
        title: draft.title.trim(),
        body_md: draft.body,
        category: scoped ? undefined : category,
        competition: competition ?? undefined,
        project: project ?? undefined,
        announcement: canAnnounce && announcement ? true : undefined,
      });
      setSubmitted(true);
      clearDraft();
      await qc.invalidateQueries({ queryKey: ["discussions"] });
      router.push(`/discussions/t/${res.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "error", friendlyError(err), null, null));
    } finally {
      setBusy(false);
    }
  }

  const fields = { ...(error?.fields ?? {}), ...localErrors };
  const generalError = error && !Object.keys(error.fields).length ? friendlyError(error) : null;

  if (scoped && context.isError) {
    return (
      <Container size="lg" className="py-16">
        <ErrorState error={context.error} onRetry={() => context.refetch()} />
      </Container>
    );
  }

  return (
    <Container size="lg" className="pb-20">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 pt-6 text-sm text-subtle">
        {competition ? (
          <>
            <Link href="/competitions" className="hover:text-fg">Competitions</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/competitions/${competition}`} className="max-w-[16rem] truncate hover:text-fg">{scopeTitle}</Link>
          </>
        ) : project ? (
          <>
            <Link href="/projects" className="hover:text-fg">Projects</Link>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            <Link href={`/projects/${project}`} className="max-w-[16rem] truncate hover:text-fg">{scopeTitle}</Link>
          </>
        ) : (
          <Link href="/discussions" className="hover:text-fg">Discussions</Link>
        )}
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="text-fg" aria-current="page">New discussion</span>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow={scoped ? (competition ? "Competition forum" : "Project forum") : "Community"}
        icon={<PenLine />}
        title="Start a discussion"
        description={
          scoped
            ? `Posting in the ${competition ? "competition" : "project"} forum for “${scopeTitle}”. Only people who can see it can read this discussion.`
            : "Give it a clear title and enough detail for others to help."
        }
      />

      {unverified ? (
        <div className="mb-5">
          <InlineNotice tone="warning" title="Verify your email to post">
            Check your inbox for the verification link we sent when you signed up.
          </InlineNotice>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="min-w-0 overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card animate-rise [animation-delay:80ms]">
          <form onSubmit={submit} className="space-y-6 p-5 sm:p-7" noValidate>
            <Field label="Title" required error={fields.title} hint="Be specific, e.g. “Why does my validation score drop after target encoding?”">
              {(p) => <Input {...p} value={draft.title} maxLength={160} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />}
            </Field>

            {!scoped ? (
              <Field
                label="Category"
                required
                error={fields.category}
                hint={
                  selectedCategory?.description ? (
                    <span className="inline-flex items-center gap-1.5">
                      <CategoryIcon slug={selectedCategory.slug} className="h-3 w-3 shrink-0 text-accent-strong" />
                      {selectedCategory.description}
                    </span>
                  ) : undefined
                }
              >
                {(p) => (
                  <Select {...p} value={category} onChange={(e) => setCategory(e.target.value)} disabled={categories.isPending}>
                    <option value="">{categories.isPending ? "Loading categories…" : "Choose a category…"}</option>
                    {categories.data?.map((c) => (
                      <option key={c.id} value={c.slug} disabled={c.staff_only_posting && !isStaff}>
                        {c.name}
                        {c.staff_only_posting ? " (staff only)" : ""}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            ) : null}
            {!scoped && categories.isError ? <FormError message="Categories couldn't be loaded. Refresh the page to try again." /> : null}

            <Field
              label="Details"
              required
              error={fields.body_md}
              hint={
                <span className="inline-flex items-start gap-1">
                  <AtSign className="mt-px h-3 w-3 shrink-0" aria-hidden />
                  Mention someone with @handle to notify them — only people who can see this discussion are notified. Your draft is saved on this device.
                </span>
              }
            >
              {(p) => (
                <MarkdownEditor
                  {...p}
                  value={draft.body}
                  onChange={(v) => setDraft({ ...draft, body: v })}
                  rows={12}
                  maxLength={40_000}
                  placeholder="What are you trying to do? What have you tried? Include code or error messages in ``` fences."
                />
              )}
            </Field>

            {canAnnounce ? (
              <Checkbox
                label="Post as an announcement"
                description={competition ? "Announcements are pinned and highlighted for all participants." : "Highlight this discussion as an official announcement."}
                checked={announcement}
                onChange={(e) => setAnnouncement(e.target.checked)}
              />
            ) : null}

            <FormError message={generalError} />

            <div className="-mx-5 -mb-5 flex flex-col-reverse gap-2 border-t border-border bg-bg-elevated/50 px-5 py-4 sm:-mx-7 sm:-mb-7 sm:flex-row sm:items-center sm:justify-end sm:px-7">
              <LinkButton href={backHref} variant="secondary">Cancel</LinkButton>
              <Button type="submit" loading={busy} disabled={unverified || (scoped && context.isPending)} icon={<PenLine className="h-4 w-4" aria-hidden />}>
                Post discussion
              </Button>
            </div>
          </form>
        </div>
        <aside aria-label="Posting tips" className="min-w-0 space-y-5 lg:sticky lg:top-24 lg:self-start">
          <div className="rounded-[var(--radius-lg)] border border-border bg-surface/60 p-4">
            <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
              <Lightbulb className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> Get a good answer
            </p>
            <ol className="mt-3 space-y-2.5 text-xs leading-relaxed text-muted">
              {["Say what you're trying to do.", "Show what you've tried — code, settings, error messages.", "Format code in ``` fences and keep it short."].map((tip, i) => (
                <li key={tip} className="flex gap-2.5">
                  <span className="tabular flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2 font-mono text-[10px] text-subtle">{i + 1}</span>
                  <span className="pt-0.5">{tip}</span>
                </li>
              ))}
            </ol>
          </div>
          <GuidelinesNote />
        </aside>
      </div>
    </Container>
  );
}

export default function NewDiscussionPage() {
  return (
    <Suspense>
      <NewDiscussionForm />
    </Suspense>
  );
}
