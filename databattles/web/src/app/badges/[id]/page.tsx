"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleX, EyeOff, Fingerprint, ListChecks, ScrollText, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useId } from "react";

import { BadgeIcon } from "@/components/profile/badge-icon";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { CopyButton, KeyValue } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { ApiError, get } from "@/lib/api";
import { formatDate, formatDateTime, titleCase } from "@/lib/format";
import type { Schemas } from "@/lib/types";

type BadgeVerify = Schemas["BadgeVerifyOut"];

const HEX = /^#[0-9a-f]{6}$/i;

function Invalid({ id }: { id: string }) {
  return (
    <div className="relative mx-auto max-w-xl overflow-hidden rounded-[var(--radius-xl)] border border-danger/30 bg-surface px-6 py-12 text-center shadow-card" role="alert">
      <div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: "radial-gradient(60% 50% at 50% 0%, var(--danger-soft), transparent)" }} />
      <div className="relative">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-danger/30 bg-danger-soft text-danger">
          <CircleX className="h-6 w-6" aria-hidden />
        </span>
        <p className="mt-5 text-eyebrow text-danger">Verification failed</p>
        <h1 className="mt-2 text-title text-fg">No badge award found</h1>
        <p className="mx-auto mt-3 w-fit max-w-full break-all rounded-md border border-border bg-bg-elevated px-2.5 py-1 font-mono text-sm text-muted">{id}</p>
        <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-muted">No badge has been awarded with this ID. Check the link you were given — a badge that can&apos;t be verified here should not be trusted.</p>
        <div className="mt-7 flex justify-center">
          <LinkButton href="/" variant="secondary">Go home</LinkButton>
        </div>
      </div>
    </div>
  );
}

/** Concentric orbit rings around the badge mark — the DataBattles motif, tinted by the badge's own colour. */
function BadgeMark({ icon, color }: { icon: string; color: string }) {
  const id = useId().replace(/:/g, "");
  const tint = HEX.test(color) ? color : "var(--accent)";
  return (
    <div className="relative mx-auto h-44 w-44 sm:h-52 sm:w-52" aria-hidden>
      <div className="absolute inset-6 rounded-full opacity-50 blur-2xl" style={{ background: tint }} />
      <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" fill="none">
        <defs>
          <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--accent-strong)" />
            <stop offset="1" stopColor="var(--cyan)" />
          </linearGradient>
        </defs>
        <circle cx="50" cy="50" r="36" stroke="var(--border-strong)" strokeWidth="0.4" />
        <circle cx="50" cy="50" r="36" stroke={`url(#${id}-g)`} strokeWidth="0.7" strokeLinecap="round" pathLength={100} strokeDasharray="22 78" transform="rotate(-90 50 50)" />
      </svg>
      <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full motion-safe:animate-spin-slow" fill="none">
        <circle cx="50" cy="50" r="47" stroke="var(--border)" strokeWidth="0.35" strokeDasharray="0.6 1.8" />
        <circle cx="97" cy="50" r="1.1" fill="var(--cyan)" />
        <circle cx="3" cy="50" r="0.8" fill="var(--accent-strong)" />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="rounded-full bg-surface p-1.5 shadow-elevated animate-scale-in">
          <BadgeIcon icon={icon} color={color} size={96} className="ring-2" />
        </span>
      </div>
    </div>
  );
}

function criteriaValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** A positive whole number from the criteria, else the backend's own default for that key. */
function criteriaCount(raw: unknown, fallback: number): number {
  const n = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN;
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : fallback;
}

const plural = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/**
 * Readable wording for the criteria types the backend evaluates (credentials/badges.py `_check`), using the same
 * defaults (`count` 1, `max_rank` 10). Unknown types return null and fall back to the raw rule.
 */
function describeCriteria(c: Record<string, unknown>): string | null {
  const slug = (k: string) => (typeof c[k] === "string" && c[k] ? `“${c[k]}”` : null);
  switch (c.type) {
    case "first_submission":
      return "A first scored submission to any competition";
    case "competition_rank":
      return `A final rank of ${criteriaCount(c.max_rank, 10).toLocaleString()} or better in a competition`;
    case "competitions_joined":
      return `Joined at least ${plural(criteriaCount(c.count, 1), "competition", "competitions")}`;
    case "course_completed":
      return slug("course_slug") ? `Completed the course ${slug("course_slug")}` : null;
    case "courses_completed":
      return `Completed at least ${plural(criteriaCount(c.count, 1), "course", "courses")}`;
    case "path_completed":
      return slug("path_slug") ? `Completed every course in the learning path ${slug("path_slug")}` : null;
    case "merged_prs":
      return `At least ${plural(criteriaCount(c.count, 1), "merged pull request", "merged pull requests")} from a linked GitHub account`;
    case "accepted_answers":
      return `At least ${plural(criteriaCount(c.count, 1), "accepted answer", "accepted answers")} in discussions`;
    default:
      return null;
  }
}

