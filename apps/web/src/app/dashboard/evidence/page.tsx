"use client";
import { CheckCircle2, FileCheck2, FileText, KeyRound, ShieldAlert, ShieldCheck, Upload } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useRef, useState } from "react";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { Metric } from "@/components/dashboard/metric";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { CodeBlock, Hash, KeyValue, Pagination } from "@/components/ui/display";
import { Field, Input, Select } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, type Page } from "@/lib/api";
import { useExports, useIntegrity, useSigningKey } from "@/lib/queries";
import type { Evidence, PackageVerification } from "@/lib/types";
import { formatDateTime, timeAgo, titleCase } from "@/lib/utils";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

const TABS = ["integrity", "records", "exports", "verify"] as const;
type Tab = (typeof TABS)[number];

export default function EvidencePage() {
  const search = useSearchParams();
  const router = useRouter();
  const initial = (TABS as readonly string[]).includes(search.get("tab") ?? "") ? (search.get("tab") as Tab) : "integrity";
  const [tab, setTab] = useState<Tab>(initial);

  return (
    <div>
      <PageHeader
        title="Evidence"
        description="Append-only, hash-chained artifacts behind every finding. Verify integrity here, export signed packages, and check any package offline."
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
          router.replace(`/dashboard/evidence?tab=${v}`);
        }}
      >
        <TabsList className="mb-5">
          <TabTrigger value="integrity">Integrity</TabTrigger>
          <TabTrigger value="records">Records</TabTrigger>
          <TabTrigger value="exports">Exports</TabTrigger>
          <TabTrigger value="verify">Verify a package</TabTrigger>
        </TabsList>
        <TabPanel value="integrity">{tab === "integrity" ? <IntegrityTab /> : null}</TabPanel>
        <TabPanel value="records">{tab === "records" ? <RecordsTab /> : null}</TabPanel>
        <TabPanel value="exports">{tab === "exports" ? <ExportsTab /> : null}</TabPanel>
        <TabPanel value="verify">{tab === "verify" ? <VerifyTab /> : null}</TabPanel>
      </Tabs>
    </div>
  );
}

