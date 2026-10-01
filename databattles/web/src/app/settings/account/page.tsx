"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, KeyRound, LogOut, Mail, Monitor, ShieldCheck, Smartphone, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input } from "@/components/ui/form";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError, del, errorMessage, get, post, type FieldErrors } from "@/lib/api";
import { formatDate, formatDateTime, relativeTime } from "@/lib/format";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import type { Message, Schemas } from "@/lib/types";
import { DangerZone, SettingsPageHeading, SettingsRow, SettingsSection } from "../_components/settings-ui";

type Session = Schemas["SessionOut"];
const SESSIONS_KEY = ["auth", "sessions"] as const;

/** Best-effort, human-readable device label from a user-agent string. */
function describeAgent(ua: string | null): { label: string; mobile: boolean } {
  if (!ua) return { label: "Unknown device", mobile: false };
  const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /curl|python|httpx|node/i.test(ua) ? "API client" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /iPhone|iPad|iPod/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X|Macintosh/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : null;
  return { label: os ? `${browser} on ${os}` : browser, mobile: /Mobi|Android|iPhone|iPad/.test(ua) };
}

function PasswordSection() {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const qc = useQueryClient();
  const change = useApiMutation((body: { current_password: string; new_password: string }) => post<Message>("/auth/change-password", body), {
    success: (m) => m.message,
    onSuccess: () => {
      setForm({ current_password: "", new_password: "", confirm: "" });
      setErrors({});
      qc.invalidateQueries({ queryKey: SESSIONS_KEY });
    },
    onError: (e) => {
      setErrors(e.fields);
      if (e.code === "reauth_failed") setErrors({ current_password: e.message });
    },
  });
  return (
    <SettingsSection title="Password" description="Changing your password signs out every other device.">
      <form
        className="grid max-w-md gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.new_password !== form.confirm) return setErrors({ confirm: "Passwords don't match." });
          change.mutate({ current_password: form.current_password, new_password: form.new_password });
        }}
      >
        <Field label="Current password" required error={errors.current_password}>
          {(p) => <Input {...p} type="password" autoComplete="current-password" value={form.current_password} onChange={(e) => setForm({ ...form, current_password: e.target.value })} />}
        </Field>
        <Field label="New password" required error={errors.new_password} hint="At least 10 characters. Avoid passwords you use elsewhere.">
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.new_password} onChange={(e) => setForm({ ...form, new_password: e.target.value })} />}
        </Field>
        <Field label="Confirm new password" required error={errors.confirm}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} />}
        </Field>
        <div className="flex flex-col items-start gap-3 border-t border-border pt-5 sm:flex-row sm:flex-wrap sm:items-center">
          <Button type="submit" icon={<KeyRound className="h-4 w-4" />} loading={change.isPending} disabled={!form.current_password || !form.new_password || !form.confirm}>Change password</Button>
          <Link href="/forgot-password" className="text-sm text-muted underline-offset-4 hover:text-fg hover:underline">Signed up with GitHub or Google? Set a password via reset</Link>
        </div>
      </form>
    </SettingsSection>
  );
}

function EmailSection({ email, verified }: { email: string; verified: boolean }) {
  const [form, setForm] = useState({ current_password: "", new_email: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [sentTo, setSentTo] = useState<string | null>(null);
  const change = useApiMutation((body: { current_password: string; new_email: string }) => post<Message>("/auth/change-email", body), {
    success: (m) => m.message,
    onSuccess: (_, vars) => {
      setSentTo(vars.new_email);
      setForm({ current_password: "", new_email: "" });
      setErrors({});
    },
    onError: (e) => {
      setErrors(e.fields);
      if (e.code === "reauth_failed") setErrors({ current_password: e.message });
    },
  });
  return (
    <SettingsSection title="Email address" description="Used to sign in and for security notices. Never shown publicly." flush>
      <SettingsRow
        icon={<Mail />}
        title={<span className="break-all">{email}</span>}
        description="Current email"
        control={verified ? <Badge tone="success">Verified</Badge> : <Badge tone="warning">Not verified</Badge>}
      />
      <div className="space-y-5 p-5 sm:p-6">
        {sentTo ? (
          <p role="status" className="flex items-start gap-2 rounded-[var(--radius-md)] border border-info/25 bg-info-soft px-3 py-2.5 text-sm text-info">
            <Mail className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>We sent a confirmation link to <span className="font-medium">{sentTo}</span>. Your email changes once you open it.</span>
          </p>
        ) : null}
        <p className="text-eyebrow text-subtle">Change email</p>
        <form
          className="-mt-2 grid max-w-md gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            change.mutate(form);
          }}
        >
          <Field label="New email" required error={errors.new_email}>
            {(p) => <Input {...p} type="email" autoComplete="email" value={form.new_email} onChange={(e) => setForm({ ...form, new_email: e.target.value })} />}
          </Field>
          <Field label="Current password" required error={errors.current_password}>
            {(p) => <Input {...p} type="password" autoComplete="current-password" value={form.current_password} onChange={(e) => setForm({ ...form, current_password: e.target.value })} />}
          </Field>
          <div className="border-t border-border pt-5">
            <Button type="submit" loading={change.isPending} disabled={!form.new_email || !form.current_password}>Send confirmation link</Button>
          </div>
        </form>
      </div>
    </SettingsSection>
  );
}

