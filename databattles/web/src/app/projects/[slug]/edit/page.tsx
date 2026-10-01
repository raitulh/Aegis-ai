"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Crown, ExternalLink, LogOut, Trash2, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { describeError, settle } from "@/components/catalog/errors";
import { MemberRow } from "@/components/catalog/project-bits";
import { ProjectForm, projectPayload, projectToForm, type ProjectFormValues } from "@/components/catalog/project-form";
import type { OrgMembership, ProjectDetail, ProjectMedia, ProjectMember } from "@/components/catalog/types";
import { DemoBadge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { FileDrop } from "@/components/ui/misc";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, NotFoundState, PermissionDenied, SkeletonRows } from "@/components/ui/states";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { ApiError, del, get, patch, post, upload } from "@/lib/api";
import { hasRole, useApiMutation, useConfig, useMe, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";

const MAX_MEDIA = 8;

/* ------------------------------------------------------------------------------------------------ details */

function DetailsTab({ p }: { p: ProjectDetail }) {
  const qc = useQueryClient();
  const [error, setError] = useState<ApiError | null>(null);
  const [formKey, setFormKey] = useState(0);
  const initial = useMemo(() => projectToForm(p), [p]);
  const memberships = useQuery({ queryKey: ["orgs", "me", "memberships"], queryFn: () => get<OrgMembership[]>("/orgs/me/memberships") });
  const save = useApiMutation((v: ProjectFormValues) => patch<ProjectDetail>(`/projects/${p.slug}`, projectPayload(v, initial)), {
    success: "Project saved",
    invalidate: [["projects", "list"]],
    onSuccess: (updated) => {
      qc.setQueryData(qk.project(p.slug), updated);
      setFormKey((k) => k + 1);
    },
    onError: (e) => setError(e),
  });
  return (
    <ProjectForm
      key={formKey}
      mode="edit"
      initial={initial}
      memberships={memberships.data}
      currentOrg={p.org}
      knownDatasets={p.datasets}
      submitting={save.isPending}
      error={error}
      submitLabel="Save changes"
      onSubmit={(v) => {
        setError(null);
        save.mutate(v);
      }}
    />
  );
}

/* ------------------------------------------------------------------------------------------------ gallery */

function MediaItem({ slug, m, index, count }: { slug: string; m: ProjectMedia; index: number; count: number }) {
  const [alt, setAlt] = useState(m.alt_text);
  const invalidate = [qk.project(slug), ["projects", "list"]] as const;
  const save = useApiMutation((body: { alt_text?: string; position?: number }) => patch<{ message: string }>(`/projects/${slug}/media/${m.id}`, body), {
    success: (d) => d.message,
    invalidate,
  });
  const remove = useApiMutation(() => del<{ message: string }>(`/projects/${slug}/media/${m.id}`), { success: (d) => d.message, invalidate });
  return (
    <li className="flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border bg-surface p-3 sm:flex-row sm:items-center">
      { }
      <img src={m.url} alt={m.alt_text || ""} className="h-24 w-full rounded-[var(--radius-md)] object-cover sm:w-40" />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2 text-xs text-subtle">
          <span className="font-medium text-fg">#{index + 1}</span>
          {index === 0 ? <span className="rounded bg-accent-soft px-1.5 py-0.5 text-accent-strong">Cover</span> : null}
          <span>{m.width}×{m.height}</span>
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate({ alt_text: alt.trim() });
          }}
        >
          <Input aria-label={`Alt text for image ${index + 1}`} value={alt} onChange={(e) => setAlt(e.target.value)} maxLength={200} placeholder="Describe the image for screen readers" className="h-9" />
          <Button type="submit" size="sm" variant="secondary" disabled={alt.trim() === m.alt_text} loading={save.isPending && save.variables?.alt_text !== undefined}>
            Save
          </Button>
        </form>
      </div>
      <div className="flex shrink-0 gap-1 sm:flex-col">
        <Button size="icon" variant="ghost" aria-label={`Move image ${index + 1} earlier`} disabled={index === 0 || save.isPending} onClick={() => save.mutate({ position: index - 1 })}>
          <ArrowUp className="h-4 w-4" />
        </Button>
        <Button size="icon" variant="ghost" aria-label={`Move image ${index + 1} later`} disabled={index === count - 1 || save.isPending} onClick={() => save.mutate({ position: index + 1 })}>
          <ArrowDown className="h-4 w-4" />
        </Button>
        <ConfirmDialog
          trigger={<Button size="icon" variant="ghost" aria-label={`Delete image ${index + 1}`}><Trash2 className="h-4 w-4 text-danger" /></Button>}
          title="Delete this image?"
          description="It is removed from the gallery permanently."
          confirmLabel="Delete image"
          onConfirm={() => settle(remove.mutateAsync(undefined))}
        />
      </div>
    </li>
  );
}

