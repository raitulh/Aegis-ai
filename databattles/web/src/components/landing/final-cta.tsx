"use client";

import { ArrowRight, Compass } from "lucide-react";

import { Magnetic } from "@/components/motion/magnetic";
import { Reveal } from "@/components/motion/reveal";
import { useSpotlight } from "@/components/motion/tilt";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { SignalLines } from "@/components/visual/signal-lines";

export function FinalCta({ signedIn }: { signedIn: boolean }) {
  const spot = useSpotlight<HTMLDivElement>();
  return (
    <section className="relative py-16 sm:py-24" aria-label="Get started">
      <Container>
        <Reveal>
          <div
            ref={spot}
            className="spotlight border-gradient relative isolate overflow-hidden rounded-[var(--radius-2xl)] bg-surface px-6 py-16 text-center shadow-elevated sm:px-12 sm:py-20"
          >
            <SignalLines className="absolute inset-0 -z-10 opacity-70" />
            <div aria-hidden className="absolute inset-0 -z-10" style={{ background: "radial-gradient(60% 80% at 50% 0%, color-mix(in oklab, var(--accent) 20%, transparent), transparent 70%)" }} />
            <p className="text-eyebrow text-accent-strong">Start the loop</p>
            <h2 className="mx-auto mt-4 max-w-3xl text-headline text-fg">
              Your next result could be <span className="text-gradient">verifiable</span>.
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted sm:text-lg">
              Join a competition, finish a course or ship a project — and leave with evidence anyone can check.
            </p>
            <div className="mt-9 flex flex-wrap justify-center gap-3">
              <Magnetic>
                {signedIn ? (
                  <LinkButton href="/dashboard" size="lg" icon={<ArrowRight className="h-4 w-4" />}>Open your dashboard</LinkButton>
                ) : (
                  <LinkButton href="/signup" size="lg" className="px-6">
                    Create a free account <ArrowRight className="h-4 w-4" aria-hidden />
                  </LinkButton>
                )}
              </Magnetic>
              <LinkButton href="/competitions" size="lg" variant="secondary" icon={<Compass className="h-4 w-4" />}>
                Browse competitions
              </LinkButton>
            </div>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
