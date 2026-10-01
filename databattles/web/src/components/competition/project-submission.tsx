"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ExternalLink, Save, Video } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Field, FormError, Input, Textarea } from "@/components/ui/form";
import { MarkdownEditor, Prose } from "@/components/ui/markdown";
import { InlineNotice, SkeletonRows } from "@/components/ui/states";
import { get, put } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation, useUnsavedChangesWarning } from "@/lib/hooks";
import type { CompetitionDetail, Message } from "@/lib/types";
import { ck } from "./context";
import { DateTime } from "./datetime";
import { describeSubmitBlocker } from "./labels";
import type { PresentationSlot, ProjectSubmission } from "./types";

interface FormState {
  title: string;
  summary: string;
  description_md: string;
  repo_url: string;
  demo_url: string;
  video_url: string;
}

const EMPTY: FormState = { title: "", summary: "", description_md: "", repo_url: "", demo_url: "", video_url: "" };

function Slots({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const query = useQuery({
    queryKey: ck.presentations(slug),
    queryFn: () => get<PresentationSlot[]>(`/competitions/${encodeURIComponent(slug)}/presentations`),
  });
  const teamId = comp.viewer.team?.id;
  const mine = (query.data ?? []).filter((s) => teamId && s.team_id === teamId);
  if (query.isPending || query.isError || !mine.length) return null;
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><CalendarClock className="h-4 w-4 text-accent-strong" aria-hidden /> Your presentation</span>} />
      <CardBody>
        <ul className="space-y-3 text-sm">
          {mine.map((s) => (
            <li key={s.id}>
              <DateTime value={s.starts_at} eventTimeZone={comp.timezone} relative className="font-medium text-fg" />
              <p className="text-xs text-muted">Until {formatDateTime(s.ends_at)}{s.location ? ` · ${s.location}` : ""}</p>
              {s.meeting_url ? (
                <a href={s.meeting_url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-accent-strong hover:underline">
                  <Video className="h-3.5 w-3.5" aria-hidden /> Meeting link <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

/** Judged events: one project submission per team (title, write-up and links), editable until the deadline. */
export function ProjectSubmissionCard({ comp }: { comp: CompetitionDetail }) {
  const slug = comp.slug;
  const blockers = comp.viewer.submit_blockers.filter((b) => b !== "not_automatically_scored");
  const query = useQuery({
    queryKey: ck.projectSubmissions(slug),
    queryFn: () => get<ProjectSubmission[]>(`/competitions/${encodeURIComponent(slug)}/project-submissions`),
    enabled: comp.viewer.is_participant,
  });
  const teamId = comp.viewer.team?.id;
  const existing = (query.data ?? []).find((s) => s.team_id === teamId) ?? null;

  const [form, setForm] = useState<FormState>(EMPTY);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  useUnsavedChangesWarning(dirty);

  // Seed the form once from the saved submission (don't clobber edits on background refetches).
  useEffect(() => {
    if (!query.isSuccess) return;
    const key = existing ? `${existing.team_id}:${existing.updated_at}` : "new";
    if (loadedFor === key || dirty) return;
    setLoadedFor(key);
    if (existing) {
      setForm({
        title: existing.title,
        summary: existing.summary ?? "",
        description_md: "",
        repo_url: existing.repo_url ?? "",
        demo_url: existing.demo_url ?? "",
        video_url: existing.video_url ?? "",
      });
    }
  }, [query.isSuccess, existing, loadedFor, dirty]);

  const save = useApiMutation(
    (data: FormState) =>
      put<Message>(`/competitions/${encodeURIComponent(slug)}/project-submission`, {
        title: data.title.trim(),
        summary: data.summary.trim() || null,
        description_md: data.description_md.trim() || null,
        repo_url: data.repo_url.trim() || null,
        demo_url: data.demo_url.trim() || null,
        video_url: data.video_url.trim() || null,
      }),
    {
      success: "Project submission saved",
      invalidate: [ck.projectSubmissions(slug)],
      onSuccess: () => setDirty(false),
    },
  );
  const fields = save.error?.fields ?? {};
  const update = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }));
    setDirty(true);
  };
  const disabled = blockers.length > 0;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Card>
        <CardHeader
          title="Project submission"
          description="Judges review your write-up and links. You can update it until the submission window closes; the latest version is judged."
        />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(form);
          }}
          noValidate
        >
          <CardBody className="space-y-4">
            {blockers.length ? (
              <InlineNotice tone="warning" title="You can't submit right now">
                <ul className="list-disc pl-5">{blockers.map((b) => <li key={b}>{describeSubmitBlocker(b, comp)}</li>)}</ul>
              </InlineNotice>
            ) : null}
            {query.isPending ? (
              <SkeletonRows rows={4} />
            ) : (
              <>
                {existing ? (
                  <p className="text-xs text-subtle">
                    Last saved {relativeTime(existing.updated_at)} · first submitted {formatDateTime(existing.submitted_at)}
                  </p>
                ) : null}
                <Field label="Project title" required error={fields.title}>
                  {(p) => <Input {...p} value={form.title} onChange={(e) => update({ title: e.target.value })} maxLength={140} disabled={disabled} />}
                </Field>
                <Field label="One-line summary" hint="Up to 300 characters." error={fields.summary}>
                  {(p) => <Textarea {...p} rows={2} value={form.summary} onChange={(e) => update({ summary: e.target.value })} maxLength={300} disabled={disabled} />}
                </Field>
                <Field
                  label="Write-up"
                  hint={
                    existing?.description_html
                      ? "Saving replaces the write-up. The saved version is shown alongside — copy it here if you're only changing links."
                      : "What problem you solved, how, and what you learned."
                  }
                  error={fields.description_md}
                >
                  {(p) => <MarkdownEditor {...p} value={form.description_md} onChange={(v) => update({ description_md: v })} rows={10} maxLength={50_000} />}
                </Field>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label="Repository URL" error={fields.repo_url}>
                    {(p) => <Input {...p} type="url" inputMode="url" placeholder="https://github.com/…" value={form.repo_url} onChange={(e) => update({ repo_url: e.target.value })} disabled={disabled} />}
                  </Field>
                  <Field label="Demo URL" error={fields.demo_url}>
                    {(p) => <Input {...p} type="url" inputMode="url" placeholder="https://…" value={form.demo_url} onChange={(e) => update({ demo_url: e.target.value })} disabled={disabled} />}
                  </Field>
                  <Field label="Video URL" error={fields.video_url}>
                    {(p) => <Input {...p} type="url" inputMode="url" placeholder="https://…" value={form.video_url} onChange={(e) => update({ video_url: e.target.value })} disabled={disabled} />}
                  </Field>
                </div>
                {existing?.description_html && !form.description_md.trim() ? (
                  <InlineNotice tone="warning">Saving now will remove your current write-up because the write-up field is empty.</InlineNotice>
                ) : null}
                {save.error && !Object.keys(fields).length ? <FormError message={save.error.message} /> : null}
              </>
            )}
          </CardBody>
          <CardFooter>
            {dirty ? <span className="mr-auto text-xs text-subtle">Unsaved changes</span> : null}
            <Button type="submit" loading={save.isPending} disabled={disabled || form.title.trim().length < 3} icon={<Save className="h-4 w-4" aria-hidden />}>
              {existing ? "Update submission" : "Submit project"}
            </Button>
          </CardFooter>
        </form>
      </Card>
      <div className="space-y-4">
        <Slots comp={comp} />
        {existing?.description_html ? (
          <Card>
            <CardHeader title="Current write-up" description="As judges will see it." />
            <CardBody><Prose html={existing.description_html} className="text-sm" /></CardBody>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