function GalleryTab({ p }: { p: ProjectDetail }) {
  const qc = useQueryClient();
  const config = useConfig();
  const maxMb = config.data?.limits.image_mb;
  const [alt, setAlt] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<{ title: string; message: string } | null>(null);
  const [dropKey, setDropKey] = useState(0);
  const full = p.media.length >= MAX_MEDIA;

  async function send(file: File) {
    setError(null);
    setProgress(0);
    const form = new FormData();
    form.append("file", file);
    form.append("alt_text", alt.trim());
    try {
      await upload(`/projects/${p.slug}/media`, form, { onProgress: setProgress });
      toast.success("Image added to the gallery");
      setAlt("");
      await Promise.all([qc.invalidateQueries({ queryKey: qk.project(p.slug) }), qc.invalidateQueries({ queryKey: ["projects", "list"] })]);
    } catch (e) {
      setError(describeError(e, "Upload failed"));
    } finally {
      setProgress(null);
      setDropKey((k) => k + 1);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Add an image" description={`PNG, JPEG or WebP${maxMb ? ` up to ${maxMb} MB` : ""}. Images are re-encoded and metadata (EXIF/GPS) is stripped. Up to ${MAX_MEDIA} images.`} />
        <CardBody className="space-y-4">
          {full ? (
            <InlineNotice tone="info" title="Gallery is full">Delete an image to add another.</InlineNotice>
          ) : (
            <>
              <Field label="Alt text" hint="Describe the image for people using screen readers. You can edit it later.">
                {(fp) => <Input {...fp} value={alt} onChange={(e) => setAlt(e.target.value)} maxLength={200} placeholder="e.g. Dashboard showing model accuracy by class" />}
              </Field>
              <FileDrop
                key={dropKey}
                accept=".png,.jpg,.jpeg,.webp"
                maxBytes={maxMb ? maxMb * 1024 * 1024 : undefined}
                disabled={progress !== null}
                progress={progress}
                hint="The first image becomes the project cover."
                onFile={send}
              />
            </>
          )}
          {error ? <InlineNotice tone="danger" title={error.title}>{error.message}</InlineNotice> : null}
        </CardBody>
      </Card>

      {p.media.length ? (
        <ol className="space-y-3" aria-label="Gallery images in display order">
          {p.media.map((m, i) => <MediaItem key={`${m.id}:${m.alt_text}`} slug={p.slug} m={m} index={i} count={p.media.length} />)}
        </ol>
      ) : (
        <EmptyState title="No images yet" description="Screenshots and diagrams make projects much easier to understand." />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ members */

function MemberControls({ slug, m, onLeave }: { slug: string; m: ProjectMember; onLeave?: () => void }) {
  const invalidate = [qk.project(slug)] as const;
  const setRole = useApiMutation((role: string) => post<{ message: string }>(`/projects/${slug}/members`, { handle: m.user.handle, role }), {
    success: "Role updated",
    invalidate,
  });
  const remove = useApiMutation(() => del<{ message: string }>(`/projects/${slug}/members/${m.user.id}`), {
    success: (d) => d.message,
    invalidate,
    onSuccess: () => onLeave?.(),
  });
  const transfer = useApiMutation(() => post<{ message: string }>(`/projects/${slug}/transfer`, { user_id: m.user.id }), {
    success: (d) => d.message,
    invalidate: [qk.project(slug), ["projects", "list"]],
  });
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Select aria-label={`Role for ${m.user.display_name}`} className="h-8 w-auto text-xs" value={m.role} disabled={setRole.isPending} onChange={(e) => setRole.mutate(e.target.value)}>
        <option value="maintainer">Maintainer</option>
        <option value="contributor">Contributor</option>
      </Select>
      <ConfirmDialog
        trigger={<Button size="icon" variant="ghost" aria-label={`Make ${m.user.display_name} the owner`}><Crown className="h-4 w-4" /></Button>}
        title={`Transfer ownership to ${m.user.display_name}?`}
        description="They become the owner and you become a maintainer. Maintainer verification is re-evaluated against their GitHub account on the next save."
        confirmLabel="Transfer ownership"
        onConfirm={() => settle(transfer.mutateAsync(undefined))}
      />
      <ConfirmDialog
        trigger={<Button size="icon" variant="ghost" aria-label={`Remove ${m.user.display_name}`}><Trash2 className="h-4 w-4 text-danger" /></Button>}
        title={`Remove ${m.user.display_name}?`}
        description="They lose access to edit the project. Their past contributions stay attributed."
        confirmLabel="Remove member"
        onConfirm={() => settle(remove.mutateAsync(undefined))}
      />
    </div>
  );
}

function AddMemberForm({ slug }: { slug: string }) {
  const [handle, setHandle] = useState("");
  const [role, setRole] = useState("contributor");
  const [error, setError] = useState<ApiError | null>(null);
  const add = useApiMutation(() => post<{ message: string }>(`/projects/${slug}/members`, { handle: handle.trim().replace(/^@/, ""), role }), {
    success: (d) => d.message,
    invalidate: [qk.project(slug)],
    onSuccess: () => {
      setHandle("");
      setError(null);
    },
    onError: (e) => setError(e),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!handle.trim()) return;
    add.mutate(undefined);
  }
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end" noValidate>
      <Field label="Handle" hint="Their DataBattles username. They'll get a notification." error={error?.fields.handle ?? (error?.status === 404 ? error.message : undefined)}>
        {(fp) => <Input {...fp} value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@handle" maxLength={31} autoComplete="off" />}
      </Field>
      <Field label="Role" error={error?.fields.role}>
        {(fp) => (
          <Select {...fp} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="contributor">Contributor</option>
            <option value="maintainer">Maintainer (can edit)</option>
          </Select>
        )}
      </Field>
      <Button type="submit" loading={add.isPending} disabled={!handle.trim()} icon={<UserPlus className="h-4 w-4" aria-hidden />} className="sm:mb-[1.375rem]">
        Add
      </Button>
    </form>
  );
}

function MembersTab({ p, meId }: { p: ProjectDetail; meId: string }) {
  const router = useRouter();
  const isOwner = p.viewer.is_owner;
  const self = p.members.find((m) => m.user.id === meId && m.role !== "owner");
  return (
    <div className="space-y-6">
      {isOwner ? (
        <Card>
          <CardHeader title="Add a member" description="Maintainers can edit the project and gallery; contributors are credited on the project page. Up to 50 members." />
          <CardBody><AddMemberForm slug={p.slug} /></CardBody>
        </Card>
      ) : null}
      <Card>
        <CardHeader title={`Members · ${p.members.length}`} description={isOwner ? "Change roles, remove members or transfer ownership." : "Only the owner can manage members."} />
        <CardBody className="py-2">
          <ul className="divide-y divide-border">
            {p.members.map((m) => (
              <MemberRow key={m.user.id} m={m}>
                {isOwner && m.role !== "owner" ? <MemberControls slug={p.slug} m={m} /> : null}
              </MemberRow>
            ))}
          </ul>
        </CardBody>
      </Card>
      {self ? (
        <Card>
          <CardHeader title="Leave project" description="You'll no longer be listed or able to edit it." />
          <CardBody>
            <LeaveButton slug={p.slug} m={self} onLeft={() => router.push(`/projects/${p.slug}`)} />
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}

function LeaveButton({ slug, m, onLeft }: { slug: string; m: ProjectMember; onLeft: () => void }) {
  const leave = useApiMutation(() => del<{ message: string }>(`/projects/${slug}/members/${m.user.id}`), {
    success: "You left the project",
    invalidate: [["projects"]],
    onSuccess: onLeft,
  });
  return (
    <ConfirmDialog
      trigger={<Button variant="outline" icon={<LogOut className="h-4 w-4" aria-hidden />}>Leave project</Button>}
      title="Leave this project?"
      description="The owner can add you back later."
      confirmLabel="Leave"
      onConfirm={() => settle(leave.mutateAsync(undefined))}
    />
  );
}

/* ------------------------------------------------------------------------------------------------ danger zone */

function DangerTab({ p }: { p: ProjectDetail }) {
  const router = useRouter();
  const remove = useApiMutation(() => del<{ message: string }>(`/projects/${p.slug}`), {
    success: (d) => d.message,
    invalidate: [["projects", "list"]],
    onSuccess: () => router.push("/projects?owner=me"),
  });
  return (
    <Card className="border-danger/40">
      <CardHeader title="Delete project" description="Permanently deletes the project, its gallery, member list and the discussion threads attached to it. This cannot be undone." />
      <CardBody>
        <ConfirmDialog
          trigger={<Button variant="danger" icon={<Trash2 className="h-4 w-4" aria-hidden />}>Delete project</Button>}
          title={`Delete “${p.title}”?`}
          description={`This permanently deletes the project${p.discussion_count ? ` and its ${p.discussion_count} discussion thread${p.discussion_count === 1 ? "" : "s"}` : ""}. It cannot be undone.`}
          confirmLabel="Delete forever"
          onConfirm={() => settle(remove.mutateAsync(undefined))}
        />
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------------------------------------ page */

export default function EditProjectPage() {
  const { slug } = useParams<{ slug: string }>();
  const me = useRequireAuth();
  const viewer = useMe().data;
  const q = useQuery({ queryKey: qk.project(slug), queryFn: () => get<ProjectDetail>(`/projects/${slug}`), enabled: Boolean(me.data) });
  const [tab, setTab] = useState("details");

  if (me.isPending || !me.data || q.isPending) return <Container size="lg" className="py-8"><SkeletonRows rows={8} /></Container>;
  if (q.isError) {
    return (
      <Container size="lg" className="py-10">
        {q.error instanceof ApiError && q.error.status === 404 ? <NotFoundState what="project" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      </Container>
    );
  }
  const p = q.data;
  if (!p.viewer.can_edit) {
    return (
      <Container size="lg" className="py-10">
        <PermissionDenied message="Only the project owner and maintainers can edit this project." />
      </Container>
    );
  }
  const canDelete = p.viewer.is_owner || hasRole(viewer, "platform_admin");

  return (
    <Container size="lg" className="pb-16">
      <nav aria-label="Breadcrumb" className="pt-6 text-sm text-subtle">
        <Link href="/projects" className="hover:text-fg">Projects</Link>
        <span aria-hidden> / </span>
        <Link href={`/projects/${p.slug}`} className="hover:text-fg">{p.title}</Link>
        <span aria-hidden> / </span>
        <span className="text-muted">Edit</span>
      </nav>
      <PageHeader
        className="pt-4"
        eyebrow={p.viewer.is_owner ? "You own this project" : "You maintain this project"}
        title={<span className="inline-flex flex-wrap items-center gap-3">Edit {p.title}{p.is_demo ? <DemoBadge /> : null}</span>}
        actions={<LinkButton href={`/projects/${p.slug}`} variant="secondary" icon={<ExternalLink className="h-4 w-4" aria-hidden />}>View project</LinkButton>}
      />
      {p.taken_down ? (
        <div className="mb-6">
          <InlineNotice tone="danger" title="Taken down by a moderator">{p.takedown_reason ?? "This project is hidden from the public."}</InlineNotice>
        </div>
      ) : null}
      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "details", label: "Details" },
          { value: "gallery", label: "Gallery", count: p.media.length },
          { value: "members", label: "Members", count: p.members.length },
          ...(canDelete ? [{ value: "danger", label: "Danger zone" }] : []),
        ]}
      >
        <TabPanel value="details"><DetailsTab p={p} /></TabPanel>
        <TabPanel value="gallery"><GalleryTab p={p} /></TabPanel>
        <TabPanel value="members"><MembersTab p={p} meId={me.data.id} /></TabPanel>
        {canDelete ? <TabPanel value="danger"><DangerTab p={p} /></TabPanel> : null}
      </Tabs>
    </Container>
  );
}
