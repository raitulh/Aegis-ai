"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleX, EyeOff } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { BadgeIcon } from "@/components/profile/badge-icon";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { CopyButton, KeyValue } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { ApiError, get } from "@/lib/api";
import { formatDate, formatDateTime, titleCase } from "@/lib/format";
import type { Schemas } from "@/lib/types";

type BadgeVerify = Schemas["BadgeVerifyOut"];

function Invalid({ id }: { id: string }) {
  return (
    <div className="mx-auto max-w-xl rounded-[var(--radius-xl)] border border-danger/40 bg-danger-soft/40 px-6 py-10 text-center" role="alert">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-danger-soft text-danger">
        <CircleX className="h-6 w-6" aria-hidden />
      </span>
      <h1 className="mt-4 text-xl font-semibold text-fg">No badge award found</h1>
      <p className="mt-2 break-all font-mono text-sm text-muted">{id}</p>
      <p className="mt-3 text-sm text-muted">No badge has been awarded with this ID. Check the link you were given — a badge that can&apos;t be verified here should not be trusted.</p>
      <div className="mt-6 flex justify-center">
        <LinkButton href="/" variant="secondary">Go home</LinkButton>
      </div>
    </div>
  );
}

function BadgeView({ v }: { v: BadgeVerify }) {
  const b = v.badge;
  const recipient = v.recipient as { handle?: string; display_name?: string } | null;
  const url = typeof window !== "undefined" ? `${window.location.origin}/badges/${v.public_id}` : `/badges/${v.public_id}`;

  useEffect(() => {
    const prev = document.title;
    document.title = `${b.name} badge · DataBattles`;
    return () => {
      document.title = prev;
    };
  }, [b.name]);

  return (
    <div className="space-y-6">
      <div role="status" className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-success/40 bg-success-soft px-4 py-3">
        <BadgeCheck className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden />
        <div className="text-sm">
          <p className="font-semibold text-success">Verified badge award</p>
          <p className="text-fg/80">This badge was awarded on DataBattles on {formatDate(v.awarded_at, { dateStyle: "long" })}.</p>
        </div>
      </div>

      <article className="rounded-[var(--radius-xl)] border border-border bg-surface px-6 py-10 text-center shadow-card">
        <div className="flex justify-center">
          <BadgeIcon icon={b.icon} color={b.color} size={96} />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{b.name}</h1>
        <p className="mx-auto mt-2 max-w-lg text-muted">{b.description}</p>
        <div className="mt-4 flex flex-wrap justify-center gap-1.5">
          <Badge tone="outline">{titleCase(b.category)}</Badge>
          <Badge tone={v.manual ? "info" : "accent"}>{v.manual ? "Awarded by an organizer" : "Automatic"}</Badge>
          {b.rarity_label ? <Badge>{b.rarity_label}</Badge> : null}
        </div>
        <div className="mt-8 text-sm">
          {recipient?.handle ? (
            <p className="text-muted">
              Awarded to{" "}
              <Link href={`/u/${recipient.handle}`} className="font-semibold text-fg hover:text-accent-strong">
                {recipient.display_name ?? recipient.handle}
              </Link>
            </p>
          ) : (
            <p className="inline-flex items-center gap-1.5 text-muted">
              <EyeOff className="h-4 w-4" aria-hidden /> The recipient has chosen not to show their name.
            </p>
          )}
        </div>
      </article>

      <section className="rounded-[var(--radius-lg)] border border-border bg-surface p-5" aria-labelledby="badge-evidence">
        <h2 id="badge-evidence" className="mb-4 text-sm font-semibold text-fg">How it was earned</h2>
        <p className="mb-5 text-sm text-fg">{v.evidence_summary}</p>
        <KeyValue
          items={[
            { label: "Awarded", value: <time dateTime={v.awarded_at}>{formatDateTime(v.awarded_at)}</time> },
            { label: "Award type", value: v.manual ? "Manual (by an authorized organizer)" : "Automatic (criteria met)" },
            { label: "Criteria version", value: `v${v.criteria_version}` },
            { label: "Award ID", value: <span className="font-mono">{v.public_id}</span> },
          ]}
        />
        {!v.manual ? (
          <p className="mt-4 text-xs text-subtle">
            Criteria can be updated over time; this award was granted under version {v.criteria_version} and is never revoked by later changes.
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
          <CopyButton value={url} label="Copy verification link" />
        </div>
      </section>
    </div>
  );
}

export default function BadgeVerifyPage() {
  const params = useParams<{ id: string }>();
  const id = (params.id ?? "").trim().toUpperCase();
  const q = useQuery({
    queryKey: ["badges", "verify", id],
    queryFn: () => get<BadgeVerify>(`/badges/verify/${encodeURIComponent(id)}`),
    enabled: Boolean(id),
  });

  return (
    <Container size="md" className="py-8 pb-16">
      {q.isPending ? (
        <div role="status" aria-label="Verifying badge" className="space-y-6">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-80 w-full rounded-[var(--radius-xl)]" />
        </div>
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 404 ? <Invalid id={id} /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <BadgeView v={q.data} />
      )}
    </Container>
  );
}