/** The stored rule, key by key, for transparency (and for criteria types without readable wording). */
function CriteriaRule({ criteria }: { criteria: [string, unknown][] }) {
  return (
    <dl className="mt-2.5 divide-y divide-border overflow-hidden rounded-[var(--radius-md)] border border-border bg-bg-elevated">
      {criteria.map(([k, val]) => (
        <div key={k} className="flex items-center justify-between gap-4 px-3 py-2 font-mono text-xs">
          <dt className="text-subtle">{k}</dt>
          <dd className="min-w-0 truncate text-fg">{criteriaValue(val)}</dd>
        </div>
      ))}
    </dl>
  );
}

function BadgeView({ v }: { v: BadgeVerify }) {
  const b = v.badge;
  const recipient = v.recipient as { handle?: string; display_name?: string } | null;
  const url = typeof window !== "undefined" ? `${window.location.origin}/badges/${v.public_id}` : `/badges/${v.public_id}`;
  const criteria = Object.entries(b.criteria ?? {});
  const criteriaText = describeCriteria((b.criteria ?? {}) as Record<string, unknown>);

  useEffect(() => {
    const prev = document.title;
    document.title = `${b.name} badge · DataBattles`;
    return () => {
      document.title = prev;
    };
  }, [b.name]);

  return (
    <div className="space-y-6">
      <article className="relative overflow-hidden rounded-[var(--radius-2xl)] border border-border bg-surface surface-sheen px-6 pb-10 pt-8 text-center shadow-elevated sm:px-10">
        <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top,black,transparent_65%)]" />
        <div className="relative">
          <div role="status" className="mx-auto inline-flex max-w-full items-center gap-3 rounded-full border border-success/30 bg-success-soft py-1.5 pl-1.5 pr-4 text-left animate-rise">
            <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-success text-[var(--bg)] motion-safe:animate-pulse-ring">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className="motion-safe:animate-check" />
              </svg>
            </span>
            <span className="min-w-0 text-sm">
              <span className="block font-semibold text-success">Verified badge award</span>
              <span className="block text-xs text-fg/80">This badge was awarded on DataBattles on {formatDate(v.awarded_at, { dateStyle: "long" })}.</span>
            </span>
          </div>

          <div className="mt-6">
            <BadgeMark icon={b.icon} color={b.color} />
          </div>
          <p className="mt-2 text-eyebrow text-subtle">{titleCase(b.category)} badge</p>
          <h1 className="mt-2 text-title text-fg">{b.name}</h1>
          <p className="mx-auto mt-3 max-w-lg text-[15px] leading-relaxed text-muted">{b.description}</p>
          <div className="mt-5 flex flex-wrap justify-center gap-1.5">
            <Badge tone="outline">{titleCase(b.category)}</Badge>
            <Badge tone={v.manual ? "info" : "accent"}>{v.manual ? "Awarded by an organizer" : "Automatic"}</Badge>
            {b.rarity_label ? <Badge>{b.rarity_label}</Badge> : null}
          </div>
          <div className="mx-auto mt-8 flex max-w-sm items-center justify-center gap-3 border-t border-border pt-6 text-sm">
            {recipient?.handle ? (
              <>
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2 text-accent-strong">
                  <UserRound className="h-4 w-4" aria-hidden />
                </span>
                <p className="text-left text-muted">
                  Awarded to{" "}
                  <Link
                    href={`/u/${recipient.handle}`}
                    className="rounded-sm font-semibold text-fg transition-colors hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                  >
                    {recipient.display_name ?? recipient.handle}
                  </Link>
                </p>
              </>
            ) : (
              <p className="inline-flex items-center gap-1.5 text-muted">
                <EyeOff className="h-4 w-4" aria-hidden /> The recipient has chosen not to show their name.
              </p>
            )}
          </div>
        </div>
      </article>

      <div className="grid gap-6 md:grid-cols-2">
        <section className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card sm:p-6" aria-labelledby="badge-evidence">
          <h2 id="badge-evidence" className="flex items-center gap-2 text-sm font-semibold text-fg">
            <span className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-surface-2 text-accent-strong" aria-hidden>
              <ScrollText className="h-3.5 w-3.5" />
            </span>
            How it was earned
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-fg">{v.evidence_summary}</p>
          {!v.manual && criteria.length ? (
            <div className="mt-5">
              <p className="flex items-center gap-1.5 text-eyebrow text-subtle">
                <ListChecks className="h-3.5 w-3.5" aria-hidden /> Current criteria · v{b.criteria_version}
              </p>
              {criteriaText ? (
                <p className="mt-2.5 flex items-start gap-2.5 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-2.5 text-sm text-fg">
                  <BadgeCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent-strong" aria-hidden />
                  <span className="min-w-0">{criteriaText}</span>
                </p>
              ) : null}
              {criteriaText ? (
                <details className="group/rule mt-2.5">
                  <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-xs text-subtle transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] [&::-webkit-details-marker]:hidden">
                    <span className="transition-transform group-open/rule:rotate-90" aria-hidden>›</span>
                    Show the rule as stored
                  </summary>
                  <CriteriaRule criteria={criteria} />
                </details>
              ) : (
                <CriteriaRule criteria={criteria} />
              )}
            </div>
          ) : null}
          {!v.manual ? (
            <p className="mt-4 text-xs leading-relaxed text-subtle">
              Criteria can be updated over time; this award was granted under version {v.criteria_version} and is never revoked by later changes.
            </p>
          ) : null}
        </section>

        <section className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card sm:p-6" aria-labelledby="badge-record">
          <h2 id="badge-record" className="flex items-center gap-2 text-sm font-semibold text-fg">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-success-soft text-success" aria-hidden>
              <Fingerprint className="h-3.5 w-3.5" />
            </span>
            Verification record
          </h2>
          <div className="mt-4">
            <KeyValue
              items={[
                { label: "Awarded", value: <time dateTime={v.awarded_at}>{formatDateTime(v.awarded_at)}</time> },
                { label: "Award type", value: v.manual ? "Manual (by an authorized organizer)" : "Automatic (criteria met)" },
                { label: "Criteria version", value: `v${v.criteria_version}` },
                { label: "Award ID", value: <span className="font-mono tracking-[0.04em]">{v.public_id}</span> },
              ]}
            />
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <CopyButton value={url} label="Copy verification link" />
            <span className="inline-flex items-center gap-1 text-xs text-subtle">
              <BadgeCheck className="h-3.5 w-3.5 text-success" aria-hidden /> Anyone with the link can check this award
            </span>
          </div>
        </section>
      </div>
    </div>
  );
}

