"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Building2, Crown, Eye, Trophy, UserPlus, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { EMPTY_ORG_PROFILE, OrgProfileFields, isHexColor, orgProfilePayload, type OrgProfileValues } from "@/components/orgs/org-form";
import { ORG_TYPES, OrgLogo } from "@/components/orgs/org-ui";
import { AccentEdge, AccentGlow, OrgTypeIcon } from "@/components/orgs/org-visuals";
import type { OrgDetail, OrgType } from "@/components/orgs/types";
import { StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { FormError } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";
import { InlineNotice, Skeleton } from "@/components/ui/states";
import { ApiError, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";
import { useRequireAuth, useUnsavedChangesWarning } from "@/lib/hooks";
import { qk } from "@/lib/query";

/** Live preview of the directory card built only from what the user has typed (no invented counts). */
function CardPreview({ type, profile }: { type: OrgType | ""; profile: OrgProfileValues }) {
  const name = profile.name.trim() || "Your organization";
  const accent = isHexColor(profile.accent_color) ? profile.accent_color : null;
  return (
    <div className="relative isolate overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-5 shadow-card">
      <AccentEdge color={accent} />
      <AccentGlow color={accent} strength={22} className="-right-20 -top-24 h-56 w-64" />
      <div className="flex items-center gap-3">
        <OrgLogo name={name} accentColor={accent} size={48} />
        <div className="min-w-0">
          <p className={cn("truncate text-[15.5px] font-semibold tracking-[-0.015em]", profile.name.trim() ? "text-fg" : "text-subtle")}>{name}</p>
          <p className="truncate text-xs text-subtle">
            {type ? titleCase(type) : "Type"}
            {profile.city.trim() ? ` · ${profile.city.trim()}` : ""}
          </p>
        </div>
      </div>
      <p className={cn("mt-3 line-clamp-2 min-h-10 text-sm leading-relaxed", profile.tagline.trim() ? "text-muted" : "text-subtle/70")}>
        {profile.tagline.trim() || "Your tagline appears here."}
      </p>
      <div className="mt-4 flex items-center gap-1.5 border-t border-border pt-3.5">
        <StatusBadge status="unverified" />
        <span className="ml-auto inline-flex items-center gap-1 text-xs text-subtle">
          <Users className="h-3.5 w-3.5" aria-hidden /> You, as owner
        </span>
      </div>
    </div>
  );
}

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

  if (me.isPending || !me.data) {
    return (
      <Container size="lg">
        <div className="pb-8 pt-12" role="status" aria-label="Loading">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="mt-4 h-8 w-72" />
          <Skeleton className="mt-3 h-4 w-96 max-w-full" />
          <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <Skeleton className="h-[28rem] w-full rounded-[var(--radius-xl)]" />
            <Skeleton className="hidden h-48 w-full rounded-[var(--radius-lg)] lg:block" />
          </div>
        </div>
      </Container>
    );
  }

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
    <Container size="lg" className="pb-20">
      <PageHeader
        eyebrow="Organizations"
        icon={<Building2 />}
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
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <form onSubmit={submit} noValidate className="min-w-0">
          <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface surface-sheen shadow-card">
            <div className="space-y-8 px-5 py-6 sm:px-7 sm:py-7">
              <fieldset>
                <legend className="flex items-center gap-2.5 text-sm font-medium text-fg">
                  <span className="tabular flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-2 font-mono text-[11px] text-accent-strong" aria-hidden>1</span>
                  <span>Type<span className="ml-0.5 text-danger" aria-hidden>*</span></span>
                </legend>
                <p className="mt-1 pl-[34px] text-xs text-subtle">The type can’t be changed later.</p>
                <div className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {ORG_TYPES.map((t) => {
                    const checked = type === t.value;
                    return (
                      <label
                        key={t.value}
                        className={cn(
                          "relative flex min-h-11 cursor-pointer gap-3 rounded-[var(--radius-md)] border p-3.5 transition-[background-color,border-color,box-shadow] duration-200 focus-within:ring-2 focus-within:ring-[var(--ring)]",
                          checked
                            ? "border-[color-mix(in_oklab,var(--accent)_55%,var(--border))] bg-accent-soft shadow-[inset_0_1px_0_var(--hairline-highlight)]"
                            : "border-border bg-bg-elevated/40 hover:border-border-strong hover:bg-surface-2",
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
                        <span className="min-w-0">
                          <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
                            <OrgTypeIcon type={t.value} className={cn("h-4 w-4", checked ? "text-accent-strong" : "text-subtle")} />
                            {t.label}
                          </span>
                          <span className="mt-1 block text-xs leading-relaxed text-muted">{t.description}</span>
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

              <div className="border-t border-border pt-7">
                <p className="mb-5 flex items-center gap-2.5 text-sm font-medium text-fg">
                  <span className="tabular flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-2 font-mono text-[11px] text-accent-strong" aria-hidden>2</span>
                  Profile
                </p>
                <OrgProfileFields value={profile} onChange={setProfile} errors={fields} />
              </div>

              <FormError message={error && !Object.keys(fields).length ? error.message : null} />
            </div>
            <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-end gap-2 border-t border-border bg-[var(--glass-strong)] px-5 py-3 backdrop-blur-xl sm:px-7">
              {!canSubmit ? <p className="mr-auto hidden text-xs text-subtle sm:block">Choose a type and a name to continue.</p> : null}
              <LinkButton href="/orgs" variant="ghost">Cancel</LinkButton>
              <Button type="submit" loading={busy} disabled={!canSubmit}>Create organization</Button>
            </div>
          </div>
        </form>

        <aside className="min-w-0 lg:sticky lg:top-24 lg:self-start" aria-label="Preview">
          <p className="mb-2.5 flex items-center gap-1.5 text-eyebrow text-subtle">
            <Eye className="h-3.5 w-3.5" aria-hidden /> Directory preview
          </p>
          <CardPreview type={type} profile={profile} />
          <p className="mt-3 text-xs leading-relaxed text-subtle">
            How your organization appears in the directory. New organizations start unverified; verification is granted by the platform team.
          </p>
          <div className="mt-6 rounded-[var(--radius-lg)] border border-border bg-surface/60 p-4">
            <p className="text-eyebrow text-subtle">After you create it</p>
            <ol className="mt-3 space-y-3 text-sm">
              {[
                { icon: <Crown className="h-3.5 w-3.5" aria-hidden />, text: "You become its owner and land in the admin console." },
                { icon: <UserPlus className="h-3.5 w-3.5" aria-hidden />, text: "Invite admins and members, and add departments." },
                { icon: <Trophy className="h-3.5 w-3.5" aria-hidden />, text: "Host competitions with this organization as the host." },
                { icon: <BadgeCheck className="h-3.5 w-3.5" aria-hidden />, text: "Request verification from settings when you’re ready." },
              ].map((s, i) => (
                <li key={i} className="flex items-start gap-2.5 text-muted">
                  <span className="mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 text-accent-strong">{s.icon}</span>
                  <span className="leading-relaxed">{s.text}</span>
                </li>
              ))}
            </ol>
          </div>
        </aside>
      </div>
    </Container>
  );
}
