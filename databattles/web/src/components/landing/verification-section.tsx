"use client";

import { BadgeCheck, Fingerprint, QrCode, ScanLine, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useTilt } from "@/components/motion/tilt";
import { Reveal } from "@/components/motion/reveal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { CERT_ID_RE, normalizeCertificateId } from "@/lib/certificates";
import { SectionHeading } from "./section-heading";

const CHECKS = [
  { icon: Fingerprint, title: "Checksummed ID", text: "Each certificate ID carries a check segment, so typos and forgeries fail fast." },
  { icon: ScanLine, title: "Exactly what it attests", text: "The public page shows the recipient, the achievement, the issuer and the date." },
  { icon: ShieldCheck, title: "Revocation is visible", text: "If a certificate is ever revoked, its page says so — with the date." },
];

/**
 * Credential trust. The specimen on the right is an illustrative layout (bars, not invented names or
 * IDs); the form verifies a real certificate by ID.
 */
export function VerificationSection() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const tilt = useTilt<HTMLDivElement>({ max: 6 });

  return (
    <section className="relative overflow-hidden py-20 sm:py-28" aria-label="Verified credentials">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10" style={{ background: "radial-gradient(50% 50% at 75% 50%, color-mix(in oklab, var(--success) 9%, transparent), transparent)" }} />
      <Container className="grid grid-cols-1 items-center gap-14 lg:grid-cols-2">
        <div>
          <SectionHeading
            index="07"
            eyebrow="Verify"
            title="Proof that holds up when someone checks."
            description="Results, certificates and badges are backed by evidence. Anyone — a recruiter, a professor, a sponsor — can confirm one in seconds."
          />
          <Reveal as="ul" delay={60} className="mt-8 space-y-4">
            {CHECKS.map((c) => (
              <li key={c.title} className="flex gap-3.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-success-soft text-success">
                  <c.icon className="h-4 w-4" aria-hidden />
                </span>
                <span>
                  <span className="block font-medium text-fg">{c.title}</span>
                  <span className="block text-sm leading-relaxed text-muted">{c.text}</span>
                </span>
              </li>
            ))}
          </Reveal>
          <Reveal delay={120}>
            <form
              className="mt-8 flex flex-col gap-2 sm:flex-row"
              onSubmit={(e) => {
                e.preventDefault();
                const id = normalizeCertificateId(value);
                if (!CERT_ID_RE.test(id)) {
                  setError("Certificate IDs look like DB-XXXX-XXXX-XX (letters and digits).");
                  return;
                }
                setError(null);
                router.push(`/verify/${id}`);
              }}
            >
              <label htmlFor="landing-cert" className="sr-only">Certificate ID</label>
              <Input
                id="landing-cert"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="DB-XXXX-XXXX-XX or a verification link"
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "landing-cert-err" : undefined}
                className="h-11 font-mono text-[13px] sm:max-w-xs"
                autoComplete="off"
                spellCheck={false}
              />
              <Button type="submit" size="lg" icon={<BadgeCheck className="h-4 w-4" />}>Verify</Button>
            </form>
            {error ? <p id="landing-cert-err" role="alert" className="mt-2 text-xs font-medium text-danger">{error}</p> : null}
          </Reveal>
        </div>

        <Reveal delay={100} className="relative mx-auto w-full max-w-md [perspective:1200px]">
          <div
            ref={tilt}
            className="spotlight relative overflow-hidden rounded-[var(--radius-2xl)] border border-border-strong bg-surface surface-sheen p-6 shadow-elevated [transform:var(--tilt)] transition-transform duration-500 ease-out-expo sm:p-8"
            aria-hidden
          >
            <div className="pointer-events-none absolute inset-0 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top_right,black,transparent_70%)]" />
            <div className="relative flex items-start justify-between">
              <div>
                <p className="text-eyebrow text-subtle">Certificate of achievement</p>
                <div className="mt-3 h-5 w-48 rounded-md bg-surface-3" />
                <div className="mt-2 h-3 w-32 rounded bg-surface-3/70" />
              </div>
              <span className="relative flex h-14 w-14 items-center justify-center rounded-full bg-success-soft text-success">
                <span className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring" />
                <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12.5l4.5 4.5L19 7.5" strokeDasharray="24" className="motion-safe:animate-check" />
                </svg>
              </span>
            </div>
            <div className="relative mt-8 grid grid-cols-2 gap-5">
              {["Recipient", "Issued by", "Achievement", "Issued on"].map((l) => (
                <div key={l}>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-subtle">{l}</p>
                  <div className="mt-2 h-3.5 w-full max-w-[9rem] rounded bg-surface-3" />
                </div>
              ))}
            </div>
            <div className="relative mt-8 flex items-center gap-4 rounded-[var(--radius-lg)] border border-border bg-bg-elevated p-4">
              <QrCode className="h-10 w-10 shrink-0 text-muted" />
              <div className="min-w-0 flex-1">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-subtle">Certificate ID</p>
                <p className="mt-1 font-mono text-sm tracking-[0.12em] text-fg">DB-····-····-··</p>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2 py-1 text-[11px] font-medium text-success">
                <BadgeCheck className="h-3.5 w-3.5" /> Valid
              </span>
            </div>
          </div>
          <p className="mt-3 text-center text-xs text-subtle">Illustrative layout. Every real certificate has its own public page.</p>
        </Reveal>
      </Container>
    </section>
  );
}
