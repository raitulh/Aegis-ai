"use client";
import { useQuery } from "@tanstack/react-query";
import { Cpu, Plus, Send, Trash2, Webhook as WebhookIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CodeBlock, CopyButton, PlanLimitNotice, SectionTitle } from "@/components/ui/display";
import { Field, Input, Select, Switch } from "@/components/ui/forms";
import { Badge, Button, Card, CardBody, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path, type Page } from "@/lib/api";
import { useCan, useInvalidate, useProviders, useWebhooks } from "@/lib/queries";
import type { Provider, Webhook, WebhookDelivery } from "@/lib/types";
import { formatDateTime, timeAgo, titleCase } from "@/lib/utils";

const PROVIDER_KINDS: { kind: string; label: string; local: boolean; needsKey: boolean; note: string }[] = [
  { kind: "ollama", label: "Ollama", local: true, needsKey: false, note: "Local models (e.g. Qwen, Llama). Data stays on infrastructure you run." },
  { kind: "gemini", label: "Google Gemini", local: false, needsKey: true, note: "Prompts and outputs are sent to Google." },
  { kind: "openai", label: "OpenAI", local: false, needsKey: true, note: "Prompts and outputs are sent to OpenAI." },
  { kind: "anthropic", label: "Anthropic", local: false, needsKey: true, note: "Prompts and outputs are sent to Anthropic." },
];

