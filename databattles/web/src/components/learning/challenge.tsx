"use client";

import { Check, CircleCheck, RefreshCw, Swords, Trophy } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { InlineNotice } from "@/components/ui/states";
import { post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SuccessMark } from "./celebrate";
import type { ChallengeResult, LessonDetail, ProgressResult } from "./types";

const STEPS = [
  "Join the linked competition and make a submission.",
  "Wait until the submission is scored.",
  "Come back here and check completion.",
];

export function ChallengePanel({
  courseSlug,
  lesson,
  canSubmit,
  gate,
  onProgress,
}: {
  courseSlug: string;
  lesson: LessonDetail;
  canSubmit: boolean;
  gate?: ReactNode;
  onProgress: (r: ProgressResult) => void | Promise<void>;
}) {
  const [result, setResult] = useState<ChallengeResult | null>(null);
  const check = useAction(() => post<ChallengeResult>(`/learn/courses/${courseSlug}/lessons/${lesson.slug}/check`), {
    onSuccess: async (r) => {
      setResult(r);
      await onProgress(r);
    },
  });
  const ch = lesson.challenge;
  const done = lesson.progress.status === "completed" || result?.passed;

  if (!ch) {
    return (
      <InlineNotice tone="warning" title="Challenge not configured">
        The course author hasn&apos;t linked a practice competition to this challenge yet.
      </InlineNotice>
    );
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={<Swords aria-hidden />}
        title="Hands-on challenge"
        description="Completion is verified on the server from your scored submissions — it can't be self-reported."
        action={done ? <Badge tone="success" icon={<CircleCheck className="h-3 w-3" aria-hidden />}>Completed</Badge> : null}
      />
      <CardBody className="space-y-5 py-5">
        {ch.instructions ? <p className="whitespace-pre-line text-[15px] leading-relaxed text-fg">{ch.instructions}</p> : null}
        {ch.competition ? (
          <div className="relative isolate flex flex-col gap-4 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-bg-elevated p-4 sm:flex-row sm:items-center sm:justify-between">
            <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 dot-grid opacity-40 [mask-image:linear-gradient(90deg,transparent,black)]" />
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-accent-soft text-accent-strong">
                <Trophy className="h-[18px] w-[18px]" aria-hidden />
              </span>
              <div className="min-w-0">
                <p className="text-eyebrow text-subtle">Linked practice competition</p>
                <p className="mt-0.5 truncate font-medium text-fg">{ch.competition.title}</p>
              </div>
            </div>
            <LinkButton href={`/competitions/${ch.competition.slug}`} variant="secondary" size="sm" className="self-start sm:self-auto">
              Open competition
            </LinkButton>
          </div>
        ) : (
          <InlineNotice tone="warning">The linked practice competition is currently unavailable.</InlineNotice>
        )}

        <ol className="relative grid grid-cols-1 gap-0 sm:grid-cols-3" aria-label="How to complete this challenge">
          {STEPS.map((s, i) => {
            const last = i === STEPS.length - 1;
            return (
              <li key={s} className="relative flex gap-3 pb-4 last:pb-0 sm:block sm:pb-0 sm:pr-5">
                {!last ? (
                  <span
                    aria-hidden
                    className={cn(
                      "absolute bottom-1 left-[13.5px] top-8 w-px sm:bottom-auto sm:left-9 sm:right-2 sm:top-[13.5px] sm:h-px sm:w-auto",
                      done ? "bg-brand" : "bg-border-strong",
                    )}
                  />
                ) : null}
                <span
                  aria-hidden
                  className={cn(
                    "tabular relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-[10.5px] font-medium",
                    done
                      ? "border-[color-mix(in_oklab,var(--success)_45%,transparent)] bg-success-soft text-success"
                      : "border-border-strong bg-bg-elevated text-subtle",
                  )}
                >
                  {done ? <Check className="h-3.5 w-3.5" strokeWidth={2.6} /> : String(i + 1).padStart(2, "0")}
                </span>
                <p className="pt-1 text-sm leading-relaxed text-muted sm:mt-2.5 sm:pt-0">{s}</p>
              </li>
            );
          })}
        </ol>

        {gate}
        {result ? (
          result.passed ? (
            <div
              role="status"
              className="flex items-start gap-3.5 rounded-[var(--radius-lg)] border border-[color-mix(in_oklab,var(--success)_30%,transparent)] bg-success-soft p-4 animate-scale-in"
            >
              <SuccessMark animate />
              <div className="min-w-0 pt-0.5">
                <p className="font-semibold text-fg">Challenge complete</p>
                <p className="mt-0.5 text-sm text-fg/80">{result.message}</p>
              </div>
            </div>
          ) : (
            <InlineNotice tone="info" title="Not complete yet">
              {result.message}
            </InlineNotice>
          )
        ) : null}
      </CardBody>
      <CardFooter className="flex-wrap justify-between">
        <span className="tabular text-xs text-subtle">
          {lesson.progress.attempts ? `${lesson.progress.attempts} check${lesson.progress.attempts === 1 ? "" : "s"} so far` : ""}
        </span>
        <Button
          onClick={() => check.mutate()}
          loading={check.isPending}
          disabled={!canSubmit}
          variant={done ? "secondary" : "primary"}
          icon={<RefreshCw className="h-4 w-4" aria-hidden />}
        >
          Check completion
        </Button>
      </CardFooter>
    </Card>
  );
}
