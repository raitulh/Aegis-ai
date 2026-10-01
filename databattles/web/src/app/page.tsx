"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, BookOpen, GitPullRequest, Hammer, Network, ShieldCheck, Sparkles, Trophy, Users } from "lucide-react";
import Link from "next/link";

import { CompetitionCard, ProjectCard } from "@/components/domain/cards";
import { LinkButton } from "@/components/ui/button";
import { Container, Section } from "@/components/ui/page";
import { InlineNotice, SkeletonCards } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import type { CompetitionCard as CompetitionCardT, ProjectCard as ProjectCardT } from "@/lib/types";

interface Landing {
  stats: Record<string, number>;
  includes_demo_data: boolean;
  featured_competitions: CompetitionCardT[];
  featured_projects: ProjectCardT[];
}

const LOOP = [
  { icon: BookOpen, title: "Learn", text: "Short courses with quizzes and hands-on challenges graded on the server." },
  { icon: Hammer, title: "Build", text: "Train anywhere — Colab, Kaggle or your laptop. No GPU needed here." },
  { icon: Trophy, title: "Compete", text: "Upload predictions, get scored reproducibly, climb public and private leaderboards." },
  { icon: GitPullRequest, title: "Contribute", text: "Find good first issues in campus open source and get merged PRs attributed." },
  { icon: BadgeCheck, title: "Verify", text: "Results, certificates and badges backed by evidence — each with a public verification page." },
  { icon: Sparkles, title: "Showcase", text: "A profile that separates verified achievements from self-declared claims." },
  { icon: Network, title: "Connect", text: "Universities, clubs and sponsors meet talent — only when students opt in." },
];

export default function HomePage() {
  const me = useMe().data;
  const landing = useQuery({ queryKey: ["landing"], queryFn: () => get<Landing>("/meta/landing") });
  const s = landing.data?.stats;

  return (
    <>
      <section className="relative overflow-hidden border-b border-border">
        <div className="grid-bg pointer-events-none absolute inset-0 opacity-60" aria-hidden />
        <div className="glow-top pointer-events-none absolute inset-0" aria-hidden />
        <Container className="relative py-20 sm:py-28">
          <div className="mx-auto max-w-3xl text-center">
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1 text-xs text-muted backdrop-blur">
              <ShieldCheck className="h-3.5 w-3.5 text-success" /> Reproducible scoring · verifiable credentials
            </span>
            <h1 className="mt-6 text-4xl font-semibold tracking-tight text-fg sm:text-6xl">
              Where students <span className="bg-gradient-to-r from-accent-strong to-cyan bg-clip-text text-transparent">learn, compete</span> and prove their AI skills.
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-base text-muted sm:text-lg">
              Competitions, datasets, courses and open-source work for universities — with results anyone can verify.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              {me ? (
                <LinkButton href="/dashboard" size="lg" icon={<ArrowRight className="h-4 w-4" />}>Go to your dashboard</LinkButton>
              ) : (
                <LinkButton href="/signup" size="lg">Create a free account</LinkButton>
              )}
              <LinkButton href="/competitions" size="lg" variant="secondary">Browse competitions</LinkButton>
            </div>
          </div>
          <dl className="mx-auto mt-14 grid max-w-4xl grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {[
              ["Competitions", s?.competitions],
              ["Datasets", s?.datasets],
              ["Projects", s?.projects],
              ["Courses", s?.courses],
              ["Universities", s?.universities],
              ["Members", s?.members],
            ].map(([label, value]) => (
              <div key={label as string} className="rounded-[var(--radius-lg)] border border-border bg-surface/70 p-4 text-center backdrop-blur">
                <dt className="text-xs text-subtle">{label}</dt>
                <dd className="mt-1 text-xl font-semibold tabular-nums">{value === undefined ? "—" : compactNumber(value as number)}</dd>
              </div>
            ))}
          </dl>
          {landing.data?.includes_demo_data ? (
            <p className="mt-4 text-center text-xs text-subtle">Counts include synthetic demo data created by the seed script.</p>
          ) : null}
        </Container>
      </section>

      <Container>
        <Section title="One loop, from first lesson to first offer" description="Every step leaves verifiable evidence on your profile.">
          <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {LOOP.map((step, i) => (
              <li key={step.title} className="rounded-[var(--radius-lg)] border border-border bg-surface p-5">
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent-strong"><step.icon className="h-4 w-4" /></span>
                  <span className="text-xs text-subtle">0{i + 1}</span>
                </div>
                <h3 className="mt-3 font-semibold">{step.title}</h3>
                <p className="mt-1 text-sm text-muted">{step.text}</p>
              </li>
            ))}
          </ol>
        </Section>

        <Section title="Featured competitions" action={<Link href="/competitions" className="text-sm text-accent-strong hover:underline">View all</Link>}>
          {landing.isPending ? <SkeletonCards count={3} /> : landing.data?.featured_competitions.length ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {landing.data.featured_competitions.map((c) => <CompetitionCard key={c.id} c={c} />)}
            </div>
          ) : (
            <InlineNotice title="No public competitions yet">Organizers can host one from the organizer tools.</InlineNotice>
          )}
        </Section>

        <Section title="Built by students" action={<Link href="/projects" className="text-sm text-accent-strong hover:underline">Explore projects</Link>}>
          {landing.isPending ? <SkeletonCards count={3} /> : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {landing.data?.featured_projects.map((p) => <ProjectCard key={p.id} p={p} />)}
            </div>
          )}
        </Section>

        <Section>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-[var(--radius-xl)] border border-border bg-surface p-8">
              <Users className="h-6 w-6 text-accent-strong" />
              <h2 className="mt-4 text-xl font-semibold">For universities and clubs</h2>
              <p className="mt-2 text-sm text-muted">
                Host public, members-only or private competitions, verify student membership by institutional email,
                issue verifiable certificates and see privacy-respecting analytics.
              </p>
              <div className="mt-6 flex gap-2">
                <LinkButton href="/organize" variant="secondary">Organizer tools</LinkButton>
                <LinkButton href="/pricing" variant="ghost">Plans</LinkButton>
              </div>
            </div>
            <div className="rounded-[var(--radius-xl)] border border-border bg-surface p-8">
              <BadgeCheck className="h-6 w-6 text-success" />
              <h2 className="mt-4 text-xl font-semibold">Verify a certificate</h2>
              <p className="mt-2 text-sm text-muted">
                Every certificate has a checksummed ID and a public page showing exactly what it attests — and whether it was revoked.
              </p>
              <LinkButton href="/verify" variant="secondary" className="mt-6">Verify an ID</LinkButton>
            </div>
          </div>
        </Section>
      </Container>
    </>
  );
}
