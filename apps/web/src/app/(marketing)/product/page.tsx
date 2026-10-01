import { ContentPage, FeatureRow } from "@/components/marketing/content-page";

export const metadata = { title: "Product", description: "The Aegis assurance loop." };

export default function Page() {
  return (
    <ContentPage eyebrow="Product" title="The AI assurance loop" lede="Policy → test → guard → evidence → finding → fix → re-test, in one system of record.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Audit engines" body="Fairness (counterfactual), grounding, safety, privacy, injection and agent-action tests, each with a documented method and stated limitations." />
        <FeatureRow title="Runtime Guard" body="Check agent actions as they happen. Observe, audit (findings with evidence) or enforce (block, or hold for human approval) per system." />
        <FeatureRow title="Policy Studio" body="Versioned runtime policies with validation, diff, simulation on your recorded traffic, publish, rollback and scoped assignments." />
        <FeatureRow title="Policy compiler" body="Compile written policies (PDF, DOCX, text) into testable controls, each traced to its source section and page." />
        <FeatureRow title="Continuous assurance" body="Scheduled and change-triggered audits with risk-based test selection, baselines and regression alerts." />
        <FeatureRow title="Evidence & findings" body="Hash-chained evidence, signed export packages, and a finding lifecycle with SLAs, risk acceptance with expiry and verified re-tests." />
        <FeatureRow title="Red team" body="Adaptive campaigns driven by a corpus you import, with full attack lineage. Aegis ships no attack payloads." />
        <FeatureRow title="Assurance graph" body="See how policies, systems, tests, evidence, findings and fixes connect — built from your records, not illustrations." />
      </div>
    </ContentPage>
  );
}
