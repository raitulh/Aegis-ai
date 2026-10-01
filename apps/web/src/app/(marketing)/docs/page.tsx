import Link from "next/link";
import { ContentPage, FeatureRow } from "@/components/marketing/content-page";

export const metadata = { title: "Docs", description: "Get Aegis running and run your first audit." };

const GUIDES: { title: string; body: string; href?: string }[] = [
  { title: "Runtime Guard", body: "Send agent events, choose observe / audit / enforce per system, and handle approvals in your agent.", href: "/developers#runtime" },
  { title: "Policy Studio", body: "Write runtime rules in YAML, validate, simulate on recorded events, publish, roll back and assign." },
  { title: "Evidence verification", body: "Export a signed package and verify it offline with the bundled verify.py and the workspace public key." },
  { title: "Continuous assurance", body: "Schedules, change triggers from CI/CD, known-good baselines and regression detection." },
  { title: "Roles and API keys", body: "Nine roles from viewer to owner; API keys are bound to a role and narrowed further by scopes." },
  { title: "Self-hosting", body: "Docker Compose for evaluation; production checklist for secrets, TLS, backups and monitoring." },
];

export default function Page() {
  return (
    <ContentPage eyebrow="Documentation" title="Get started" lede="Run Aegis locally, connect a model, and run your first audit. The full guides ship with the source in the docs/ folder.">
      <section aria-labelledby="quickstart" className="space-y-3">
        <h2 id="quickstart" className="text-xl font-semibold text-[var(--color-text)]">
          Quickstart (local)
        </h2>
        <pre className="overflow-x-auto rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 font-mono text-xs">{`cp .env.example .env          # production: set SECRETS_ENCRYPTION_KEY, API_KEY_PEPPER, EVIDENCE_SIGNING_KEY
docker compose up --build      # postgres, redis, api, worker, scheduler, web
open http://localhost:3000     # create a workspace, or try the sandbox`}</pre>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>Add a model provider under Integrations (Ollama for local models, or a hosted provider with explicit consent).</li>
          <li>Register an AI system and run a baseline audit; mark it as the known-good baseline.</li>
          <li>Create an API key under API &amp; SDK and instrument your agent with the SDK or MCP server.</li>
          <li>Start a runtime policy from the library, simulate it on recorded events, then publish it.</li>
        </ol>
      </section>
      <section aria-labelledby="guides" className="space-y-3">
        <h2 id="guides" className="text-xl font-semibold text-[var(--color-text)]">
          Guides
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {GUIDES.map((g) => (
            <FeatureRow key={g.title} title={g.title} body={g.body} />
          ))}
        </div>
      </section>
      <section aria-labelledby="reference" className="space-y-2">
        <h2 id="reference" className="text-xl font-semibold text-[var(--color-text)]">
          API reference
        </h2>
        <p className="text-sm">
          Your Aegis API serves an interactive OpenAPI reference at <code className="font-mono text-[var(--color-accent-bright)]">/docs</code> on the API host (for a local install: <code className="font-mono">http://localhost:8000/docs</code>). See also{" "}
          <Link className="text-[var(--color-accent-bright)] hover:underline" href="/developers">
            API, SDK &amp; MCP
          </Link>
          .
        </p>
      </section>
    </ContentPage>
  );
}
