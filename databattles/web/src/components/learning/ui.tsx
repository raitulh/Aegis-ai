"use client";

import { Award, Check, ChevronRight, Circle, CircleCheck, CircleDot, FileText, ListChecks, Swords } from "lucide-react";
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

const DIFFICULTY_LEVEL: Record<string, number> = { beginner: 1, intermediate: 2, advanced: 3 };

/**
 * Three-step level glyph. Purely decorative (aria-hidden): it always sits next to the difficulty text,
 * so colour and bar height are never the only signal.
 */
export function DifficultyMeter({ difficulty, className }: { difficulty: string; className?: string }) {
  const level = DIFFICULTY_LEVEL[difficulty] ?? 0;
  return (
    <span aria-hidden className={cn("inline-flex h-3 items-end gap-[2px]", className)}>
      {[1, 2, 3].map((n) => (
        <span
          key={n}
          className={cn("w-[3px] rounded-full", n === 1 ? "h-1.5" : n === 2 ? "h-2.5" : "h-3", n <= level ? "bg-current" : "bg-border-strong")}
        />
      ))}
    </span>
  );
}

export function DifficultyBadge({ difficulty, className }: { difficulty: string; className?: string }) {
  return (
    <Badge tone={DIFFICULTY_TONE[difficulty] ?? "neutral"} className={className} icon={<DifficultyMeter difficulty={difficulty} />}>
      {titleCase(difficulty)}
    </Badge>
  );
}

export const KIND_LABEL: Record<LessonKind, string> = { article: "Reading", quiz: "Quiz", challenge: "Challenge" };
const KIND_PLURAL: Record<LessonKind, string> = { article: "readings", quiz: "quizzes", challenge: "challenges" };

export function LessonKindIcon({ kind, className }: { kind: LessonKind; className?: string }) {
  const Icon = kind === "quiz" ? ListChecks : kind === "challenge" ? Swords : FileText;
  return <Icon className={cn("h-4 w-4 shrink-0", className)} aria-hidden />;
}

/** "2 readings · 1 quiz" — counted from the real lesson list. */
export function kindSummary(lessons: { kind: LessonKind }[]): string {
  const counts = new Map<LessonKind, number>();
  for (const l of lessons) counts.set(l.kind, (counts.get(l.kind) ?? 0) + 1);
  return (["article", "quiz", "challenge"] as LessonKind[])
    .filter((k) => counts.get(k))
    .map((k) => {
      const n = counts.get(k) ?? 0;
      return `${n} ${n === 1 ? KIND_LABEL[k].toLowerCase() : KIND_PLURAL[k]}`;
    })
    .join(" · ");
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
    <Badge tone={tone} icon={<LessonStatusIcon status={status} className="text-current [&_svg]:h-3 [&_svg]:w-3" />}>
      <span aria-hidden>{STATUS_LABEL[status]}</span>
    </Badge>
  );
}

/** Badge chip for course/path badges. `color` is badge data (hex), used only for the icon tint. */
export function EarnableBadge({
  name,
  color,
  description,
  className,
  earned = false,
}: {
  name: string;
  color?: string | null;
  description?: string | null;
  className?: string;
  /** True only when the API says the learner already holds it. */
  earned?: boolean;
}) {
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <span
        className={cn(
          "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full border bg-bg-elevated shadow-[inset_0_1px_0_var(--hairline-highlight)]",
          earned ? "border-[color-mix(in_oklab,var(--success)_45%,transparent)]" : "border-border-strong",
        )}
      >
        <span aria-hidden className="absolute inset-1 rounded-full border border-dashed border-border" />
        <Award className="relative h-4 w-4 text-accent-strong" style={color ? { color } : undefined} aria-hidden />
      </span>
      <div className="min-w-0 pt-0.5">
        <p className="text-sm font-medium text-fg">{name}</p>
        {description ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p> : null}
      </div>
    </div>
  );
}

/**
 * A station on a lesson track: the lesson number, or a check once the server marks it complete.
 * The current lesson gets an accent halo (it is also marked with aria-current on its link).
 */
