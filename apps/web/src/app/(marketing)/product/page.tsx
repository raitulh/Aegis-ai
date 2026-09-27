import { ContentPage, FeatureRow } from "@/components/marketing/content-page";
export const metadata = { title: "Product" };
export default function Page() {
  return (
    <ContentPage eyebrow="Product" title="The AI assurance loop" lede="Observe → generate tests → execute → verify → detect risk → map to policy → collect evidence → create finding → recommend remediation → re-test → monitor.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Six audit engines" body="Fairness, truth, safety, privacy, security and governance — each with a documented methodology and limitations." />
        <FeatureRow title="Policy compiler" body="Compile natural-language policies into executable controls with full source provenance." />
        <FeatureRow title="Adaptive red team" body="Run adversarial probes from an imported corpus and track the full attack lineage." />
        <FeatureRow title="Evidence & re-test" body="Immutable, hash-chained evidence and measured before/after re-testing." />
      </div>
    </ContentPage>
  );
}
