"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Award, Building2, CalendarDays, Check, CircleX, Printer, ShieldAlert, ShieldCheck, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { DemoBadge, StatusBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { MetaItem } from "@/components/ui/extras";
import { CopyButton } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { API_BASE, ApiError, get } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, titleCase } from "@/lib/format";
import type { CertificateOut } from "@/lib/types";

interface Rendered {
  heading?: string;
  body?: string;
  accent_color?: string;
  signatory_name?: string | null;
  signatory_title?: string | null;
  template_name?: string;
}

const HEX = /^#[0-9a-f]{6}$/i;

/** Print: hide the global chrome and print on white regardless of the on-screen theme. */
const PRINT_CSS = `
@media print {
  body > div > header, body > div > footer, section[aria-label*="otifications"] { display: none !important; }
  html[data-theme] {
    --bg: #ffffff; --bg-elevated: #ffffff; --surface: #ffffff; --surface-2: #f3f4f8; --surface-3: #e9ebf2;
    --border: #d9dce5; --border-strong: #c3c8d4; --fg: #0e1320; --fg-muted: #3f4757; --fg-subtle: #5b6475;
    --success: #047857; --danger: #b91c1c; color-scheme: light;
  }
  html, body { background: #ffffff !important; }
  .cert-paper { box-shadow: none !important; break-inside: avoid; }
  @page { margin: 12mm; }
}`;

/** The ID with its check segment (the last group) set apart, so readers can see what the checksum covers. */
function CertificateId({ id, className }: { id: string; className?: string }) {
  const cut = id.lastIndexOf("-");
  const body = cut > 0 ? id.slice(0, cut + 1) : id;
  const check = cut > 0 ? id.slice(cut + 1) : "";
  return (
    <span className={cn("font-mono tracking-[0.08em]", className)}>
      {body}
      {check ? <span className="rounded-[4px] bg-accent-soft px-0.5 text-accent-strong">{check}</span> : null}
    </span>
  );
}

/** One evidence line in the verification checklist; the label always states the outcome in words. */
function EvidenceCheck({ ok, title, children }: { ok: boolean; title: ReactNode; children: ReactNode }) {
  return (
    <li className="flex gap-3 py-3">
      <span
        aria-hidden
        className={cn(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
          ok ? "bg-success-soft text-success ring-1 ring-inset ring-success/30" : "bg-danger-soft text-danger ring-1 ring-inset ring-danger/30",
        )}
      >
        {ok ? <Check className="h-3 w-3" strokeWidth={3} /> : <X className="h-3 w-3" strokeWidth={3} />}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{title}</p>
        <div className="mt-0.5 text-xs leading-relaxed text-muted">{children}</div>
      </div>
    </li>
  );
}

