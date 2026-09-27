import { ContentPage, FeatureRow } from "@/components/marketing/content-page";
export const metadata = { title: "Docs" };
export default function Page() {
  return (
    <ContentPage eyebrow="Documentation" title="Get started in minutes" lede="Register a system, connect a provider, compile a policy, and run your first audit.">
      <div className="grid gap-3 sm:grid-cols-2">
        <FeatureRow title="Quickstart" body="docker compose up, then open the dashboard and try the Hiring Agent audit." />
        <FeatureRow title="Providers" body="Connect Ollama, Gemini, OpenAI or Anthropic — or a generic HTTP endpoint." />
        <FeatureRow title="Policies" body="Upload a PDF/DOCX policy; Aegis compiles it into controls you can audit." />
        <FeatureRow title="API reference" body="Full OpenAPI at /docs, plus the Python SDK and MCP server." />
      </div>
    </ContentPage>
  );
}
