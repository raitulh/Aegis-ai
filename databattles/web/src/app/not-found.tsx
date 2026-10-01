import { ArrowRight, BookOpen, Database, FolderGit2, Home, Trophy, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { OrbitMotif } from "@/components/auth/orbit-motif";
import { BackButton } from "@/components/auth/recovery";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { GridPlane } from "@/components/visual/grid-plane";

const DESTINATIONS: { href: string; label: string; text: string; icon: LucideIcon }[] = [
  { href: "/competitions", label: "Competitions", text: "Find a challenge and get scored reproducibly.", icon: Trophy },
  { href: "/datasets", label: "Datasets", text: "Versioned data with licences and previews.", icon: Database },
  { href: "/learn", label: "Courses", text: "Short courses with server-graded challenges.", icon: BookOpen },
  { href: "/projects", label: "Projects", text: "See what students have built and shipped.", icon: FolderGit2 },
];

export default function NotFound() {
  return (
    <section className="relative isolate overflow-hidden" aria-labelledby="not-found-title">
      <GridPlane className="-z-10 h-[42%] opacity-50" />
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-0 -z-10 h-[28rem] w-[min(48rem,100%)] -translate-x-1/2"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--accent) 14%, transparent), transparent)" }}
      />
      <Container size="md" className="flex flex-col items-center pb-20 pt-14 text-center sm:pb-28 sm:pt-20">
        <OrbitMotif size="xl" className="animate-fade-in [animation-duration:700ms]">
          <span className="tabular font-mono text-2xl font-semibold tracking-[-0.02em] text-fg sm:text-3xl">404</span>
        </OrbitMotif>
        <p className="mt-10 animate-rise text-eyebrow text-accent-strong">Error 404 · Off the map</p>
        <h1 id="not-found-title" className="mt-3 animate-rise text-headline text-fg [animation-delay:60ms]">
          This page doesn&apos;t exist
        </h1>
        <p className="mt-4 max-w-md animate-rise text-base leading-relaxed text-muted [animation-delay:120ms]">
          It may have been moved, made private, or never existed. Check the address, or pick up from one of the places below.
        </p>
        <div className="mt-8 flex animate-rise flex-wrap justify-center gap-2 [animation-delay:180ms]">
          <LinkButton href="/" icon={<Home className="h-4 w-4" />}>
            Go home
          </LinkButton>
          <BackButton />
        </div>

        <nav aria-label="Popular destinations" className="mt-16 w-full animate-fade-in [animation-delay:260ms]">
          <p className="text-eyebrow text-subtle">Popular destinations</p>
          <ul className="mt-4 grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border text-left shadow-card sm:grid-cols-2">
            {DESTINATIONS.map((d) => (
              <li key={d.href} className="min-w-0">
                <Link
                  href={d.href}
                  className="group flex h-full items-start gap-3 bg-surface p-4 transition-colors duration-200 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--ring)] sm:p-5"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong">
                    <d.icon className="h-4 w-4" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
                      {d.label}
                      <ArrowRight className="h-3.5 w-3.5 text-subtle transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-muted">{d.text}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </Container>
    </section>
  );
}
