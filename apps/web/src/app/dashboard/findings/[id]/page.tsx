"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpenText, Clock, GitBranch, MessageSquare, Scale, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { CounterfactualComparison } from "@/components/dashboard/counterfactual";
import { EvidenceList } from "@/components/dashboard/evidence-list";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { CodeBlock, KeyValue } from "@/components/ui/display";
import { Field, Input, Select, Textarea } from "@/components/ui/forms";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, RiskBadge, SeverityBadge, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage, path } from "@/lib/api";
import {
  useCan,
  useFinding,
  useFindingComments,
  useFindingEvents,
  useFindingEvidence,
  useFindingExplanation,
  useFindingTransitions,
  useInvalidate,
  useSystem,
  useUpdateFinding,
} from "@/lib/queries";
import type { Finding } from "@/lib/types";
import { cn, formatDate, formatDateTime, timeAgo, titleCase } from "@/lib/utils";

type Recommendation = { category: string; title: string; description: string; change: Record<string, unknown>; creates_regression_test: boolean };
type RegressionRun = { id: string; status: string; verdict: string | null; results: { name?: string; before?: { severity?: string }; after?: { after_status?: string; severity?: string } }[] };
type Occurrence = { source_type: string; source_id: string; audit_id: string | null; severity: string; risk_level: string | null; occurrences: number; sample_size: number; system_version: string | null; observed_at: string };

export default function FindingDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useFinding(id);
  return (
    <QueryBoundary query={query} skeleton={<div className="h-96 skeleton rounded-[var(--radius-lg)]" />}>
      {(f) => <FindingView f={f} />}
    </QueryBoundary>
  );
}

function FindingView({ f }: { f: Finding }) {
  const system = useSystem(f.system_id);
  const cf = (f.details?.counterfactual ?? f.details?.observed) as Record<string, unknown> | undefined;
  const isCounterfactual = f.category === "fairness" && !!(cf?.example || cf?.case_a);
  return (
    <div>
      <PageHeader
        title={`#${f.number} · ${f.title}`}
        breadcrumbs={[{ label: "Findings", href: "/dashboard/findings" }, { label: `#${f.number}` }]}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={f.status} />
            <SeverityBadge severity={f.severity} />
            {f.source !== "audit" ? <Badge>{titleCase(f.source)}</Badge> : null}
          </div>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardBody>
              <p className="text-sm text-[var(--color-text)]">{f.description}</p>
              {f.impact ? <p className="mt-2 text-sm text-[var(--color-text-muted)]">{f.impact}</p> : null}
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Meta label="System" value={system.data?.name ?? "…"} href={`/dashboard/systems/${f.system_id}`} />
                {f.control_ref ? <Meta label="Control" value={f.control_ref} /> : null}
                {f.test_type ? <Meta label="Test" value={titleCase(f.test_type)} /> : null}
                {f.model_version ? <Meta label="Model" value={f.model_version} /> : null}
                {f.evaluator_key ? <Meta label="Evaluator" value={`${f.evaluator_key} v${f.evaluator_version ?? "?"}`} /> : null}
                {f.audit_id ? <Meta label="First seen in" value="audit" href={`/dashboard/audits/${f.audit_id}`} /> : null}
                {f.last_audit_id && f.last_audit_id !== f.audit_id ? <Meta label="Last seen in" value="audit" href={`/dashboard/audits/${f.last_audit_id}`} /> : null}
              </div>
            </CardBody>
          </Card>

          {isCounterfactual ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Scale className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden /> Counterfactual treatment test
                </CardTitle>
              </CardHeader>
              <CardBody>
                <CounterfactualComparison data={cf} />
                <p className="mt-3 text-xs text-[var(--color-text-subtle)]">Fairness results describe controlled behavioural differences under test conditions, not proof of real-world discrimination.</p>
              </CardBody>
            </Card>
          ) : null}

          <ExplanationCard f={f} />
          <EvidenceCard f={f} />
          <OccurrencesCard f={f} />
          <ActivityCard f={f} />
        </div>

        <div className="space-y-4">
          <RiskCard f={f} />
          <LifecycleCard f={f} />
          <RemediationCard f={f} />
        </div>
      </div>
    </div>
  );
}

function Meta({ label, value, href }: { label: string; value: string; href?: string }) {
  const body = (
    <>
      <span className="text-[var(--color-text-subtle)]">{label}:</span>
      <span className="font-mono text-[var(--color-text)]">{value}</span>
    </>
  );
  const cls = "inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1";
  return href ? (
    <Link href={href} className={cn(cls, "hover:border-[var(--color-border-strong)]")}>
      {body}
    </Link>
  ) : (
    <span className={cls}>{body}</span>
  );
}