function IntegrityTab() {
  const query = useIntegrity();
  return (
    <QueryBoundary query={query}>
      {(s) => (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Workspace status" value={titleCase(s.status.toLowerCase())} icon={s.status === "TAMPERED" ? ShieldAlert : ShieldCheck} tone={s.status === "TAMPERED" ? "var(--color-critical)" : s.status === "VERIFIED" ? "var(--color-success)" : undefined} hint={`Checked ${timeAgo(s.checked_at)}`} />
            <Metric label="Audits verified" value={`${s.audits_verified}/${s.audits_checked}`} icon={CheckCircle2} hint="Most recent completed audits" />
            <Metric label="Evidence records" value={s.evidence_total.toLocaleString()} icon={FileText} />
            <Metric label="Signing key" value={s.signing_key_id.slice(0, 10)} icon={KeyRound} hint={s.signing_key_development ? "Development key — not for production" : "Ed25519"} tone={s.signing_key_development ? "var(--color-medium)" : undefined} />
          </div>
          {s.results.length === 0 ? (
            <EmptyState icon={FileCheck2} title="No completed audits yet" description="Each completed audit produces a hash chain that can be recomputed here at any time." />
          ) : (
            <DataTable caption="Hash-chain verification per audit">
              <THead>
                <tr>
                  <TH>Audit</TH>
                  <TH>Chain status</TH>
                  <TH>Records</TH>
                  <TH>Head hash</TH>
                  <TH>Completed</TH>
                </tr>
              </THead>
              <tbody>
                {s.results.map((r) => (
                  <TR key={r.audit_id} href={`/dashboard/audits/${r.audit_id}?tab=evidence`}>
                    <TD>
                      <RowLink href={`/dashboard/audits/${r.audit_id}?tab=evidence`}>{r.audit_name}</RowLink>
                    </TD>
                    <TD>
                      <StatusBadge status={r.status} />
                    </TD>
                    <TD className="font-mono">{r.records}</TD>
                    <TD className="relative z-10">
                      <Hash value={r.head} />
                    </TD>
                    <TD className="text-[var(--color-text-subtle)]">{formatDateTime(r.completed_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </DataTable>
          )}
          <p className="text-xs text-[var(--color-text-subtle)]">
            Verification recomputes every content hash and chain link from stored artifacts. A VERIFIED chain shows the records have not changed since capture; it is not a legal attestation.
          </p>
        </div>
      )}
    </QueryBoundary>
  );
}

const KINDS = ["prompt", "model_output", "source", "dataset", "trace", "tool_call", "policy_excerpt", "metric_result", "evaluator_result", "configuration", "model_version"];

function RecordsTab() {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const query = useQuery({
    queryKey: ["evidence-list", page, kind],
    queryFn: () => api.get<Page<Evidence>>("/evidence", { page, page_size: 50, kind: kind || undefined }),
    placeholderData: keepPreviousData,
  });
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Select
          aria-label="Evidence kind"
          className="w-56"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All kinds</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {titleCase(k)}
            </option>
          ))}
        </Select>
      </div>
      <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState icon={FileText} title="No evidence yet" description="Evidence is captured automatically during audits, red-team runs and runtime policy decisions." />
          ) : (
            <>
              <DataTable caption="Evidence records">
                <THead>
                  <tr>
                    <TH>Title</TH>
                    <TH>Kind</TH>
                    <TH>Seq</TH>
                    <TH>Confidence</TH>
                    <TH>Content hash</TH>
                    <TH>Captured</TH>
                  </tr>
                </THead>
                <tbody>
                  {data.items.map((e) => (
                    <TR key={e.id} href={`/dashboard/evidence/${e.id}`}>
                      <TD className="max-w-sm">
                        <span className="line-clamp-1">
                          <RowLink href={`/dashboard/evidence/${e.id}`}>{e.title}</RowLink>
                        </span>
                      </TD>
                      <TD>
                        <Badge>{titleCase(e.kind)}</Badge>
                      </TD>
                      <TD className="font-mono text-xs">{e.seq}</TD>
                      <TD className="capitalize text-[var(--color-text-muted)]">{e.confidence_level}</TD>
                      <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{e.content_hash.slice(0, 12)}…</TD>
                      <TD className="text-[var(--color-text-subtle)]">{timeAgo(e.created_at)}</TD>
                    </TR>
                  ))}
                </tbody>
              </DataTable>
              <Pagination page={data.meta.page} totalPages={data.meta.total_pages} onPage={setPage} />
            </>
          )
        }
      </QueryBoundary>
    </div>
  );
}