function InvalidState({ id, reason }: { id: string; reason: "invalid" | "not_found" }) {
  return (
    <div className="mx-auto max-w-2xl pt-6 sm:pt-10" role="alert">
      <div className="relative overflow-hidden rounded-[var(--radius-2xl)] border border-danger/35 bg-surface shadow-elevated">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-48"
          style={{ background: "radial-gradient(60% 100% at 50% 0%, color-mix(in oklab, var(--danger) 14%, transparent), transparent)" }}
        />
        <div className="relative px-6 pb-8 pt-10 text-center sm:px-10">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-danger-soft text-danger ring-1 ring-inset ring-danger/30 animate-scale-in">
            <CircleX className="h-7 w-7" aria-hidden />
          </span>
          <p className="mt-5 text-eyebrow text-danger">Verification failed</p>
          <h1 className="mt-2 text-xl font-semibold tracking-[-0.02em] text-fg sm:text-2xl">This certificate ID is not valid</h1>
          <p className="mx-auto mt-3 inline-block max-w-full break-all rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 py-1.5 font-mono text-sm text-muted">{id}</p>
          <p className="mx-auto mt-4 max-w-lg text-sm leading-relaxed text-muted">
            {reason === "invalid"
              ? "The ID is malformed or its check segment doesn't match — it may have been mistyped or altered."
              : "The ID is well-formed, but no certificate with this ID has been issued by DataBattles."}{" "}
            Double-check the ID printed on the certificate. A certificate that fails verification should not be trusted.
          </p>
        </div>
        <ul className="relative divide-y divide-border border-t border-border bg-bg-elevated/50 px-6 sm:px-10">
          <EvidenceCheck ok={reason !== "invalid"} title={reason === "invalid" ? "ID format and check segment: failed" : "ID format and check segment: passed"}>
            {reason === "invalid" ? "The ID does not match the DB-XXXX-XXXX-XX pattern or its checksum." : "The ID is correctly formed."}
          </EvidenceCheck>
          {reason === "not_found" ? (
            <EvidenceCheck ok={false} title="Issued certificate record: not found">No certificate with this ID exists in DataBattles records.</EvidenceCheck>
          ) : null}
        </ul>
        <div className="relative flex flex-col items-center justify-center gap-3 border-t border-border px-6 py-5 sm:flex-row">
          <LinkButton href="/verify" variant="secondary">Try another ID</LinkButton>
        </div>
      </div>
    </div>
  );
}

/** Result hero: the verdict first, then the facts the certificate attests to. */
function ResultHero({ c, checkedAt }: { c: CertificateOut; checkedAt: number }) {
  const valid = c.status === "valid";
  const revoked = c.status === "revoked";
  const tone = valid ? "success" : revoked ? "danger" : "warning";

  return (
    <section
      aria-labelledby="verify-result"
      className={cn(
        "relative overflow-hidden rounded-[var(--radius-2xl)] border bg-surface surface-sheen shadow-elevated animate-rise",
        tone === "success" && "border-success/30",
        tone === "danger" && "border-danger/40",
        tone === "warning" && "border-warning/40",
      )}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 print:hidden"
        style={{
          background: `radial-gradient(70% 120% at 0% 0%, color-mix(in oklab, var(--${tone}) ${valid ? 13 : 11}%, transparent), transparent 60%)`,
        }}
      />
      <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-25 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_65%)] print:hidden" />

      <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border px-5 py-3 sm:px-7">
        <p className="text-eyebrow text-subtle">Verification result</p>
        <p className="text-xs text-subtle">
          Checked <time dateTime={new Date(checkedAt).toISOString()} className="tabular">{formatDateTime(new Date(checkedAt))}</time>
        </p>
      </div>

      <div className="relative px-5 pb-6 pt-6 sm:px-7 sm:pb-7">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
          {valid ? (
            <span className="relative flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-success-soft text-success ring-1 ring-inset ring-success/30">
              <span aria-hidden className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring print:hidden" style={{ animationIterationCount: 3 }} />
              <span aria-hidden className="absolute -inset-2 rounded-full border border-dashed border-success/30 print:hidden" />
              <svg viewBox="0 0 24 24" className="h-8 w-8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className="motion-safe:animate-check" />
              </svg>
            </span>
          ) : (
            <span
              className={cn(
                "flex h-16 w-16 shrink-0 items-center justify-center rounded-full ring-1 ring-inset animate-scale-in",
                revoked ? "bg-danger-soft text-danger ring-danger/30" : "bg-warning-soft text-warning ring-warning/30",
              )}
            >
              <ShieldAlert className="h-8 w-8" aria-hidden />
            </span>
          )}

          <div className="min-w-0 flex-1" role={valid ? "status" : "alert"}>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={c.status} />
              {c.is_demo ? <DemoBadge /> : null}
            </div>
            <h1 id="verify-result" className="mt-3 text-2xl font-semibold tracking-[-0.025em] text-fg sm:text-[1.75rem]">
              {valid ? "Valid certificate" : revoked ? "This certificate has been revoked" : `Status: ${titleCase(c.status)}`}
            </h1>
            {valid ? (
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
                Issued by {c.issuer_name} to {c.recipient_name} on {formatDate(c.issued_at, { dateStyle: "long" })}. This record is held by DataBattles and has not been revoked.
              </p>
            ) : revoked ? (
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
                Revoked{c.revoked_at ? <> on <time dateTime={c.revoked_at} className="font-medium text-danger">{formatDateTime(c.revoked_at)}</time></> : null}.
                {c.revoked_reason ? <> Reason: <span className="font-medium text-fg">{c.revoked_reason}</span></> : null} It should no longer be relied on.
              </p>
            ) : (
              <p className="mt-2 text-sm text-muted">This certificate is not currently valid.</p>
            )}
          </div>
        </div>

        <div className="mt-6 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-bg-elevated/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-eyebrow text-subtle">Certificate ID</p>
            <p className="mt-1 text-lg text-fg sm:text-xl">
              <CertificateId id={c.public_id} className={cn(revoked && "text-muted line-through decoration-danger/60")} />
            </p>
          </div>
          <span className="shrink-0 print:hidden">
            <CopyButton value={c.public_id} label="Copy ID" className="h-9 px-3 text-sm" />
          </span>
        </div>

        <dl className="mt-6 grid grid-cols-1 gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
          <MetaItem icon={<UserRound />} label="Recipient">
            <span className="block whitespace-normal">{c.recipient_name}</span>
          </MetaItem>
          <MetaItem icon={<Award />} label="Achievement">
            <span className="block whitespace-normal">
              {c.result_label}
              {c.rank ? <span className="tabular text-muted"> · rank {c.rank}</span> : null}
            </span>
            <span className="mt-0.5 block whitespace-normal text-[13px] font-normal text-muted">{c.event_title}</span>
          </MetaItem>
          <MetaItem icon={<Building2 />} label="Issuer">
            <span className="block whitespace-normal">{c.issuer_name}</span>
          </MetaItem>
          <MetaItem icon={<CalendarDays />} label="Issued">
            <time dateTime={c.issued_at} className="tabular">{formatDate(c.issued_at, { dateStyle: "long" })}</time>
          </MetaItem>
        </dl>
      </div>
    </section>
  );
}

