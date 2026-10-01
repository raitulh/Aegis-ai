"use client";
import { BookOpen, Plus, ScrollText, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { DataTable, RowLink, TD, TH, THead, TR } from "@/components/dashboard/data-table";
import { NewPolicyDialog } from "@/components/dashboard/new-policy-dialog";
import { PageHeader } from "@/components/dashboard/page-header";
import { QueryBoundary } from "@/components/dashboard/query-boundary";
import { Dialog } from "@/components/ui/dialog";
import { PlanLimitNotice } from "@/components/ui/display";
import { Field, Input, Select, Textarea } from "@/components/ui/forms";
import { TabPanel, Tabs, TabsList, TabTrigger } from "@/components/ui/overlays";
import { Badge, Button, Card, CardBody, EmptyState, StatusBadge } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { useCan, useInvalidate, usePolicies, usePolicyTemplates, useRuntimePolicies } from "@/lib/queries";
import type { PolicyTemplate, RuntimePolicy } from "@/lib/types";
import { timeAgo, titleCase } from "@/lib/utils";

const TABS = ["runtime", "compliance", "library"] as const;
type Tab = (typeof TABS)[number];

export default function PoliciesPage() {
  const search = useSearchParams();
  const router = useRouter();
  const can = useCan();
  const initial = (TABS as readonly string[]).includes(search.get("tab") ?? "") ? (search.get("tab") as Tab) : "runtime";
  const [tab, setTab] = useState<Tab>(initial);
  const [newRuntime, setNewRuntime] = useState<{ template?: PolicyTemplate } | null>(null);
  const [newCompliance, setNewCompliance] = useState(false);

  const deepLink = search.get("new") === "1" && can("policies:write");
  const runtimeDialog = newRuntime ?? (deepLink ? {} : null);

  const action =
    can("policies:write") && tab !== "library" ? (
      <Button icon={Plus} onClick={() => (tab === "runtime" ? setNewRuntime({}) : setNewCompliance(true))}>
        {tab === "runtime" ? "New runtime policy" : "New compliance policy"}
      </Button>
    ) : null;

  return (
    <div>
      <PageHeader
        title="Policies & controls"
        description="Runtime policies decide what agents may do as it happens. Compliance policies compile your written requirements into testable controls. Neither is a certification."
        actions={action}
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
          router.replace(`/dashboard/policies?tab=${v}`);
        }}
      >
        <TabsList className="mb-5">
          <TabTrigger value="runtime">Runtime policies</TabTrigger>
          <TabTrigger value="compliance">Compliance policies</TabTrigger>
          <TabTrigger value="library">Policy library</TabTrigger>
        </TabsList>
        <TabPanel value="runtime">{tab === "runtime" ? <RuntimeTab onNew={() => setNewRuntime({})} /> : null}</TabPanel>
        <TabPanel value="compliance">{tab === "compliance" ? <ComplianceTab onNew={() => setNewCompliance(true)} /> : null}</TabPanel>
        <TabPanel value="library">{tab === "library" ? <LibraryTab onUse={(t) => setNewRuntime({ template: t })} /> : null}</TabPanel>
      </Tabs>
      {runtimeDialog ? (
        <NewRuntimePolicyDialog
          key={runtimeDialog.template?.key ?? "blank"}
          template={runtimeDialog.template}
          onClose={() => {
            setNewRuntime(null);
            if (deepLink) router.replace("/dashboard/policies?tab=runtime");
          }}
        />
      ) : null}
      <NewPolicyDialog open={newCompliance} onOpenChange={setNewCompliance} />
    </div>
  );
}

