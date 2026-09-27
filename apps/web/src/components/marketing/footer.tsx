import Link from "next/link";
import { Logo } from "@/components/logo";

const GROUPS = [
  { title: "Product", links: [["Product", "/product"], ["Solutions", "/solutions"], ["Pricing", "/pricing"], ["Live Audit", "/demo"]] },
  { title: "Developers", links: [["Docs", "/docs"], ["API", "/developers"], ["SDK", "/developers"], ["MCP Server", "/developers"]] },
  { title: "Company", links: [["About", "/about"], ["Security", "/security"]] },
];

export function MarketingFooter() {
  return (
    <footer className="border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
      <div className="mx-auto grid max-w-7xl gap-10 px-6 py-14 md:grid-cols-[1.5fr_repeat(3,1fr)]">
        <div>
          <Logo />
          <p className="mt-3 max-w-xs text-sm text-[var(--color-text-muted)]">Continuous assurance for intelligent systems. Evidence-backed AI governance.</p>
        </div>
        {GROUPS.map((g) => (
          <div key={g.title}>
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
          </div>
        ))}
      </div>
      <div className="border-t border-[var(--color-border)] px-6 py-5">
        <p className="mx-auto max-w-7xl text-xs text-[var(--color-text-subtle)]">
          © {new Date().getFullYear()} Aegis AI. Automated evaluations are assessments, not proof of perfect safety. Compliance mappings are informational and do not constitute legal advice.
        </p>
      </div>
    </footer>
  );
}