function CertificateView({ c, checkedAt }: { c: CertificateOut; checkedAt: number }) {
  const r = (c.rendered ?? {}) as Rendered;
  const accent = r.accent_color && HEX.test(r.accent_color) ? r.accent_color : "var(--accent)";
  const valid = c.status === "valid";
  const revoked = c.status === "revoked";

  useEffect(() => {
    const prev = document.title;
    document.title = `Certificate ${c.public_id} · ${c.recipient_name} · DataBattles`;
    return () => {
      document.title = prev;
    };
  }, [c.public_id, c.recipient_name]);

  return (
    <>
      <style>{PRINT_CSS}</style>
      <div className="space-y-6">
        <ResultHero c={c} checkedAt={checkedAt} />

        {c.is_demo ? (
          <p className="flex flex-wrap items-center gap-2 rounded-[var(--radius-md)] border border-warning/25 bg-warning-soft/60 px-4 py-2.5 text-sm text-muted">
            <DemoBadge /> This certificate was generated from synthetic demo data and does not represent a real achievement.
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0 animate-rise [animation-delay:80ms]">
            <p className="mb-3 text-eyebrow text-subtle print:hidden">Certificate snapshot</p>
            <article
              className="cert-paper relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface px-6 py-10 text-center shadow-card sm:px-10 sm:py-12"
              style={{ borderTop: `6px solid ${accent}` }}
              aria-label="Certificate"
            >
              <div className="pointer-events-none absolute inset-3 rounded-[var(--radius-lg)] border" style={{ borderColor: accent, opacity: 0.35 }} aria-hidden />
              {revoked ? (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
                  <span className="-rotate-12 rounded-lg border-4 border-danger px-6 py-2 text-4xl font-bold uppercase tracking-widest text-danger opacity-25 sm:text-6xl">
                    Revoked
                  </span>
                </div>
              ) : null}
              <p className="text-xs font-semibold uppercase tracking-[0.2em]" style={{ color: accent }}>{c.issuer_name}</p>
              <h2 className="mt-4 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{r.heading || "Certificate"}</h2>
              <p className="mt-6 text-sm text-muted">This certifies that</p>
              <p className="mt-2 text-3xl font-semibold text-fg sm:text-4xl">{c.recipient_name}</p>
              {r.body ? <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted">{r.body}</p> : null}
              <div className="mx-auto mt-6 inline-flex flex-wrap items-center justify-center gap-2">
                <span className="rounded-full px-3 py-1 text-sm font-semibold" style={{ color: accent, boxShadow: `inset 0 0 0 1px ${accent}` }}>
                  {c.result_label}
                </span>
                <span className="text-sm text-muted">{c.event_title}</span>
              </div>
              <div className="mt-10 grid grid-cols-1 items-end gap-8 sm:grid-cols-3">
                <div className="text-center sm:text-left">
                  <p className="text-xs text-subtle">Issued</p>
                  <p className="text-sm font-medium text-fg">{formatDate(c.issued_at, { dateStyle: "long" })}</p>
                </div>
                <div className="flex justify-center">
                  <img
                    src={`${API_BASE}/certificates/${encodeURIComponent(c.public_id)}/qr.svg`}
                    alt={`QR code linking to the verification page for certificate ${c.public_id}`}
                    width={112}
                    height={112}
                    className="h-28 w-28 rounded-md bg-white p-1.5"
                  />
                </div>
                <div className="text-center sm:text-right">
                  {r.signatory_name ? (
                    <>
                      <p className="border-t border-border-strong pt-2 text-sm font-medium text-fg">{r.signatory_name}</p>
                      {r.signatory_title ? <p className="text-xs text-subtle">{r.signatory_title}</p> : null}
                    </>
                  ) : (
                    <>
                      <p className="text-xs text-subtle">Certificate ID</p>
                      <p className="font-mono text-sm text-fg">{c.public_id}</p>
                    </>
                  )}
                </div>
              </div>
              {r.signatory_name ? <p className="mt-8 font-mono text-xs text-subtle">Certificate ID {c.public_id}</p> : null}
            </article>
            <p className="mt-3 text-xs text-subtle print:hidden">
              The certificate text is a snapshot taken at issuance; later edits to the issuer&apos;s template never change it.
            </p>
          </div>

          <aside className="min-w-0 space-y-4 animate-rise [animation-delay:140ms] lg:sticky lg:top-24 lg:self-start" aria-labelledby="cert-details">
            <section className="rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card">
              <div className="flex items-center gap-2 border-b border-border px-4 py-3">
                <ShieldCheck className="h-4 w-4 text-accent-strong" aria-hidden />
                <h2 id="cert-details" className="text-sm font-semibold text-fg">Verification details</h2>
              </div>
              <ul className="divide-y divide-border px-4">
                <EvidenceCheck ok title="ID format and check segment: passed">
                  Check segment <CertificateId id={c.public_id.slice(c.public_id.lastIndexOf("-") + 1)} className="text-fg" /> matches the rest of the ID.
                </EvidenceCheck>
                <EvidenceCheck ok title="Issued certificate record: found">
                  Issued by {c.issuer_name} on <time dateTime={c.issued_at}>{formatDateTime(c.issued_at)}</time>.
                </EvidenceCheck>
                <EvidenceCheck ok={!revoked} title={revoked ? "Revocation status: revoked" : "Revocation status: not revoked"}>
                  {revoked ? (
                    <>Revoked{c.revoked_at ? <> on <time dateTime={c.revoked_at}>{formatDateTime(c.revoked_at)}</time></> : null}.</>
                  ) : (
                    "No revocation has been recorded for this certificate."
                  )}
                </EvidenceCheck>
              </ul>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border px-4 py-4 text-sm">
                <div className="min-w-0">
                  <dt className="text-eyebrow text-subtle">Status</dt>
                  <dd className="mt-1">{valid ? <span className="font-medium text-success">Valid</span> : <span className="font-medium text-danger">{titleCase(c.status)}</span>}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-eyebrow text-subtle">Type</dt>
                  <dd className="mt-1 text-fg">{titleCase(c.kind)}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-eyebrow text-subtle">Template version</dt>
                  <dd className="tabular mt-1 text-fg">v{c.template_version}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-eyebrow text-subtle">Event</dt>
                  <dd className="mt-1 break-words text-fg">{c.event_title}</dd>
                </div>
              </dl>
              <div className="border-t border-border px-4 py-4">
                <p className="text-eyebrow text-subtle">Verification URL</p>
                <code className="mt-1.5 block break-all rounded-[var(--radius-sm)] bg-surface-2 px-2 py-1.5 font-mono text-xs text-fg">{c.verification_url}</code>
                <span className="mt-2 inline-flex print:hidden">
                  <CopyButton value={c.verification_url} label="Copy link" className="h-9 px-3 text-sm" />
                </span>
              </div>
            </section>

            <div className="flex flex-col gap-2 print:hidden">
              <Button variant="secondary" icon={<Printer className="h-4 w-4" />} onClick={() => window.print()}>Print or save as PDF</Button>
              <Link
                href="/verify"
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-[var(--radius-md)] text-sm text-muted transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]"
              >
                <ArrowLeft className="h-4 w-4" aria-hidden />
                Verify another certificate
              </Link>
            </div>
          </aside>
        </div>
      </div>
    </>
  );
}

export default function VerifyCertificatePage() {
  const params = useParams<{ id: string }>();
  let id = "";
  try {
    id = decodeURIComponent(params.id ?? "").trim().toUpperCase();
  } catch {
    id = (params.id ?? "").toUpperCase();
  }
  const q = useQuery({
    queryKey: ["certificates", "verify", id],
    queryFn: () => get<CertificateOut>(`/certificates/verify/${encodeURIComponent(id)}`),
    enabled: Boolean(id),
  });

  return (
    <Container size="lg" className="py-8 pb-20 sm:py-10">
      {q.isPending ? (
        <div role="status" aria-label="Verifying certificate" className="space-y-6">
          <div className="rounded-[var(--radius-2xl)] border border-border bg-surface p-6 sm:p-7">
            <div className="flex items-start gap-5">
              <Skeleton className="h-16 w-16 shrink-0 rounded-full" />
              <div className="flex-1">
                <Skeleton className="h-5 w-24 rounded-full" />
                <Skeleton className="mt-4 h-7 w-2/3 max-w-sm" />
                <Skeleton className="mt-3 h-4 w-full max-w-lg" />
              </div>
            </div>
            <Skeleton className="mt-6 h-16 w-full rounded-[var(--radius-lg)]" />
            <div className="mt-6 grid grid-cols-2 gap-5 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i}>
                  <Skeleton className="h-2.5 w-16" />
                  <Skeleton className="mt-2 h-4 w-28" />
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <Skeleton className="h-96 w-full rounded-[var(--radius-xl)]" />
            <Skeleton className="h-72 w-full rounded-[var(--radius-lg)]" />
          </div>
        </div>
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 404 ? (
          <InvalidState id={id} reason={q.error.code === "certificate_invalid" ? "invalid" : "not_found"} />
        ) : (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        )
      ) : (
        <CertificateView c={q.data} checkedAt={q.dataUpdatedAt} />
      )}
    </Container>
  );
}