export function LessonNode({
  index,
  status,
  showProgress,
  current,
  dense,
}: {
  index: number;
  status: LessonStatus;
  showProgress: boolean;
  current?: boolean;
  dense?: boolean;
}) {
  const done = showProgress && status === "completed";
  const active = showProgress && status === "in_progress";
  return (
    <span
      aria-hidden
      className={cn(
        "tabular relative z-10 flex shrink-0 items-center justify-center rounded-full border font-mono font-medium transition-[box-shadow,border-color,color] duration-300",
        dense ? "h-6 w-6 text-[10px]" : "h-8 w-8 text-[11px]",
        done
          ? "border-[color-mix(in_oklab,var(--success)_45%,transparent)] bg-success-soft text-success"
          : active
            ? "border-[color-mix(in_oklab,var(--accent)_55%,transparent)] bg-accent-soft text-accent-strong"
            : "border-border-strong bg-bg-elevated text-subtle",
        current && "border-accent text-accent-strong shadow-[0_0_0_4px_var(--accent-soft)]",
      )}
    >
      {done ? <Check className={dense ? "h-3 w-3" : "h-3.5 w-3.5"} strokeWidth={2.6} /> : String(index + 1).padStart(2, "0")}
    </span>
  );
}

/**
 * Ordered lesson outline drawn as a track: each lesson is a station on one line, completed stretches of
 * the line are lit with the brand gradient. Highlights the current lesson (`aria-current="page"`).
 */
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
    <ol className={cn(!dense && "rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-1.5 shadow-card sm:p-2")}>
      {lessons.map((l, i) => {
        const current = l.slug === currentSlug;
        const last = i === lessons.length - 1;
        const lit = showProgress && l.status === "completed";
        return (
          <li key={l.id} className="relative">
            {!last ? (
              <span
                aria-hidden
                className={cn(
                  "absolute w-px",
                  dense ? "left-[19.5px] top-[34px] -bottom-[10px]" : "left-[27.5px] top-[44px] -bottom-[12px]",
                  lit ? "bg-brand" : "bg-border-strong",
                )}
              />
            ) : null}
            <Link
              href={`/learn/${courseSlug}/${l.slug}`}
              aria-current={current ? "page" : undefined}
              className={cn(
                "group flex items-start rounded-[var(--radius-md)] transition-colors duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--ring)]",
                dense ? "gap-3 px-2 py-2.5" : "gap-4 px-3 py-3",
                current ? "bg-accent-soft" : "hover:bg-surface-2",
              )}
            >
              <LessonNode index={i} status={l.status} showProgress={showProgress} current={current} dense={dense} />
              <span className={cn("min-w-0 flex-1", dense ? "pt-px" : "pt-0.5")}>
                <span
                  className={cn(
                    "block truncate font-medium transition-colors",
                    dense ? "text-[13px]" : "text-[15px] tracking-[-0.01em]",
                    current ? "text-accent-strong" : "text-fg group-hover:text-accent-strong",
                  )}
                >
                  {l.title}
                </span>
                <span className={cn("mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-subtle", dense ? "text-[11px]" : "text-xs")}>
                  {!dense ? <LessonKindIcon kind={l.kind} className="h-3.5 w-3.5" /> : null}
                  <span>{KIND_LABEL[l.kind]}</span>
                  <span aria-hidden>·</span>
                  <span className="tabular">{formatMinutes(l.estimated_minutes)}</span>
                  {showProgress && l.kind === "quiz" && l.quiz_score !== null ? (
                    <>
                      <span aria-hidden>·</span>
                      <span className="tabular">Best score {l.quiz_score}%</span>
                    </>
                  ) : null}
                  {showProgress && l.status === "in_progress" ? (
                    <>
                      <span aria-hidden>·</span>
                      <span className="text-accent-strong">In progress</span>
                    </>
                  ) : null}
                  {showProgress && l.status !== "in_progress" ? <span className="sr-only">({STATUS_LABEL[l.status]})</span> : null}
                </span>
              </span>
              {!dense ? (
                <ChevronRight
                  className="mt-2 h-4 w-4 shrink-0 text-subtle transition-[transform,color] duration-300 group-hover:translate-x-0.5 group-hover:text-accent-strong"
                  aria-hidden
                />
              ) : null}
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
