"use client";

import { ArrowRight, Trophy } from "lucide-react";
import Link from "next/link";

import { CompetitionCard } from "@/components/domain/cards";
import { Reveal } from "@/components/motion/reveal";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { EmptyState, SkeletonCards } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import type { CompetitionCard as CompetitionCardT } from "@/lib/types";
import { SectionHeading } from "./section-heading";

/** Featured competitions as a bento: the lead event gets the large tile, the rest flank it. */
export function CompetitionShowcase({ items, loading }: { items?: CompetitionCardT[]; loading: boolean }) {
  const list = items ?? [];
  const shown = list.slice(0, 5);
  const bento = shown.length >= 3;
  // Empty cells left in the last row: the lead tile fills 2×2 on lg (2 cells wide on sm).
  const lgGap = (3 - (shown.length % 3)) % 3;
  const smGap = (shown.length + 1) % 2;
  return (
    <section className="relative py-20 sm:py-28" aria-label="Featured competitions">
      <Container>
        <SectionHeading
          index="02"
          eyebrow="Compete"
          title="Featured competitions"
          description="Machine-learning challenges, datathons and hackathons from universities and clubs — scored by versioned evaluators."
          action={
            <LinkButton href="/competitions" variant="secondary" icon={<ArrowRight className="h-4 w-4" />}>
              View all
            </LinkButton>
          }
        />
        <div className="mt-12">
          {loading ? (
            <SkeletonCards count={3} />
          ) : list.length ? (
            <div className={cn("grid gap-4 sm:grid-cols-2", bento && "lg:grid-cols-3")}>
              {shown.map((c, i) => (
                <Reveal
                  key={c.id}
                  delay={i * 70}
                  className={cn("flex min-w-0 [&>a]:w-full", i === 0 && bento && "sm:col-span-2 lg:row-span-2")}
                >
                  <CompetitionCard c={c} featured={i === 0 && bento} />
                </Reveal>
              ))}
              {/* Fill the bento's last row with a way onward instead of leaving a hole. */}
              {bento && lgGap ? (
                <Reveal className={cn("min-w-0 [&>a]:w-full", lgGap === 2 && "lg:col-span-2", smGap ? "flex" : "hidden lg:flex")}>
                  <Link
                    href="/competitions"
                    className="group flex min-h-40 flex-col justify-between rounded-[var(--radius-lg)] border border-dashed border-border-strong bg-surface/40 p-5 transition-colors hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border-strong))] hover:bg-surface-2/60"
                  >
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-accent-soft text-accent-strong">
                      <Trophy className="h-4 w-4" aria-hidden />
                    </span>
                    <span>
                      <span className="block text-[15.5px] font-semibold text-fg">Browse every competition</span>
                      <span className="mt-1 flex items-center gap-1 text-sm text-muted transition-colors group-hover:text-accent-strong">
                        Filter by task, difficulty and prizes <ArrowRight className="h-3.5 w-3.5 transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
                      </span>
                    </span>
                  </Link>
                </Reveal>
              ) : null}
            </div>
          ) : (
            <EmptyState
              icon={<Trophy />}
              title="No public competitions yet"
              description="Organizers can host one from the organizer tools."
              action={<LinkButton href="/organize" variant="secondary">Organizer tools</LinkButton>}
            />
          )}
        </div>
      </Container>
    </section>
  );
}
