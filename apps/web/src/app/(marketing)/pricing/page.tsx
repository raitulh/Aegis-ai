import Link from "next/link";
import { ContentPage } from "@/components/marketing/content-page";
import { Button } from "@/components/ui/primitives";
export const metadata = { title: "Pricing" };
const PLANS = [
  { name: "Free", price: "$0", features: ["1 workspace", "Simulated systems", "Manual audits", "Community support"] },
  { name: "Developer", price: "$—", features: ["API & SDK access", "5 AI systems", "Adaptive red team", "Framework mapping"] },
  { name: "Team", price: "$—", features: ["Unlimited systems", "Continuous monitoring", "Webhooks & Slack", "RBAC & audit log"] },
  { name: "Enterprise", price: "Contact", features: ["SSO & SCIM", "Custom frameworks", "On-prem models", "Dedicated support"] },
];
export default function Page() {
  return (
    <ContentPage eyebrow="Pricing" title="Simple, usage-aware plans" lede="Start free with the starter workspace. Scale by systems, tests and storage.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PLANS.map((p) => (
          <div key={p.name} className="rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">{p.name}</h3>
            <p className="mt-1 font-mono text-2xl font-semibold text-[var(--color-text)]">{p.price}</p>
            <ul className="mt-3 space-y-1.5 text-sm">{p.features.map((f) => <li key={f}>• {f}</li>)}</ul>
          </div>
        ))}
      </div>
      <Link href="/signup"><Button>Get started free</Button></Link>
    </ContentPage>
  );
}
