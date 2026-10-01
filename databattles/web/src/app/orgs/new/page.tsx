"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { EMPTY_ORG_PROFILE, OrgProfileFields, isHexColor, orgProfilePayload, type OrgProfileValues } from "@/components/orgs/org-form";
import { ORG_TYPES } from "@/components/orgs/org-ui";
import type { OrgDetail, OrgType } from "@/components/orgs/types";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardFooter } from "@/components/ui/card";
import { FormError } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { InlineNotice, Spinner } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";
import { qk } from "@/lib/query";

export default function NewOrgPage() {
  const me = useRequireAuth();
  const router = useRouter();
  const qc = useQueryClient();
  const [type, setType] = useState<OrgType | "">("");
  const [profile, setProfile] = useState<OrgProfileValues>(EMPTY_ORG_PROFILE);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const dirty = !done && (Boolean(type) || JSON.stringify(profile) !== JSON.stringify(EMPTY_ORG_PROFILE));
  useUnsavedChangesWarning(dirty);

  if (me.isPending || !me.data) return <Spinner />;

  const fields = error?.fields ?? {};
  const canSubmit = type !== "" && profile.name.trim().length >= 3 && (!profile.accent_color || isHexColor(profile.accent_color));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const org = await post<OrgDetail>("/orgs", { ...orgProfilePayload(profile), type });
      setDone(true);
      await Promise.all([qc.invalidateQueries({ queryKey: qk.me }), qc.invalidateQueries({ queryKey: ["orgs"] })]);
      router.push(`/orgs/${org.slug}/admin`);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "error", "Could not create the organization.", null, null));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Container size="md">
      <PageHeader
        eyebrow="Organizations"
        title="Create an organization"
        description="You become its owner. You can invite admins and members, add departments and host competitions afterwards."
      />
      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email first">
            Creating an organization requires a verified email address. Check your inbox for the verification link.
          </InlineNotice>
        </div>
      ) : null}
      <form onSubmit={submit} noValidate>
        <Card>
          <CardBody className="space-y-8 py-6">
            <fieldset>
              <legend className="text-sm font-medium text-fg">
                Type<span className="ml-0.5 text-danger" aria-hidden>*</span>
              </legend>
              <p className="mt-0.5 text-xs text-subtle">The type can’t be changed later.</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {ORG_TYPES.map((t) => {
                  const checked = type === t.value;
                  return (
                    <label
                      key={t.value}
                      className={cn(
                        "flex cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3 transition-colors focus-within:ring-2 focus-within:ring-[var(--ring)]",
                        checked ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-surface-2",
                      )}
                    >
                      <input
                        type="radio"
                        name="org-type"
                        value={t.value}
                        checked={checked}
                        onChange={() => setType(t.value)}
                        className="mt-1 h-4 w-4 shrink-0 accent-[var(--accent)]"
                      />
                      <span>
                        <span className="block text-sm font-medium text-fg">{t.label}</span>
                        <span className="mt-0.5 block text-xs text-muted">{t.description}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
              {fields.type ? <p role="alert" className="mt-2 text-xs font-medium text-danger">{fields.type}</p> : null}
            </fieldset>

            {type === "university" ? (
              <InlineNotice tone="info" title="Universities start unverified">
                After creating it, request verification from the organization’s admin settings. Once the platform team verifies it and
                registers your institutional email domains, students can verify membership with their university email.
              </InlineNotice>
            ) : null}

            <OrgProfileFields value={profile} onChange={setProfile} errors={fields} />

            <FormError message={error && !Object.keys(fields).length ? error.message : null} />
          </CardBody>
          <CardFooter>
            <LinkButton href="/orgs" variant="ghost">Cancel</LinkButton>
            <Button type="submit" loading={busy} disabled={!canSubmit}>Create organization</Button>
          </CardFooter>
        </Card>
      </form>
    </Container>
  );
}
