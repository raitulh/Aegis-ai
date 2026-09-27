import { ContentPage, FeatureRow } from "@/components/marketing/content-page";
export const metadata = { title: "Security" };
export default function Page() {
  return (
    <ContentPage eyebrow="Security" title="Security architecture" lede="Multi-tenant isolation, least privilege and evidence integrity by design.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Tenant isolation" body="PostgreSQL Row Level Security plus server-side organization scoping. Organization context is derived from authenticated membership, never the client." />
        <FeatureRow title="Secrets" body="Provider credentials are encrypted at rest; API keys are hashed with a server-side pepper and shown once." />
        <FeatureRow title="Evidence integrity" body="Audit evidence is append-only and hash-chained, so tampering is detectable." />
        <FeatureRow title="SSRF & uploads" body="Outbound URLs are validated against internal ranges; uploads are type-sniffed and size-limited." />
      </div>
      <p className="text-sm text-[var(--color-text-subtle)]">Data is not sent to external model providers without explicit per-provider consent.</p>
    </ContentPage>
  );
}