function SessionsSection() {
  const sessions = useQuery({ queryKey: SESSIONS_KEY, queryFn: () => get<Session[]>("/auth/sessions") });
  const revokeOthers = useApiMutation(() => post<Message>("/auth/sessions/revoke-others"), { success: (m) => m.message, invalidate: [SESSIONS_KEY] });
  const qc = useQueryClient();
  const others = sessions.data?.filter((s) => !s.current).length ?? 0;
  return (
    <SettingsSection
      title="Active sessions"
      description="Devices currently signed in to your account. Revoke any you don't recognize."
      action={sessions.data ? <Badge tone="neutral"><span className="tabular">{sessions.data.length}</span> {sessions.data.length === 1 ? "session" : "sessions"}</Badge> : undefined}
      flush
    >
      {sessions.isPending ? (
        <div className="p-5 sm:p-6"><SkeletonRows rows={3} /></div>
      ) : sessions.isError ? (
        <div className="p-5 sm:p-6"><ErrorState error={sessions.error} onRetry={() => sessions.refetch()} /></div>
      ) : (
        <div>
          <ul className="divide-y divide-border">
            {sessions.data.map((s) => {
              const d = describeAgent(s.user_agent);
              const Icon = d.mobile ? Smartphone : Monitor;
              return (
                <li key={s.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:px-6">
                  <span
                    aria-hidden
                    className={
                      s.current
                        ? "hidden h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-success/30 bg-success-soft text-success sm:flex"
                        : "hidden h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-border bg-surface-2 text-muted sm:flex"
                    }
                  >
                    <Icon className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-fg">
                      {d.label}
                      {s.current ? <Badge tone="success">This device</Badge> : null}
                    </p>
                    <p className="tabular mt-0.5 text-xs text-muted">
                      Signed in {formatDate(s.created_at)} · active <span title={formatDateTime(s.last_seen_at)}>{relativeTime(s.last_seen_at)}</span> · expires{" "}
                      <span title={formatDateTime(s.expires_at)}>{relativeTime(s.expires_at)}</span>
                    </p>
                    {s.user_agent ? <p className="mt-1 truncate font-mono text-[11px] text-subtle" title={s.user_agent}>{s.user_agent}</p> : null}
                  </div>
                  {!s.current ? (
                    <ConfirmDialog
                      trigger={<Button variant="outline" size="sm">Revoke</Button>}
                      title="Revoke this session?"
                      description={`${d.label} will be signed out immediately.`}
                      confirmLabel="Revoke session"
                      onConfirm={async () => {
                        try {
                          const m = await del<Message>(`/auth/sessions/${s.id}`);
                          toast.success(m.message);
                          await qc.invalidateQueries({ queryKey: SESSIONS_KEY });
                        } catch (e) {
                          toast.error(errorMessage(e));
                        }
                      }}
                    />
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div className="flex flex-col gap-3 border-t border-border bg-bg-elevated/50 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <p className="text-xs text-subtle">{others ? "Signing out keeps you signed in on this device." : "This is the only device signed in."}</p>
            <ConfirmDialog
              trigger={
                <Button variant="secondary" size="sm" icon={<LogOut className="h-4 w-4" />} disabled={!others} loading={revokeOthers.isPending}>
                  Sign out other devices{others ? ` (${others})` : ""}
                </Button>
              }
              title="Sign out all other devices?"
              description="Every session except this one will be revoked. You'll stay signed in here."
              confirmLabel="Sign out others"
              onConfirm={() => revokeOthers.mutateAsync(undefined).catch(() => undefined)}
            />
          </div>
        </div>
      )}
    </SettingsSection>
  );
}

function ExportSection({ handle }: { handle: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <SettingsSection title="Your data" flush>
      <SettingsRow
        icon={<Download />}
        title="Download your data"
        description={<>A JSON copy of your profile, privacy settings, results, certificates and badges. <span className="text-subtle">Limited to 5 exports per hour.</span></>}
        className="flex-col items-start sm:flex-row sm:items-center"
        control={
          <Button
            variant="secondary"
            icon={<Download className="h-4 w-4" />}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const data = await get<unknown>("/me/export");
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `databattles-${handle}-${new Date().toISOString().slice(0, 10)}.json`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
                toast.success("Export downloaded");
              } catch (e) {
                toast.error(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Download JSON
          </Button>
        }
      />
    </SettingsSection>
  );
}

function DeleteSection() {
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setPassword("");
    setConfirm("");
    setError(null);
  };

  return (
    <DangerZone>
      <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-start sm:p-6">
        <span aria-hidden className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-danger/30 bg-danger-soft text-danger sm:flex">
          <Trash2 className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1 space-y-3 text-sm text-muted">
          <p className="font-medium text-fg">Delete your account</p>
          <ul className="list-disc space-y-1.5 pl-5 marker:text-danger/60">
            <li>Your profile, bio, avatar, skills and linked GitHub account are removed and you are signed out everywhere.</li>
            <li>
              Historical competition results and issued certificates are kept for leaderboard integrity and remain attributed to{" "}
              <span className="font-medium text-fg">“Deleted user”</span>. Certificate verification pages keep working.
            </li>
            <li>This cannot be undone. Download your data first if you want a copy.</li>
          </ul>
          <Dialog
            open={open}
            onOpenChange={(o) => {
              setOpen(o);
              if (!o) reset();
            }}
            trigger={<Button variant="danger" className="mt-2" icon={<Trash2 className="h-4 w-4" />}>Delete account…</Button>}
            title="Delete your account permanently?"
            description="Results and certificates stay attributed to “Deleted user”. Everything else about you is removed."
            size="sm"
            footer={
              <>
                <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
                <Button
                  variant="danger"
                  loading={busy}
                  disabled={confirm !== "DELETE"}
                  onClick={async () => {
                    setBusy(true);
                    setError(null);
                    try {
                      const m = await post<Message>("/me/delete", { password: password || null, confirm: "DELETE" });
                      qc.clear();
                      toast.success(m.message);
                      router.replace("/");
                      router.refresh();
                    } catch (e) {
                      setError(e instanceof ApiError && e.code === "reauth_failed" ? "Your current password is incorrect." : errorMessage(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Delete my account
                </Button>
              </>
            }
          >
            <div className="space-y-4">
              <Field label="Current password" hint="Leave blank only if you signed up with GitHub or Google and never set a password.">
                {(p) => <Input {...p} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
              </Field>
              <Field label={<>Type <span className="font-mono">DELETE</span> to confirm</>}>
                {(p) => <Input {...p} value={confirm} autoComplete="off" spellCheck={false} onChange={(e) => setConfirm(e.target.value)} />}
              </Field>
              <FormError message={error} />
            </div>
          </Dialog>
        </div>
      </div>
    </DangerZone>
  );
}

export default function AccountSettingsPage() {
  const me = useRequireAuth();
  if (!me.data) return null;
  return (
    <div className="space-y-10">
      <SettingsPageHeading
        icon={<ShieldCheck />}
        title="Account & security"
        description="Your sign-in email, password, signed-in devices and a copy of your data."
      />
      <EmailSection email={me.data.email} verified={me.data.email_verified} />
      <PasswordSection />
      <SessionsSection />
      <ExportSection handle={me.data.handle} />
      <DeleteSection />
    </div>
  );
}
