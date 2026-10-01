"use client";
import { CheckCircle2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Field, Input, Select, Textarea } from "@/components/ui/forms";
import { Button } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";

const KINDS = [
  { value: "sales", label: "Talk to sales" },
  { value: "demo", label: "Book a guided demo" },
  { value: "security_review", label: "Request a security review" },
  { value: "private_deployment", label: "Private / self-hosted deployment" },
];

export function ContactForm() {
  const params = useSearchParams();
  const initialKind = KINDS.some((k) => k.value === params.get("kind")) ? (params.get("kind") as string) : "sales";
  const [kind, setKind] = useState(initialKind);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ message: string }>("/public/contact", { kind, name, email, company: company || undefined, message: message || undefined });
      setDone(r.message);
    } catch (err) {
      setError(errorMessage(err, "Could not send your request."));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div role="status" className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <CheckCircle2 className="mt-0.5 h-5 w-5 text-[var(--color-success)]" aria-hidden />
        <p className="text-sm text-[var(--color-text)]">{done}</p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="max-w-xl space-y-4">
      <Field label="What can we help with?">
        {(p) => (
          <Select {...p} value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">{(p) => <Input {...p} required autoComplete="name" maxLength={160} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Work email">{(p) => <Input {...p} required type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      </div>
      <Field label="Company (optional)">{(p) => <Input {...p} autoComplete="organization" maxLength={200} value={company} onChange={(e) => setCompany(e.target.value)} />}</Field>
      <Field label="Message (optional)" hint="Tell us about the AI systems you want to assure and any deadlines.">
        {(p) => <Textarea {...p} rows={5} maxLength={5000} value={message} onChange={(e) => setMessage(e.target.value)} />}
      </Field>
      {error ? (
        <p role="alert" className="text-sm text-[var(--color-critical)]">
          {error}
        </p>
      ) : null}
      <Button type="submit" loading={busy}>
        Send request
      </Button>
      <p className="text-xs text-[var(--color-text-subtle)]">We use these details only to reply to this request.</p>
    </form>
  );
}