function ExportsTab() {
  const query = useExports();
  return (
    <QueryBoundary query={query} skeleton={<div className="h-48 skeleton rounded-[var(--radius-lg)]" />}>
      {(data) =>
        data.items.length === 0 ? (
          <EmptyState icon={FileCheck2} title="No evidence packages exported" description="Open a completed audit and choose “Export evidence package”. Each export is signed and logged here with its root hash." />
        ) : (
          <DataTable caption="Evidence exports">
            <THead>
              <tr>
                <TH>Exported</TH>
                <TH>Scope</TH>
                <TH>Artifacts</TH>
                <TH>Integrity at export</TH>
                <TH>Root hash</TH>
                <TH>Key</TH>
              </tr>
            </THead>
            <tbody>
              {data.items.map((x) => (
                <TR key={x.id} href={x.audit_id ? `/dashboard/audits/${x.audit_id}?tab=evidence` : undefined}>
                  <TD>{x.audit_id ? <RowLink href={`/dashboard/audits/${x.audit_id}?tab=evidence`}>{formatDateTime(x.created_at)}</RowLink> : formatDateTime(x.created_at)}</TD>
                  <TD className="text-[var(--color-text-muted)]">{titleCase(x.scope)}</TD>
                  <TD className="font-mono">{x.artifact_count}</TD>
                  <TD>
                    <StatusBadge status={x.integrity_status} />
                  </TD>
                  <TD className="relative z-10">
                    <Hash value={x.root_hash} />
                  </TD>
                  <TD className="font-mono text-xs text-[var(--color-text-subtle)]">{x.key_id ?? "—"}</TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )
      }
    </QueryBoundary>
  );
}

function VerifyTab() {
  const key = useSigningKey();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [publicKey, setPublicKey] = useState("");
  const [result, setResult] = useState<PackageVerification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function verify() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      setResult(await api.upload<PackageVerification>("/evidence/verify-package", form, { public_key: publicKey.trim() || undefined }));
    } catch (e) {
      setError(errorMessage(e, "Verification failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Verify an evidence package</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-[var(--color-text-muted)]">
            Upload a package exported from Aegis. The server runs the same verifier that ships inside the package as <code className="font-mono text-xs">verify.py</code>, so the result can be reproduced offline without Aegis.
          </p>
          <Field label="Package (.zip)">
            {(p) => (
              <div className="flex items-center gap-2">
                <input
                  {...p}
                  ref={fileRef}
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setResult(null);
                  }}
                  className="sr-only"
                />
                <Button variant="secondary" icon={Upload} onClick={() => fileRef.current?.click()} type="button">
                  Choose file
                </Button>
                <span className="truncate text-sm text-[var(--color-text-muted)]">{file ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB` : "No file selected"}</span>
              </div>
            )}
          </Field>
          <Field label="Trusted public key (optional)" hint="Paste a base64 Ed25519 key you obtained out-of-band to check the signature against it rather than the key embedded in the package.">
            {(p) => <Input {...p} value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder="base64 public key" className="font-mono text-xs" />}
          </Field>
          <Button onClick={verify} disabled={!file} loading={busy} icon={ShieldCheck}>
            Verify package
          </Button>
          {error ? (
            <p role="alert" className="text-sm text-[var(--color-critical)]">
              {error}
            </p>
          ) : null}
          {result ? <VerificationResult result={result} /> : null}
        </CardBody>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Workspace signing key</CardTitle>
          </CardHeader>
          <CardBody>
            <QueryBoundary query={key} skeleton={<div className="h-24 skeleton" />}>
              {(k) => (
                <div className="space-y-3">
                  {k.development_key ? <Badge tone="warning">Development key — packages are not production-trustworthy</Badge> : null}
                  <KeyValue items={[["Algorithm", k.algorithm], ["Key id", <span key="id" className="font-mono text-xs">{k.key_id}</span>]]} />
                  <CodeBlock code={k.public_key} language="public key (base64)" />
                </div>
              )}
            </QueryBoundary>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Verify offline</CardTitle>
          </CardHeader>
          <CardBody>
            <CodeBlock language="shell" code={"unzip -j aegis-evidence-<audit>.zip '*/verify.py'\npython3 verify.py aegis-evidence-<audit>.zip --public-key <base64-key>"} />
            <p className="mt-2 text-xs text-[var(--color-text-subtle)]">Prints a JSON report and exits 0 only when the status is VERIFIED. Requires Python 3.10+; signature checks also need the <code className="font-mono">cryptography</code> package.</p>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function VerificationResult({ result }: { result: PackageVerification }) {
  return (
    <div className="space-y-3 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] p-4" aria-live="polite">
      <div className="flex items-center gap-2">
        <StatusBadge status={result.status} />
        {result.audit ? <span className="text-sm text-[var(--color-text-muted)]">{result.audit.name}</span> : null}
      </div>
      <KeyValue
        items={[
          ["Format", result.format],
          ["Generated", formatDateTime(result.generated_at)],
          ["Root hash", <Hash key="root" value={result.root_hash} />],
          ["Chain", result.chain ? `${titleCase(result.chain.status.toLowerCase())} · ${result.chain.records} records` : "—"],
          ["Signature", result.signature ? (result.signature.valid === null ? "Not checked" : result.signature.valid ? `Valid (${result.signature.trusted_key_supplied ? "trusted key" : "embedded key"})` : "Invalid") : "—"],
        ]}
      />
      {result.problems.length ? (
        <ul className="space-y-1 text-xs text-[var(--color-critical)]">
          {result.problems.slice(0, 20).map((p, i) => (
            <li key={i}>
              <span className="font-mono">{p.check}</span>: {p.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
