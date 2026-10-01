"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Mail, MailX, MonitorSmartphone } from "lucide-react";
import { toast } from "sonner";

import { MiniSwitch } from "@/components/profile/mini-switch";
import { NOTIFICATION_KINDS } from "@/components/profile/notification-kinds";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/states";
import { errorMessage, get, put } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";
import { HitArea, SaveStatus, SettingsPageHeading, SettingsSection, SettingsSkeleton } from "../_components/settings-ui";

type Prefs = Record<string, { in_app: boolean; email: boolean }>;
const PREFS_KEY = ["notifications", "preferences"] as const;

export default function NotificationSettingsPage() {
  const me = useRequireAuth();
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: PREFS_KEY, queryFn: () => get<Prefs>("/notifications/preferences"), enabled: Boolean(me.data) });

  const save = useMutation({
    mutationFn: (rows: { kind: string; in_app: boolean; email: boolean }[]) => put<Prefs>("/notifications/preferences", { preferences: rows }),
    onMutate: async (rows) => {
      await qc.cancelQueries({ queryKey: PREFS_KEY });
      const prev = qc.getQueryData<Prefs>(PREFS_KEY);
      qc.setQueryData<Prefs>(PREFS_KEY, (p) => {
        const next = { ...(p ?? {}) };
        for (const r of rows) next[r.kind] = { in_app: r.in_app, email: r.email };
        return next;
      });
      return { prev };
    },
    onError: (e, _rows, ctx) => {
      if (ctx?.prev) qc.setQueryData(PREFS_KEY, ctx.prev);
      toast.error(errorMessage(e));
    },
    onSuccess: (data, rows) => {
      qc.setQueryData(PREFS_KEY, data);
      toast.success(rows.length > 1 ? "Notification preferences saved" : "Preference saved");
    },
  });

  const description = "Choose where each kind of update reaches you. Every email also includes a one-click unsubscribe link.";
  if (prefs.isPending) return <SettingsSkeleton sections={3} rows={3} />;
  if (prefs.isError)
    return (
      <div className="space-y-8">
        <SettingsPageHeading icon={<Bell />} title="Notifications" description={description} />
        <ErrorState error={prefs.error} onRetry={() => prefs.refetch()} />
      </div>
    );

  const data = prefs.data;
  const kinds = Object.keys(data);
  const groups = new Map<string, string[]>();
  for (const k of kinds) {
    const g = NOTIFICATION_KINDS[k]?.group ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), k]);
  }
  const emailOn = kinds.filter((k) => data[k].email).length;
  const inAppOn = kinds.filter((k) => data[k].in_app).length;

  const toggle = (kind: string, channel: "in_app" | "email", value: boolean) => save.mutate([{ kind, ...data[kind], [channel]: value }]);

  return (
    <div className="space-y-10">
      <SettingsPageHeading
        icon={<Bell />}
        title="Notifications"
        description={description}
        actions={
          <>
            <SaveStatus state={save.status} />
            <ConfirmDialog
              trigger={<Button variant="secondary" size="sm" icon={<MailX className="h-4 w-4" />} disabled={!emailOn}>Turn off all email</Button>}
              title="Turn off all notification emails?"
              description="In-app notifications stay as they are. Account security emails (password resets, sign-in changes) are always sent."
              confirmLabel="Turn off email"
              tone="primary"
              onConfirm={() => save.mutateAsync(kinds.map((k) => ({ kind: k, in_app: data[k].in_app, email: false }))).catch(() => undefined)}
            />
          </>
        }
      />

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border shadow-card">
        {[
          { label: "In-app", icon: <MonitorSmartphone className="h-3.5 w-3.5" aria-hidden />, on: inAppOn },
          { label: "Email", icon: <Mail className="h-3.5 w-3.5" aria-hidden />, on: emailOn },
        ].map((c) => (
          <div key={c.label} className="bg-surface px-5 py-4">
            <dt className="flex items-center gap-1.5 text-eyebrow text-subtle">{c.icon}{c.label}</dt>
            <dd className="mt-2 flex items-baseline gap-1.5">
              <span className="tabular text-2xl font-semibold tracking-[-0.03em] text-fg">{c.on}</span>
              <span className="tabular text-sm text-muted">of {kinds.length} types on</span>
            </dd>
            <div aria-hidden className="mt-3 h-1 overflow-hidden rounded-full bg-surface-3">
              <div className="h-full rounded-full bg-brand transition-[width] duration-500 ease-out-expo" style={{ width: `${kinds.length ? (c.on / kinds.length) * 100 : 0}%` }} />
            </div>
          </div>
        ))}
      </dl>

      {[...groups.entries()].map(([group, ks]) => (
        <SettingsSection key={group} title={group} flush>
          <div aria-hidden className="flex items-center gap-2 bg-bg-elevated/60 px-5 py-2 font-mono text-[10px] uppercase tracking-[0.06em] text-subtle sm:gap-3 sm:px-6 sm:text-[10.5px] sm:tracking-[0.12em]">
            <span className="flex-1">Type</span>
            <span className="w-12 whitespace-nowrap text-center sm:w-16">In-app</span>
            <span className="w-12 whitespace-nowrap text-center sm:w-16">Email</span>
          </div>
          {ks.map((k) => {
            const meta = NOTIFICATION_KINDS[k] ?? { label: titleCase(k), description: "" };
            return (
              <div key={k} className="flex items-center gap-2 px-5 py-3.5 sm:gap-3 transition-colors duration-150 hover:bg-surface-2/40 sm:px-6">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-fg">{meta.label}</p>
                  {meta.description ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{meta.description}</p> : null}
                </div>
                <HitArea className="w-12 sm:w-16">
                  <MiniSwitch checked={data[k].in_app} label={`${meta.label}: in-app`} onChange={(v) => toggle(k, "in_app", v)} />
                </HitArea>
                <HitArea className="w-12 sm:w-16">
                  <MiniSwitch checked={data[k].email} label={`${meta.label}: email`} onChange={(v) => toggle(k, "email", v)} />
                </HitArea>
              </div>
            );
          })}
        </SettingsSection>
      ))}
    </div>
  );
}
