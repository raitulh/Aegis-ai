"use client";

import { FileSearch, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/form";
import { Container, PageHeader } from "@/components/ui/page";

const ID_RE = /^DB-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{2}$/;

/** Accepts "DB-7K2M-9QXA-F3", "db7k2m9qxaf3" or a pasted verification URL, and returns the canonical form. */
function normalizeCertificateId(raw: string): string {
  let v = raw.trim();
  const fromUrl = v.match(/\/verify\/([^/?#\s]+)/i);
  if (fromUrl) {
    try {
      v = decodeURIComponent(fromUrl[1]);
    } catch {
      v = fromUrl[1];
    }
  }
  const compact = v.toUpperCase().replace(/[^0-9A-Z]/g, "");
  if (compact.length === 12 && compact.startsWith("DB")) return `DB-${compact.slice(2, 6)}-${compact.slice(6, 10)}-${compact.slice(10)}`;
  return v.toUpperCase();
}

export default function VerifyIndexPage() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <Container size="md">
      <PageHeader
        eyebrow="Credential verification"
        title="Verify a certificate"
        description="Every DataBattles certificate has a unique ID. Enter it to confirm who it was issued to, for what, and whether it is still valid."
      />
      <Card>
        <CardBody className="py-6">
          <form
            className="space-y-4"
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
                  className="h-12 font-mono text-base tracking-wider"
                  maxLength={200}
                />
              )}
            </Field>
            <Button type="submit" size="lg" icon={<FileSearch className="h-4 w-4" />} disabled={!value.trim()}>
              Verify certificate
            </Button>
          </form>
        </CardBody>
      </Card>
      <div className="mt-6 flex gap-3 pb-16 text-sm text-muted">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <p>
          IDs include a check segment, so mistyped or forged IDs are rejected immediately. Verification pages show only what the certificate states —
          never email addresses or other private data.
        </p>
      </div>
    </Container>
  );
}
