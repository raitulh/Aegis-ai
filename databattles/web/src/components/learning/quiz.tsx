"use client";

import { Check, CircleCheck, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { EmptyState, InlineNotice } from "@/components/ui/states";
import { post } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { LessonDetail, ProgressResult, QuizQuestionView, QuizResult } from "./types";

function OptionLine({ text, state }: { text: string; state: "correct" | "wrong" | "neutral" }) {
  return (
    <li
      className={cn(
        "flex items-start gap-2 rounded-[var(--radius-md)] border px-3 py-2 text-sm",
        state === "correct" && "border-success/40 bg-success-soft text-fg",
        state === "wrong" && "border-danger/40 bg-danger-soft text-fg",
        state === "neutral" && "border-border text-muted",
      )}
    >
      {state === "correct" ? (
        <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
      ) : state === "wrong" ? (
        <X className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden />
      ) : (
        <span className="h-4 w-4 shrink-0" aria-hidden />
      )}
      <span>
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
    <li className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-subtle">Question {index + 1} of {total}</span>
        {correct === undefined ? null : correct ? (
          <Badge tone="success" icon={<Check className="h-3 w-3" aria-hidden />}>Correct</Badge>
        ) : (
          <Badge tone="danger" icon={<X className="h-3 w-3" aria-hidden />}>Incorrect</Badge>
        )}
      </div>
      <p className="mt-1.5 whitespace-pre-line font-medium text-fg">{q.prompt}</p>
      <ul className="mt-3 space-y-1.5">
        {q.options.map((opt, oi) => {
          const isCorrect = known && oi === q.correct_option;
          const isChosenWrong = chosen === oi && (known ? oi !== q.correct_option : correct === false);
          return <OptionLine key={oi} text={opt} state={isCorrect ? "correct" : isChosenWrong ? "wrong" : "neutral"} />;
        })}
      </ul>
      {chosen === null || chosen === undefined ? (
        correct === false ? <p className="mt-2 text-xs text-muted">You didn&apos;t answer this question.</p> : null
      ) : !known && correct !== undefined ? (
        <p className="mt-2 text-xs text-muted">Your answer: {q.options[chosen] ?? "—"}</p>
      ) : null}
      {q.explanation ? (
        <p className="mt-3 rounded-[var(--radius-md)] bg-surface-2 px-3 py-2 text-sm text-muted">
          <span className="font-medium text-fg">Why: </span>
          {q.explanation}
        </p>
      ) : null}
    </li>
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

  const answered = questions.filter((q) => answers[q.id] !== undefined).length;
  const allAnswered = answered === questions.length;
  const meta = (
    <p className="text-sm text-muted">
      {questions.length} question{questions.length === 1 ? "" : "s"} · pass mark {lesson.pass_threshold}%
      {lesson.progress.attempts ? ` · ${lesson.progress.attempts} attempt${lesson.progress.attempts === 1 ? "" : "s"}` : ""}
      {lesson.progress.quiz_score !== null ? ` · best score ${lesson.progress.quiz_score}%` : ""}
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
            "rounded-[var(--radius-lg)] border p-5 outline-none",
            result.passed ? "border-success/40 bg-success-soft" : "border-warning/40 bg-warning-soft",
          )}
        >
          <div className="flex items-center gap-3">
            {result.passed ? (
              <CircleCheck className="h-7 w-7 shrink-0 text-success" aria-hidden />
            ) : (
              <TriangleAlert className="h-7 w-7 shrink-0 text-warning" aria-hidden />
            )}
            <div>
              <h2 className="text-lg font-semibold text-fg">{result.passed ? "Quiz passed!" : "Not quite yet"}</h2>
              <p className="text-sm text-fg/80">
                You scored <strong>{result.score}%</strong> ({result.correct} of {result.total} correct). The pass mark is {result.pass_threshold}%.
                {" "}Attempt {result.attempts}.
              </p>
            </div>
          </div>
          {!result.passed ? (
            <p className="mt-3 text-sm text-fg/80">
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

        <div className="flex flex-wrap gap-2">
          {result.passed ? (
            lesson.next ? (
              <LinkButton href={`/learn/${courseSlug}/${lesson.next.slug}`}>Next lesson: {lesson.next.title}</LinkButton>
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
      {questions.map((q, i) => (
        <fieldset key={q.id} className="rounded-[var(--radius-lg)] border border-border bg-surface p-4">
          <legend className="float-left w-full">
            <span className="block text-xs text-subtle">
              Question {i + 1} of {questions.length}
            </span>
            <span className="mt-1 block whitespace-pre-line font-medium text-fg">{q.prompt}</span>
          </legend>
          <div className="clear-left space-y-2 pt-3">
            {q.options.map((opt, oi) => {
              const id = `q-${q.id}-${oi}`;
              const checked = answers[q.id] === oi;
              return (
                <label
                  key={oi}
                  htmlFor={id}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 text-sm transition-colors",
                    "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--ring)]",
                    checked ? "border-accent bg-accent-soft text-fg" : "border-border text-fg hover:bg-surface-2",
                  )}
                >
                  <input
                    id={id}
                    type="radio"
                    name={`q-${q.id}`}
                    value={oi}
                    checked={checked}
                    onChange={() => setAnswers((a) => ({ ...a, [q.id]: oi }))}
                    disabled={submit.isPending}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                  />
                  <span>{opt}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
      {gate}
      <div className="flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-surface-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted" aria-live="polite">
          {answered} of {questions.length} answered
          {!allAnswered ? " — answer every question to submit." : "."}
        </p>
        <div className="flex gap-2">
          {retaking && passedBefore ? (
            <Button variant="ghost" onClick={() => { setRetaking(false); setAnswers({}); }}>
              Cancel
            </Button>
          ) : null}
          <Button type="submit" loading={submit.isPending} disabled={!canSubmit || !allAnswered}>
            Submit answers
          </Button>
        </div>
      </div>
    </form>
  );
}
