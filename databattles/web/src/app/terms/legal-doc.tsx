import { ArrowRight, ArrowUp, ChevronDown, Scale, ShieldCheck, Users, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Container, PageHeader } from "@/components/ui/page";
import { cn } from "@/lib/cn";
import { LegalToc } from "./legal-toc";

export interface LegalSection {
  id: string;
  title: string;
  body: ReactNode;
}

type DocKey = "terms" | "privacy" | "guidelines";

const DOCS: { key: DocKey; href: string; label: string; short: string; icon: LucideIcon; blurb: string }[] = [
  { key: "terms", href: "/terms", label: "Terms of Service", short: "Terms", icon: Scale, blurb: "Accounts, competitions, your content, credentials and organizations." },
  { key: "privacy", href: "/privacy", label: "Privacy Policy", short: "Privacy", icon: ShieldCheck, blurb: "What is collected, who can see it, how long it is kept and your choices." },
  { key: "guidelines", href: "/guidelines", label: "Community Guidelines", short: "Guidelines", icon: Users, blurb: "How we compete and collaborate, and how reports and moderation work." },
];

const num = (i: number) => String(i + 1).padStart(2, "0");

/**
 * Static, server-rendered layout shared by /terms, /privacy and /guidelines.
 * These are templates: the operator of a deployment must review and adapt them.
 * Long-form reading column with a sticky table of contents on desktop and a collapsible one on mobile.
 */
export function LegalDoc({
  eyebrow,
  title,
  description,
  sections,
  template = true,
  current,
}: {
  eyebrow: string;
  title: string;
  description: ReactNode;
  sections: LegalSection[];
  template?: boolean;
  current?: DocKey;
}) {
  const doc = DOCS.find((d) => d.key === current);
  const Icon = doc?.icon ?? Scale;
  const toc = sections.map((s) => ({ id: s.id, title: s.title }));
  return (
    <Container size="xl" className="pb-20">
      <PageHeader
        eyebrow={eyebrow}
        icon={<Icon />}
        title={title}
        description={description}
        meta={
          <>
            <span className="tabular">{sections.length} sections</span>
            {template ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden /> Template — requires legal review
              </span>
            ) : null}
          </>
        }
      />

      <nav aria-label="Policies" className="mb-10 inline-flex max-w-full gap-0.5 overflow-x-auto rounded-[var(--radius-md)] border border-border bg-bg-elevated p-0.5 [scrollbar-width:none]">
        {DOCS.map((d) => {
          const on = d.key === current;
          return (
            <Link
              key={d.key}
              href={d.href}
              aria-current={on ? "page" : undefined}
              className={cn(
                "inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-[8px] px-3 text-[13px] font-medium transition-[background-color,color,box-shadow] duration-200",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)]",
                on ? "bg-surface-3 text-fg shadow-[inset_0_1px_0_var(--hairline-highlight),0_1px_2px_rgb(0_0_0/0.2)]" : "text-muted hover:text-fg",
              )}
            >
              <d.icon className="h-3.5 w-3.5" aria-hidden />
              {d.short}
            </Link>
          );
        })}
      </nav>

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-[15rem_minmax(0,1fr)] xl:gap-16">
        <aside className="hidden lg:block">
          <div className="sticky top-24">
            <LegalToc items={toc} />
          </div>
        </aside>

        <div className="min-w-0">
          {template ? (
            <div role="note" className="mb-8 flex max-w-[46rem] gap-3 rounded-[var(--radius-lg)] border border-warning/30 bg-warning-soft px-4 py-3.5 text-[13px] sm:text-sm">
              <Scale className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
              <div>
                <p className="font-semibold text-warning">Template — requires legal review</p>
                <p className="mt-0.5 leading-relaxed text-fg/80">
                  This text ships with the DataBattles platform as a starting point. It is not legal advice. The organization operating this
                  deployment (“the operator”, “we”) must have it reviewed by its own legal counsel and adapt it to its jurisdiction, institution and
                  practices before relying on it.
                </p>
              </div>
            </div>
          ) : null}

          <details className="group mb-8 max-w-[46rem] rounded-[var(--radius-lg)] border border-border bg-surface/60 lg:hidden">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--radius-lg)] px-4 text-sm font-medium text-fg [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-2">
                <span className="text-eyebrow text-subtle">On this page</span>
                <span className="tabular text-xs text-subtle">· {sections.length}</span>
              </span>
              <ChevronDown className="h-4 w-4 text-subtle transition-transform duration-200 group-open:rotate-180" aria-hidden />
            </summary>
            <nav aria-label="On this page" className="border-t border-border px-2 py-2">
              <ol>
                {sections.map((s, i) => (
                  <li key={s.id}>
                    <a href={`#${s.id}`} className="flex min-h-9 items-center gap-3 rounded-md px-2 text-sm text-muted hover:bg-surface-2 hover:text-fg">
                      <span className="tabular w-5 shrink-0 font-mono text-[11px] text-subtle">{num(i)}</span>
                      {s.title}
                    </a>
                  </li>
                ))}
              </ol>
            </nav>
          </details>

          <article className="max-w-[46rem]">
            {sections.map((s, i) => (
              <section
                key={s.id}
                id={s.id}
                aria-labelledby={`${s.id}-h`}
                className="scroll-mt-28 border-t border-border py-9 first:border-t-0 first:pt-1"
              >
                <div className="mb-4 flex items-baseline gap-3">
                  <span className="tabular shrink-0 font-mono text-xs text-accent-strong" aria-hidden>
                    {num(i)}
                  </span>
                  <h2 id={`${s.id}-h`} className="text-xl font-semibold tracking-[-0.02em] text-fg">
                    {s.title}
                  </h2>
                </div>
                <div className="prose-db sm:pl-8">{s.body}</div>
              </section>
            ))}
          </article>

          <footer className="max-w-[46rem] border-t border-border pt-8">
            <p className="text-eyebrow text-subtle">Related policies</p>
            <div className="mt-4 grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-2">
              {DOCS.filter((d) => d.key !== current)
                .slice(0, 2)
                .map((d) => (
                  <Link
                    key={d.key}
                    href={d.href}
                    className="group flex min-w-0 items-start gap-3 bg-surface p-5 transition-colors duration-200 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--ring)]"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong">
                      <d.icon className="h-4 w-4" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
                        {d.label}
                        <ArrowRight className="h-3.5 w-3.5 text-subtle transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden />
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted">{d.blurb}</span>
                    </span>
                  </Link>
                ))}
            </div>
            <a
              href="#main"
              className="mt-6 inline-flex min-h-9 items-center gap-1.5 rounded-md text-xs text-subtle transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] lg:hidden"
            >
              <ArrowUp className="h-3.5 w-3.5" aria-hidden /> Back to top
            </a>
          </footer>
        </div>
      </div>
    </Container>
  );
}
