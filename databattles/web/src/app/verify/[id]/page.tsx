"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleX, Printer, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { CopyButton, KeyValue } from "@/components/ui/misc";
import { Container } from "@/components/ui/page";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { API_BASE, ApiError, get } from "@/lib/api";
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

function InvalidState({ id, reason }: { id: string; reason: "invalid" | "not_found" }) {
  return (
    <div className="mx-auto max-w-xl rounded-[var(--radius-xl)] border border-danger/40 bg-danger-soft/40 px-6 py-10 text-center" role="alert">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-danger-soft text-danger">
        <CircleX className="h-6 w-6" aria-hidden />
      </span>
      <h1 className="mt-4 text-xl font-semibold text-fg">This certificate ID is not valid</h1>
      <p className="mt-2 font-mono text-sm text-muted break-all">{id}</p>
      <p className="mt-3 text-sm text-muted">
        {reason === "invalid"
          ? "The ID is malformed or its check segment doesn't match — it may have been mistyped or altered."
          : "The ID is well-formed, but no certificate with this ID has been issued by DataBattles."}{" "}
        Double-check the ID printed on the certificate. A certificate that fails verification should not be trusted.
      </p>
      <div className="mt-6 flex justify-center">
        <LinkButton href="/verify" variant="secondary">Try another ID</LinkButton>
      </div>
    </div>
  );
}

function CertificateView({ c }: { c: CertificateOut }) {
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
        {valid ? (
          <div role="status" className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-success/40 bg-success-soft px-4 py-3">
            <BadgeCheck className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-success">Valid certificate</p>
              <p className="text-fg/80">
                Issued by {c.issuer_name} to {c.recipient_name} on {formatDate(c.issued_at, { dateStyle: "long" })}. This record is held by DataBattles and has not been revoked.
              </p>
            </div>
          </div>
        ) : (
          <div role="alert" className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-danger/40 bg-danger-soft px-4 py-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-danger">{revoked ? "This certificate has been revoked" : `Status: ${titleCase(c.status)}`}</p>
              {revoked ? (
                <p className="text-fg/80">
                  Revoked{c.revoked_at ? <> on <time dateTime={c.revoked_at}>{formatDateTime(c.revoked_at)}</time></> : null}.
                  {c.revoked_reason ? <> Reason: <span className="font-medium text-fg">{c.revoked_reason}</span></> : null} It should no longer be relied on.
                </p>
              ) : (
                <p className="text-fg/80">This certificate is not currently valid.</p>
              )}
            </div>
          </div>
        )}

        {c.is_demo ? (
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
            <DemoBadge /> This certificate was generated from synthetic demo data and does not represent a real achievement.
          </p>
        ) : null}

        <article
          className="cert-paper relative overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface px-6 py-10 text-center shadow-card sm:px-12 sm:py-14"
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
          <h1 className="mt-4 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{r.heading || "Certificate"}</h1>
          <p className="mt-6 text-sm text-muted">This certifies that</p>
          <p className="mt-2 text-3xl font-semibold text-fg sm:text-4xl">{c.recipient_name}</p>
          {r.body ? <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted">{r.body}</p> : null}
          <div className="mx-auto mt-6 inline-flex flex-wrap items-center justify-center gap-2">
            <span className="rounded-full px-3 py-1 text-sm font-semibold" style={{ color: accent, boxShadow: `inset 0 0 0 1px ${accent}` }}>
              {c.result_label}
            </span>
            <span className="text-sm text-muted">{c.event_title}</span>
          </div>
          <div className="mt-10 grid items-end gap-8 sm:grid-cols-3">
            <div className="text-left sm:text-left">
              <p className="text-xs text-subtle">Issued</p>
              <p className="text-sm font-medium text-fg">{formatDate(c.issued_at, { dateStyle: "long" })}</p>
            </div>
            <div className="flex justify-center">
              { }
              <img
                src={`${API_BASE}/certificates/${encodeURIComponent(c.public_id)}/qr.svg`}
                alt={`QR code linking to the verification page for certificate ${c.public_id}`}
                width={112}
                height={112}
                className="h-28 w-28 rounded-md bg-white p-1.5"
              />
            </div>
            <div className="text-right">
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

        <section className="rounded-[var(--radius-lg)] border border-border bg-surface p-5" aria-labelledby="cert-details">
          <h2 id="cert-details" className="mb-4 text-sm font-semibold text-fg">Verification details</h2>
          <KeyValue
            items={[
              { label: "Recipient", value: c.recipient_name },
              { label: "Event", value: c.event_title },
              { label: "Result", value: <>{c.result_label}{c.rank ? <span className="text-muted"> · rank {c.rank}</span> : null}</> },
              { label: "Issuer", value: c.issuer_name },
              { label: "Type", value: titleCase(c.kind) },
              { label: "Issued", value: <time dateTime={c.issued_at}>{formatDateTime(c.issued_at)}</time> },
              { label: "Status", value: valid ? <span className="font-medium text-success">Valid</span> : <span className="font-medium text-danger">{titleCase(c.status)}</span> },
              { label: "Template version", value: `v${c.template_version}` },
            ]}
          />
          <div className="mt-5 border-t border-border pt-4">
            <p className="text-xs text-subtle">Verification URL</p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <code className="min-w-0 break-all rounded bg-surface-2 px-2 py-1 font-mono text-xs text-fg">{c.verification_url}</code>
              <span className="print:hidden"><CopyButton value={c.verification_url} label="Copy link" /></span>
            </div>
          </div>
        </section>

        <div className="flex flex-wrap items-center gap-2 print:hidden">
          <Button variant="secondary" icon={<Printer className="h-4 w-4" />} onClick={() => window.print()}>Print or save as PDF</Button>
          <CopyButton value={c.public_id} label="Copy ID" className="h-10 px-3 text-sm" />
          <Link href="/verify" className="ml-auto text-sm text-muted hover:text-fg">Verify another certificate</Link>
        </div>
        <p className="text-xs text-subtle print:hidden">
          The certificate text is a snapshot taken at issuance; later edits to the issuer&apos;s template never change it.
        </p>
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
    <Container size="md" className="py-8 pb-16">
      {q.isPending ? (
        <div role="status" aria-label="Verifying certificate" className="space-y-6">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-96 w-full rounded-[var(--radius-xl)]" />
        </div>
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 404 ? (
          <InvalidState id={id} reason={q.error.code === "certificate_invalid" ? "invalid" : "not_found"} />
        ) : (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        )
      ) : (
        <CertificateView c={q.data} />
      )}
    </Container>
  );
}
