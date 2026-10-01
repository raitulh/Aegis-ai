"use client";

import { ArrowRight, BadgeCheck, Compass, FlaskConical, Scale, ShieldCheck } from "lucide-react";

import { Magnetic } from "@/components/motion/magnetic";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { DataOrbit } from "@/components/visual/data-orbit";
import { GridPlane } from "@/components/visual/grid-plane";

const TRUST = [
  { icon: Scale, label: "Reproducible scoring" },
  { icon: BadgeCheck, label: "Public verification pages" },
  { icon: ShieldCheck, label: "Verified vs. self-declared, always labelled" },
];

export function Hero({ signedIn, demo }: { signedIn: boolean; demo: boolean }) {
  return (
    <section className="relative isolate overflow-hidden" aria-labelledby="hero-title">
      <GridPlane className="opacity-70" />
      <div
        aria-hidden
        className="pointer-events-none absolute right-[-10%] top-[5%] -z-10 h-[70%] w-[60%]"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--accent) 18%, transparent), transparent)" }}
      />
      <Container className="relative grid grid-cols-1 items-center gap-6 pb-10 pt-10 sm:pt-14 lg:min-h-[calc(100dvh-5.5rem)] lg:grid-cols-[minmax(0,1.02fr)_minmax(0,1fr)] lg:gap-4 lg:pb-16 lg:pt-6">
        <div className="relative z-10 max-w-2xl">
          <p className="inline-flex animate-rise items-center gap-2 rounded-full border border-border bg-glass py-1 pl-1.5 pr-3 text-xs text-muted shadow-[inset_0_1px_0_var(--hairline-highlight)] backdrop-blur-md">
            <span className="flex items-center gap-1.5 rounded-full bg-success-soft px-2 py-0.5 font-medium text-success">
              <span className="relative flex h-1.5 w-1.5" aria-hidden>
                <span className="absolute inset-0 rounded-full bg-current motion-safe:animate-[ping_2.4s_cubic-bezier(0,0,0.2,1)_infinite]" />
                <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
              </span>
              Evidence-backed
            </span>
            Reproducible scoring · verifiable credentials
          </p>

          <h1 id="hero-title" className="mt-6 animate-rise-lg text-display text-fg [animation-delay:60ms]">
            Where students <span className="text-gradient">learn, compete</span> and prove their AI skills.
          </h1>

          <p className="mt-6 max-w-xl animate-rise-lg text-base leading-relaxed text-muted [animation-delay:140ms] sm:text-lg">
            Competitions, datasets, courses and open-source work for universities — with results anyone can verify. Train anywhere;
            DataBattles scores, records and proves what you did.
          </p>

          <div className="mt-8 flex animate-rise-lg flex-wrap items-center gap-3 [animation-delay:220ms]">
            <Magnetic>
              {signedIn ? (
                <LinkButton href="/dashboard" size="lg" icon={<ArrowRight className="h-4 w-4" />} className="pr-6">
                  Go to your dashboard
                </LinkButton>
              ) : (
                <LinkButton href="/signup" size="lg" className="px-6">
                  Create a free account <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover/btn:translate-x-0.5" aria-hidden />
                </LinkButton>
              )}
            </Magnetic>
            <Magnetic strength={0.15}>
              <LinkButton href="/competitions" size="lg" variant="secondary" icon={<Compass className="h-4 w-4" />}>
                Browse competitions
              </LinkButton>
            </Magnetic>
          </div>

          <ul className="mt-10 flex animate-rise-lg flex-wrap gap-x-5 gap-y-2.5 text-[13px] text-muted [animation-delay:300ms]" aria-label="What DataBattles guarantees">
            {TRUST.map((t) => (
              <li key={t.label} className="inline-flex items-center gap-1.5">
                <t.icon className="h-3.5 w-3.5 text-accent-strong" aria-hidden /> {t.label}
              </li>
            ))}
          </ul>
          {demo ? (
            <p className="mt-4 inline-flex animate-fade-in items-center gap-1.5 text-xs text-subtle [animation-delay:400ms]">
              <FlaskConical className="h-3.5 w-3.5 text-warning" aria-hidden /> This instance is running with seeded demo data.
            </p>
          ) : null}
        </div>

        <div className="relative -mx-4 h-[360px] animate-fade-in [animation-delay:120ms] [animation-duration:900ms] sm:mx-0 sm:h-[460px] lg:h-[min(640px,calc(100dvh-8rem))]">
          <DataOrbit className="h-full w-full" />
          <p className="sr-only">
            Illustration: a data core with six platform stages orbiting it — data, learn, build, compete, verify and showcase.
          </p>
        </div>
      </Container>
    </section>
  );
}
