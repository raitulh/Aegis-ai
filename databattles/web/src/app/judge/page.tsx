"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, CheckCircle2, ClipboardList, Gavel, ListChecks, Lock, ShieldAlert, Timer } from "lucide-react";
import Link from "next/link";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { ProgressBar, ProgressRing } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

import { BlockHeading, JudgeHomeSkeleton, SummaryTile, SummaryTiles } from "./_components/judge-ui";

interface JudgeEvent {
  slug: string;
  title: string;
  status: string;
  assigned: number;
  submitted: number;
  finalized: boolean;
  ends_at: string | null;
}

/** How judging works — restates the rules the event workspace enforces; no data. */
const STEPS = [
  { title: "Open an event's queue", text: "Entries appear once teams submit and the organizers assign them to you." },
  { title: "Score against the rubric", text: "Each criterion has its own scale and weight. Drafts are private until you submit." },
  { title: "Submit your scores", text: "Submitted scores count toward results. You can revise them until the organizers finalize judging." },
];

function AssignmentRow({ e, index }: { e: JudgeEvent; index: number }) {
  const pct = e.assigned ? (e.submitted / e.assigned) * 100 : 0;
  const done = e.assigned > 0 && e.submitted >= e.assigned;
  const remaining = Math.max(0, e.assigned - e.submitted);
  return (
    <li className="group relative animate-rise transition-colors hover:bg-surface-2/50" style={{ animationDelay: `${Math.min(index, 6) * 50}ms` }}>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-4 py-4 sm:px-5 md:grid-cols-[auto_minmax(0,1fr)_11rem_auto]">
        <div className="row-span-2 self-start md:row-span-1 md:self-center">
          <ProgressRing value={pct} size={44} />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={e.status} />
            {e.finalized ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Judging finalized</Badge> : done ? <Badge tone="success">All scored</Badge> : null}
          </div>
          <h3 className="mt-1.5 truncate text-[15px] font-semibold tracking-[-0.01em] text-fg">
            <Link href={`/judge/${e.slug}`} className="rounded-sm hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">{e.title}</Link>
          </h3>
          {e.ends_at ? (
            <p className="mt-1 flex items-center gap-1.5 text-xs text-subtle" title={formatDateTime(e.ends_at)}>
              <CalendarClock className="h-3.5 w-3.5 shrink-0" aria-hidden /> {new Date(e.ends_at) > new Date() ? "Ends" : "Ended"} {relativeTime(e.ends_at)}
            </p>
          ) : null}
        </div>
        <div className="min-w-0">
          <div className="mb-1.5 flex items-baseline justify-between gap-2 text-xs text-muted">
            <span>Submitted scores</span>
            <span className="tabular text-fg">
              {formatNumber(e.submitted)} <span className="text-subtle">/ {formatNumber(e.assigned)}</span>
            </span>
          </div>
          <ProgressBar value={pct} label={`${e.submitted} of ${e.assigned} entries scored`} />
          <p className="mt-1.5 text-[11px] text-subtle">
            {e.assigned === 0 ? "No entries assigned yet." : done ? "Every assigned entry is scored." : `${formatNumber(remaining)} still to score`}
          </p>
        </div>
        <div className="col-start-2 md:col-start-auto">
          <Link
            href={`/judge/${e.slug}`}
            className={cn(
              "inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-md)] px-3 text-sm font-medium transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
              done || e.finalized
                ? "border border-border text-fg hover:border-border-strong hover:bg-surface-2"
                : "bg-accent-soft text-accent-strong ring-1 ring-inset ring-[color-mix(in_oklab,var(--accent)_30%,transparent)] hover:bg-[color-mix(in_oklab,var(--accent)_22%,transparent)]",
            )}
          >
            {e.finalized ? "Review your scores" : done ? "Review or adjust scores" : "Open queue"}
            <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden />
          </Link>
        </div>
      </div>
    </li>
  );
}

function HowItWorks() {
  return (
    <aside aria-labelledby="judge-how-heading" className="lg:sticky lg:top-24 lg:self-start">
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
        <div className="border-b border-border px-5 py-4">
          <p className="text-eyebrow text-subtle">Guide</p>
          <h2 id="judge-how-heading" className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-fg">How judging works</h2>
        </div>
        <ol className="relative space-y-4 px-5 py-5">
          <span aria-hidden className="absolute bottom-8 left-[calc(2rem-0.5px)] top-8 w-px bg-[linear-gradient(180deg,var(--accent),var(--cyan),transparent)] opacity-40" />
          {STEPS.map((s, i) => (
            <li key={s.title} className="relative flex gap-3">
              <span className="tabular relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border-strong bg-bg-elevated font-mono text-[10.5px] text-accent-strong">
                {i + 1}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-fg">{s.title}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted">{s.text}</p>
              </div>
            </li>
          ))}
        </ol>
        <p className="flex items-start gap-2 border-t border-border bg-bg-elevated/40 px-5 py-3.5 text-xs leading-relaxed text-muted">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
          Know a team member personally or professionally? Declare a conflict from the entry instead of scoring it.
        </p>
      </div>
    </aside>
  );
}

export default function JudgeHomePage() {
  const me = useRequireAuth();
  const events = useQuery({
    queryKey: ["judge", "events"],
    queryFn: () => get<JudgeEvent[]>("/judge/events"),
    enabled: !!me.data,
  });
  if (me.isPending || !me.data) return <Spinner />;

  return (
    <Container size="xl">
      <PageHeader
        eyebrow="Judging"
        icon={<Gavel />}
        title="Your judging queue"
        description="Events where you've been invited to judge. Score each assigned entry against the organizers' rubric — drafts are private until you submit."
      />
      <QueryState
        query={events}
        loading={<JudgeHomeSkeleton />}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState
            icon={<Gavel />}
            title="You're not judging any events"
            description="Organizers invite judges from their competition's staff settings. You'll get a notification when you're added or assigned entries."
          />
        }
      >
        {(list) => {
          const assigned = list.reduce((n, e) => n + e.assigned, 0);
          const submitted = list.reduce((n, e) => n + e.submitted, 0);
          const remaining = list.reduce((n, e) => n + Math.max(0, e.assigned - e.submitted), 0);
          const open = list.filter((e) => !e.finalized).length;
          return (
            <div className="space-y-10">
              <SummaryTiles className="animate-rise [animation-delay:80ms]">
                <SummaryTile label="Events" value={formatNumber(list.length)} hint={`${formatNumber(open)} open for scoring`} icon={<ClipboardList />} />
                <SummaryTile label="Assigned" value={formatNumber(assigned)} hint="entries to review" icon={<ListChecks />} tone="cyan" />
                <SummaryTile label="Submitted" value={formatNumber(submitted)} hint={assigned ? `${Math.min(100, Math.round((submitted / assigned) * 100))}% of assigned` : "no entries yet"} icon={<CheckCircle2 />} tone="success" />
                <SummaryTile label="To score" value={formatNumber(remaining)} hint={remaining ? "entries remaining" : "nothing left to score"} icon={<Timer />} tone={remaining ? "warning" : "muted"} />
              </SummaryTiles>
              <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
                <section aria-labelledby="judge-assignments-heading" className="min-w-0">
                  <BlockHeading
                    id="judge-assignments-heading"
                    title="Assignments"
                    count={list.length}
                    description="Open an event to review its entries and score them against the rubric."
                  />
                  <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
                    {list.map((e, i) => (
                      <AssignmentRow key={e.slug} e={e} index={i} />
                    ))}
                  </ul>
                </section>
                <HowItWorks />
              </div>
            </div>
          );
        }}
      </QueryState>
    </Container>
  );
}
