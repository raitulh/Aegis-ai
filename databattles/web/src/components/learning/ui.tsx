"use client";

import { Award, Circle, CircleCheck, CircleDot, FileText, ListChecks, Swords } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";
import type { LessonKind, LessonStatus, OutlineLesson } from "./types";

export function formatMinutes(min: number | null | undefined): string {
  if (!min || min <= 0) return "—";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

const DIFFICULTY_TONE: Record<string, "success" | "info" | "warning"> = {
  beginner: "success",
  intermediate: "info",
  advanced: "warning",
};

export function DifficultyBadge({ difficulty, className }: { difficulty: string; className?: string }) {
  return (
    <Badge tone={DIFFICULTY_TONE[difficulty] ?? "neutral"} className={className}>
      {titleCase(difficulty)}
    </Badge>
  );
}

export const KIND_LABEL: Record<LessonKind, string> = { article: "Reading", quiz: "Quiz", challenge: "Challenge" };

export function LessonKindIcon({ kind, className }: { kind: LessonKind; className?: string }) {
  const Icon = kind === "quiz" ? ListChecks : kind === "challenge" ? Swords : FileText;
  return <Icon className={cn("h-4 w-4 shrink-0", className)} aria-hidden />;
}

export const STATUS_LABEL: Record<LessonStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
};

/** Status glyph with a text alternative (color is never the only signal). */
export function LessonStatusIcon({ status, className }: { status: LessonStatus; className?: string }) {
  const Icon = status === "completed" ? CircleCheck : status === "in_progress" ? CircleDot : Circle;
  const tone = status === "completed" ? "text-success" : status === "in_progress" ? "text-accent-strong" : "text-subtle";
  return (
    <span className={cn("inline-flex shrink-0", tone, className)}>
      <Icon className="h-4 w-4" aria-hidden />
      <span className="sr-only">{STATUS_LABEL[status]}</span>
    </span>
  );
}

export function LessonStatusBadge({ status }: { status: LessonStatus }) {
  const tone = status === "completed" ? "success" : status === "in_progress" ? "accent" : "neutral";
  return (
    <Badge tone={tone} icon={<LessonStatusIcon status={status} className="text-current" />}>
      <span aria-hidden>{STATUS_LABEL[status]}</span>
    </Badge>
  );
}

/** Badge chip for course/path badges. `color` is badge data (hex), used only for the icon tint. */
export function EarnableBadge({ name, color, description, className }: { name: string; color?: string | null; description?: string | null; className?: string }) {
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2">
        <Award className="h-4 w-4" style={color ? { color } : undefined} aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{name}</p>
        {description ? <p className="text-xs text-muted">{description}</p> : null}
      </div>
    </div>
  );
}

/** Ordered lesson outline with per-lesson status; highlights the current lesson. */
export function CourseOutline({
  courseSlug,
  lessons,
  currentSlug,
  showProgress = true,
  dense,
}: {
  courseSlug: string;
  lessons: OutlineLesson[];
  currentSlug?: string;
  showProgress?: boolean;
  dense?: boolean;
}) {
  if (!lessons.length) return <p className="px-1 py-3 text-sm text-muted">No lessons yet.</p>;
  return (
    <ol className={cn("divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface", dense && "text-sm")}>
      {lessons.map((l, i) => {
        const current = l.slug === currentSlug;
        return (
          <li key={l.id}>
            <Link
              href={`/learn/${courseSlug}/${l.slug}`}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                dense ? "px-3 py-2.5" : "px-4 py-3",
                current && "bg-accent-soft",
              )}
            >
              {showProgress ? <LessonStatusIcon status={l.status} /> : null}
              <span className="w-5 shrink-0 text-right text-xs tabular-nums text-subtle" aria-hidden>
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className={cn("block truncate font-medium", current ? "text-accent-strong" : "text-fg")}>{l.title}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-subtle">
                  <span>{KIND_LABEL[l.kind]}</span>
                  <span aria-hidden>·</span>
                  <span>{formatMinutes(l.estimated_minutes)}</span>
                  {showProgress && l.kind === "quiz" && l.quiz_score !== null ? (
                    <>
                      <span aria-hidden>·</span>
                      <span>Best score {l.quiz_score}%</span>
                    </>
                  ) : null}
                </span>
              </span>
              <LessonKindIcon kind={l.kind} className="text-subtle" />
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
