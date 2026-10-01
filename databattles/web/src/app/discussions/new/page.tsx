"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useCategories } from "@/components/discussions/thread-list";
import type { ThreadPage } from "@/components/discussions/types";
import { friendlyError } from "@/components/discussions/use-action";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Checkbox, Field, FormError, Input, Select } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { Container, PageHeader } from "@/components/ui/page";
import { ErrorState, InlineNotice, Spinner } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { hasRole, useDraft, useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";

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

  if (me.isPending || !me.data) return <Spinner />;

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
      <Container size="md" className="py-12">
        <ErrorState error={context.error} onRetry={() => context.refetch()} />
      </Container>
    );
  }

  return (
    <Container size="md" className="pb-16">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 pt-6 text-sm text-muted">
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

      <Card>
        <CardBody>
          <form onSubmit={submit} className="space-y-5" noValidate>
            <Field label="Title" required error={fields.title} hint="Be specific, e.g. “Why does my validation score drop after target encoding?”">
              {(p) => <Input {...p} value={draft.title} maxLength={160} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />}
            </Field>

            {!scoped ? (
              <Field label="Category" required error={fields.category} hint={selectedCategory?.description ?? undefined}>
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

            <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
              <LinkButton href={backHref} variant="secondary">Cancel</LinkButton>
              <Button type="submit" loading={busy} disabled={unverified || (scoped && context.isPending)}>
                Post discussion
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
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
