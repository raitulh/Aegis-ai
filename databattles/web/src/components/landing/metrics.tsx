"use client";

import { BookOpen, Database, FolderGit2, Trophy, University, Users } from "lucide-react";

import { CountUp } from "@/components/motion/count-up";
import { Reveal } from "@/components/motion/reveal";
import { Container } from "@/components/ui/page";
import { compactNumber } from "@/lib/format";

const ITEMS = [
  { key: "competitions", label: "Competitions", icon: Trophy },
  { key: "datasets", label: "Datasets", icon: Database },
  { key: "projects", label: "Projects", icon: FolderGit2 },
  { key: "courses", label: "Courses", icon: BookOpen },
  { key: "universities", label: "Universities", icon: University },
  { key: "members", label: "Members", icon: Users },
];

/** Live platform counts from /meta/landing. Values animate only once real numbers arrive. */
export function Metrics({ stats, demo, loading }: { stats?: Record<string, number>; demo: boolean; loading: boolean }) {
  return (
    <section aria-label="Platform metrics" className="relative">
      <Container>
        <Reveal className="relative">
          <div aria-hidden className="pointer-events-none absolute inset-x-6 top-0 z-10 h-px bg-[linear-gradient(90deg,transparent,var(--accent),var(--cyan),transparent)] opacity-60" />
          <dl className="relative grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border shadow-card sm:grid-cols-3 lg:grid-cols-6">
            {ITEMS.map((it) => (
              <div
                key={it.key}
                className="group relative flex flex-col gap-2 bg-surface px-5 py-5 transition-colors duration-300 hover:bg-surface-2/80 sm:px-6"
              >
                <dt className="flex items-center gap-2 text-eyebrow text-subtle">
                  <it.icon className="h-3.5 w-3.5 text-accent-strong transition-transform duration-300 group-hover:scale-110" aria-hidden />
                  {it.label}
                </dt>
                <dd className="text-[1.9rem] font-semibold leading-none tracking-[-0.035em] text-fg">
                  {loading ? <span className="skeleton inline-block h-7 w-14 align-middle" /> : <CountUp value={stats?.[it.key]} format={compactNumber} />}
                </dd>
              </div>
            ))}
          </dl>
        </Reveal>
        {demo ? <p className="mt-3 text-center text-xs text-subtle">Counts include synthetic demo data created by the seed script.</p> : null}
      </Container>
    </section>
  );
}
