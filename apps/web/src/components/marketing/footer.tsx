import Link from "next/link";
import { Logo } from "@/components/logo";

const GROUPS = [
  { title: "Product", links: [["Product", "/product"], ["Solutions", "/solutions"], ["Pricing", "/pricing"], ["Sandbox", "/demo"]] },
  { title: "Developers", links: [["Docs", "/docs"], ["API, SDK & MCP", "/developers"]] },
  { title: "Company", links: [["About", "/about"], ["Trust center", "/trust"], ["Security", "/security"], ["Contact", "/contact"]] },
];

export function MarketingFooter() {
  return (
    <footer className="border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
      <div className="mx-auto grid max-w-7xl gap-10 px-6 py-14 md:grid-cols-[1.5fr_repeat(3,1fr)]">
        <div>
          <Logo />
          <p className="mt-3 max-w-xs text-sm text-[var(--color-text-muted)]">Continuous AI assurance: test, guard and prove how your AI systems behave.</p>
        </div>
        {GROUPS.map((g) => (
          <nav key={g.title} aria-label={g.title}>
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-subtle)]">{g.title}</p>
            <ul className="mt-3 space-y-2">
              {g.links.map(([label, href]) => (
                <li key={label}>
                  <Link href={href} className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
      <div className="border-t border-[var(--color-border)] px-6 py-5">
        <p className="mx-auto max-w-7xl text-xs text-[var(--color-text-subtle)]">
          © Aegis AI. Automated evaluations are assessments of the behaviour that was tested, not proof of safety. Framework mappings are informational, are not legal advice and do not certify compliance.
        </p>
      </div>
    </footer>
  );
}
