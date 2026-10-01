"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Clock, ShieldQuestion } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { useAdminOrg } from "@/components/orgs/org-admin-context";
import { OrgProfileFields, isHexColor, orgProfilePayload, type OrgProfileValues } from "@/components/orgs/org-form";
import { OrgLogo, OrgVerificationBadge } from "@/components/orgs/org-ui";
import type { OrgDetail } from "@/components/orgs/types";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Field, FormError, Switch, Textarea } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { InlineNotice } from "@/components/ui/states";
import { ApiError, errorMessage, patch, post, upload } from "@/lib/api";
import { useApiMutation, useConfig, useUnsavedChangesWarning } from "@/lib/hooks";


function toValues(o: OrgDetail): OrgProfileValues & { allow_membership_requests: boolean } {
  return {
    name: o.name,
    tagline: o.tagline ?? "",
    description_md: o.description_md ?? "",
    website_url: o.website_url ?? "",
    country: o.country ?? "",
    city: o.city ?? "",
    accent_color: o.accent_color ?? "",
    allow_membership_requests: o.allow_membership_requests,
  };
}

function ProfileForm({ org }: { org: OrgDetail }) {
  const initial = useMemo(() => toValues(org), [org]);
  const [base, setBase] = useState(initial);
  const [values, setValues] = useState(initial);
  const [error, setError] = useState<ApiError | null>(null);
  // When the org is refetched (e.g. after a logo upload), refresh the form only if the user hasn't edited it.
  useEffect(() => {
    setValues((v) => (JSON.stringify(v) === JSON.stringify(base) ? initial : v));
    setBase(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);
  const dirty = JSON.stringify(values) !== JSON.stringify(initial);
  useUnsavedChangesWarning(dirty);

  const save = useApiMutation((body: Record<string, unknown>) => patch<OrgDetail>(`/orgs/${org.slug}`, body), {
    success: "Settings saved",
    invalidate: [["orgs"]],
    onSuccess: (saved) => {
      setError(null);
      setValues(toValues(saved));
    },
    onError: setError,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    save.mutate({ ...orgProfilePayload(values), allow_membership_requests: values.allow_membership_requests });
  }

  const fields = error?.fields ?? {};
  const valid = values.name.trim().length >= 3 && (!values.accent_color || isHexColor(values.accent_color));
  return (
    <form onSubmit={submit} noValidate>
      <Card>
        <CardHeader title="Profile" description="Shown on your public organization page and in search." />
        <CardBody className="space-y-6">
          <OrgProfileFields value={values} onChange={(v) => setValues({ ...values, ...v })} errors={fields} />
          <div className="border-t border-border pt-5">
            <Switch
              checked={values.allow_membership_requests}
              onChange={(v) => setValues({ ...values, allow_membership_requests: v })}
              label="Accept membership requests"
              description="People can ask to join; owners and admins approve or decline each request. Turn off to make membership invite-only (institutional email verification still works when available)."
            />
          </div>
          <FormError message={error && !Object.keys(fields).length ? error.message : null} />
        </CardBody>
        <CardFooter>
          {dirty ? <span className="mr-auto text-xs text-warning">Unsaved changes</span> : null}
          <Button variant="ghost" disabled={!dirty || save.isPending} onClick={() => { setValues(initial); setError(null); }}>Discard</Button>
          <Button type="submit" loading={save.isPending} disabled={!dirty || !valid}>Save changes</Button>
        </CardFooter>
      </Card>
    </form>
  );
}

function LogoCard({ org }: { org: OrgDetail }) {
  const qc = useQueryClient();
  const config = useConfig().data;
  const maxMb = config?.limits.image_mb ?? 5;
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File) {
    setError(null);
    setProgress(0);
    const form = new FormData();
    form.append("file", file);
    try {
      await upload<OrgDetail>(`/orgs/${org.slug}/logo`, form, { onProgress: setProgress });
      toast.success("Logo updated");
      await qc.invalidateQueries({ queryKey: ["orgs"] });
    } catch (e) {
      setError(errorMessage(e, "Upload failed."));
    } finally {
      setProgress(null);
    }
  }

  return (
    <Card>
      <CardHeader title="Logo" description="Square images work best. It’s cropped to a square and resized to 512 px." />
      <CardBody className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <OrgLogo name={org.name} logoUrl={org.logo_url} accentColor={org.accent_color} size={88} className="rounded-xl" />
        <div className="flex-1">
          <FileDrop accept=".png,.jpg,.jpeg,.webp" maxBytes={maxMb * 1024 * 1024} onFile={onFile} disabled={progress !== null} progress={progress} hint={`PNG, JPEG or WebP · up to ${maxMb} MB`} />
          {error ? <p role="alert" className="mt-2 text-xs font-medium text-danger">{error}</p> : null}
        </div>
      </CardBody>
    </Card>
  );
}

function VerificationCard({ org }: { org: OrgDetail }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const request = useApiMutation((r: string) => post<{ message: string }>(`/orgs/${org.slug}/verification-request`, { reason: r }), {
    success: (r) => r.message,
    invalidate: [["orgs"]],
    onSuccess: () => {
      setReason("");
      setError(null);
    },
    onError: setError,
  });
  const status = org.verification_status;

  return (
    <Card>
      <CardHeader title="Verification" action={<OrgVerificationBadge status={status} />} />
      <CardBody className="space-y-4 text-sm">
        {status === "verified" ? (
          <InlineNotice tone="success" title="Verified by the platform team">
            Your organization shows a verified badge.
          </InlineNotice>
        ) : status === "pending" ? (
          <InlineNotice tone="info" title="Verification requested">
            <span className="inline-flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" aria-hidden />The platform team will review your request. You can add more details below.</span>
          </InlineNotice>
        ) : (
          <p className="flex items-start gap-2 text-muted">
            <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Verification confirms your organization is who it says it is. For universities, it’s required before members can verify with their
            institutional email.
          </p>
        )}

        <div>
          <p className="font-medium text-fg">Institutional email domains</p>
          {org.email_domains.length ? (
            <ul className="mt-2 flex flex-wrap gap-1.5 font-mono text-xs">
              {org.email_domains.map((d) => <li key={d} className="rounded bg-surface-2 px-2 py-1 text-muted">@{d}</li>)}
            </ul>
          ) : (
            <p className="mt-1 text-muted">None registered{status !== "verified" ? " (domains take effect only after verification)" : ""}.</p>
          )}
          <p className="mt-2 text-xs text-subtle">
            Email domains are managed by platform administrators to prevent impersonation. Include the domains you need in your verification request.
          </p>
        </div>

        {status !== "verified" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              request.mutate(reason.trim());
            }}
            className="space-y-3 border-t border-border pt-4"
            noValidate
          >
            <Field
              label={status === "pending" ? "Additional details" : "Verification request"}
              required
              error={error?.fields.reason}
              hint="Explain who you are and how the team can confirm it — e.g. an official web page listing this group, a contact at the institution, and the email domains your members use."
            >
              {(p) => <Textarea {...p} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} maxLength={1000} rows={4} />}
            </Field>
            <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
            <Button type="submit" variant="secondary" loading={request.isPending} disabled={reason.trim().length < 3} icon={<BadgeCheck className="h-4 w-4" />}>
              {status === "pending" ? "Update request" : "Request verification"}
            </Button>
          </form>
        ) : null}
      </CardBody>
    </Card>
  );
}

export default function OrgSettingsPage() {
  const org = useAdminOrg();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Settings</h1>
        <p className="mt-1 text-sm text-muted">The organization type and address (slug) can’t be changed.</p>
      </div>
      <ProfileForm org={org} />
      <LogoCard org={org} />
      <VerificationCard org={org} />
    </div>
  );
}
