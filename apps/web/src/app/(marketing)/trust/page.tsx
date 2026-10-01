import Link from "next/link";
import { ContentPage } from "@/components/marketing/content-page";
import { TrustCenter } from "@/components/marketing/trust-center";

export const metadata = { title: "Trust center", description: "How Aegis protects tenant data and evidence, and what has and has not been independently attested." };

export default function Page() {
  return (
    <ContentPage eyebrow="Trust center" title="What we can show you" lede="Security controls built into the product, what depends on your deployment, and an honest status of external attestations.">
      <TrustCenter />
      <p className="text-sm">
        Need a security questionnaire or architecture review?{" "}
        <Link className="text-[var(--color-accent-bright)] hover:underline" href="/contact?kind=security_review">
          Request a security review
        </Link>
        .
      </p>
    </ContentPage>
  );
}
