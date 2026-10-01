import Link from "next/link";
import { ContentPage } from "@/components/marketing/content-page";
import { PricingTable } from "@/components/marketing/pricing-table";

export const metadata = { title: "Pricing", description: "Aegis plans, quotas and features." };

export default function Page() {
  return (
    <ContentPage eyebrow="Pricing" title="Plans that scale with what you assure" lede="Every plan includes the full assurance loop. Plans differ in capacity, retention, enforcement and support. Self-hosted deployments configure their own catalogue.">
      <PricingTable />
      <p className="text-sm">Need a private deployment, custom retention or a security review first? <Link className="text-[var(--color-accent-bright)] hover:underline" href="/contact?kind=private_deployment">Talk to us</Link>.</p>
    </ContentPage>
  );
}
