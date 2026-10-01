"use client";

import { BadgeCheck, Database, FlaskConical, Scale, ShieldCheck, Trophy, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId, type ReactNode } from "react";

import { Logo, LogoMark } from "@/components/brand/logo";
import { GridPlane } from "@/components/visual/grid-plane";
import { cn } from "@/lib/cn";
import { useConfig } from "@/lib/hooks";

export const VALUE_PROPS: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Scale, title: "Reproducible scoring", text: "Versioned evaluators score every submission; final ranks use a hidden private split." },
  { icon: BadgeCheck, title: "Verifiable credentials", text: "Certificates carry checksummed IDs and a public verification page." },
  { icon: ShieldCheck, title: "Honest profiles", text: "Verified results stay clearly separate from self-declared claims." },
];

export const LEGAL_LINKS = [
  { href: "/terms", label: "Terms" },
  { href: "/privacy", label: "Privacy" },
  { href: "/guidelines", label: "Guidelines" },
];

/**
 * Left half of the auth shell (lg+): brand, an illustrative 2.5D schematic of the platform loop and the three
 * promises the product makes. Sticky so it stays put while long forms (signup, onboarding) scroll.
 */
export function BrandPanel() {
  const demo = useConfig().data?.demo_mode;
  return (
    <aside
      aria-label="About DataBattles"
      className="relative isolate hidden overflow-hidden border-r border-border lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col"
    >
      <div aria-hidden className="absolute inset-0 -z-10 bg-bg-elevated/40" />
      <div
        aria-hidden
        className="absolute inset-0 -z-10"
        style={{ background: "radial-gradient(60% 50% at 38% 34%, color-mix(in oklab, var(--accent) 15%, transparent), transparent 72%)" }}
      />
      <GridPlane className="-z-10 h-[38%] opacity-60" />

      <div className="px-10 pt-8 xl:px-14">
        <Link
          href="/"
          className="inline-flex rounded-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--ring)]"
          aria-label="DataBattles home"
        >
          <Logo />
        </Link>
      </div>

      <div className="flex min-h-0 flex-1 flex-col justify-center px-10 py-8 xl:px-14">
        <Schematic />
        <div className="mt-8 max-w-[30rem] animate-rise [animation-delay:120ms] [@media(max-height:760px)]:mt-0">
          <p className="text-eyebrow text-accent-strong">Learn · Build · Compete · Verify</p>
          <p className="mt-3 text-[1.9rem] font-semibold leading-[1.08] tracking-[-0.035em] text-fg xl:text-[2.25rem]">
            The operating system for student <span className="text-gradient">AI competition</span>.
          </p>
          <ul className="mt-7 space-y-4">
            {VALUE_PROPS.map((v) => (
              <li key={v.title} className="flex gap-3.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-2 text-accent-strong shadow-[inset_0_1px_0_var(--hairline-highlight)]">
                  <v.icon className="h-4 w-4" aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-fg">{v.title}</span>
                  <span className="block text-[13px] leading-relaxed text-muted">{v.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-10 pb-8 text-xs text-subtle xl:px-14">
        <nav aria-label="Legal" className="flex items-center gap-4">
          {LEGAL_LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="transition-colors hover:text-fg">
              {l.label}
            </Link>
          ))}
        </nav>
        {demo ? (
          <span className="inline-flex items-center gap-1.5">
            <FlaskConical className="h-3.5 w-3.5 text-warning" aria-hidden /> Running with seeded demo data
          </span>
        ) : null}
      </div>
    </aside>
  );
}

/** Floating glass pane: the float animation lives on the wrapper so it never fights the 3D tilt. */
function Pane({ className, tilt, delay, children }: { className: string; tilt: string; delay: string; children: ReactNode }) {
  return (
    <div className={cn("absolute w-[11.5rem] motion-safe:animate-float", className)} style={{ animationDelay: delay }}>
      <div
        className="rounded-[var(--radius-lg)] border border-border-strong surface-glass surface-sheen p-3 shadow-elevated"
        style={{ transform: tilt }}
      >
        {children}
      </div>
    </div>
  );
}

function PaneHead({ icon: Icon, label, tone }: { icon: LucideIcon; label: string; tone: string }) {
  return (
    <div className="mb-2.5 flex items-center gap-2">
      <span className={cn("flex h-5 w-5 items-center justify-center rounded-md", tone)}>
        <Icon className="h-3 w-3" />
      </span>
      <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-subtle">{label}</span>
    </div>
  );
}

const Bar = ({ className }: { className?: string }) => <span className={cn("block h-1.5 rounded-full bg-surface-3", className)} />;

/**
 * Illustrative schematic (no data): the orbit around the DataBattles core with three floating panes that stand
 * for the platform's evidence — a leaderboard, a dataset and a certificate — drawn as bars, never as numbers.
 */
function Schematic() {
  const id = useId().replace(/:/g, "");
  return (
    // Drawn at a fixed design size (34rem × 300px) and zoomed to fit: by viewport height (middle) and panel width
    // (inner container query), so the panes never collide with the core. Hidden on short viewports. The caption
    // sits outside the zoomed box so it always renders at its full, legible size.
    <div aria-hidden className="w-full animate-fade-in [animation-duration:900ms] [@media(max-height:760px)]:hidden">
      <div className="@container w-full [@media(max-height:880px)]:[zoom:0.82]">
        <div className="relative h-[300px] w-[34rem] max-w-none @max-[34rem]:[zoom:0.86] @max-[30rem]:[zoom:0.76]">
          <svg viewBox="0 0 600 300" className="absolute inset-0 h-full w-full" fill="none" preserveAspectRatio="xMidYMid meet">
            <defs>
              <linearGradient id={`${id}-ring`} x1="0" x2="1">
                <stop offset="0" stopColor="var(--accent-strong)" stopOpacity="0.12" />
                <stop offset="0.5" stopColor="var(--accent-strong)" stopOpacity="0.55" />
                <stop offset="1" stopColor="var(--cyan)" stopOpacity="0.18" />
              </linearGradient>
              <radialGradient id={`${id}-glow`} cx="50%" cy="50%" r="50%">
                <stop offset="0" stopColor="var(--accent)" stopOpacity="0.32" />
                <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
              </radialGradient>
            </defs>
            <ellipse cx="300" cy="150" rx="150" ry="120" fill={`url(#${id}-glow)`} />
            <ellipse cx="300" cy="150" rx="282" ry="104" transform="rotate(-6 300 150)" stroke="var(--border-strong)" strokeDasharray="3 7" />
            <ellipse cx="300" cy="150" rx="212" ry="74" transform="rotate(-8 300 150)" stroke={`url(#${id}-ring)`} strokeWidth="1.4" />
            <ellipse
              cx="300"
              cy="150"
              rx="212"
              ry="74"
              transform="rotate(-8 300 150)"
              stroke="var(--cyan)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeDasharray="34 917"
              className="motion-safe:animate-[orbit-flow_9s_linear_infinite]"
            />
            <ellipse cx="300" cy="150" rx="118" ry="44" transform="rotate(20 300 150)" stroke="var(--border-strong)" />
            {[
              { x: 456, y: 181, c: "var(--cyan)" },
              { x: 95, y: 192, c: "var(--accent-strong)" },
              { x: 347, y: 217, c: "var(--success)" },
              { x: 362, y: 71, c: "var(--blue)" },
            ].map((n) => (
              <g key={`${n.x}-${n.y}`}>
                <circle cx={n.x} cy={n.y} r="10" fill={n.c} fillOpacity="0.14" />
                <circle cx={n.x} cy={n.y} r="4.5" fill="var(--bg)" stroke={n.c} strokeWidth="1.6" />
              </g>
            ))}
          </svg>

          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <div className="rounded-[22px] shadow-[0_0_60px_-6px_color-mix(in_oklab,var(--accent)_65%,transparent)]">
              <LogoMark size={68} />
            </div>
          </div>

          <Pane className="left-0 top-[2%]" tilt="perspective(900px) rotateY(16deg) rotateX(6deg)" delay="-1.2s">
            <PaneHead icon={Trophy} label="Leaderboard" tone="bg-accent-soft text-accent-strong" />
            <div className="space-y-1.5">
              {[1, 2, 3].map((r) => (
                <div key={r} className={cn("flex items-center gap-2 rounded-md px-1.5 py-1", r === 1 && "bg-accent-soft")}>
                  <span className="tabular flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-surface-3 font-mono text-[9px] text-muted">{r}</span>
                  <Bar className="flex-1" />
                  <Bar className="w-6 bg-surface-3/70" />
                </div>
              ))}
            </div>
          </Pane>

          <Pane className="right-0 top-[22%]" tilt="perspective(900px) rotateY(-18deg) rotateX(6deg)" delay="-3.4s">
            <div className="flex items-start justify-between gap-2">
              <PaneHead icon={BadgeCheck} label="Certificate" tone="bg-success-soft text-success" />
            </div>
            <Bar className="w-4/5" />
            <Bar className="mt-1.5 w-1/2 bg-surface-3/70" />
            <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-border bg-bg-elevated/70 px-2 py-1.5">
              <span className="font-mono text-[9.5px] tracking-[0.12em] text-muted">DB-····-····</span>
              <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-1.5 py-0.5 text-[9.5px] font-medium text-success">
                <BadgeCheck className="h-2.5 w-2.5" /> Valid
              </span>
            </div>
          </Pane>

          <Pane className="bottom-[2%] left-[14%]" tilt="perspective(900px) rotateY(12deg) rotateX(-8deg)" delay="-5.1s">
            <PaneHead icon={Database} label="Dataset" tone="bg-cyan-soft text-cyan" />
            <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border border-border bg-border">
              {Array.from({ length: 9 }).map((_, i) => (
                <span key={i} className="bg-surface px-1.5 py-1.5">
                  <Bar className={cn("h-1", i < 3 ? "w-3/4 bg-cyan-soft" : i % 2 ? "w-1/2" : "w-2/3")} />
                </span>
              ))}
            </div>
          </Pane>
        </div>
      </div>
      <p className="mt-3 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
        <span className="h-px w-5 bg-border-strong" />
        Illustrative schematic
      </p>
    </div>
  );
}
