import { Scale } from "lucide-react";
import type { ReactNode } from "react";

import { Container, PageHeader } from "@/components/ui/page";

export interface LegalSection {
  id: string;
  title: string;
  body: ReactNode;
}

/**
 * Static, server-rendered layout shared by /terms, /privacy and /guidelines.
 * These are templates: the operator of a deployment must review and adapt them.
 */
export function LegalDoc({
  eyebrow,
  title,
  description,
  sections,
  template = true,
}: {
  eyebrow: string;
  title: string;
  description: ReactNode;
  sections: LegalSection[];
  template?: boolean;
}) {
  return (
    <Container size="lg">
      <PageHeader eyebrow={eyebrow} title={title} description={description} />
      {template ? (
        <div role="note" className="mb-8 flex gap-3 rounded-[var(--radius-md)] border border-warning/30 bg-warning-soft px-4 py-3 text-sm">
          <Scale className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
          <div>
            <p className="font-semibold text-warning">Template — requires legal review</p>
            <p className="mt-0.5 text-fg/80">
              This text ships with the DataBattles platform as a starting point. It is not legal advice. The organization operating this
              deployment (“the operator”, “we”) must have it reviewed by its own legal counsel and adapt it to its jurisdiction, institution and
              practices before relying on it.
            </p>
          </div>
        </div>
      ) : null}
      <div className="grid gap-10 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="On this page" className="hidden lg:sticky lg:top-20 lg:block lg:self-start">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-subtle">On this page</p>
          <ol className="space-y-1.5 text-sm">
            {sections.map((s, i) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="text-muted hover:text-fg">
                  {i + 1}. {s.title}
                </a>
              </li>
            ))}
          </ol>
        </nav>
        <article className="prose-db max-w-3xl">
          {sections.map((s, i) => (
            <section key={s.id} id={s.id} aria-labelledby={`${s.id}-h`} className="scroll-mt-24">
              <h2 id={`${s.id}-h`}>
                {i + 1}. {s.title}
              </h2>
              {s.body}
            </section>
          ))}
        </article>
      </div>
    </Container>
  );
}
