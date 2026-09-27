import { ContentPage } from "@/components/marketing/content-page";
export const metadata = { title: "About" };
export default function Page() {
  return (
    <ContentPage eyebrow="About" title="Governance you can prove" lede="Aegis exists to help humans make informed governance decisions about AI — not to reduce complex risk to a single 'AI is safe' score.">
      <p>The product thesis is simple: Policy → Test → Attack → Evidence → Finding → Remediation → Re-test. Every automated judgment is deterministic where possible, model-assisted where useful, and evidence-backed everywhere.</p>
    </ContentPage>
  );
}
