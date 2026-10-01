"use client";

import { Check, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type FocusEvent, type ReactNode } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { EmptyState, InlineNotice } from "@/components/ui/states";
import { post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ScoreBar, SuccessMark } from "./celebrate";
import type { LessonDetail, ProgressResult, QuizQuestionView, QuizResult } from "./types";

const LETTERS = "ABCDEFGH";
const letter = (i: number) => LETTERS[i] ?? String(i + 1);

function OptionLine({ text, index, state }: { text: string; index: number; state: "correct" | "wrong" | "neutral" }) {
  return (
    <li
      className={cn(
        "flex items-start gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 text-sm",
        state === "correct" && "border-[color-mix(in_oklab,var(--success)_40%,transparent)] bg-success-soft text-fg",
        state === "wrong" && "border-[color-mix(in_oklab,var(--danger)_40%,transparent)] bg-danger-soft text-fg",
        state === "neutral" && "border-border text-muted",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[11px] font-medium",
          // Page-ink glyph on the solid fill keeps >= 3:1 in both themes (white on the dark theme's light fills does not).
          state === "correct" && "border-transparent bg-success text-bg",
          state === "wrong" && "border-transparent bg-danger text-bg",
          state === "neutral" && "border-border-strong text-subtle",
        )}
      >
        {state === "correct" ? <Check className="h-3.5 w-3.5" strokeWidth={2.6} /> : state === "wrong" ? <X className="h-3.5 w-3.5" strokeWidth={2.6} /> : letter(index)}
      </span>
      <span className="min-w-0 pt-0.5">
        {text}
        {state === "correct" ? <span className="sr-only"> (correct answer)</span> : null}
        {state === "wrong" ? <span className="sr-only"> (your answer, incorrect)</span> : null}
      </span>
    </li>
  );
}

/** Read-only review of a question: which option is correct (when the server revealed it) and the explanation. */
function QuestionReview({
  q,
  index,
  total,
  chosen,
  correct,
}: {
  q: QuizQuestionView;
  index: number;
  total: number;
  chosen: number | null | undefined;
  correct: boolean | undefined;
}) {
  const known = q.correct_option !== undefined;
  return (
    <li className="rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-5 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="tabular text-eyebrow text-subtle">
          Question {index + 1} of {total}
        </span>
        {correct === undefined ? null : correct ? (
          <Badge tone="success" icon={<Check className="h-3 w-3" aria-hidden />}>Correct</Badge>
        ) : (
          <Badge tone="danger" icon={<X className="h-3 w-3" aria-hidden />}>Incorrect</Badge>
        )}
      </div>
      <p className="mt-2 whitespace-pre-line text-[15.5px] font-medium leading-relaxed tracking-[-0.01em] text-fg">{q.prompt}</p>
      <ul className="mt-4 space-y-2">
        {q.options.map((opt, oi) => {
          const isCorrect = known && oi === q.correct_option;
          const isChosenWrong = chosen === oi && (known ? oi !== q.correct_option : correct === false);
          return <OptionLine key={oi} index={oi} text={opt} state={isCorrect ? "correct" : isChosenWrong ? "wrong" : "neutral"} />;
        })}
      </ul>
      {chosen === null || chosen === undefined ? (
        correct === false ? <p className="mt-3 text-xs text-muted">You didn&apos;t answer this question.</p> : null
      ) : !known && correct !== undefined ? (
        <p className="mt-3 text-xs text-muted">Your answer: {q.options[chosen] ?? "—"}</p>
      ) : null}
      {q.explanation ? (
        <p className="mt-4 rounded-[var(--radius-md)] border-l-2 border-accent bg-[linear-gradient(90deg,var(--accent-soft),transparent_80%)] px-3 py-2.5 text-sm leading-relaxed text-muted">
          <span className="font-medium text-fg">Why: </span>
          {q.explanation}
        </p>
      ) : null}
    </li>
  );
}

/** Space reserved for the sticky site header when revealing a focused quiz option. */
const HEADER_CLEARANCE = 96;

/** One segment per question, filled once it has an answer (from the learner's own selections). */
function AnsweredSegments({ questions, answers }: { questions: QuizQuestionView[]; answers: Record<string, number> }) {
  return (
    <div aria-hidden className="mt-2 flex max-w-xs gap-1">
      {questions.map((q) => (
        <span
          key={q.id}
          className={cn("h-1 min-w-0 flex-1 rounded-full transition-colors duration-300", answers[q.id] !== undefined ? "bg-brand" : "bg-surface-3")}
        />
      ))}
    </div>
  );
}

