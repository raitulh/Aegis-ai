"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MailX } from "lucide-react";
import { toast } from "sonner";

import { MiniSwitch } from "@/components/profile/mini-switch";
import { NOTIFICATION_KINDS } from "@/components/profile/notification-kinds";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { errorMessage, get, put } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { useRequireAuth } from "@/lib/hooks";

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

  if (prefs.isPending) return <SkeletonRows rows={10} />;
  if (prefs.isError) return <ErrorState error={prefs.error} onRetry={() => prefs.refetch()} />;

  const data = prefs.data;
  const kinds = Object.keys(data);
  const groups = new Map<string, string[]>();
  for (const k of kinds) {
    const g = NOTIFICATION_KINDS[k]?.group ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), k]);
  }
  const emailOn = kinds.filter((k) => data[k].email).length;

  const toggle = (kind: string, channel: "in_app" | "email", value: boolean) => save.mutate([{ kind, ...data[kind], [channel]: value }]);

  return (
    <Card>
      <CardHeader
        title="Notification preferences"
        description="Choose where each kind of update reaches you. Every email also includes a one-click unsubscribe link."
        action={
          <ConfirmDialog
            trigger={<Button variant="secondary" size="sm" icon={<MailX className="h-4 w-4" />} disabled={!emailOn}>Turn off all email</Button>}
            title="Turn off all notification emails?"
            description="In-app notifications stay as they are. Account security emails (password resets, sign-in changes) are always sent."
            confirmLabel="Turn off email"
            tone="primary"
            onConfirm={() => save.mutateAsync(kinds.map((k) => ({ kind: k, in_app: data[k].in_app, email: false }))).catch(() => undefined)}
          />
        }
      />
      <CardBody>
        <Table>
          <THead>
            <tr>
              <TH>Type</TH>
              <TH className="w-24 text-center">In-app</TH>
              <TH className="w-24 text-center">Email</TH>
            </tr>
          </THead>
          <TBody>
            {[...groups.entries()].map(([group, ks]) => [
              <tr key={`g-${group}`} className="bg-surface-2/50">
                <th scope="colgroup" colSpan={3} className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-subtle">{group}</th>
              </tr>,
              ...ks.map((k) => {
                const meta = NOTIFICATION_KINDS[k] ?? { label: titleCase(k), description: "" };
                return (
                  <TR key={k}>
                    <TD>
                      <p className="font-medium text-fg">{meta.label}</p>
                      {meta.description ? <p className="text-xs text-muted">{meta.description}</p> : null}
                    </TD>
                    <TD className="text-center">
                      <MiniSwitch checked={data[k].in_app} label={`${meta.label}: in-app`} onChange={(v) => toggle(k, "in_app", v)} />
                    </TD>
                    <TD className="text-center">
                      <MiniSwitch checked={data[k].email} label={`${meta.label}: email`} onChange={(v) => toggle(k, "email", v)} />
                    </TD>
                  </TR>
                );
              }),
            ])}
          </TBody>
        </Table>
      </CardBody>
    </Card>
  );
}
