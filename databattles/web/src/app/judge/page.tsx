"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Gavel, Lock } from "lucide-react";
import Link from "next/link";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ProgressBar } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, QueryState, SkeletonCards, Spinner } from "@/components/ui/states";
import { get } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

interface JudgeEvent {
  slug: string;
  title: string;
  status: string;
  assigned: number;
  submitted: number;
  finalized: boolean;
  ends_at: string | null;
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
    <Container size="lg">
      <PageHeader
        eyebrow="Judging"
        title="Your judging queue"
        description="Events where you've been invited to judge. Score each assigned entry against the organizers' rubric — drafts are private until you submit."
      />
      <QueryState
        query={events}
        loading={<SkeletonCards count={3} className="lg:grid-cols-2" />}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState
            icon={<Gavel className="h-5 w-5" />}
            title="You're not judging any events"
            description="Organizers invite judges from their competition's staff settings. You'll get a notification when you're added or assigned entries."
          />
        }
      >
        {(list) => (
          <ul className="grid gap-4 lg:grid-cols-2">
            {list.map((e) => {
              const pct = e.assigned ? (e.submitted / e.assigned) * 100 : 0;
              const done = e.assigned > 0 && e.submitted >= e.assigned;
              return (
                <li key={e.slug}>
                  <Card className="flex h-full flex-col p-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={e.status} />
                      {e.finalized ? <Badge tone="neutral" icon={<Lock className="h-3 w-3" aria-hidden />}>Judging finalized</Badge> : done ? <Badge tone="success">All scored</Badge> : null}
                    </div>
                    <h2 className="mt-2 text-base font-semibold text-fg">
                      <Link href={`/judge/${e.slug}`} className="hover:text-accent-strong">{e.title}</Link>
                    </h2>
                    {e.ends_at ? (
                      <p className="mt-1 flex items-center gap-1.5 text-xs text-subtle" title={formatDateTime(e.ends_at)}>
                        <CalendarClock className="h-3.5 w-3.5" aria-hidden /> {new Date(e.ends_at) > new Date() ? "Ends" : "Ended"} {relativeTime(e.ends_at)}
                      </p>
                    ) : null}
                    <div className="mt-4">
                      <div className="mb-1.5 flex justify-between text-xs text-muted">
                        <span>Submitted scores</span>
                        <span className="tabular-nums">{formatNumber(e.submitted)} / {formatNumber(e.assigned)}</span>
                      </div>
                      <ProgressBar value={pct} label={`${e.submitted} of ${e.assigned} entries scored`} />
                      {e.assigned === 0 ? <p className="mt-2 text-xs text-subtle">No entries assigned yet.</p> : null}
                    </div>
                    <div className="mt-auto pt-4">
                      <Link href={`/judge/${e.slug}`} className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong hover:underline">
                        {e.finalized ? "Review your scores" : done ? "Review or adjust scores" : "Open queue"} <ArrowRight className="h-4 w-4" aria-hidden />
                      </Link>
                    </div>
                  </Card>
                </li>
              );
            })}
          </ul>
        )}
      </QueryState>
    </Container>
  );
}
