"use client";

import { useQuery } from "@tanstack/react-query";
import { Flag, Globe, Lock, Pencil, Plus, ToggleRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AdminHeader, TileGrid } from "@/components/admin/admin-ui";
import type { FlagRow } from "@/components/admin/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Switch } from "@/components/ui/form";
import { EmptyState, QueryState, SkeletonRows } from "@/components/ui/states";
import { get, put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import { useApiMutation } from "@/lib/hooks";

type FlagBody = { key: string; enabled: boolean; description?: string | null; is_public?: boolean };
const KEY_RE = /^[a-z0-9_.]{1,64}$/;

function useSetFlag() {
  return useApiMutation(
    ({ key, ...body }: FlagBody) => put<{ message: string }>(`/admin/flags/${encodeURIComponent(key)}`, body),
    { success: (r) => r.message, invalidate: [["admin", "flags"], ["config"]] },
  );
}

function EditFlag({ flag }: { flag: FlagRow }) {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState(flag.description ?? "");
  const [isPublic, setIsPublic] = useState(flag.is_public);
  const set = useSetFlag();
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setDescription(flag.description ?? "");
          setIsPublic(flag.is_public);
        }
      }}
      trigger={<Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} aria-label={`Edit ${flag.key}`}>Edit</Button>}
      title={<span className="font-mono">{flag.key}</span>}
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            loading={set.isPending}
            onClick={() => set.mutate({ key: flag.key, enabled: flag.enabled, description: description.trim(), is_public: isPublic }, { onSuccess: () => setOpen(false) })}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Description">
          {(p) => <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />}
        </Field>
        <Checkbox
          checked={isPublic}
          onChange={(e) => setIsPublic(e.target.checked)}
          label="Expose to the browser"
          description="Public flags are included in /meta/config so the web app can read them. Keep internal kill-switches private."
        />
      </div>
    </Dialog>
  );
}

function NewFlag() {
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [isPublic, setIsPublic] = useState(false);
  const set = useSetFlag();
  const valid = KEY_RE.test(key);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    set.mutate(
      { key, enabled, description: description.trim() || null, is_public: isPublic },
      { onSuccess: () => { setKey(""); setDescription(""); setEnabled(false); setIsPublic(false); } },
    );
  }

  return (
    <Card>
      <CardHeader icon={<Plus />} title="Add a flag" description="Creates the flag if it doesn’t exist. Code decides what each flag does." />
      <form onSubmit={submit} noValidate>
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <Field label="Key" required hint="Lowercase letters, digits, dots and underscores." error={key && !valid ? "Use a–z, 0–9, “.” and “_” only (max 64)." : undefined}>
            {(p) => <Input {...p} value={key} onChange={(e) => setKey(e.target.value.toLowerCase())} maxLength={64} className="font-mono" placeholder="e.g. discussions.enabled" />}
          </Field>
          <Field label="Description">
            {(p) => <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />}
          </Field>
          <div className="rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 p-3">
            <Switch checked={enabled} onChange={setEnabled} label="Enabled" />
          </div>
          <div className="rounded-[var(--radius-md)] border border-border bg-bg-elevated/60 p-3">
            <Switch checked={isPublic} onChange={setIsPublic} label="Public" description="Readable by the browser via /meta/config." />
          </div>
        </CardBody>
        <CardFooter>
          <Button type="submit" loading={set.isPending} disabled={!valid} icon={<Plus className="h-4 w-4" />}>Add flag</Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function AdminFlagsPage() {
  const flags = useQuery({ queryKey: ["admin", "flags"], queryFn: () => get<FlagRow[]>("/admin/flags") });
  const set = useSetFlag();

  return (
    <div className="space-y-10">
      <div>
        <AdminHeader eyebrow="Governance" icon={<Flag />} title="Feature flags" description="Server-side checks update immediately; open browsers pick up public flags within a few minutes. Every change is recorded in the audit log." />
        <QueryState
          query={flags}
          loading={<SkeletonRows rows={5} />}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState icon={<Flag className="h-5 w-5" />} title="No flags defined" description="Add one below." />}
        >
          {(rows) => {
            const on = rows.filter((f) => f.enabled).length;
            const pub = rows.filter((f) => f.is_public).length;
            return (
              <div className="space-y-4">
                <TileGrid className="grid-cols-3" label="Flag summary">
                  {[
                    { label: "Flags", value: rows.length },
                    { label: "Enabled", value: on },
                    { label: "Public", value: pub },
                  ].map((t) => (
                    <div key={t.label} className="bg-surface px-4 py-3.5">
                      <p className="text-eyebrow text-subtle">{t.label}</p>
                      <p className="tabular mt-1.5 text-xl font-semibold tracking-[-0.02em] text-fg">{formatNumber(t.value)}</p>
                    </div>
                  ))}
                </TileGrid>
                <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface shadow-card">
                  {rows.map((f) => (
                    <li key={f.key} className="flex flex-col gap-3 px-4 py-3.5 transition-colors hover:bg-surface-2/40 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
                      <span
                        aria-hidden
                        className={cn(
                          "hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg border sm:flex",
                          f.enabled ? "border-[color-mix(in_oklab,var(--accent)_35%,var(--border))] bg-accent-soft text-accent-strong" : "border-border bg-surface-2 text-subtle",
                        )}
                      >
                        <ToggleRight className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="break-all font-mono text-sm font-medium text-fg">{f.key}</span>
                          {f.is_public ? (
                            <Badge tone="info" icon={<Globe className="h-3 w-3" aria-hidden />}>Public</Badge>
                          ) : (
                            <Badge tone="outline" icon={<Lock className="h-3 w-3" aria-hidden />}>Server only</Badge>
                          )}
                        </div>
                        {f.description ? <p className="mt-0.5 text-sm text-muted">{f.description}</p> : null}
                        {f.updated_at ? <p className="tabular mt-0.5 text-xs text-subtle" title={formatDateTime(f.updated_at)}>Updated {relativeTime(f.updated_at)}</p> : null}
                      </div>
                      <div className="flex items-center justify-end gap-3">
                        <EditFlag flag={f} />
                        <span className={cn("w-6 text-right text-xs font-medium", f.enabled ? "text-accent-strong" : "text-subtle")} aria-hidden>{f.enabled ? "On" : "Off"}</span>
                        <Switch
                          checked={f.enabled}
                          disabled={set.isPending && set.variables?.key === f.key}
                          onChange={(v) => set.mutate({ key: f.key, enabled: v })}
                          label={<span className="sr-only">{f.enabled ? `Disable ${f.key}` : `Enable ${f.key}`}</span>}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            );
          }}
        </QueryState>
      </div>
      <NewFlag />
    </div>
  );
}
