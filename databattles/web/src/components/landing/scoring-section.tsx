"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, Box, Eye, EyeOff, FileCheck2, Medal, Upload } from "lucide-react";
import Link from "next/link";

import { Reveal } from "@/components/motion/reveal";
import { DemoBadge } from "@/components/ui/badge";
import { Container } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatScore, relativeTime } from "@/lib/format";
import { useInView } from "@/lib/motion";
import { qk } from "@/lib/query";
import type { CompetitionCard, LeaderboardOut } from "@/lib/types";
import { SectionHeading } from "./section-heading";

const STEPS = [
  { icon: Upload, title: "Upload predictions", text: "A CSV from wherever you trained." },
  { icon: FileCheck2, title: "Validate", text: "Format and IDs checked before scoring." },
  { icon: Box, title: "Sandboxed evaluator", text: "A versioned metric, run in isolation." },
  { icon: Eye, title: "Public leaderboard", text: "Best score on the public split." },
  { icon: EyeOff, title: "Private split", text: "Final ranks use data nobody saw." },
  { icon: BadgeCheck, title: "Verified result", text: "Recorded as evidence on your profile." },
];

const PARAMS = { page: 1, page_size: 5 };

function LiveBoard({ comp }: { comp: CompetitionCard }) {
  const [ref, near] = useInView<HTMLDivElement>({ rootMargin: "300px 0px" });
  const lb = useQuery({
    queryKey: qk.leaderboard(comp.slug, PARAMS),
    queryFn: ({ signal }) => get<LeaderboardOut>(`/competitions/${encodeURIComponent(comp.slug)}/leaderboard`, PARAMS, signal),
    enabled: near,
    staleTime: 60_000,
  });
  const rows = lb.data?.rows ?? [];
  return (
    <div ref={ref} className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-elevated/70 px-5 py-3">
        <Medal className="h-4 w-4 text-accent-strong" aria-hidden />
        <Link href={`/competitions/${comp.slug}/leaderboard`} className="min-w-0 truncate text-sm font-medium text-fg hover:text-accent-strong">
          {comp.title}
        </Link>
        {comp.is_demo ? <DemoBadge /> : null}
        <span className="ml-auto font-mono text-[11px] text-subtle">
          {lb.data ? (
            <>
              {lb.data.metric ?? comp.metric ?? "score"} · {lb.data.direction === "minimize" ? "lower is better" : "higher is better"}
            </>
          ) : null}
        </span>
      </div>
      {lb.isPending ? (
        <div className="divide-y divide-border" role="status" aria-label="Loading leaderboard">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-3">
              <Skeleton className="h-6 w-6 rounded-full" />
              <Skeleton className="h-3.5 flex-1" />
              <Skeleton className="h-3.5 w-16" />
            </div>
          ))}
        </div>
      ) : lb.isError || lb.data?.hidden_reason || !rows.length ? (
        <p className="px-5 py-10 text-center text-sm text-muted">
          {lb.data?.hidden_reason ? "This leaderboard is hidden right now." : "No scored entries on this leaderboard yet."}
        </p>
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Top of the {comp.title} leaderboard</caption>
          <thead className="sr-only">
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Team</th>
              <th scope="col">Score</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((r, i) => (
              <tr key={r.team_id} className="animate-rise" style={{ animationDelay: `${i * 60}ms` }}>
                <td className="w-12 py-3 pl-5">
                  <span
                    className={cn(
                      "tabular flex h-7 w-7 items-center justify-center rounded-full font-mono text-xs font-semibold",
                      r.rank === 1 ? "bg-[linear-gradient(135deg,#fde68a,#f59e0b)] text-black" : r.rank === 2 ? "bg-[linear-gradient(135deg,#e5e7eb,#9ca3af)] text-black" : r.rank === 3 ? "bg-[linear-gradient(135deg,#fdba74,#c2410c)] text-black" : "bg-surface-3 text-muted",
                    )}
                  >
                    {r.rank ?? "—"}
                  </span>
                </td>
                <td className="max-w-0 px-3 py-3">
                  <span className="block truncate font-medium text-fg">{r.team_name ?? r.members[0]?.display_name ?? "Team"}</span>
                  {r.submitted_at ? <span className="block text-xs text-subtle">{relativeTime(r.submitted_at)}</span> : null}
                </td>
                <td className="tabular py-3 pr-5 text-right font-mono text-[13px] text-fg">{formatScore(r.score ?? r.public_score ?? null)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex items-center justify-between border-t border-border px-5 py-3 text-xs text-subtle">
        <span>{lb.data?.evaluator_version ? `Evaluator ${lb.data.evaluator_version}` : "Versioned evaluator"} · {lb.data?.is_final ? "final results" : "public split"}</span>
        <Link href={`/competitions/${comp.slug}/leaderboard`} className="inline-flex items-center gap-1 text-accent-strong hover:underline">
          Full board <ArrowRight className="h-3 w-3" aria-hidden />
        </Link>
      </div>
    </div>
  );
}

/** How a score is produced, step by step, next to a real leaderboard from a featured competition. */
export function ScoringSection({ competitions }: { competitions?: CompetitionCard[] }) {
  const comp = competitions?.find((c) => c.scoring_mode === "automatic" && ["active", "ended", "completed"].includes(c.status));
  return (
    <section className="relative py-20 sm:py-28" aria-label="Reproducible scoring">
      <Container>
        <SectionHeading
          index="09"
          eyebrow="Platform intelligence"
          title="Scores you can audit, not just admire."
          description="Every submission flows through the same pipeline. Public ranks guide you; final ranks use a hidden private split, so over-fitting doesn't pay."
          align="center"
        />
        <div className={cn("mt-14 grid grid-cols-1 gap-6", comp && "lg:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)] lg:items-start")}>
          <Reveal as="ol" className="relative grid gap-3 sm:grid-cols-2">
            {STEPS.map((s, i) => (
              <li key={s.title} className="group relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-4 shadow-card">
                <span
                  aria-hidden
                  className="absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,var(--cyan),transparent)] opacity-0 motion-safe:animate-[step-scan_6s_ease-in-out_infinite]"
                  style={{ animationDelay: `${i}s` }}
                />
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-bg-elevated text-accent-strong transition-colors group-hover:bg-accent-soft">
                    <s.icon className="h-4 w-4" aria-hidden />
                  </span>
                  <span className="font-mono text-[11px] text-subtle">{String(i + 1).padStart(2, "0")}</span>
                </div>
                <p className="mt-3 font-medium text-fg">{s.title}</p>
                <p className="mt-0.5 text-sm text-muted">{s.text}</p>
              </li>
            ))}
          </Reveal>
          {comp ? (
            <Reveal delay={100}>
              <LiveBoard comp={comp} />
            </Reveal>
          ) : null}
        </div>
      </Container>
    </section>
  );
}
