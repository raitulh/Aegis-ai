"use client";

import { ArrowDown, ArrowUp, Check, ListChecks, Plus, Save, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/form";
import { EmptyState, InlineNotice } from "@/components/ui/states";
import { put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useUnsavedChangesWarning } from "@/lib/hooks";
import type { AuthoringLesson, AuthoringQuestion } from "./types";

interface QDraft {
  key: string;
  prompt: string;
  options: string[];
  correct: number;
  explanation: string;
}

const MAX_OPTIONS = 8;
const MAX_QUESTIONS = 50;

function toDrafts(qs: AuthoringQuestion[]): QDraft[] {
  return qs.map((q) => ({ key: q.id, prompt: q.prompt, options: [...q.options], correct: q.correct_option, explanation: q.explanation ?? "" }));
}

interface QuestionPayload {
  prompt: string;
  options: string[];
  correct_option: number;
  explanation: string | null;
}

/** Client-side checks mirroring the server (which strips empty options — so the correct index is remapped here). */
function buildPayload(items: QDraft[]): { questions: QuestionPayload[] } | { errors: Record<number, string> } {
  const errors: Record<number, string> = {};
  const questions: QuestionPayload[] = [];
  items.forEach((q, i) => {
    const prompt = q.prompt.trim();
    const opts = q.options.map((text, j) => ({ text: text.trim(), j })).filter((o) => o.text);
    const correct = opts.findIndex((o) => o.j === q.correct);
    if (prompt.length < 3) errors[i] = "Write the question (at least 3 characters).";
    else if (opts.length < 2) errors[i] = "Add at least two non-empty options.";
    else if (correct < 0) errors[i] = "Mark which option is correct (it can't be empty).";
    questions.push({ prompt, options: opts.map((o) => o.text), correct_option: Math.max(0, correct), explanation: q.explanation.trim() || null });
  });
  return Object.keys(errors).length ? { errors } : { questions };
}

/** Server field keys: service uses 1-based `questions.N`, request validation uses 0-based `questions.N.field`. */
function mapServerErrors(fields: Record<string, string>): Record<number, string> {
  const out: Record<number, string> = {};
  for (const [key, msg] of Object.entries(fields)) {
    const nested = /^questions\.(\d+)\.\w+/.exec(key);
    const flat = /^questions\.(\d+)$/.exec(key);
    if (nested) out[Number(nested[1])] = msg;
    else if (flat) out[Number(flat[1]) - 1] = msg;
  }
  return out;
}

export function QuestionsEditor({ courseSlug, lesson, onSaved }: { courseSlug: string; lesson: AuthoringLesson; onSaved: () => Promise<void> | void }) {
  const [items, setItems] = useState<QDraft[]>(() => toDrafts(lesson.questions));
  const [baseline, setBaseline] = useState(() => JSON.stringify(toDrafts(lesson.questions)));
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [generalError, setGeneralError] = useState<string | null>(null);
  const counter = useRef(0);
  const dirty = JSON.stringify(items) !== baseline;
  useUnsavedChangesWarning(dirty);

  const save = useAction(
    (snapshot: QDraft[]) => {
      const built = buildPayload(snapshot);
      if ("errors" in built) return Promise.reject(Object.assign(new Error("invalid"), { clientErrors: built.errors }));
      return put<{ message: string }>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}/questions`, built);
    },
    {
      success: "Questions saved",
      toastErrors: false,
      onSuccess: async (_d, snapshot) => {
        setBaseline(JSON.stringify(snapshot));
        setErrors({});
        setGeneralError(null);
        await onSaved();
      },
      onError: (e) => {
        const client = (e as unknown as { clientErrors?: Record<number, string> }).clientErrors;
        if (client) {
          setErrors(client);
          setGeneralError("Fix the highlighted questions before saving.");
          return;
        }
        const mapped = e.fields ? mapServerErrors(e.fields) : {};
        setErrors(mapped);
        setGeneralError(Object.keys(mapped).length ? "Fix the highlighted questions before saving." : e.message);
      },
    },
  );

  const update = (i: number, patch: Partial<QDraft>) => setItems((list) => list.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  const move = (i: number, dir: -1 | 1) => {
    setErrors({});
    const key = items[i]?.key;
    requestAnimationFrame(() => {
      const same = document.getElementById(`qmove-${key}-${dir < 0 ? "up" : "down"}`) as HTMLButtonElement | null;
      if (same && !same.disabled) same.focus();
      else document.getElementById(`qmove-${key}-${dir < 0 ? "down" : "up"}`)?.focus();
    });
    setItems((list) => {
      const next = [...list];
      const [q] = next.splice(i, 1);
      next.splice(i + dir, 0, q);
      return next;
    });
  };
  const add = () => {
    counter.current += 1;
    const key = `new-${Date.now()}-${counter.current}`;
    setItems((list) => [...list, { key, prompt: "", options: ["", ""], correct: 0, explanation: "" }]);
    requestAnimationFrame(() => document.getElementById(`qprompt-${key}`)?.focus());
  };

  return (
    <section aria-labelledby={`questions-${lesson.id}`} className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-accent-soft text-accent-strong">
            <ListChecks className="h-3.5 w-3.5" aria-hidden />
          </span>
          <div className="min-w-0">
            <h4 id={`questions-${lesson.id}`} className="font-semibold tracking-[-0.01em] text-fg">Quiz questions</h4>
            <p className="text-xs leading-relaxed text-muted">
              Graded on the server. Learners see correct answers and explanations only after they pass.
            </p>
          </div>
        </div>
        <span className="tabular rounded-full border border-border bg-bg-elevated px-2 py-0.5 font-mono text-[11px] text-subtle">
          {items.length} / {MAX_QUESTIONS}
        </span>
      </div>

      {items.length === 0 ? (
        <EmptyState icon={<ListChecks />} title="No questions yet" description="Quizzes need at least one question before the course can be published." />
      ) : (
        <ol className="space-y-3">
          {items.map((q, i) => (
            <li key={q.key}>
              <fieldset
                className={cn(
                  "rounded-[var(--radius-lg)] border bg-bg-elevated p-4 shadow-[inset_0_1px_0_var(--hairline-highlight)]",
                  errors[i] ? "border-[color-mix(in_oklab,var(--danger)_60%,transparent)]" : "border-border",
                )}
                aria-describedby={errors[i] ? `qerr-${q.key}` : undefined}
              >
                <legend className="tabular float-left py-1.5 text-eyebrow text-accent-strong">Question {i + 1}</legend>
                <div className="float-right flex gap-1">
                  <Button id={`qmove-${q.key}-up`} variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move question ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                    <ArrowUp className="h-4 w-4" aria-hidden />
                  </Button>
                  <Button id={`qmove-${q.key}-down`} variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move question ${i + 1} down`} disabled={i === items.length - 1} onClick={() => move(i, 1)}>
                    <ArrowDown className="h-4 w-4" aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-danger hover:text-danger"
                    aria-label={`Remove question ${i + 1}`}
                    onClick={() => {
                      setErrors({});
                      setItems((list) => list.filter((_, j) => j !== i));
                    }}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden />
                  </Button>
                </div>
                <div className="clear-both space-y-3 pt-2">
                  {errors[i] ? (
                    <p id={`qerr-${q.key}`} role="alert" className="text-xs font-medium text-danger">{errors[i]}</p>
                  ) : null}
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={`qprompt-${q.key}`} className="text-sm font-medium text-fg">Prompt</label>
                    <Textarea
                      id={`qprompt-${q.key}`}
                      rows={2}
                      value={q.prompt}
                      maxLength={2000}
                      onChange={(e) => update(i, { prompt: e.target.value })}
                      placeholder="What does a confusion matrix show?"
                    />
                  </div>
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium text-fg">
                      Options <span className="font-normal text-subtle">(select the correct answer)</span>
                    </legend>
                    {q.options.map((opt, oi) => {
                      const isCorrect = q.correct === oi;
                      return (
                        <div key={oi} className="flex items-center gap-2">
                          <input
                            type="radio"
                            name={`correct-${q.key}`}
                            checked={isCorrect}
                            onChange={() => update(i, { correct: oi })}
                            aria-label={`Option ${oi + 1} is correct`}
                            className="h-4 w-4 shrink-0 accent-[var(--success)]"
                          />
                          <span
                            aria-hidden
                            className={cn(
                              "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[10.5px] font-medium",
                              isCorrect ? "border-transparent bg-success text-bg" : "border-border-strong text-subtle",
                            )}
                          >
                            {isCorrect ? <Check className="h-3.5 w-3.5" strokeWidth={2.6} /> : "ABCDEFGH"[oi]}
                          </span>
                          <input
                            value={opt}
                            maxLength={300}
                            aria-label={`Option ${oi + 1}`}
                            placeholder={`Option ${oi + 1}`}
                            onChange={(e) => update(i, { options: q.options.map((o, k) => (k === oi ? e.target.value : o)) })}
                            className={cn(
                              "h-9 w-full min-w-0 rounded-[var(--radius-md)] border bg-surface px-3 text-sm text-fg placeholder:text-subtle transition-[border-color,box-shadow] duration-200 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[var(--ring)]",
                              isCorrect ? "border-[color-mix(in_oklab,var(--success)_60%,transparent)]" : "border-border hover:border-border-strong",
                            )}
                          />
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 shrink-0"
                            aria-label={`Remove option ${oi + 1}`}
                            disabled={q.options.length <= 2}
                            onClick={() =>
                              update(i, {
                                options: q.options.filter((_, k) => k !== oi),
                                correct: q.correct === oi ? 0 : q.correct > oi ? q.correct - 1 : q.correct,
                              })
                            }
                          >
                            <X className="h-4 w-4" aria-hidden />
                          </Button>
                        </div>
                      );
                    })}
                    {q.options.length < MAX_OPTIONS ? (
                      <Button variant="link" size="sm" icon={<Plus className="h-3.5 w-3.5" aria-hidden />} onClick={() => update(i, { options: [...q.options, ""] })}>
                        Add option
                      </Button>
                    ) : null}
                  </fieldset>
                  <Field label="Explanation" hint="Optional. Shown with the correct answer after the learner passes.">
                    {(p) => (
                      <Textarea {...p} rows={2} maxLength={2000} value={q.explanation} onChange={(e) => update(i, { explanation: e.target.value })} />
                    )}
                  </Field>
                </div>
              </fieldset>
            </li>
          ))}
        </ol>
      )}

      {generalError ? <InlineNotice tone="danger">{generalError}</InlineNotice> : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <Button variant="secondary" icon={<Plus className="h-4 w-4" aria-hidden />} onClick={add} disabled={items.length >= MAX_QUESTIONS}>
          Add question
        </Button>
        <div className="flex items-center gap-2">
          {dirty ? <span className="text-xs text-warning">Unsaved changes</span> : null}
          <Button icon={<Save className="h-4 w-4" aria-hidden />} loading={save.isPending} disabled={!dirty} onClick={() => save.mutate(items)}>
            Save questions
          </Button>
        </div>
      </div>
    </section>
  );
}