function RuntimeTab({ onNew }: { onNew: () => void }) {
  const can = useCan();
  const query = useRuntimePolicies();
  return (
    <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No runtime policies yet"
            description="Start from the policy library or write your own rules. Draft, simulate against recorded events, then publish."
            action={
              can("policies:write") ? (
                <Button size="sm" icon={Plus} onClick={onNew}>
                  New runtime policy
                </Button>
              ) : null
            }
          />
        ) : (
          <DataTable caption="Runtime policies">
            <THead>
              <tr>
                <TH>Policy</TH>
                <TH>Status</TH>
                <TH>Latest version</TH>
                <TH>Category</TH>
                <TH>Updated</TH>
              </tr>
            </THead>
            <tbody>
              {page.items.map((p) => (
                <TR key={p.id} href={`/dashboard/policies/runtime/${p.id}`}>
                  <TD>
                    <RowLink href={`/dashboard/policies/runtime/${p.id}`}>{p.name}</RowLink>
                    <p className="font-mono text-xs text-[var(--color-text-subtle)]">{p.key}</p>
                  </TD>
                  <TD>
                    <StatusBadge status={p.status} />
                  </TD>
                  <TD className="font-mono text-xs">v{p.latest_version}</TD>
                  <TD className="text-[var(--color-text-muted)]">{p.category ? titleCase(p.category) : "—"}</TD>
                  <TD className="text-[var(--color-text-subtle)]">{timeAgo(p.updated_at)}</TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )
      }
    </QueryBoundary>
  );
}

function ComplianceTab({ onNew }: { onNew: () => void }) {
  const can = useCan();
  const query = usePolicies();
  return (
    <QueryBoundary query={query} skeleton={<div className="h-64 skeleton rounded-[var(--radius-lg)]" />}>
      {(page) =>
        page.items.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No compliance policies yet"
            description="Paste policy text or upload a PDF/DOCX. Aegis compiles it into controls that audits test against, with provenance back to the source."
            action={
              can("policies:write") ? (
                <Button size="sm" icon={Plus} onClick={onNew}>
                  New compliance policy
                </Button>
              ) : null
            }
          />
        ) : (
          <DataTable caption="Compliance policies">
            <THead>
              <tr>
                <TH>Policy</TH>
                <TH>Key</TH>
                <TH>Status</TH>
                <TH>Category</TH>
                <TH>Updated</TH>
              </tr>
            </THead>
            <tbody>
              {page.items.map((p) => (
                <TR key={p.id} href={`/dashboard/policies/${p.id}`}>
                  <TD>
                    <div className="flex items-center gap-2">
                      <RowLink href={`/dashboard/policies/${p.id}`}>{p.name}</RowLink>
                      {p.is_demo ? <Badge tone="warning">DEMO</Badge> : null}
                    </div>
                  </TD>
                  <TD className="font-mono text-xs">{p.key}</TD>
                  <TD>
                    <StatusBadge status={p.status} />
                  </TD>
                  <TD className="text-[var(--color-text-muted)]">{p.category ?? "—"}</TD>
                  <TD className="text-[var(--color-text-subtle)]">{timeAgo(p.updated_at)}</TD>
                </TR>
              ))}
            </tbody>
          </DataTable>
        )
      }
    </QueryBoundary>
  );
}

function LibraryTab({ onUse }: { onUse: (t: PolicyTemplate) => void }) {
  const can = useCan();
  const query = usePolicyTemplates();
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Templates are starting points for technical controls. Using one creates a draft policy you own, edit and version. Adopting a template does not make a system compliant with any regulation.
      </p>
      <QueryBoundary query={query}>
        {(templates) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {templates.map((t) => (
              <Card key={t.key} className="flex flex-col">
                <CardBody className="flex flex-1 flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <BookOpen className="h-4 w-4 text-[var(--color-accent-bright)]" aria-hidden />
                      <h3 className="text-sm font-semibold">{t.name}</h3>
                    </div>
                    <Badge>{titleCase(t.category)}</Badge>
                  </div>
                  <p className="flex-1 text-sm text-[var(--color-text-muted)]">{t.summary}</p>
                  {can("policies:write") ? (
                    <Button variant="secondary" size="sm" onClick={() => onUse(t)}>
                      Use template
                    </Button>
                  ) : null}
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}

const BLANK = `name: My runtime policy
description: Describe what this policy protects.
rules:
  - id: example-rule
    when:
      event: [tool.call]
      tool: [delete_records]
    action: require_approval
    severity: high
    message: Deleting records requires a human decision.
`;

/** Mounted per open (keyed by template), so its form state always starts from the chosen template. */
function NewRuntimePolicyDialog({ template, onClose }: { template?: PolicyTemplate; onClose: () => void }) {
  const router = useRouter();
  const invalidate = useInvalidate();
  const templates = usePolicyTemplates();
  const [templateKey, setTemplateKey] = useState(template?.key ?? "");
  const [name, setName] = useState(template?.name ?? "");
  const [source, setSource] = useState(template?.source_yaml ?? BLANK);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const policy = await api.post<RuntimePolicy>("/runtime-policies", templateKey ? { template_key: templateKey, name: name || undefined } : { name: name || undefined, source_yaml: source });
      invalidate("runtime-policies");
      onClose();
      router.push(`/dashboard/policies/runtime/${policy.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New runtime policy"
      description="Creates version 1 as a draft. Nothing is enforced until you publish it and assign it to systems."
      className="max-w-2xl"
    >
      <form id="new-runtime-policy" onSubmit={create} className="space-y-4">
        <Field label="Start from">
          {(p) => (
            <Select
              {...p}
              value={templateKey}
              onChange={(e) => {
                const t = templates.data?.find((x) => x.key === e.target.value);
                setTemplateKey(e.target.value);
                setSource(t?.source_yaml ?? BLANK);
                if (t && !name) setName(t.name);
              }}
            >
              <option value="">Blank policy</option>
              {(templates.data ?? []).map((t) => (
                <option key={t.key} value={t.key}>
                  Template · {t.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Name" hint="Defaults to the name inside the policy source.">
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />}
        </Field>
        <Field label="Policy source (YAML)" hint={templateKey ? "The template source is used as-is; edit it in Policy Studio after creating." : "You can keep editing in Policy Studio."}>
          {(p) => <Textarea {...p} value={source} onChange={(e) => setSource(e.target.value)} readOnly={!!templateKey} rows={14} spellCheck={false} className="font-mono text-xs" />}
        </Field>
        {error ? <PlanLimitNotice error={error} /> : null}
        {error && !(error as { isPlanLimit?: boolean }).isPlanLimit ? (
          <p role="alert" className="text-sm text-[var(--color-critical)]">
            {errorMessage(error)}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            Create draft
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
