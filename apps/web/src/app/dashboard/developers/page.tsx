"use client";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CodeBlock, CopyButton } from "@/components/ui/display";
import { Field, Input, Select } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, Button, EmptyState } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import { useApiKeys, useCan, useInvalidate, useRoles, useSession } from "@/lib/queries";
import type { ApiKey } from "@/lib/types";
import { formatDate, timeAgo, titleCase } from "@/lib/utils";

const TABS = ["keys", "sdk", "runtime", "mcp", "api"] as const;
type Tab = (typeof TABS)[number];

export default function DevelopersPage() {
  const search = useSearchParams();
  const router = useRouter();
  const can = useCan();
  const initial = (TABS as readonly string[]).includes(search.get("tab") ?? "") ? (search.get("tab") as Tab) : can("api_keys:manage") ? "keys" : "sdk";
  const [tab, setTab] = useState<Tab>(initial);
  return (
    <div>
      <PageHeader title="API & SDK" description="Integrate Aegis into agents, CI/CD and tooling. Every integration authenticates with an API key whose role and scopes the API enforces." />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
          router.replace(`/dashboard/developers?tab=${v}`);
        }}
      >
        <TabsList className="mb-5">
          {can("api_keys:manage") ? <TabTrigger value="keys">API keys</TabTrigger> : null}
          <TabTrigger value="sdk">Python SDK</TabTrigger>
          <TabTrigger value="runtime">Runtime Guard</TabTrigger>
          <TabTrigger value="mcp">MCP server</TabTrigger>
          <TabTrigger value="api">HTTP API</TabTrigger>
        </TabsList>
        <TabPanel value="keys">{tab === "keys" ? <KeysTab /> : null}</TabPanel>
        <TabPanel value="sdk">
          <Guide
            intro="The Python SDK wraps the HTTP API with retries (idempotent where it matters), typed errors and helpers for runtime tracing."
            blocks={[
              { language: "shell", code: "pip install aegis-ai" },
              {
                language: "python",
                code: `import os
from aegis_ai import Aegis

aegis = Aegis(os.environ["AEGIS_API_KEY"], base_url=os.environ.get("AEGIS_BASE_URL", "http://localhost:8000"))

# Run an audit and wait for the result
audit = aegis.audit(system_id="<system-id>", categories=["safety", "privacy", "prompt_injection"], wait=True)
print(audit["status"], audit["findings_count"])

# Verify its evidence chain
print(aegis.evidence.verify(audit["id"])["status"])  # VERIFIED / TAMPERED / ...`,
              },
            ]}
            note="Errors raise typed exceptions: PermissionDeniedError (role/scope), PlanLimitError (quota, with used/limit details), RateLimitError, NotFoundError."
          />
        </TabPanel>
        <TabPanel value="runtime">
          <Guide
            intro="Send agent telemetry and ask for a decision before sensitive actions. In observe and audit mode the answer is always “allowed”; in enforce mode honour it."
            blocks={[
              {
                language: "python",
                code: `with aegis.runtime.trace("<system-id>", agent="support-agent") as trace:
    decision = trace.check("tool.call", tool="send_email",
                           payload={"destination": "external", "data_classification": "confidential"})
    if decision.requires_approval:
        approved = aegis.runtime.wait_for_approval(decision.approval_id, timeout=300)
    elif decision.allowed:
        send_email(...)
    trace.event("model.response", payload={"output": text})  # batched, flushed on exit`,
              },
              {
                language: "shell",
                code: `curl -X POST "$AEGIS_BASE_URL/api/v1/runtime/check" \\
  -H "Authorization: Bearer $AEGIS_API_KEY" -H "Content-Type: application/json" \\
  -d '{"system_id":"<system-id>","event_type":"tool.call","tool":"delete_records","payload":{}}'`,
              },
            ]}
            note="Use an API key with the “runtime” scope. Payload fields that look like secrets or personal data are redacted before storage."
          />
        </TabPanel>
        <TabPanel value="mcp">
          <Guide
            intro="The MCP server exposes Aegis to MCP-capable assistants and agents (runtime checks, posture, findings, evidence verification, policy simulation). It has no privileges of its own — every call uses your API key."
            blocks={[
              { language: "shell", code: "pip install aegis-mcp\nAEGIS_API_KEY=aeg_live_... AEGIS_BASE_URL=https://aegis.example.com aegis-mcp" },
              {
                language: "json",
                code: `{
  "mcpServers": {
    "aegis": {
      "command": "aegis-mcp",
      "env": { "AEGIS_API_KEY": "aeg_live_...", "AEGIS_BASE_URL": "https://aegis.example.com" }
    }
  }
}`,
              },
            ]}
            note="Give the MCP key the narrowest role that works (for example a viewer key with the read and runtime scopes)."
          />
        </TabPanel>
        <TabPanel value="api">
          <Guide
            intro="All endpoints live under /api/v1 and return a consistent error envelope with a request id. Mutating calls accept an Idempotency-Key header so retries never double-apply."
            blocks={[
              {
                language: "shell",
                code: `curl "$AEGIS_BASE_URL/api/v1/findings?severity=critical&open_only=true" \\
  -H "Authorization: Bearer $AEGIS_API_KEY"`,
              },
              {
                language: "json",
                code: `{ "error": { "code": "plan_limit_exceeded", "message": "…", "request_id": "…", "details": { "metric": "audit_run", "used": 25, "limit": 25 } } }`,
              },
            ]}
            note="The interactive OpenAPI reference is served by the API at /docs."
          />
        </TabPanel>
      </Tabs>
    </div>
  );
}

