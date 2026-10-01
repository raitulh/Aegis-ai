import Link from "next/link";
import { ContentPage, FeatureRow } from "@/components/marketing/content-page";

export const metadata = { title: "Security", description: "Aegis security architecture." };

export default function Page() {
  return (
    <ContentPage eyebrow="Security" title="Security architecture" lede="Tenant isolation, least privilege and evidence integrity are enforced by the server — the interface only mirrors them.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Tenant isolation" body="PostgreSQL Row Level Security on every tenant table, behind application-level scoping. The workspace comes from your authenticated membership, never from the client. Cross-tenant tests run in CI." />
        <FeatureRow title="Roles and keys" body="Nine roles from viewer to owner, checked on every request. API keys are bound to a role, narrowed by scopes, hashed with a server-side pepper and shown once." />
        <FeatureRow title="Evidence integrity" body="Evidence is append-only (database triggers reject edits) and hash-chained. Exports are Ed25519-signed and verifiable offline." />
        <FeatureRow title="Outbound requests" body="Provider, endpoint and webhook URLs are checked at connect time with IP pinning, so DNS rebinding cannot reach internal networks. Webhooks are HMAC-signed." />
        <FeatureRow title="Secrets" body="Provider credentials and webhook secrets are encrypted at rest and never returned by the API. Sensitive evidence is masked; revealing it is permissioned and audit-logged." />
        <FeatureRow title="Web application" body="Same-origin backend-for-frontend with httpOnly session cookies, Origin checks on writes, a Content Security Policy and per-account login throttling." />
      </div>
      <p className="text-sm">
        Attestation status and data handling are listed in the{" "}
        <Link className="text-[var(--color-accent-bright)] hover:underline" href="/trust">
          trust center
        </Link>
        . Report a vulnerability via the{" "}
        <Link className="text-[var(--color-accent-bright)] hover:underline" href="/contact?kind=security_review">
          contact form
        </Link>
        .
      </p>
    </ContentPage>
  );
}