export default function BadgeVerifyPage() {
  const params = useParams<{ id: string }>();
  const id = (params.id ?? "").trim().toUpperCase();
  const q = useQuery({
    queryKey: ["badges", "verify", id],
    queryFn: () => get<BadgeVerify>(`/badges/verify/${encodeURIComponent(id)}`),
    enabled: Boolean(id),
  });

  return (
    <Container size="lg" className="py-8 pb-16 sm:py-10">
      <div className="mx-auto max-w-4xl">
        {q.isPending ? (
          <div role="status" aria-label="Verifying badge" className="space-y-6">
            <div className="rounded-[var(--radius-2xl)] border border-border bg-surface px-6 py-10">
              <Skeleton className="mx-auto h-10 w-72 max-w-full rounded-full" />
              <Skeleton className="mx-auto mt-8 h-40 w-40 rounded-full" />
              <Skeleton className="mx-auto mt-6 h-8 w-56" />
              <Skeleton className="mx-auto mt-3 h-4 w-80 max-w-full" />
            </div>
            <div className="grid gap-6 md:grid-cols-2">
              <Skeleton className="h-48 w-full rounded-[var(--radius-lg)]" />
              <Skeleton className="h-48 w-full rounded-[var(--radius-lg)]" />
            </div>
          </div>
        ) : q.isError ? (
          q.error instanceof ApiError && q.error.status === 404 ? <Invalid id={id} /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : (
          <BadgeView v={q.data} />
        )}
      </div>
    </Container>
  );
}
