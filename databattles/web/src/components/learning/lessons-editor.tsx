"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { MarkdownEditor } from "@/components/ui/markdown";
import { EmptyState, InlineNotice } from "@/components/ui/states";
import { del, patch, post, put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useUnsavedChangesWarning } from "@/lib/hooks";
import { QuestionsEditor } from "./questions-editor";
import { LESSON_KINDS, type AuthoringCourse, type AuthoringLesson, type LessonKind } from "./types";
import { KIND_LABEL, LessonKindIcon, formatMinutes } from "./ui";

interface LessonFormState {
  title: string;
  kind: LessonKind;
  body_md: string;
  estimated_minutes: string;
  pass_threshold: string;
  competition_slug: string;
  instructions: string;
  min_score: string;
}

function toLessonForm(l: AuthoringLesson): LessonFormState {
  return {
    title: l.title,
    kind: l.kind,
    body_md: l.body_md ?? "",
    estimated_minutes: String(l.estimated_minutes),
    pass_threshold: String(l.pass_threshold),
    competition_slug: l.challenge?.competition_slug ?? "",
    instructions: l.challenge?.instructions ?? "",
    min_score: l.challenge?.min_score !== null && l.challenge?.min_score !== undefined ? String(l.challenge.min_score) : "",
  };
}

