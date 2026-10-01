"use client";

import { useQuery } from "@tanstack/react-query";
import { Building2, Pencil, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAdminOrg } from "@/components/orgs/org-admin-context";
import type { Department } from "@/components/orgs/types";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Textarea } from "@/components/ui/form";
import { EmptyState, QueryState, SkeletonRows } from "@/components/ui/states";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useApiMutation } from "@/lib/hooks";

type DeptBody = { name: string; description: string | null };

function EditDepartment({ slug, dept }: { slug: string; dept: Department }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(dept.name);
  const [description, setDescription] = useState(dept.description ?? "");
  const [error, setError] = useState<ApiError | null>(null);
  const save = useApiMutation((body: DeptBody) => patch<Department>(`/orgs/${slug}/departments/${dept.id}`, body), {
    success: "Department updated",
    invalidate: [["orgs", slug]],
    onSuccess: () => setOpen(false),
    onError: setError,
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setName(dept.name);
          setDescription(dept.description ?? "");
          setError(null);
        }
      }}
      trigger={<Button size="sm" variant="ghost" icon={<Pencil className="h-4 w-4" />} aria-label={`Edit ${dept.name}`}>Edit</Button>}
      title="Edit department"
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button loading={save.isPending} disabled={name.trim().length < 2} onClick={() => save.mutate({ name: name.trim(), description: description.trim() || null })}>Save</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name" required error={error?.fields.name}>
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} minLength={2} maxLength={160} />}
        </Field>
        <Field label="Description" error={error?.fields.description}>
          {(p) => <Textarea {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} rows={3} />}
        </Field>
        <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
      </div>
    </Dialog>
  );
}

export default function OrgDepartmentsPage() {
  const org = useAdminOrg();
  const depts = useQuery({ queryKey: ["orgs", org.slug, "departments"], queryFn: () => get<Department[]>(`/orgs/${org.slug}/departments`) });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const create = useApiMutation((body: DeptBody) => post<Department>(`/orgs/${org.slug}/departments`, body), {
    success: (d) => `Added ${d.name}`,
    invalidate: [["orgs", org.slug]],
    onSuccess: () => {
      setName("");
      setDescription("");
      setError(null);
    },
    onError: setError,
  });
  const remove = useApiMutation((id: string) => del<{ message: string }>(`/orgs/${org.slug}/departments/${id}`), {
    success: (r) => r.message,
    invalidate: [["orgs", org.slug]],
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    create.mutate({ name: name.trim(), description: description.trim() || null });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Departments</h1>
        <p className="mt-1 text-sm text-muted">Members can pick a department when requesting to join or during onboarding. Department counts appear on the dashboard.</p>
      </div>

      <Card>
        <CardHeader title="Add a department" />
        <form onSubmit={submit} noValidate>
          <CardBody className="space-y-4">
            <Field label="Name" required error={error?.fields.name}>
              {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} minLength={2} maxLength={160} placeholder="e.g. Computer Science" />}
            </Field>
            <Field label="Description" error={error?.fields.description}>
              {(p) => <Textarea {...p} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} rows={2} />}
            </Field>
            <FormError message={error && !Object.keys(error.fields).length ? error.message : null} />
          </CardBody>
          <CardFooter>
            <Button type="submit" loading={create.isPending} disabled={name.trim().length < 2} icon={<Plus className="h-4 w-4" />}>Add department</Button>
          </CardFooter>
        </form>
      </Card>

      <QueryState
        query={depts}
        loading={<SkeletonRows rows={4} />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={<Building2 className="h-5 w-5" />} title="No departments yet" description="Add faculties, schools or teams to organize your members." />}
      >
        {(rows) => (
          <ul className="divide-y divide-border overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface">
            {rows.map((d) => (
              <li key={d.id} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-fg">{d.name}</p>
                  {d.description ? <p className="mt-0.5 text-sm text-muted">{d.description}</p> : null}
                </div>
                <div className="flex gap-1">
                  <EditDepartment slug={org.slug} dept={d} />
                  <ConfirmDialog
                    trigger={<Button size="sm" variant="ghost" className="text-danger" icon={<Trash2 className="h-4 w-4" />} aria-label={`Delete ${d.name}`}>Delete</Button>}
                    title={`Delete ${d.name}?`}
                    description="Members in this department stay members; their department is cleared."
                    confirmLabel="Delete department"
                    onConfirm={async () => {
                      await remove.mutateAsync(d.id).catch(() => undefined);
                    }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </div>
  );
}