function Guide({ intro, blocks, note }: { intro: string; blocks: { language: string; code: string }[]; note?: string }) {
  return (
    <div className="max-w-3xl space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">{intro}</p>
      {blocks.map((b, i) => (
        <CodeBlock key={i} language={b.language} code={b.code} />
      ))}
      {note ? <p className="text-xs text-[var(--color-text-subtle)]">{note}</p> : null}
    </div>
  );
}

const SCOPES = [
  { key: "read", label: "read", hint: "Read resources" },
  { key: "write", label: "write", hint: "Create and change resources" },
  { key: "run", label: "run", hint: "Start and cancel audits and red-team runs" },
  { key: "ingest", label: "ingest", hint: "Send traces, monitoring and runtime events" },
  { key: "runtime", label: "runtime", hint: "Runtime events and synchronous decisions" },
];

function KeysTab() {
  const can = useCan();
  const invalidate = useInvalidate();
  const query = useApiKeys();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);

  async function revoke() {
    if (!revoking) return;
    try {
      await api.delete(path`/api-keys/${revoking.id}`);
      toast.success(`${revoking.name} revoked`);
      invalidate("apiKeys");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <div className="space-y-4">
      {created ? (
        <div role="status" className="rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-success)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-success)_8%,transparent)] p-4">
          <p className="text-sm font-medium text-[var(--color-success)]">Copy your new API key now — it will not be shown again.</p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 truncate rounded-[var(--radius)] bg-[var(--color-bg)] px-3 py-2 font-mono text-xs">{created}</code>
            <CopyButton value={created} label="Copy API key" />
            <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>
              Dismiss
            </Button>
          </div>
        </div>
      ) : null}
      <div className="flex justify-end">
        {can("api_keys:manage") ? (
          <Button icon={Plus} onClick={() => setCreating(true)}>
            Create API key
          </Button>
        ) : null}
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(keys) =>
          keys.length === 0 ? (
            <EmptyState icon={KeyRound} title="No API keys" description="Create a key for the SDK, CI/CD or the MCP server. Give each integration its own key with the narrowest role." />
          ) : (
            <DataTable caption="API keys">
              <THead>
                <tr>
                  <TH>Name</TH>
                  <TH>Prefix</TH>
                  <TH>Role</TH>
                  <TH>Scopes</TH>
                  <TH>Last used</TH>
                  <TH>Expires</TH>
                  <TH />
                </tr>
              </THead>
              <tbody>
                {keys.map((k) => (
                  <TR key={k.id} className={k.revoked_at ? "opacity-60" : undefined}>
                    <TD className="font-medium">
                      {k.name} {k.revoked_at ? <Badge>Revoked</Badge> : null}
                    </TD>
                    <TD className="font-mono text-xs">{k.prefix}…</TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(k.role)}</TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {k.scopes.map((s) => (
                          <Badge key={s}>{s}</Badge>
                        ))}
                      </div>
                    </TD>
                    <TD className="text-[var(--color-text-subtle)]">{k.last_used_at ? timeAgo(k.last_used_at) : "Never"}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{k.expires_at ? formatDate(k.expires_at) : "No expiry"}</TD>
                    <TD>
                      {!k.revoked_at ? (
                        <button type="button" aria-label={`Revoke ${k.name}`} onClick={() => setRevoking(k)} className="rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
      <CreateKeyDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(plaintext) => {
          setCreated(plaintext);
          invalidate("apiKeys");
        }}
      />
      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke “${revoking?.name}”?`}
        confirmLabel="Revoke key"
        description="Every integration using this key stops working immediately. This cannot be undone — create a new key to restore access."
        onConfirm={revoke}
      />
    </div>
  );
}

function CreateKeyDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (plaintext: string) => void }) {
  const { data: session } = useSession();
  const roles = useRoles();
  const [name, setName] = useState("");
  const [role, setRole] = useState("developer");
  const [scopes, setScopes] = useState<string[]>(["read"]);
  const [expires, setExpires] = useState("90");
  const [busy, setBusy] = useState(false);
  const myRank = roles.data?.find((r) => r.role === session?.role)?.rank ?? 0;
  const choices = (roles.data ?? []).filter((r) => r.rank <= myRank && r.role !== "owner").sort((a, b) => a.rank - b.rank);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.post<{ plaintext: string }>("/api-keys", { name, role, scopes, expires_in_days: expires ? Number(expires) : undefined });
      onCreated(res.plaintext);
      setName("");
      onClose();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Create API key" description="A key can never have more access than your own role. The secret is shown once.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name">{(p) => <Input {...p} required maxLength={120} placeholder="ci-pipeline" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Role">
            {(p) => (
              <Select {...p} value={role} onChange={(e) => setRole(e.target.value)}>
                {choices.map((r) => (
                  <option key={r.role} value={r.role}>
                    {titleCase(r.role)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Expires">
            {(p) => (
              <Select {...p} value={expires} onChange={(e) => setExpires(e.target.value)}>
                <option value="30">In 30 days</option>
                <option value="90">In 90 days</option>
                <option value="365">In 1 year</option>
                <option value="">Never</option>
              </Select>
            )}
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-xs font-medium text-[var(--color-text-muted)]">Scopes (narrow the role further)</legend>
          <div className="space-y-1.5">
            {SCOPES.map((s) => (
              <label key={s.key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="accent-[var(--color-accent)]" checked={scopes.includes(s.key)} onChange={(e) => setScopes((prev) => (e.target.checked ? [...prev, s.key] : prev.filter((x) => x !== s.key)))} />
                <span className="font-mono text-xs">{s.label}</span>
                <span className="text-xs text-[var(--color-text-subtle)]">{s.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={!name || !scopes.length}>
            Create key
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

