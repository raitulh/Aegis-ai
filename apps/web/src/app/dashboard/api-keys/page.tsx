"use client";
import { useState } from "react";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { DataTable, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Badge, Button, EmptyState } from "@/components/ui/primitives";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { api, ApiError } from "@/lib/api";
import { useApiKeys, useInvalidate } from "@/lib/queries";
import { timeAgo, titleCase } from "@/lib/utils";

export default function ApiKeysPage() {
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const invalidate = useInvalidate();
  const query = useApiKeys();

  async function revoke(id: string) {
    await api.delete(`/api-keys/${id}`).catch(() => {});
    invalidate("apiKeys");
  }

  return (
    <div>
      <PageHeader title="API Keys" description="Programmatic access for the SDK, MCP server and API. Keys are scoped by role and shown once." actions={<Button icon={Plus} onClick={() => setOpen(true)}>Create Key</Button>} />
      {created ? (
        <div className="mb-4 rounded-[var(--radius)] border border-[color-mix(in_srgb,var(--color-success)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-success)_8%,transparent)] p-4">
          <p className="text-sm font-medium text-[var(--color-success)]">Your new API key — copy it now, it won't be shown again.</p>
          <div className="mt-2 flex items-center gap-2">
            <code className="flex-1 truncate rounded-[var(--radius-sm)] bg-[var(--color-bg)] px-3 py-2 font-mono text-xs">{created}</code>
            <Button size="sm" variant="secondary" icon={Copy} onClick={() => { navigator.clipboard.writeText(created); toast.success("Copied"); }}>Copy</Button>
            <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>Dismiss</Button>
          </div>
        </div>
      ) : null}
      <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
        {(keys) =>
          keys.length === 0 ? (
            <EmptyState icon={KeyRound} title="No API keys" description="Create a key to use the Aegis SDK, API and MCP server." action={<Button size="sm" icon={Plus} onClick={() => setOpen(true)}>Create Key</Button>} />
          ) : (
            <DataTable>
              <THead><TR><TH>Name</TH><TH>Prefix</TH><TH>Role</TH><TH>Scopes</TH><TH>Last used</TH><TH></TH></TR></THead>
              <tbody>
                {keys.map((k) => (
                  <TR key={k.id}>
                    <TD className="font-medium">{k.name}{k.revoked_at ? <Badge>Revoked</Badge> : null}</TD>
                    <TD className="font-mono text-xs">{k.prefix}…</TD>
                    <TD className="text-[var(--color-text-muted)]">{titleCase(k.role)}</TD>
                    <TD>{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</TD>
                    <TD className="text-[var(--color-text-subtle)]">{k.last_used_at ? timeAgo(k.last_used_at) : "never"}</TD>
                    <TD>{!k.revoked_at ? <button onClick={() => revoke(k.id)} className="text-[var(--color-text-subtle)] hover:text-[var(--color-critical)]"><Trash2 className="h-4 w-4" /></button> : null}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )
        }
      </QueryBoundary>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent title="Create API Key" description="Choose a role and scopes. The key is shown once.">
          <form
            className="space-y-4 p-6"
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              try {
                const scopes = ["read", "write", "run", "ingest"].filter((s) => f.get(s));
                const res = await api.post<{ plaintext: string }>("/api-keys", { name: f.get("name"), role: f.get("role"), scopes: scopes.length ? scopes : ["read"] });
                setCreated(res.plaintext);
                invalidate("apiKeys");
                setOpen(false);
              } catch (err) {
                toast.error(err instanceof ApiError ? err.message : "Failed");
              }
            }}
          >
            <label className="block"><span className="mb-1 block text-xs text-[var(--color-text-muted)]">Name</span><input name="name" required placeholder="CI pipeline" className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]" /></label>
            <label className="block"><span className="mb-1 block text-xs text-[var(--color-text-muted)]">Role</span><select name="role" className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none"><option value="viewer">Viewer</option><option value="analyst">Analyst</option><option value="auditor">Auditor</option></select></label>
            <div><span className="mb-1 block text-xs text-[var(--color-text-muted)]">Scopes</span><div className="flex flex-wrap gap-3">{["read", "write", "run", "ingest"].map((s) => <label key={s} className="flex items-center gap-1.5 text-sm"><input type="checkbox" name={s} defaultChecked={s === "read"} className="accent-[var(--color-accent)]" />{s}</label>)}</div></div>
            <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit">Create</Button></div>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