export default function IntegrationsPage() {
  return (
    <div className="space-y-10">
      <PageHeader title="Integrations" description="Model providers that audits run against, and signed webhooks that push Aegis events into your systems." />
      <ProvidersSection />
      <WebhooksSection />
      <section>
        <SectionTitle>Other channels</SectionTitle>
        <div className="grid gap-3 md:grid-cols-3">
          {[
            ["Slack", "Not built in yet. Point a webhook at a small relay (or Slack Workflow webhook) to post critical events."],
            ["GitHub / CI", "Report changes with the assurance trigger API from any CI system — see API & SDK."],
            ["Email", "Invitations and alerts use the SMTP server your operator configures for this deployment."],
          ].map(([name, text]) => (
            <Card key={name}>
              <CardBody>
                <p className="text-sm font-medium">{name}</p>
                <p className="mt-1 text-xs text-[var(--color-text-muted)]">{text}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}

/* --------------------------------- Providers --------------------------------- */
function ProvidersSection() {
  const can = useCan();
  const invalidate = useInvalidate();
  const query = useProviders();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Provider | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const manage = can("providers:manage");

  async function test(p: Provider) {
    setTesting(p.id);
    try {
      const r = await api.post<{ ok: boolean; detail: string; latency_ms: number | null; models: string[] }>(path`/providers/${p.id}/test`);
      if (r.ok) toast.success(`${p.name}: connected${r.latency_ms ? ` in ${r.latency_ms} ms` : ""}${r.models.length ? ` · ${r.models.length} models` : ""}`);
      else toast.error(`${p.name}: ${r.detail}`);
      invalidate("providers");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setTesting(null);
    }
  }

  async function remove() {
    if (!removing) return;
    try {
      await api.delete(path`/providers/${removing.id}`);
      invalidate("providers");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <section>
      <SectionTitle
        action={
          manage ? (
            <Button size="sm" icon={Plus} onClick={() => setAdding(true)}>
              Add provider
            </Button>
          ) : null
        }
      >
        Model providers
      </SectionTitle>
      <QueryBoundary query={query} skeleton={<div className="h-32 skeleton rounded-[var(--radius-lg)]" />}>
        {(providers) => {
          const real = providers.filter((p) => p.kind !== "demo");
          return real.length === 0 ? (
            <EmptyState icon={Cpu} title="No model providers" description="Add Ollama for local models or a hosted provider. Credentials are encrypted at rest and never returned by the API." />
          ) : (
            <DataTable caption="Model providers">
              <THead>
                <tr>
                  <TH>Provider</TH>
                  <TH>Default model</TH>
                  <TH>Status</TH>
                  <TH>Last checked</TH>
                  <TH />
                </tr>
              </THead>
              <tbody>
                {real.map((p) => (
                  <TR key={p.id}>
                    <TD>
                      <p className="font-medium">{p.name}</p>
                      <p className="text-xs text-[var(--color-text-subtle)]">
                        {PROVIDER_KINDS.find((k) => k.kind === p.kind)?.label ?? titleCase(p.kind)}
                        {p.base_url ? ` · ${p.base_url}` : ""}
                        {p.has_credentials ? " · key stored" : ""}
                      </p>
                    </TD>
                    <TD className="font-mono text-xs">{p.default_model ?? "—"}</TD>
                    <TD>
                      <StatusBadge status={p.status === "connected" ? "succeeded" : p.status === "error" ? "failed" : "pending"} />
                      {p.last_error ? <p className="mt-0.5 max-w-56 truncate text-xs text-[var(--color-critical)]" title={p.last_error}>{p.last_error}</p> : null}
                    </TD>
                    <TD className="text-xs text-[var(--color-text-subtle)]">{p.last_checked_at ? timeAgo(p.last_checked_at) : "Never"}</TD>
                    <TD>
                      {manage ? (
                        <div className="flex items-center justify-end gap-1">
                          <Button size="sm" variant="ghost" loading={testing === p.id} onClick={() => test(p)}>
                            Test
                          </Button>
                          <button type="button" aria-label={`Remove ${p.name}`} onClick={() => setRemoving(p)} className="rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          );
        }}
      </QueryBoundary>
      <AddProviderDialog open={adding} onClose={() => setAdding(false)} />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.name}?`}
        description="Systems using this provider can no longer be audited until you connect another one. The stored credential is deleted."
        confirmLabel="Remove provider"
        onConfirm={remove}
      />
    </section>
  );
}

function AddProviderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [kind, setKind] = useState("ollama");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [allowExternal, setAllowExternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const meta = PROVIDER_KINDS.find((k) => k.kind === kind)!;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/providers", {
        kind,
        name: name || meta.label,
        base_url: kind === "ollama" && baseUrl ? baseUrl : undefined,
        default_model: model || undefined,
        api_key: meta.needsKey ? apiKey : undefined,
        allow_data_processing: meta.local ? true : allowExternal,
      });
      invalidate("providers");
      toast.success("Provider added — run a connection test");
      setApiKey("");
      onClose();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Add model provider">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Provider" hint={meta.note}>
          {(p) => (
            <Select {...p} value={kind} onChange={(e) => setKind(e.target.value)}>
              {PROVIDER_KINDS.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} placeholder={meta.label} maxLength={120} />}</Field>
        {kind === "ollama" ? (
          <Field label="Base URL (optional)" hint="Leave empty to use the operator-configured Ollama. Custom URLs must be publicly routable unless your operator allows private targets.">
            {(p) => <Input {...p} type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://ollama.internal.example:11434" />}
          </Field>
        ) : null}
        <Field label="Default model (optional)">{(p) => <Input {...p} value={model} onChange={(e) => setModel(e.target.value)} className="font-mono text-xs" />}</Field>
        {meta.needsKey ? (
          <>
            <Field label="API key" hint="Encrypted at rest; it is never shown again or returned by the API.">
              {(p) => <Input {...p} type="password" autoComplete="off" required value={apiKey} onChange={(e) => setApiKey(e.target.value)} />}
            </Field>
            <label className="flex items-start gap-2.5 text-sm">
              <Switch checked={allowExternal} onChange={setAllowExternal} label="Allow sending data to this provider" />
              <span className="text-[var(--color-text-muted)]">I confirm test prompts and model outputs may be processed by this external provider.</span>
            </label>
          </>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={meta.needsKey && (!apiKey || !allowExternal)}>
            Add provider
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/* --------------------------------- Webhooks ---------------------------------- */
type Catalogue = { events: string[]; signature: { header: string; scheme: string; replay_window_seconds: number; idempotency: string }; retries: { max_attempts: number } };

function WebhooksSection() {
  const can = useCan();
  const invalidate = useInvalidate();
  const query = useWebhooks();
  const catalogue = useQuery({ queryKey: ["webhook-events"], queryFn: () => api.get<Catalogue>("/webhooks/events"), staleTime: 10 * 60_000 });
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Webhook | null>(null);
  const [deliveries, setDeliveries] = useState<Webhook | null>(null);
  const manage = can("webhooks:manage");

  async function toggle(w: Webhook, active: boolean) {
    try {
      await api.patch(path`/webhooks/${w.id}`, { active });
      invalidate("webhooks");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function sendTest(w: Webhook) {
    try {
      const d = await api.post<WebhookDelivery>(path`/webhooks/${w.id}/test`);
      if (d.status === "succeeded") toast.success(`Test delivered (HTTP ${d.response_status})`);
      else toast.error(`Test delivery ${d.status}${d.response_status ? ` (HTTP ${d.response_status})` : ""}${d.last_error ? `: ${d.last_error}` : ""}`);
      invalidate("webhooks");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function remove() {
    if (!removing) return;
    try {
      await api.delete(path`/webhooks/${removing.id}`);
      invalidate("webhooks");
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  return (
    <section>
      <SectionTitle
        action={
          manage ? (
            <Button size="sm" icon={Plus} onClick={() => setCreating(true)}>
              Add webhook
            </Button>
          ) : null
        }
      >
        Webhooks
      </SectionTitle>
      {secret ? (
        <div role="status" className="mb-3 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-success)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-success)_8%,transparent)] p-4">
          <p className="text-sm font-medium text-[var(--color-success)]">Signing secret — copy it now; it will not be shown again.</p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 truncate rounded-[var(--radius)] bg-[var(--color-bg)] px-3 py-2 font-mono text-xs">{secret}</code>
            <CopyButton value={secret} label="Copy signing secret" />
            <Button size="sm" variant="ghost" onClick={() => setSecret(null)}>
              Dismiss
            </Button>
          </div>
        </div>
      ) : null}
      <QueryBoundary query={query} skeleton={<div className="h-32 skeleton rounded-[var(--radius-lg)]" />}>
        {(hooks) =>
          hooks.length === 0 ? (
            <EmptyState icon={WebhookIcon} title="No webhooks" description="Receive signed, retried events such as audit.completed, critical_risk.detected or regression.detected." />
          ) : (
            <DataTable caption="Webhooks">
              <THead>
                <tr>
                  <TH>Endpoint</TH>
                  <TH>Events</TH>
                  <TH>Last delivery</TH>
                  <TH>Active</TH>
                  <TH />
                </tr>
              </THead>
              <tbody>
                {hooks.map((w) => (
                  <TR key={w.id}>
                    <TD className="max-w-72">
                      <p className="truncate font-mono text-xs">{w.url}</p>
                      {w.description ? <p className="truncate text-xs text-[var(--color-text-subtle)]">{w.description}</p> : null}
                      {w.failure_count ? <p className="text-xs text-[var(--color-high)]">{w.failure_count} consecutive failures</p> : null}
                    </TD>
                    <TD className="max-w-64 text-xs text-[var(--color-text-muted)]">{w.events.length ? w.events.join(", ") : "All events"}</TD>
                    <TD className="text-xs text-[var(--color-text-subtle)]">{w.last_delivery_at ? timeAgo(w.last_delivery_at) : "Never"}</TD>
                    <TD>
                      <Switch checked={w.active} onChange={(v) => toggle(w, v)} label={`Webhook ${w.url} active`} disabled={!manage} />
                    </TD>
                    <TD>
                      <div className="flex items-center justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setDeliveries(w)}>
                          Deliveries
                        </Button>
                        {manage ? (
                          <>
                            <Button size="sm" variant="ghost" icon={Send} onClick={() => sendTest(w)}>
                              Test
                            </Button>
                            <button type="button" aria-label={`Delete webhook ${w.url}`} onClick={() => setRemoving(w)} className="rounded-md p-1 text-[var(--color-text-subtle)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-critical)] focus-ring">
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </>
                        ) : null}
                      </div>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>
      {catalogue.data ? (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]">How to verify webhook signatures</summary>
          <div className="mt-2 max-w-3xl space-y-2 text-xs text-[var(--color-text-muted)]">
            <p>
              Header <code className="font-mono">{catalogue.data.signature.header}</code>: <code className="font-mono">{catalogue.data.signature.scheme}</code>. Reject timestamps older than {catalogue.data.signature.replay_window_seconds}s. {catalogue.data.signature.idempotency} Failed deliveries are retried up to {catalogue.data.retries.max_attempts} times.
            </p>
            <CodeBlock
              language="python"
              code={`import hmac, hashlib, time

def verify(secret: str, header: str, body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])`}
            />
          </div>
        </details>
      ) : null}
      <CreateWebhookDialog
        open={creating}
        events={catalogue.data?.events ?? []}
        onClose={() => setCreating(false)}
        onCreated={(s) => {
          setSecret(s);
          invalidate("webhooks");
        }}
      />
      <DeliveriesDialog webhook={deliveries} onClose={() => setDeliveries(null)} />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Delete this webhook?"
        description="No further events are delivered to this endpoint. Pending retries are dropped."
        confirmLabel="Delete webhook"
        onConfirm={remove}
      />
    </section>
  );
}

function CreateWebhookDialog({ open, events, onClose, onCreated }: { open: boolean; events: string[]; onClose: () => void; onCreated: (secret: string) => void }) {
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ signing_secret: string }>("/webhooks", { url, description: description || undefined, events: selected });
      onCreated(r.signing_secret);
      setUrl("");
      setDescription("");
      setSelected([]);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Add webhook" description="HTTPS is required in production. Requests are signed with HMAC-SHA256; private and internal addresses are rejected.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Endpoint URL">{(p) => <Input {...p} type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/aegis" />}</Field>
        <Field label="Description (optional)">{(p) => <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />}</Field>
        <fieldset>
          <legend className="mb-1.5 text-xs font-medium text-[var(--color-text-muted)]">Events (none selected = all)</legend>
          <div className="grid grid-cols-2 gap-1.5">
            {events.map((ev) => (
              <label key={ev} className="flex items-center gap-2 font-mono text-xs">
                <input type="checkbox" className="accent-[var(--color-accent)]" checked={selected.includes(ev)} onChange={(e) => setSelected((prev) => (e.target.checked ? [...prev, ev] : prev.filter((x) => x !== ev)))} />
                {ev}
              </label>
            ))}
          </div>
        </fieldset>
        {error ? <PlanLimitNotice error={error} /> : null}
        {error && !(error as { isPlanLimit?: boolean }).isPlanLimit ? (
          <p role="alert" className="text-sm text-[var(--color-critical)]">
            {errorMessage(error)}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy} disabled={!url}>
            Add webhook
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function DeliveriesDialog({ webhook, onClose }: { webhook: Webhook | null; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["webhooks", webhook?.id, "deliveries"],
    queryFn: () => api.get<Page<WebhookDelivery>>(path`/webhooks/${webhook!.id}/deliveries`, { page_size: 50 }),
    enabled: !!webhook,
  });
  return (
    <Dialog open={!!webhook} onOpenChange={(o) => !o && onClose()} title="Recent deliveries" description={webhook?.url} className="max-w-2xl">
      <QueryBoundary query={query} skeleton={<div className="h-32 skeleton" />}>
        {(page) =>
          page.items.length === 0 ? (
            <p className="text-sm text-[var(--color-text-subtle)]">No deliveries yet.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {page.items.map((d) => (
                <li key={d.id} className="flex flex-wrap items-center gap-2 rounded border border-[var(--color-border)] px-2.5 py-1.5">
                  <StatusBadge status={d.status} />
                  <span className="font-mono">{d.event_type}</span>
                  <span className="text-[var(--color-text-muted)]">
                    {d.response_status ? `HTTP ${d.response_status}` : ""} · {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
                  </span>
                  {d.last_error ? <span className="max-w-60 truncate text-[var(--color-critical)]" title={d.last_error}>{d.last_error}</span> : null}
                  <span className="ml-auto text-[var(--color-text-subtle)]">{formatDateTime(d.created_at)}</span>
                </li>
              ))}
            </ul>
          )
        }
      </QueryBoundary>
      <p className="mt-3 text-xs text-[var(--color-text-subtle)]">
        <Badge>Note</Badge> After 20 consecutive failures a webhook is disabled automatically; re-enable it once the endpoint is fixed.
      </p>
    </Dialog>
  );
}
