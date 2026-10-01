"use client";

import { CircleCheck, RefreshCw, Trophy } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useAction } from "@/components/discussions/use-action";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { InlineNotice } from "@/components/ui/states";
import { post } from "@/lib/api";
import type { ChallengeResult, LessonDetail, ProgressResult } from "./types";

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
    <Card>
      <CardHeader
        title="Hands-on challenge"
        description="Completion is verified on the server from your scored submissions — it can't be self-reported."
        action={done ? <Badge tone="success" icon={<CircleCheck className="h-3 w-3" aria-hidden />}>Completed</Badge> : null}
      />
      <CardBody className="space-y-4">
        {ch.instructions ? <p className="whitespace-pre-line text-sm text-fg">{ch.instructions}</p> : null}
        {ch.competition ? (
          <div className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-border bg-surface-2 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <Trophy className="h-5 w-5 shrink-0 text-accent-strong" aria-hidden />
              <div>
                <p className="text-xs text-subtle">Linked practice competition</p>
                <p className="font-medium text-fg">{ch.competition.title}</p>
              </div>
            </div>
            <LinkButton href={`/competitions/${ch.competition.slug}`} variant="secondary" size="sm">
              Open competition
            </LinkButton>
          </div>
        ) : (
          <InlineNotice tone="warning">The linked practice competition is currently unavailable.</InlineNotice>
        )}
        <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
          <li>Join the linked competition and make a submission.</li>
          <li>Wait until the submission is scored.</li>
          <li>Come back here and check completion.</li>
        </ol>
        {gate}
        {result ? (
          <InlineNotice tone={result.passed ? "success" : "info"} title={result.passed ? "Challenge complete" : "Not complete yet"}>
            {result.message}
          </InlineNotice>
        ) : null}
      </CardBody>
      <CardFooter className="justify-between">
        <span className="text-xs text-subtle">
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
