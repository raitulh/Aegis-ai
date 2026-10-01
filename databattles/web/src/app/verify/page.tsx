"use client";

import { EyeOff, FileSearch, Fingerprint, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { Container } from "@/components/ui/page";
import { CERT_ID_RE as ID_RE, normalizeCertificateId } from "@/lib/certificates";

const ASSURANCES = [
  {
    icon: Fingerprint,
    title: "Checksummed IDs",
    text: "IDs include a check segment, so mistyped or forged IDs are rejected immediately.",
  },
  {
    icon: EyeOff,
    title: "Nothing private",
    text: "Verification pages show only what the certificate states — never email addresses or other private data.",
  },
  {
    icon: ShieldCheck,
    title: "Revocation is visible",
    text: "If a certificate is ever revoked, its verification page says so, with the date.",
  },
];

/** Decorative seal: a shield inside the DataBattles orbit motif. */
function Seal() {
  return (
    <div aria-hidden className="relative mx-auto flex h-24 w-24 items-center justify-center">
      <span className="absolute inset-0 rounded-full border border-dashed border-border-strong motion-safe:animate-spin-slow" style={{ animationIterationCount: 1 }} />
      <span className="absolute inset-3 rounded-full border border-border" />
      <span className="absolute right-1 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-cyan shadow-[0_0_10px_var(--cyan)]" />
      <span className="absolute left-3 top-4 h-1 w-1 rounded-full bg-accent-strong opacity-70" />
      <span className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-border-strong bg-surface-2 text-success shadow-[inset_0_1px_0_var(--hairline-highlight),0_10px_30px_-12px_color-mix(in_oklab,var(--success)_60%,transparent)]">
        <span className="absolute inset-0 rounded-2xl bg-[radial-gradient(circle_at_50%_30%,var(--success-soft),transparent_70%)]" />
        <ShieldCheck className="relative h-6 w-6" />
      </span>
    </div>
  );
}

export default function VerifyIndexPage() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <Container size="md" className="pb-20">
      <header className="relative isolate pb-10 pt-12 text-center sm:pt-16">
        <div
          aria-hidden
          className="pointer-events-none absolute left-1/2 top-0 -z-10 h-72 w-[36rem] max-w-full -translate-x-1/2"
          style={{ background: "radial-gradient(closest-side, color-mix(in oklab, var(--success) 12%, transparent), transparent)" }}
        />
        <div className="animate-rise">
          <Seal />
          <p className="mt-6 text-eyebrow text-accent-strong">Credential verification</p>
          <h1 className="mt-3 text-title text-fg">Verify a certificate</h1>
          <p className="mx-auto mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
            Every DataBattles certificate has a unique ID. Enter it to confirm who it was issued to, for what, and whether it is still valid.
          </p>
        </div>
      </header>

      <div className="relative animate-rise [animation-delay:80ms]">
        <div className="border-gradient relative overflow-hidden rounded-[var(--radius-2xl)] bg-surface surface-sheen shadow-elevated">
          <div aria-hidden className="pointer-events-none absolute inset-0 dot-grid opacity-30 [mask-image:radial-gradient(ellipse_at_top,black,transparent_70%)]" />
          <form
            className="relative space-y-4 p-5 sm:p-8"
            onSubmit={(e) => {
              e.preventDefault();
              const id = normalizeCertificateId(value);
              if (!ID_RE.test(id)) {
                setError("Certificate IDs look like DB-XXXX-XXXX-XX (letters and digits).");
                return;
              }
              setError(null);
              router.push(`/verify/${id}`);
            }}
          >
            <Field label="Certificate ID" error={error} hint="Found at the bottom of the certificate, e.g. DB-7K2M-9QXA-F3. You can also paste a verification link.">
              {(p) => (
                <div className="flex flex-col gap-2.5 sm:flex-row">
                  <Input
                    {...p}
                    value={value}
                    onChange={(e) => {
                      setValue(e.target.value);
                      if (error) setError(null);
                    }}
                    placeholder="DB-XXXX-XXXX-XX"
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="characters"
                    className="h-12 min-w-0 font-mono text-base tracking-[0.12em] sm:flex-1"
                    maxLength={200}
                  />
                  <Button type="submit" size="lg" className="h-12 shrink-0" icon={<FileSearch className="h-4 w-4" />} disabled={!value.trim()}>
                    Verify certificate
                  </Button>
                </div>
              )}
            </Field>
          </form>
          <div className="relative flex items-center gap-2 border-t border-border bg-bg-elevated/50 px-5 py-3 text-xs text-subtle sm:px-8">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-success" aria-hidden />
            Checked against DataBattles records each time — no account needed.
          </div>
        </div>
      </div>

      <ul className="mt-10 grid grid-cols-1 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border bg-border sm:grid-cols-3">
        {ASSURANCES.map(({ icon: Icon, title, text }) => (
          <li key={title} className="bg-surface/80 p-5">
            <span aria-hidden className="flex h-9 w-9 items-center justify-center rounded-xl border border-success/25 bg-success-soft text-success">
              <Icon className="h-4 w-4" />
            </span>
            <p className="mt-3 text-sm font-medium text-fg">{title}</p>
            <p className="mt-1 text-[13px] leading-relaxed text-muted">{text}</p>
          </li>
        ))}
      </ul>
    </Container>
  );
}
