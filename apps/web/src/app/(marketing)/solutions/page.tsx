import { ContentPage, FeatureRow } from "@/components/marketing/content-page";
export const metadata = { title: "Solutions" };
export default function Page() {
  return (
    <ContentPage eyebrow="Solutions" title="Assurance for every AI system" lede="LLM apps, RAG systems, tool-using agents and traditional ML — governed with one evidence model.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Hiring & HR AI" body="Counterfactual fairness testing and human-oversight controls for consequential decisions." />
        <FeatureRow title="Customer support RAG" body="Groundedness verification and PII leakage detection across retrieval." />
        <FeatureRow title="Autonomous agents" body="Tool-action auditing, authorization checks and data-exfiltration testing." />
        <FeatureRow title="Regulated industries" body="Framework-mapped control coverage and audit-ready evidence packages." />
      </div>
    </ContentPage>
  );
}
