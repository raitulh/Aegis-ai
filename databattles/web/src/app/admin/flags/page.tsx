"use client";

import { useQuery } from "@tanstack/react-query";
import { Flag, Globe, Lock, Pencil, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { AdminHeader } from "@/components/admin/admin-ui";
import type { FlagRow } from "@/components/admin/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Switch } from "@/components/ui/form";
import { EmptyState, QueryState, SkeletonRows } from "@/components/ui/states";
import { get, put } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
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
      <CardHeader title="Add a flag" description="Creates the flag if it doesn’t exist. Code decides what each flag does." />
      <form onSubmit={submit} noValidate>
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <Field label="Key" required hint="Lowercase letters, digits, dots and underscores." error={key && !valid ? "Use a–z, 0–9, “.” and “_” only (max 64)." : undefined}>
            {(p) => <Input {...p} value={key} onChange={(e) => setKey(e.target.value.toLowerCase())} maxLength={64} className="font-mono" placeholder="e.g. discussions.enabled" />}
          </Field>
          <Field label="Description">
            {(p) => <Input {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />}
          </Field>
          <Switch checked={enabled} onChange={setEnabled} label="Enabled" />
          <Switch checked={isPublic} onChange={setIsPublic} label="Public" description="Readable by the browser via /meta/config." />
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
    <div className="space-y-6">
      <AdminHeader title="Feature flags" description="Toggles take effect immediately and are recorded in the audit log." />
      <QueryState
        query={flags}
        loading={<SkeletonRows rows={5} />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={<Flag className="h-5 w-5" />} title="No flags defined" description="Add one below." />}
      >
        {(rows) => (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {rows.map((f) => (
              <li key={f.key} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium text-fg">{f.key}</span>
                    {f.is_public ? (
                      <Badge tone="info" icon={<Globe className="h-3 w-3" aria-hidden />}>Public</Badge>
                    ) : (
                      <Badge tone="outline" icon={<Lock className="h-3 w-3" aria-hidden />}>Server only</Badge>
                    )}
                  </div>
                  {f.description ? <p className="mt-0.5 text-sm text-muted">{f.description}</p> : null}
                  {f.updated_at ? <p className="mt-0.5 text-xs text-subtle" title={formatDateTime(f.updated_at)}>Updated {relativeTime(f.updated_at)}</p> : null}
                </div>
                <div className="flex items-center gap-3">
                  <EditFlag flag={f} />
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
        )}
      </QueryState>
      <NewFlag />
    </div>
  );
}