export function QuizPanel({
  courseSlug,
  lesson,
  canSubmit,
  gate,
  onProgress,
}: {
  courseSlug: string;
  lesson: LessonDetail;
  /** False when the viewer is not enrolled (the server would answer 409 not_enrolled). */
  canSubmit: boolean;
  gate?: ReactNode;
  onProgress: (r: ProgressResult) => void | Promise<void>;
}) {
  const questions = lesson.questions;
  const passedBefore = lesson.progress.status === "completed";
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [result, setResult] = useState<QuizResult | null>(null);
  const [retaking, setRetaking] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  const submit = useAction((a: Record<string, number>) => post<QuizResult>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}/quiz`, { answers: a }), {
    onSuccess: async (r) => {
      setResult(r);
      await onProgress(r);
    },
  });

  useEffect(() => {
    if (result) resultRef.current?.focus();
  }, [result]);

  if (!questions.length) {
    return <EmptyState title="No questions yet" description="The course author hasn't added questions to this quiz." />;
  }

  /**
   * Keeps a keyboard-focused option clear of the sticky site header and the sticky submit bar (WCAG 2.4.11).
   * Browsers skip focus scrolling for anything already inside the viewport even when a sticky bar covers it,
   * so an option that lands under either is centred. Pointer focus is left alone so clicks never jump the page.
   */
  const revealFocusedOption = (e: FocusEvent<HTMLInputElement>) => {
    if (!e.currentTarget.matches(":focus-visible")) return;
    const label = e.currentTarget.closest("label");
    if (!label) return;
    const r = label.getBoundingClientRect();
    const floor = Math.min(barRef.current?.getBoundingClientRect().top ?? Infinity, window.innerHeight) - 12;
    if (r.bottom > floor || r.top < HEADER_CLEARANCE) label.scrollIntoView({ block: "center" });
  };

  const answered = questions.filter((q) => answers[q.id] !== undefined).length;
  const allAnswered = answered === questions.length;
  const meta = (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
      <span className="tabular">
        {questions.length} question{questions.length === 1 ? "" : "s"}
      </span>
      <span aria-hidden className="text-subtle">·</span>
      <span className="tabular">pass mark {lesson.pass_threshold}%</span>
      {lesson.progress.attempts ? (
        <>
          <span aria-hidden className="text-subtle">·</span>
          <span className="tabular">
            {lesson.progress.attempts} attempt{lesson.progress.attempts === 1 ? "" : "s"}
          </span>
        </>
      ) : null}
      {lesson.progress.quiz_score !== null ? (
        <>
          <span aria-hidden className="text-subtle">·</span>
          <span className="tabular">best score {lesson.progress.quiz_score}%</span>
        </>
      ) : null}
    </p>
  );

  // Result of the attempt just submitted
  if (result) {
    const byId = new Map(result.results.map((r) => [r.question_id, r]));
    return (
      <div className="space-y-4">
        <div
          ref={resultRef}
          tabIndex={-1}
          role="status"
          className={cn(
            "relative isolate overflow-hidden rounded-[var(--radius-xl)] border p-5 outline-none animate-scale-in sm:p-6",
            result.passed
              ? "border-[color-mix(in_oklab,var(--success)_35%,transparent)] bg-success-soft"
              : "border-[color-mix(in_oklab,var(--warning)_35%,transparent)] bg-warning-soft",
          )}
        >
          <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_65%)]" />
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            {result.passed ? (
              <SuccessMark animate size="lg" />
            ) : (
              <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-[color-mix(in_oklab,var(--warning)_35%,transparent)] bg-warning-soft text-warning">
                <TriangleAlert className="h-6 w-6" aria-hidden />
              </span>
            )}
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold tracking-[-0.02em] text-fg">{result.passed ? "Quiz passed!" : "Not quite yet"}</h2>
              <p className="mt-0.5 text-sm text-fg/80">
                You scored <strong>{result.score}%</strong> ({result.correct} of {result.total} correct). The pass mark is {result.pass_threshold}%.
                {" "}Attempt {result.attempts}.
              </p>
            </div>
            <p aria-hidden className="tabular shrink-0 text-4xl font-semibold leading-none tracking-[-0.04em] text-fg sm:text-5xl">
              {result.score}
              <span className="text-xl text-muted sm:text-2xl">%</span>
            </p>
          </div>
          <ScoreBar score={result.score} threshold={result.pass_threshold} passed={result.passed} label={`Your score: ${result.score}%`} />
          {!result.passed ? (
            <p className="mt-4 text-sm text-fg/80">
              Review the lesson and try again. Correct answers and explanations are revealed once you pass.
            </p>
          ) : null}
        </div>

        <ol className="space-y-3" aria-label="Your answers">
          {questions.map((q, i) => {
            const r = byId.get(q.id);
            const merged: QuizQuestionView = {
              ...q,
              correct_option: r?.correct_option ?? q.correct_option,
              explanation: r?.explanation ?? q.explanation,
            };
            return <QuestionReview key={q.id} q={merged} index={i} total={questions.length} chosen={r?.chosen} correct={r?.correct} />;
          })}
        </ol>

        <div className="flex flex-wrap gap-2 border-t border-border pt-4">
          {result.passed ? (
            lesson.next ? (
              <LinkButton href={`/learn/${courseSlug}/${lesson.next.slug}`} className="max-w-full">
                <span className="min-w-0 truncate">Next lesson: {lesson.next.title}</span>
              </LinkButton>
            ) : (
              <LinkButton href={`/learn/${courseSlug}`}>Back to course</LinkButton>
            )
          ) : null}
          <Button
            variant={result.passed ? "secondary" : "primary"}
            icon={<RotateCcw className="h-4 w-4" aria-hidden />}
            onClick={() => {
              setResult(null);
              setRetaking(true);
              if (result.passed) setAnswers({});
            }}
          >
            {result.passed ? "Retake quiz" : "Try again"}
          </Button>
        </div>
      </div>
    );
  }

  // Already passed: review mode with the answers the server now reveals
  const reviewAvailable = passedBefore && questions.every((q) => q.correct_option !== undefined);
  if (reviewAvailable && !retaking) {
    return (
      <div className="space-y-4">
        <InlineNotice
          tone="success"
          title="You passed this quiz"
          action={
            <Button variant="secondary" size="sm" icon={<RotateCcw className="h-4 w-4" aria-hidden />} onClick={() => setRetaking(true)}>
              Retake for practice
            </Button>
          }
        >
          {lesson.progress.quiz_score !== null ? `Best score ${lesson.progress.quiz_score}%. ` : ""}
          Review the correct answers and explanations below.
        </InlineNotice>
        <ol className="space-y-3" aria-label="Answer review">
          {questions.map((q, i) => (
            <QuestionReview key={q.id} q={q} index={i} total={questions.length} chosen={undefined} correct={undefined} />
          ))}
        </ol>
      </div>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit || !allAnswered) return;
        submit.mutate(answers);
      }}
      className="space-y-4"
      aria-label="Quiz"
    >
      {meta}
      {questions.map((q, i) => {
        const has = answers[q.id] !== undefined;
        return (
          <fieldset key={q.id} className="rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-5 shadow-card">
            <legend className="float-left w-full">
              <span className="flex items-center justify-between gap-2">
                <span className="tabular text-eyebrow text-subtle">
                  Question {i + 1} of {questions.length}
                </span>
                {has ? (
                  <span className="inline-flex items-center gap-1 text-xs text-accent-strong">
                    <Check className="h-3.5 w-3.5" aria-hidden /> Answered
                  </span>
                ) : null}
              </span>
              <span className="mt-2 block whitespace-pre-line text-[15.5px] font-medium leading-relaxed tracking-[-0.01em] text-fg">{q.prompt}</span>
            </legend>
            <div className="clear-left space-y-2 pt-4">
              {q.options.map((opt, oi) => {
                const id = `q-${q.id}-${oi}`;
                const checked = answers[q.id] === oi;
                return (
                  <label
                    key={oi}
                    htmlFor={id}
                    className={cn(
                      "group flex min-h-11 cursor-pointer items-center gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 text-sm transition-[background-color,border-color,box-shadow] duration-200",
                      "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--ring)]",
                      "has-[:disabled]:cursor-progress has-[:disabled]:opacity-70",
                      checked
                        ? "border-[color-mix(in_oklab,var(--accent)_70%,transparent)] bg-accent-soft text-fg shadow-[0_0_0_1px_color-mix(in_oklab,var(--accent)_40%,transparent)]"
                        : "border-border bg-bg-elevated/40 text-fg hover:border-border-strong hover:bg-surface-2",
                    )}
                  >
                    <input
                      id={id}
                      type="radio"
                      name={`q-${q.id}`}
                      value={oi}
                      checked={checked}
                      onChange={() => setAnswers((a) => ({ ...a, [q.id]: oi }))}
                      onFocus={revealFocusedOption}
                      disabled={submit.isPending}
                      className="sr-only"
                    />
                    <span
                      aria-hidden
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[11px] font-medium transition-colors duration-200",
                        checked ? "border-transparent bg-accent text-accent-fg" : "border-border-strong text-subtle group-hover:text-fg",
                      )}
                    >
                      {letter(oi)}
                    </span>
                    <span className="min-w-0 flex-1">{opt}</span>
                    {checked ? <Check className="h-4 w-4 shrink-0 text-accent-strong animate-pop" aria-hidden /> : null}
                  </label>
                );
              })}
            </div>
          </fieldset>
        );
      })}
      {gate}
      <div ref={barRef} className="sticky bottom-3 z-20 flex items-center gap-3 rounded-[var(--radius-lg)] border border-border-strong bg-[var(--glass-strong)] px-3 py-2 shadow-elevated backdrop-blur-xl sm:px-4 sm:py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] text-muted sm:text-sm" aria-live="polite">
            <span className="tabular">
              {answered} of {questions.length}
            </span>{" "}
            answered
            <span className="max-sm:sr-only">{!allAnswered ? " — answer every question to submit." : "."}</span>
          </p>
          <AnsweredSegments questions={questions} answers={answers} />
        </div>
        <div className="ml-auto flex shrink-0 gap-2">
          {retaking && passedBefore ? (
            <Button variant="ghost" className="max-sm:px-3" onClick={() => { setRetaking(false); setAnswers({}); }}>
              Cancel
            </Button>
          ) : null}
          <Button type="submit" className="max-sm:px-3.5" loading={submit.isPending} disabled={!canSubmit || !allAnswered}>
            Submit<span className="max-sm:sr-only"> answers</span>
          </Button>
        </div>
      </div>
    </form>
  );
}