function RiskCard({ f }: { f: Finding }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Risk</CardTitle>
        <RiskBadge level={f.risk_level} />
      </CardHeader>
      <CardBody>
        <p className="font-mono text-3xl font-semibold" style={{ color: `var(--color-${f.risk_level === "informational" ? "info" : f.risk_level})` }}>
          {f.risk_score.toFixed(0)}
          <span className="text-sm text-[var(--color-text-subtle)]"> / 100</span>
        </p>
        <p className="mt-1 text-xs text-[var(--color-text-subtle)]">
          Confidence {(f.confidence * 100).toFixed(0)}% · {f.occurrences}/{f.sample_size} failing samples
        </p>
        {f.risk_reasons.length ? (
          <>
            <p className="mb-1 mt-3 text-xs font-medium text-[var(--color-text-muted)]">Why this risk level</p>
            <ul className="space-y-1 text-xs text-[var(--color-text-muted)]">
              {f.risk_reasons.map((r, i) => (
                <li key={i}>• {r}</li>
              ))}
            </ul>
          </>
        ) : null}
        <div className="mt-3 space-y-1.5">
          {f.risk_factors.slice(0, 6).map((factor) => (
            <div key={factor.key}>
              <div className="flex justify-between text-[11px] text-[var(--color-text-subtle)]">
                <span>{factor.label}</span>
                <span className="font-mono">{(factor.value * 100).toFixed(0)}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-[var(--color-surface-3)]" aria-hidden>
                <div className="h-full rounded-full bg-[var(--color-accent)]" style={{ width: `${Math.min(100, factor.value * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function LifecycleCard({ f }: { f: Finding }) {
  const can = useCan();
  const transitions = useFindingTransitions(f.id);
  const update = useUpdateFinding(f.id);
  const invalidate = useInvalidate();
  const [target, setTarget] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [expires, setExpires] = useState("");
  const [tags, setTags] = useState(f.tags.join(", "));
  const canWrite = can("findings:write");
  const overdue = f.sla_due_at && new Date(f.sla_due_at) < new Date() && !["resolved", "accepted_risk", "false_positive"].includes(f.status);

  async function save(body: Record<string, unknown>, ok: string) {
    try {
      await update.mutateAsync(body);
      invalidate("finding", f.id);
      toast.success(ok);
    } catch (e) {
      toast.error(errorMessage(e));
      throw e;
    }
  }

  async function applyTransition() {
    if (!target) return;
    if (target === "accepted_risk") {
      await save({ status: target, note: note || undefined, risk_acceptance: { reason, expires_at: new Date(expires).toISOString() } }, "Risk accepted until " + formatDate(expires));
    } else {
      await save({ status: target, note: note || undefined }, `Moved to ${titleCase(target)}`);
    }
    setTarget(null);
    setNote("");
    setReason("");
  }

  const [[minDate, maxDate]] = useState(() => {
    const now = new Date().getTime();
    return [new Date(now + 86_400_000).toISOString().slice(0, 10), new Date(now + 365 * 86_400_000).toISOString().slice(0, 10)];
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lifecycle</CardTitle>
        <StatusBadge status={f.status} />
      </CardHeader>
      <CardBody className="space-y-4">
        <KeyValue
          items={[
            ["SLA due", f.sla_due_at ? <span className={overdue ? "font-medium text-[var(--color-critical)]" : undefined}>{overdue ? "Overdue · " : ""}{formatDate(f.sla_due_at)}</span> : "—"],
            ["Priority", f.priority ? f.priority.toUpperCase() : "—"],
            ["Last seen", timeAgo(f.last_seen_at ?? f.created_at)],
            ...(f.risk_acceptance
              ? ([
                  ["Accepted until", formatDate(f.risk_acceptance.expires_at)],
                  ["Justification", <span key="j" className="text-xs">{f.risk_acceptance.reason}</span>],
                ] as [string, React.ReactNode][])
              : []),
          ]}
        />
        {canWrite ? (
          <>
            <QueryBoundary query={transitions} skeleton={<div className="h-9 skeleton" />}>
              {(t) =>
                t.allowed.length ? (
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-[var(--color-text-muted)]">Move to</p>
                    <div className="flex flex-wrap gap-1.5">
                      {t.allowed.map((s) => (
                        <Button key={s} size="sm" variant="secondary" onClick={() => setTarget(s)}>
                          {titleCase(s === "acknowledged" ? "triaged" : s)}
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-[var(--color-text-subtle)]">No further transitions from this status.</p>
                )
              }
            </QueryBoundary>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Priority">
                {(p) => (
                  <Select {...p} value={f.priority ?? ""} onChange={(e) => e.target.value && save({ priority: e.target.value }, "Priority updated").catch(() => {})}>
                    <option value="">Not set</option>
                    {["p1", "p2", "p3", "p4"].map((x) => (
                      <option key={x} value={x}>
                        {x.toUpperCase()}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Tags" hint="Comma-separated">
                {(p) => (
                  <Input
                    {...p}
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    onBlur={() => {
                      const next = tags.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 20);
                      if (next.join(",") !== f.tags.join(",")) save({ tags: next }, "Tags updated").catch(() => {});
                    }}
                  />
                )}
              </Field>
            </div>
          </>
        ) : (
          <p className="text-xs text-[var(--color-text-subtle)]">Your role can view this finding but not change it.</p>
        )}
      </CardBody>
      <Dialog
        open={!!target}
        onOpenChange={(o) => !o && setTarget(null)}
        title={target === "accepted_risk" ? "Accept this risk" : `Move to ${titleCase(target ?? "")}`}
        description={
          target === "accepted_risk"
            ? "Accepting a risk is a recorded business decision. It needs a justification and an expiry; when it expires the finding re-opens automatically."
            : target === "resolved"
              ? "Resolve only after the fix is verified (re-test). If a later audit observes this failure again, the finding re-opens as a regression."
              : "The change is recorded in the finding history and the audit log."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button onClick={() => applyTransition().catch(() => {})} loading={update.isPending} disabled={target === "accepted_risk" && (reason.trim().length < 10 || !expires)}>
              Confirm
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {target === "accepted_risk" ? (
            <>
              <Field label="Justification" hint="At least 10 characters. Who decided, why the residual risk is acceptable, compensating controls.">
                {(p) => <Textarea {...p} value={reason} onChange={(e) => setReason(e.target.value)} rows={4} maxLength={2000} />}
              </Field>
              <Field label="Expires on" hint="Maximum one year.">
                {(p) => <Input {...p} type="date" min={minDate} max={maxDate} value={expires} onChange={(e) => setExpires(e.target.value)} />}
              </Field>
            </>
          ) : null}
          <Field label="Note (optional)">
            {(p) => <Textarea {...p} value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} />}
          </Field>
        </div>
      </Dialog>
    </Card>
  );
}

function RemediationCard({ f }: { f: Finding }) {
  const can = useCan();
  const invalidate = useInvalidate();
  const [reviewing, setReviewing] = useState(false);
  const [retesting, setRetesting] = useState(false);
  const [retest, setRetest] = useState<RegressionRun | null>(null);
  const recommendation = useQuery({
    queryKey: ["finding", f.id, "recommendation"],
    queryFn: () => api.get<Recommendation>(path`/findings/${f.id}/recommendation`),
    enabled: reviewing,
  });
  const canApply = can("remediations:write") && can("remediations:approve");

  async function applyFix() {
    const rec = recommendation.data;
    if (!rec) return;
    try {
      const rem = await api.post<{ id: string }>(path`/findings/${f.id}/remediations`, { category: rec.category, title: rec.title, description: rec.description, change: rec.change });
      await api.post(path`/remediations/${rem.id}/apply`);
      toast.success("Remediation applied to the system configuration. Re-test to verify it.");
      invalidate("finding", f.id);
    } catch (e) {
      toast.error(errorMessage(e, "Failed to apply remediation"));
      throw e;
    }
  }

  async function runRetest() {
    setRetesting(true);
    setRetest(null);
    try {
      const run = await api.post<RegressionRun>(path`/findings/${f.id}/retest`);
      // Bounded wait: re-tests are short; past that the result stays available on the finding history.
      for (let i = 0; i < 90; i++) {
        const rr = await api.get<RegressionRun>(path`/regression-runs/${run.id}`);
        if (["completed", "failed"].includes(rr.status)) {
          setRetest(rr);
          invalidate("finding", f.id);
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      toast.info("The re-test is still running; its result will appear in the finding history.");
    } catch (e) {
      toast.error(errorMessage(e, "Re-test failed"));
    } finally {
      setRetesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Remediation</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        {canApply ? (
          <Button className="w-full" variant="secondary" icon={Sparkles} onClick={() => setReviewing(true)}>
            Review recommended fix
          </Button>
        ) : null}
        {can("audits:run") ? (
          <Button className="w-full" icon={GitBranch} loading={retesting} onClick={runRetest}>
            Re-test this finding
          </Button>
        ) : null}
        {!canApply && !can("audits:run") ? <p className="text-xs text-[var(--color-text-subtle)]">Your role cannot change remediation.</p> : null}
        {retest ? (
          <div className="space-y-2 rounded-[var(--radius)] border border-[var(--color-border)] p-3" aria-live="polite">
            <div className="flex items-center gap-2 text-sm">
              <ShieldCheck className="h-4 w-4 text-[var(--color-success)]" aria-hidden /> Re-test verdict <StatusBadge status={retest.verdict ?? retest.status} />
            </div>
            {retest.results.map((r, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="flex-1 truncate">{r.name ?? `Test ${i + 1}`}</span>
                <SeverityBadge severity={r.before?.severity ?? "high"} />
                <ArrowRight className="h-3.5 w-3.5 text-[var(--color-text-subtle)]" aria-hidden />
                {r.after?.after_status === "failed" ? <SeverityBadge severity={r.after?.severity ?? "high"} /> : <StatusBadge status="pass" />}
              </div>
            ))}
            <p className="text-[11px] text-[var(--color-text-subtle)]">Measured by re-running the exact failing tests against the current system.</p>
          </div>
        ) : null}
      </CardBody>
      <ConfirmDialog
        open={reviewing}
        onOpenChange={setReviewing}
        tone="primary"
        title="Apply this remediation?"
        confirmLabel="Apply to system configuration"
        description="This changes the system's configuration in Aegis (for example its guardrails or system prompt). It does not deploy anything to your production environment — mirror the change there and re-test."
        onConfirm={applyFix}
      >
        <QueryBoundary query={recommendation} skeleton={<div className="h-24 skeleton" />}>
          {(rec) => (
            <div className="space-y-2">
              <p className="font-medium text-[var(--color-text)]">{rec.title}</p>
              <p>{rec.description}</p>
              <CodeBlock language="configuration change" code={JSON.stringify(rec.change, null, 2)} />
            </div>
          )}
        </QueryBoundary>
      </ConfirmDialog>
    </Card>
  );
}

function ExplanationCard({ f }: { f: Finding }) {
  const [open, setOpen] = useState(false);
  const query = useFindingExplanation(f.id, open);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpenText className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden /> Explanation
        </CardTitle>
        {!open ? (
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            Explain this finding
          </Button>
        ) : null}
      </CardHeader>
      {open ? (
        <CardBody>
          <QueryBoundary query={query} skeleton={<div className="h-32 skeleton" />}>
            {(x) => (
              <div className="space-y-3 text-sm">
                <Section title="What happened">{x.what_happened}</Section>
                <Section title="Why it matters">{x.why_it_matters}</Section>
                <Section title="Recommended remediation">
                  <span className="font-medium">{x.remediation.title}.</span> {x.remediation.description}
                </Section>
                {x.next_steps.length ? (
                  <div>
                    <p className="mb-1 text-xs font-medium text-[var(--color-text-muted)]">Next steps</p>
                    <ol className="list-decimal space-y-0.5 pl-5 text-[var(--color-text-muted)]">
                      {x.next_steps.map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ol>
                  </div>
                ) : null}
                <p className="text-[11px] text-[var(--color-text-subtle)]">
                  {x.generated_by.startsWith("deterministic") ? "Generated from the finding's recorded data (no model)." : `Generated by ${x.generated_by}.`} {x.disclaimer}
                </p>
              </div>
            )}
          </QueryBoundary>
        </CardBody>
      ) : null}
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-0.5 text-xs font-medium text-[var(--color-text-muted)]">{title}</p>
      <p className="text-[var(--color-text)]">{children}</p>
    </div>
  );
}

function EvidenceCard({ f }: { f: Finding }) {
  const evidence = useFindingEvidence(f.id);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Evidence</CardTitle>
      </CardHeader>
      <CardBody>
        <QueryBoundary query={evidence} skeleton={<div className="h-32 skeleton" />}>
          {(items) => (items.length ? <EvidenceList items={items} /> : <p className="text-sm text-[var(--color-text-subtle)]">{f.evidence_unavailable_reason ?? "No evidence recorded."}</p>)}
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}

function OccurrencesCard({ f }: { f: Finding }) {
  const query = useQuery({ queryKey: ["finding", f.id, "occurrences"], queryFn: () => api.get<Occurrence[]>(path`/findings/${f.id}/occurrences`) });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Observations</CardTitle>
        <span className="text-xs text-[var(--color-text-subtle)]">Each audit or run that observed this finding</span>
      </CardHeader>
      <CardBody>
        <QueryBoundary query={query} skeleton={<div className="h-24 skeleton" />}>
          {(rows) =>
            rows.length === 0 ? (
              <p className="text-sm text-[var(--color-text-subtle)]">No recorded observations.</p>
            ) : (
              <ul className="space-y-1.5 text-xs">
                {rows.map((o, i) => (
                  <li key={`${o.source_id}-${i}`} className="flex flex-wrap items-center gap-2 rounded-[var(--radius)] border border-[var(--color-border)] px-2.5 py-1.5">
                    <Badge>{titleCase(o.source_type)}</Badge>
                    <SeverityBadge severity={o.severity} />
                    <span className="font-mono">
                      {o.occurrences}/{o.sample_size}
                    </span>
                    {o.system_version ? <span className="text-[var(--color-text-subtle)]">v{o.system_version}</span> : null}
                    {o.audit_id ? (
                      <Link href={`/dashboard/audits/${o.audit_id}`} className="text-[var(--color-accent-bright)] hover:underline">
                        Open audit
                      </Link>
                    ) : null}
                    <span className="ml-auto text-[var(--color-text-subtle)]">{formatDateTime(o.observed_at)}</span>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryBoundary>
      </CardBody>
    </Card>
  );
}

function ActivityCard({ f }: { f: Finding }) {
  const can = useCan();
  const events = useFindingEvents(f.id);
  const comments = useFindingComments(f.id);
  const invalidate = useInvalidate();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  async function post(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    try {
      await api.post(path`/findings/${f.id}/comments`, { body: body.trim() });
      setBody("");
      invalidate("finding", f.id, "comments");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const timeline = [
    ...(events.data ?? []).map((e) => ({ kind: "event" as const, at: e.created_at, id: e.id, e })),
    ...(comments.data ?? []).map((c) => ({ kind: "comment" as const, at: c.created_at, id: c.id, c })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <Card>
      <CardHeader>
        <CardTitle>History & discussion</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        {events.isLoading || comments.isLoading ? (
          <div className="h-24 skeleton" />
        ) : timeline.length === 0 ? (
          <p className="text-sm text-[var(--color-text-subtle)]">No activity yet.</p>
        ) : (
          <ol className="space-y-3">
            {timeline.map((item) =>
              item.kind === "event" ? (
                <li key={item.id} className="flex gap-2.5 text-xs">
                  <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-subtle)]" aria-hidden />
                  <div>
                    <p className="text-[var(--color-text-muted)]">
                      <span className="font-medium text-[var(--color-text)]">{item.e.actor_label ?? "Aegis"}</span> {titleCase(item.e.type).toLowerCase()}
                      {item.e.from_status && item.e.to_status ? ` · ${titleCase(item.e.from_status)} → ${titleCase(item.e.to_status)}` : ""}
                    </p>
                    {item.e.note ? <p className="mt-0.5 text-[var(--color-text-muted)]">“{item.e.note}”</p> : null}
                    <p className="text-[var(--color-text-subtle)]">{formatDateTime(item.at)}</p>
                  </div>
                </li>
              ) : (
                <li key={item.id} className="flex gap-2.5 text-sm">
                  <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-accent-bright)]" aria-hidden />
                  <div className="min-w-0 flex-1 rounded-[var(--radius)] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2">
                    <p className="text-xs text-[var(--color-text-subtle)]">
                      <span className="font-medium text-[var(--color-text)]">{item.c.author_label ?? "Member"}</span> · {formatDateTime(item.at)}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap break-words">{item.c.body}</p>
                  </div>
                </li>
              ),
            )}
          </ol>
        )}
        {can("findings:comment") ? (
          <form onSubmit={post} className="space-y-2">
            <label htmlFor="comment" className="sr-only">
              Add a comment
            </label>
            <Textarea id="comment" value={body} onChange={(e) => setBody(e.target.value)} rows={3} maxLength={10_000} placeholder="Add context, a decision or a link to the fix…" />
            <div className="flex justify-end">
              <Button type="submit" size="sm" loading={busy} disabled={!body.trim()}>
                Comment
              </Button>
            </div>
          </form>
        ) : null}
      </CardBody>
    </Card>
  );
}
