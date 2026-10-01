"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, ExternalLink, Globe, Lock, Trophy, UserRound, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Switch } from "@/components/ui/form";
import { ErrorState, InlineNotice } from "@/components/ui/states";
import { errorMessage, get, patch } from "@/lib/api";
import { useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import { SaveStatus, SettingsPageHeading, SettingsSection, SettingsSkeleton } from "../_components/settings-ui";

type Privacy = Record<string, boolean>;
const PRIVACY_KEY = ["me", "privacy"] as const;

const GROUPS: { title: string; description: string; icon: LucideIcon; items: { key: string; label: string; description: string }[] }[] = [
  {
    title: "Profile details",
    icon: UserRound,
    description: "Self-declared and verified details on your public profile.",
    items: [
      {
        key: "show_university",
        label: "Show my university",
        description: "Verified memberships display a Verified badge; otherwise the university is labeled self-declared.",
      },
      { key: "show_department", label: "Show my department", description: "Your department within your university." },
      { key: "show_graduation_year", label: "Show my graduation year", description: "Off by default. Some people prefer not to reveal their age range." },
      { key: "show_skills", label: "Show my skills", description: "The skill tags from your profile settings." },
    ],
  },
  {
    title: "Activity & achievements",
    icon: Award,
    description: "What others can see about what you've done on the platform.",
    items: [
      {
        key: "show_activity",
        label: "Show activity calendar and timeline",
        description: "Aggregate daily counts of submissions, posts, lessons and merged PRs — never the content itself.",
      },
      {
        key: "show_contributions",
        label: "Show open-source contributions",
        description: "Merged pull requests attributed through your linked GitHub account, and your GitHub username.",
      },
      {
        key: "show_certificates",
        label: "Show certificates",
        description: "Certificates stay publicly verifiable by their ID even when hidden from your profile.",
      },
      {
        key: "show_badges",
        label: "Show badges",
        description: "When off, badge verification pages also stop showing your name.",
      },
    ],
  },
  {
    title: "Leaderboards",
    icon: Trophy,
    description: "How you appear next to your scores.",
    items: [
      {
        key: "show_university_on_leaderboards",
        label: "Show my university on leaderboards",
        description: "Adds your university name next to your entry on competition leaderboards.",
      },
    ],
  },
  {
    title: "Search engines",
    icon: Globe,
    description: "Discoverability outside DataBattles.",
    items: [
      {
        key: "indexable",
        label: "Allow search engines to index my profile",
        description: "Off by default. When on, your public profile may appear in Google and other search results.",
      },
    ],
  },
];

export default function PrivacySettingsPage() {
  const me = useRequireAuth();
  const qc = useQueryClient();
  const privacy = useQuery({ queryKey: PRIVACY_KEY, queryFn: () => get<Privacy>("/me/privacy"), enabled: Boolean(me.data) });

  const update = useMutation({
    mutationFn: (change: Privacy) => patch<Privacy>("/me/privacy", change),
    onMutate: async (change) => {
      await qc.cancelQueries({ queryKey: PRIVACY_KEY });
      const prev = qc.getQueryData<Privacy>(PRIVACY_KEY);
      qc.setQueryData<Privacy>(PRIVACY_KEY, (p) => ({ ...(p ?? {}), ...change }));
      return { prev };
    },
    onError: (e, _change, ctx) => {
      if (ctx?.prev) qc.setQueryData(PRIVACY_KEY, ctx.prev);
      toast.error(errorMessage(e));
    },
    onSuccess: (data) => {
      qc.setQueryData(PRIVACY_KEY, data);
      toast.success("Privacy setting saved");
      qc.invalidateQueries({ queryKey: qk.me });
      if (me.data) qc.invalidateQueries({ queryKey: qk.profile(me.data.handle) });
    },
  });

  const description = "Decide what others can see on your public profile, on leaderboards and in search engines.";
  if (privacy.isPending) return <SettingsSkeleton sections={3} rows={3} />;
  if (privacy.isError)
    return (
      <div className="space-y-8">
        <SettingsPageHeading icon={<Lock />} title="Privacy" description={description} />
        <ErrorState error={privacy.error} onRetry={() => privacy.refetch()} />
      </div>
    );
  const values = privacy.data;

  return (
    <div className="space-y-10">
      <SettingsPageHeading icon={<Lock />} title="Privacy" description={description} actions={<SaveStatus state={update.status} idleLabel="All changes saved" />} />
      <InlineNotice
        tone="info"
        action={
          me.data ? (
            <Link href={`/u/${me.data.handle}`} className="inline-flex items-center gap-1 text-sm font-medium text-accent-strong hover:underline">
              Preview public profile <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : null
        }
      >
        Changes save instantly. Your email address is never shown publicly. Competition results stay on leaderboards regardless of these settings.
      </InlineNotice>
      {GROUPS.map((g) => {
        const on = g.items.filter((item) => Boolean(values[item.key])).length;
        return (
          <SettingsSection
            key={g.title}
            title={
              <span className="inline-flex items-center gap-2">
                <g.icon className="h-4 w-4 text-subtle" aria-hidden />
                {g.title}
              </span>
            }
            description={g.description}
            action={
              g.items.length > 1 ? (
                <span className="tabular font-mono text-[11px] uppercase tracking-[0.12em] text-subtle">
                  {on}/{g.items.length} visible
                </span>
              ) : undefined
            }
            flush
          >
            {g.items.map((item) => (
              <div key={item.key} className="px-5 py-4 transition-colors duration-150 hover:bg-surface-2/40 sm:px-6">
                <Switch
                  checked={Boolean(values[item.key])}
                  disabled={update.isPending && update.variables && item.key in update.variables}
                  onChange={(v) => update.mutate({ [item.key]: v })}
                  label={item.label}
                  description={item.description}
                />
              </div>
            ))}
          </SettingsSection>
        );
      })}
      <p className="text-xs text-subtle">
        Individual certificates, badges and results can also be hidden from{" "}
        <Link href="/settings/achievements" className="text-accent-strong hover:underline">achievement settings</Link>.
      </p>
    </div>
  );
}