function LessonForm({
  courseSlug,
  lesson,
  onUpdate,
  onDeleted,
}: {
  courseSlug: string;
  lesson: AuthoringLesson;
  onUpdate: (c: AuthoringCourse) => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<LessonFormState>(() => toLessonForm(lesson));
  const [baseline, setBaseline] = useState(() => JSON.stringify(toLessonForm(lesson)));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const dirty = JSON.stringify(form) !== baseline;
  useUnsavedChangesWarning(dirty);
  const set = (p: Partial<LessonFormState>) => setForm((f) => ({ ...f, ...p }));

  const save = useAction(
    (snapshot: LessonFormState) => {
      const body: Record<string, unknown> = {
        title: snapshot.title.trim(),
        kind: snapshot.kind,
        body_md: snapshot.body_md,
        estimated_minutes: Number(snapshot.estimated_minutes) || undefined,
      };
      if (snapshot.kind === "quiz") body.pass_threshold = Number(snapshot.pass_threshold) || undefined;
      if (snapshot.kind === "challenge") {
        body.challenge = {
          competition_slug: snapshot.competition_slug.trim(),
          instructions: snapshot.instructions,
          min_score: snapshot.min_score.trim() === "" ? null : Number(snapshot.min_score),
        };
      }
      return patch<AuthoringCourse>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}`, body);
    },
    {
      success: "Lesson saved",
      onSuccess: (d, snapshot) => {
        onUpdate(d);
        setBaseline(JSON.stringify(snapshot));
        setErrors({});
      },
      onError: (e) => setErrors(e.fields ?? {}),
    },
  );

  const remove = useAction(() => del<{ message: string }>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}`), {
    success: "Lesson deleted",
    invalidate: [["courses", courseSlug, "authoring"]],
    onSuccess: () => onDeleted(),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (form.title.trim().length < 2) local.title = "Enter a title.";
    const mins = Number(form.estimated_minutes);
    if (!Number.isInteger(mins) || mins < 1 || mins > 600) local.estimated_minutes = "Use a whole number between 1 and 600.";
    if (form.kind === "quiz") {
      const t = Number(form.pass_threshold);
      if (!Number.isInteger(t) || t < 1 || t > 100) local.pass_threshold = "Use a whole number between 1 and 100.";
    }
    if (form.kind === "challenge") {
      if (!form.competition_slug.trim()) local.challenge = "Link an existing competition by its slug.";
      if (form.min_score.trim() !== "" && Number.isNaN(Number(form.min_score))) local.min_score = "Enter a number or leave empty.";
    }
    setErrors(local);
    if (Object.keys(local).length) return;
    save.mutate(form);
  }

  const kindChanged = form.kind !== lesson.kind;
  const id = lesson.id;

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="space-y-4" noValidate aria-label={`Edit lesson ${lesson.title}`}>
        <div className="grid gap-4 sm:grid-cols-[1fr_160px_140px]">
          <Field label="Title" required error={errors.title}>
            {(p) => <Input {...p} value={form.title} maxLength={140} onChange={(e) => set({ title: e.target.value })} />}
          </Field>
          <Field label="Type" error={errors.kind}>
            {(p) => (
              <Select {...p} value={form.kind} onChange={(e) => set({ kind: e.target.value as LessonKind })}>
                {LESSON_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Minutes" error={errors.estimated_minutes}>
            {(p) => (
              <Input {...p} type="number" min={1} max={600} inputMode="numeric" value={form.estimated_minutes} onChange={(e) => set({ estimated_minutes: e.target.value })} />
            )}
          </Field>
        </div>

        <Field
          label={form.kind === "article" ? "Lesson content" : form.kind === "quiz" ? "Introduction (shown above the questions)" : "Background (shown above the challenge)"}
          error={errors.body_md}
        >
          {(p) => <MarkdownEditor {...p} value={form.body_md} onChange={(v) => set({ body_md: v })} rows={form.kind === "article" ? 14 : 6} maxLength={100_000} />}
        </Field>

        {form.kind === "quiz" ? (
          <Field label="Pass mark (%)" hint="Learners must score at least this much to complete the lesson." error={errors.pass_threshold} className="max-w-xs">
            {(p) => (
              <Input {...p} type="number" min={1} max={100} inputMode="numeric" value={form.pass_threshold} onChange={(e) => set({ pass_threshold: e.target.value })} />
            )}
          </Field>
        ) : null}

        {form.kind === "challenge" ? (
          <div className="space-y-4 rounded-[var(--radius-lg)] border border-border bg-surface-2 p-4">
            <p className="text-sm text-muted">
              Learners complete a challenge by making a scored submission to the linked competition (optionally reaching a minimum score). The server checks the
              evidence — it can&apos;t be self-reported.
            </p>
            <div className="grid gap-4 sm:grid-cols-[1fr_200px]">
              <Field label="Competition slug" required hint="From the competition URL: /competitions/<slug>" error={errors.challenge}>
                {(p) => (
                  <Input {...p} value={form.competition_slug} onChange={(e) => set({ competition_slug: e.target.value })} placeholder="titanic-practice" autoComplete="off" spellCheck={false} />
                )}
              </Field>
              <Field label="Minimum score" hint="Optional. Uses the metric's direction." error={errors.min_score}>
                {(p) => <Input {...p} type="number" step="any" value={form.min_score} onChange={(e) => set({ min_score: e.target.value })} />}
              </Field>
            </div>
            <Field label="Instructions" hint="Plain text shown to learners.">
              {(p) => <Textarea {...p} rows={4} maxLength={2000} value={form.instructions} onChange={(e) => set({ instructions: e.target.value })} />}
            </Field>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
          <ConfirmDialog
            trigger={
              <Button variant="ghost" className="text-danger hover:text-danger" icon={<Trash2 className="h-4 w-4" aria-hidden />}>
                Delete lesson
              </Button>
            }
            title={`Delete “${lesson.title}”?`}
            description="The lesson, its questions and learners' progress on it will be removed. This cannot be undone."
            confirmLabel="Delete lesson"
            onConfirm={() => remove.mutateAsync().then(() => undefined, () => undefined)}
          />
          <div className="flex items-center gap-2">
            {dirty ? <span className="text-xs text-warning">Unsaved changes</span> : null}
            {dirty ? (
              <Button variant="ghost" onClick={() => { setForm(JSON.parse(baseline) as LessonFormState); setErrors({}); }}>
                Discard
              </Button>
            ) : null}
            <Button type="submit" loading={save.isPending} disabled={!dirty} icon={<Save className="h-4 w-4" aria-hidden />}>
              Save lesson
            </Button>
          </div>
        </div>
      </form>

      {lesson.kind === "quiz" && !kindChanged ? (
        <div className="border-t border-border pt-6">
          <QuestionsEditor
            key={`${id}-questions`}
            courseSlug={courseSlug}
            lesson={lesson}
            onSaved={() => qc.invalidateQueries({ queryKey: ["courses", courseSlug, "authoring"] })}
          />
        </div>
      ) : form.kind === "quiz" ? (
        <InlineNotice tone="info">Save the lesson as a quiz to start adding questions.</InlineNotice>
      ) : null}
    </div>
  );
}

function lessonWarning(l: AuthoringLesson): string | null {
  if (l.kind === "quiz" && l.questions.length === 0) return "No questions";
  if (l.kind === "challenge" && !l.challenge) return "No competition linked";
  return null;
}

export function LessonsEditor({ course, onUpdate }: { course: AuthoringCourse; onUpdate: (c: AuthoringCourse) => void }) {
  const qc = useQueryClient();
  const slug = course.slug;
  const lessons = course.lessons;
  const [openId, setOpenId] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newKind, setNewKind] = useState<LessonKind>("article");
  const [addError, setAddError] = useState<string | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
  }, []);

  const add = useAction((v: { title: string; kind: LessonKind }) => post<AuthoringCourse>(`/learn/courses/${slug}/lessons`, v), {
    success: "Lesson added",
    onSuccess: (d) => {
      const before = new Set(lessons.map((l) => l.id));
      const created = d.lessons.find((l) => !before.has(l.id));
      onUpdate(d);
      setNewTitle("");
      setAddError(null);
      if (created) {
        setOpenId(created.id);
        requestAnimationFrame(() => document.getElementById(`lesson-${created.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
      }
    },
    onError: (e) => setAddError(e.fields?.title ?? null),
  });

  const reorder = useAction((slugs: string[]) => put<{ message: string }>(`/learn/courses/${slug}/lessons-order`, { slugs }), {
    onError: () => {
      void qc.invalidateQueries({ queryKey: ["courses", slug, "authoring"] });
    },
  });

  function move(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= lessons.length) return;
    const next = [...lessons];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    onUpdate({ ...course, lessons: next.map((l, i) => ({ ...l, position: i + 1 })) });
    setAnnounce(`Moved “${item.title}” to position ${target + 1} of ${next.length}.`);
    // Debounce the save so rapid keyboard moves send one final order.
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const slugs = next.map((l) => l.slug);
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      reorder.mutate(slugs);
    }, 600);
    // Keep keyboard focus on the moved lesson's control.
    requestAnimationFrame(() => {
      const same = document.getElementById(`move-${item.id}-${dir < 0 ? "up" : "down"}`) as HTMLButtonElement | null;
      const other = document.getElementById(`move-${item.id}-${dir < 0 ? "down" : "up"}`) as HTMLButtonElement | null;
      if (same && !same.disabled) same.focus();
      else other?.focus();
    });
  }

  return (
    <div className="space-y-6">
      <p className="sr-only" aria-live="polite">{announce}</p>

      {lessons.length === 0 ? (
        <EmptyState title="No lessons yet" description="Add your first lesson below. Courses need at least one lesson to be published." />
      ) : (
        <ol className="space-y-3" aria-label="Lessons">
          {lessons.map((l, i) => {
            const open = openId === l.id;
            const warning = lessonWarning(l);
            return (
              <li key={l.id} id={`lesson-${l.id}`} className={cn("scroll-mt-20 rounded-[var(--radius-lg)] border bg-surface", open ? "border-border-strong" : "border-border")}>
                <div className="flex items-center gap-2 px-3 py-2.5 sm:gap-3">
                  <div className="flex shrink-0 flex-col sm:flex-row">
                    <button
                      type="button"
                      id={`move-${l.id}-up`}
                      aria-label={`Move “${l.title}” up`}
                      disabled={i === 0}
                      onClick={() => move(i, -1)}
                      className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)] disabled:opacity-30"
                    >
                      <ArrowUp className="h-4 w-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      id={`move-${l.id}-down`}
                      aria-label={`Move “${l.title}” down`}
                      disabled={i === lessons.length - 1}
                      onClick={() => move(i, 1)}
                      className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-[var(--ring)] disabled:opacity-30"
                    >
                      <ArrowDown className="h-4 w-4" aria-hidden />
                    </button>
                  </div>
                  <span className="w-5 shrink-0 text-center text-sm tabular-nums text-subtle">{i + 1}</span>
                  <LessonKindIcon kind={l.kind} className="hidden text-muted sm:block" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-fg">{l.title}</p>
                    <p className="flex flex-wrap gap-x-1.5 text-xs text-subtle">
                      <span>{KIND_LABEL[l.kind]}</span>
                      <span aria-hidden>·</span>
                      <span>{formatMinutes(l.estimated_minutes)}</span>
                      {l.kind === "quiz" ? (
                        <>
                          <span aria-hidden>·</span>
                          <span>{l.questions.length} question{l.questions.length === 1 ? "" : "s"} · pass {l.pass_threshold}%</span>
                        </>
                      ) : null}
                      {l.kind === "challenge" && l.challenge ? (
                        <>
                          <span aria-hidden>·</span>
                          <span className="font-mono">{l.challenge.competition_slug}</span>
                        </>
                      ) : null}
                    </p>
                  </div>
                  {warning ? <Badge tone="warning" className="hidden sm:inline-flex">{warning}</Badge> : null}
                  <Button
                    size="sm"
                    variant={open ? "ghost" : "secondary"}
                    aria-expanded={open}
                    aria-controls={`lesson-editor-${l.id}`}
                    onClick={() => setOpenId(open ? null : l.id)}
                    icon={open ? <ChevronUp className="h-4 w-4" aria-hidden /> : <ChevronDown className="h-4 w-4" aria-hidden />}
                  >
                    {open ? "Close" : "Edit"}
                  </Button>
                </div>
                {open ? (
                  <div id={`lesson-editor-${l.id}`} className="border-t border-border p-4">
                    {warning ? <div className="mb-4 sm:hidden"><Badge tone="warning">{warning}</Badge></div> : null}
                    <LessonForm key={l.id} courseSlug={slug} lesson={l} onUpdate={onUpdate} onDeleted={() => setOpenId(null)} />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {reorder.isPending ? <p className="text-xs text-muted" role="status">Saving lesson order…</p> : null}

      <form
        className="rounded-[var(--radius-lg)] border border-dashed border-border-strong p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (newTitle.trim().length < 2) {
            setAddError("Enter a title (at least 2 characters).");
            return;
          }
          add.mutate({ title: newTitle.trim(), kind: newKind });
        }}
        aria-labelledby="add-lesson-heading"
      >
        <h3 id="add-lesson-heading" className="mb-3 text-sm font-semibold text-fg">Add a lesson</h3>
        <div className="grid gap-3 sm:grid-cols-[1fr_180px_auto] sm:items-end">
          <Field label="Title" error={addError}>
            {(p) => <Input {...p} value={newTitle} maxLength={140} onChange={(e) => setNewTitle(e.target.value)} placeholder="e.g. Train/test splits" />}
          </Field>
          <Field label="Type">
            {(p) => (
              <Select {...p} value={newKind} onChange={(e) => setNewKind(e.target.value as LessonKind)}>
                {LESSON_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </Select>
            )}
          </Field>
          <Button type="submit" loading={add.isPending} icon={<Plus className="h-4 w-4" aria-hidden />}>
            Add lesson
          </Button>
        </div>
      </form>
    </div>
  );
}
