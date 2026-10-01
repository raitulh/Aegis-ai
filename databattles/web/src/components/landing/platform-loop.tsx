"use client";

import { ArrowRight, BadgeCheck, BookOpen, GitPullRequest, Hammer, Network, Sparkles, Trophy, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { Reveal } from "@/components/motion/reveal";
import { Container } from "@/components/ui/page";
import { cn } from "@/lib/cn";
import { SectionHeading } from "./section-heading";

interface Stage {
  icon: LucideIcon;
  title: string;
  text: string;
  detail: string;
  href: string;
  cta: string;
}

/**
 * The DataBattles loop as an orbit: seven stages on a ring, a signal travelling between them, and a detail
 * panel for the focused stage. Implemented as an ARIA tab list so it works with keyboard (arrow keys, Home,
 * End) and screen readers; hover only previews what focus/click selects.
 */
export function PlatformLoop({ handle }: { handle?: string | null }) {
  const stages: Stage[] = [
    {
      icon: BookOpen,
      title: "Learn",
      text: "Short courses with quizzes and hands-on challenges graded on the server.",
      detail: "Progress, quiz results and completions are recorded as you go — and can issue certificates and badges.",
      href: "/learn",
      cta: "Explore courses",
    },
    {
      icon: Hammer,
      title: "Build",
      text: "Train anywhere — Colab, Kaggle or your laptop. No GPU needed here.",
      detail: "Publish projects with repositories, demos and technologies, linked to the competitions they came from.",
      href: "/projects",
      cta: "See student projects",
    },
    {
      icon: Trophy,
      title: "Compete",
      text: "Upload predictions, get scored reproducibly, climb public and private leaderboards.",
      detail: "Every score comes from a versioned evaluator in a sandbox; final ranks use a hidden private split.",
      href: "/competitions",
      cta: "Browse competitions",
    },
    {
      icon: GitPullRequest,
      title: "Contribute",
      text: "Find good first issues in campus open source and get merged PRs attributed.",
      detail: "Link GitHub once; merged pull requests on campus repositories are attributed to your profile.",
      href: "/open-source",
      cta: "Find a first issue",
    },
    {
      icon: BadgeCheck,
      title: "Verify",
      text: "Results, certificates and badges backed by evidence — each with a public verification page.",
      detail: "Certificate IDs are checksummed; anyone can confirm what one attests and whether it was revoked.",
      href: "/verify",
      cta: "Verify a certificate",
    },
    {
      icon: Sparkles,
      title: "Showcase",
      text: "A profile that separates verified achievements from self-declared claims.",
      detail: "Your public profile collects results, projects, contributions and credentials — clearly labelled.",
      href: handle ? `/u/${handle}` : "/signup",
      cta: handle ? "Open your profile" : "Start your profile",
    },
    {
      icon: Network,
      title: "Connect",
      text: "Universities, clubs and sponsors meet talent — only when students opt in.",
      detail: "Join your university or club with an institutional email and take part in members-only events.",
      href: "/orgs",
      cta: "Find your university",
    },
  ];

  const [active, setActive] = useState(0);
  const [preview, setPreview] = useState<number | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  const shown = preview ?? active;
  const n = stages.length;
  const current = stages[shown];

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    else return;
    e.preventDefault();
    setActive(next);
    tabRefs.current[next]?.focus();
  };

  // Positions on the ring, clockwise from the top (percent of the square stage).
  const pos = (i: number) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    return { left: 50 + Math.cos(a) * 41, top: 50 + Math.sin(a) * 41 };
  };
  const arc = (shown / n) * 100;

  return (
    <section className="relative py-24 sm:py-32" aria-labelledby={`${id}-title`}>
      <Container>
        <SectionHeading
          index="01"
          eyebrow="The DataBattles loop"
          title={<span id={`${id}-title`}>One loop, from first lesson to first offer.</span>}
          description="Every step leaves verifiable evidence on your profile. Pick a stage to see what it records."
        />

        <div className="mt-14 grid grid-cols-1 items-center gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-16">
          <Reveal className="relative mx-auto aspect-square w-full max-w-[560px]">
            {/* Ring + travelling signal */}
            <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" aria-hidden fill="none">
              <defs>
                <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="var(--accent-strong)" />
                  <stop offset="1" stopColor="var(--cyan)" />
                </linearGradient>
                <radialGradient id={`${id}-c`} cx="50%" cy="50%" r="50%">
                  <stop offset="0" stopColor="var(--accent)" stopOpacity="0.18" />
                  <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
                </radialGradient>
              </defs>
              <circle cx="50" cy="50" r="30" fill={`url(#${id}-c)`} />
              <circle cx="50" cy="50" r="41" stroke="var(--border-strong)" strokeWidth="0.35" />
              <circle cx="50" cy="50" r="47" stroke="var(--border)" strokeWidth="0.25" strokeDasharray="0.6 1.6" />
              <circle cx="50" cy="50" r="28" stroke="var(--border)" strokeWidth="0.25" />
              {/* Progress arc up to the shown stage */}
              <circle
                cx="50"
                cy="50"
                r="41"
                pathLength={100}
                stroke={`url(#${id}-g)`}
                strokeWidth="0.7"
                strokeLinecap="round"
                strokeDasharray={`${arc} 100`}
                transform="rotate(-90 50 50)"
                className="transition-[stroke-dasharray] duration-700 ease-out-expo"
              />
              {/* Signal pulse running around the loop */}
              <circle
                cx="50"
                cy="50"
                r="41"
                pathLength={100}
                stroke="var(--cyan)"
                strokeWidth="1"
                strokeLinecap="round"
                strokeDasharray="2.5 97.5"
                transform="rotate(-90 50 50)"
                className="motion-safe:animate-[loop-signal_8s_linear_infinite]"
                opacity="0.9"
              />
              {/* Spoke to the shown stage */}
              <line
                x1="50"
                y1="50"
                x2={pos(shown).left}
                y2={pos(shown).top}
                stroke={`url(#${id}-g)`}
                strokeWidth="0.35"
                strokeDasharray="1 1"
                className="transition-all duration-500 ease-out-expo"
              />
            </svg>

            {/* Centre: current stage */}
            <div className="absolute left-1/2 top-1/2 flex w-[46%] -translate-x-1/2 -translate-y-1/2 flex-col items-center text-center">
              <span key={shown} className="flex h-12 w-12 animate-pop items-center justify-center rounded-2xl border border-border-strong bg-surface-2 text-accent-strong shadow-glow sm:h-14 sm:w-14">
                <current.icon className="h-5 w-5 sm:h-6 sm:w-6" aria-hidden />
              </span>
              <p className="mt-3 font-mono text-[10px] tracking-[0.16em] text-subtle sm:text-[11px]">STAGE {String(shown + 1).padStart(2, "0")} / {String(n).padStart(2, "0")}</p>
              <p key={`t-${shown}`} className="mt-1 animate-rise text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">{current.title}</p>
            </div>

            {/* Stage nodes (tabs) */}
            <div role="tablist" aria-label="Loop stages" aria-orientation="horizontal" className="absolute inset-0">
              {stages.map((s, i) => {
                const p = pos(i);
                const selected = i === active;
                const lit = i === shown;
                return (
                  <button
                    key={s.title}
                    ref={(el) => {
                      tabRefs.current[i] = el;
                    }}
                    type="button"
                    role="tab"
                    id={`${id}-tab-${i}`}
                    aria-selected={selected}
                    aria-label={`${String(i + 1).padStart(2, "0")} ${s.title}`}
                    aria-controls={`${id}-panel`}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => setActive(i)}
                    onKeyDown={(e) => onKey(e, i)}
                    onPointerEnter={() => setPreview(i)}
                    onPointerLeave={() => setPreview(null)}
                    style={{ left: `${p.left}%`, top: `${p.top}%` }}
                    className="group absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5 rounded-xl p-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
                  >
                    <span
                      className={cn(
                        "relative flex h-10 w-10 items-center justify-center rounded-full border transition-all duration-300 ease-out-expo sm:h-12 sm:w-12",
                        lit
                          ? "scale-110 border-transparent bg-brand text-white shadow-glow"
                          : i < shown
                            ? "border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] bg-surface-2 text-accent-strong"
                            : "border-border-strong bg-surface text-muted group-hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border))] group-hover:text-fg",
                      )}
                    >
                      <s.icon className="h-4 w-4 sm:h-[18px] sm:w-[18px]" aria-hidden />
                      {lit ? <span aria-hidden className="absolute inset-0 rounded-full text-accent motion-safe:animate-pulse-ring" /> : null}
                    </span>
                    <span aria-hidden className={cn("hidden whitespace-nowrap rounded-md px-1.5 font-mono text-[10px] tracking-[0.12em] transition-colors min-[480px]:block sm:text-[10.5px]", lit ? "text-fg" : "text-subtle")}>
                      {String(i + 1).padStart(2, "0")} {s.title.toUpperCase()}
                    </span>
                  </button>
                );
              })}
            </div>
          </Reveal>

          {/* Detail panel */}
          <Reveal delay={80}>
            <div
              role="tabpanel"
              id={`${id}-panel`}
              aria-labelledby={`${id}-tab-${active}`}
              className="relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen p-6 shadow-card sm:p-8"
            >
              <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 h-48 w-48" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
              <div key={shown} className="relative animate-rise">
                <p className="text-eyebrow text-accent-strong">Stage {String(shown + 1).padStart(2, "0")}</p>
                <h3 className="mt-2 text-title text-fg">{current.title}</h3>
                <p className="mt-4 text-[17px] leading-relaxed text-fg/90">{current.text}</p>
                <p className="mt-3 leading-relaxed text-muted">{current.detail}</p>
                <Link
                  href={current.href}
                  className="group mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-accent-strong transition-colors hover:text-fg"
                >
                  {current.cta} <ArrowRight className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
                </Link>
              </div>
              <ol className="relative mt-8 flex gap-1.5" aria-hidden>
                {stages.map((s, i) => (
                  <li key={s.title} className={cn("h-1 flex-1 rounded-full transition-colors duration-500", i <= shown ? "bg-brand" : "bg-surface-3")} />
                ))}
              </ol>
            </div>
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
